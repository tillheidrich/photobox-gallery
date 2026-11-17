"""Aufbewahrung und Löschprotokoll — löschen, und beweisen können, dass gelöscht wurde.

Zwei Dinge, die zusammengehören:

* **Speicherbegrenzung**: Ein Event hat eine Aufbewahrungsdauer und ein
  Ablaufdatum. Bisher standen beide nur im Formular — abgelaufene Galerien
  waren für Gäste unsichtbar, die Dateien lagen weiter auf der Platte. Das
  räumt :func:`sweep` jetzt wirklich ab.
* **Nachweis**: Jede Löschung schreibt eine Zeile in ``deletion_log`` — wann,
  was, wie viele Dateien, wie viele Bytes, auf wessen Veranlassung. Das
  Protokoll enthält selbst keine personenbezogenen Daten: Kennungen, Zahlen,
  Zeitpunkte. Es ist der Beleg dafür, dass gelöscht wurde, nicht eine Kopie
  dessen, was gelöscht wurde.

Wer löschen darf, steht woanders; hier steht, dass es passiert ist.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import db

# Wer die Löschung veranlasst hat.
# "assistant": an MCP access (its name goes into the note).
ACTORS = ("system", "admin", "device", "guest", "assistant")

# Warum gelöscht wurde. Frei erweiterbar, diese hier tauchen in der Oberfläche auf.
REASONS = {
    "retention": "Aufbewahrungsdauer abgelaufen",
    "expiry": "Ablaufdatum der Galerie erreicht",
    "admin": "von Hand gelöscht",
    "booth": "an der Box gelöscht",
    "guest": "vom Gast gelöscht",
    "event": "Event gelöscht",
    "privacy": "datensparsamer Betrieb",
}

# Das Protokoll ist ein Nachweis, kein Archiv — aber es muss lange genug halten,
# um eine Auskunft beantworten zu können. Drei Jahre decken die üblichen Fristen.
LOG_RETENTION_DAYS = 1095


def init_schema() -> None:
    con = db.get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS deletion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL DEFAULT (datetime('now')),
            scope TEXT NOT NULL,              -- event | session | media
            event_slug TEXT,
            session_uid TEXT,
            files INTEGER NOT NULL DEFAULT 0,
            bytes INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'system',
            note TEXT
        );""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_deletion_log_at ON deletion_log(at DESC);")
    con.commit()
    con.close()


# ── Protokoll ─────────────────────────────────────────────

def record(*, scope: str, reason: str, actor: str = "system", event_slug: str = "",
           session_uid: str = "", files: int = 0, size: int = 0, note: str = "") -> int:
    """Eine Löschung festhalten. Schlägt nie fehl — ein fehlender Beleg darf
    das Löschen selbst nicht verhindern."""
    try:
        init_schema()
        con = db.get_db()
        cur = con.execute(
            """INSERT INTO deletion_log(scope, event_slug, session_uid, files, bytes, reason, actor, note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (str(scope)[:20], str(event_slug or "")[:120], str(session_uid or "")[:64],
             int(files), int(size), str(reason)[:40],
             actor if actor in ACTORS else "system", str(note or "")[:300]))
        con.execute("DELETE FROM deletion_log WHERE at < datetime('now', ?)",
                    (f"-{LOG_RETENTION_DAYS} days",))
        con.commit()
        rid = int(cur.lastrowid)
        con.close()
        return rid
    except Exception:                     # pragma: no cover - Protokoll ist Beiwerk
        return 0


def entries(limit: int = 200, reason: str = "", event_slug: str = "") -> List[Dict[str, Any]]:
    init_schema()
    sql = ["SELECT * FROM deletion_log WHERE 1=1"]
    args: List[Any] = []
    if reason:
        sql.append("AND reason=?")
        args.append(reason)
    if event_slug:
        sql.append("AND event_slug=?")
        args.append(event_slug)
    sql.append("ORDER BY id DESC LIMIT ?")
    args.append(max(1, min(int(limit), 2000)))
    con = db.get_db()
    rows = [dict(r) for r in con.execute(" ".join(sql), args)]
    con.close()
    return rows


def summary(days: int = 30) -> Dict[str, Any]:
    init_schema()
    con = db.get_db()
    row = con.execute("""SELECT COUNT(*) AS runs, COALESCE(SUM(files),0) AS files,
                                COALESCE(SUM(bytes),0) AS bytes
                         FROM deletion_log WHERE at >= datetime('now', ?)""",
                      (f"-{int(days)} days",)).fetchone()
    by_reason = {r["reason"]: r["n"] for r in con.execute(
        """SELECT reason, COUNT(*) AS n FROM deletion_log
           WHERE at >= datetime('now', ?) GROUP BY reason""", (f"-{int(days)} days",))}
    con.close()
    return {"days": days, "runs": row["runs"], "files": row["files"], "bytes": row["bytes"],
            "by_reason": by_reason}


def as_csv(rows: List[Dict[str, Any]]) -> str:
    """Für die Akte: eine Datei, die man einem Prüfer hinlegen kann."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Zeitpunkt (UTC)", "Umfang", "Event", "Durchgang", "Dateien", "Bytes",
                     "Grund", "Veranlasst von", "Notiz"])
    for r in rows:
        writer.writerow([r["at"], r["scope"], r["event_slug"] or "", r["session_uid"] or "",
                         r["files"], r["bytes"], REASONS.get(r["reason"], r["reason"]),
                         r["actor"], r["note"] or ""])
    return buf.getvalue()


# ── Löschen ───────────────────────────────────────────────

def _remove_files(row: Dict[str, Any]) -> tuple:
    """Dateien eines Medien-Eintrags entfernen. Gibt (Anzahl, Bytes) zurück."""
    from app import media

    files = size = 0
    for key in ("path_original", "path_webp", "path_thumb", "path_poster"):
        rel = row.get(key)
        if not rel:
            continue
        try:
            full = (media.data_dir() / rel).resolve()
            if full.exists() and full.is_file():
                size += full.stat().st_size
        except Exception:
            pass
        try:
            media.delete_rel_path(rel)
            files += 1
        except Exception:
            pass
    try:
        media.clear_download_cache(int(row["id"]))
    except Exception:
        pass
    # Watermarked previews are made on demand (blueprints/sharing.py) and
    # named after the media id; without this they outlived their picture.
    try:
        for marked in (media.data_dir() / "cache" / "watermark").glob(f"{int(row['id'])}_*.jpg"):
            marked.unlink()
    except Exception:
        pass
    # The copy on the NAS goes too, later and with a retry (app/storage.py).
    if row.get("storage") == "remote" and row.get("remote_path"):
        try:
            from app import storage
            storage.queue_remote_delete(row["remote_path"])
        except Exception:
            pass
    return files, size


def purge_event(event_id: int, *, reason: str, actor: str = "system",
                delete_event_row: bool = False, note: str = "") -> Dict[str, Any]:
    """Alle Aufnahmen eines Events löschen und das im Protokoll festhalten.

    ``delete_event_row=False`` is "delete the photos, keep the event": slug,
    old slugs, signing key, QR settings and the forward stay, so every printed
    code keeps leading somewhere. An event that forwards elsewhere
    (app/redirects.py) keeps its row unless the operator deletes it by hand:
    the forward is the reason it exists.
    """
    event = db.get_event_by_id(event_id)
    if not event:
        return {"ok": False, "files": 0, "bytes": 0}
    from app import ecosystem, redirects
    if delete_event_row and actor != "admin" and redirects.clean_url(event.get("redirect_url")):
        delete_event_row = False

    # Every row, hidden ones included: db.list_media() skips hidden rows, and
    # those are exactly the full-size originals and the items held for review.
    con = db.get_db()
    rows = [dict(r) for r in con.execute("SELECT * FROM media WHERE event_id=?", (event_id,))]
    con.close()
    # A booth that is still delivering originals of these runs must not
    # bring them back: note every run first, then delete.
    if not delete_event_row:
        ecosystem.remember_deleted_rows(rows, reason=reason, scope="session")
    files = size = 0
    for row in rows:
        f, s = _remove_files(row)
        files += f
        size += s
        db.delete_media(int(row["id"]))
    # Pictures in the trash (app/trash.py) belong to the event as well; this
    # deletion is immediate, so they go now, under this one log line.
    try:
        from app import trash
        trashed = trash.purge_event(event_id)
    except Exception:                       # noqa: BLE001 — never stops the purge itself
        trashed = 0
    if delete_event_row:
        db.delete_event(event_id)
        # Tombstones live as long as their event row.
        ecosystem.forget_deleted_for_event(event_id)
    record(scope="event", reason=reason, actor=actor, event_slug=event["slug"],
           files=files, size=size,
           note=f"{len(rows)} Aufnahmen" + (f" (+{trashed} aus dem Papierkorb)" if trashed else "")
           + (", Event entfernt" if delete_event_row else ", Event bleibt")
           + (f" · {note}" if note else ""))
    return {"ok": True, "event": event["slug"], "media": len(rows), "files": files, "bytes": size}


# ── Kind of gallery and the hard stop ─────────────────────────
# A photo booth event is gone after its period; that is the promise to the
# guests. A photographer's client gallery stays for years and arrives from a
# move with old dates - deleting it by date would be data loss. Hence a kind
# per gallery, and a stop in the run itself that no setting can route around:
# a gallery that belongs to a client, is assigned to one or carries an old id
# is deleted only if deletion was switched on at that very gallery.

KINDS = ("booth", "client")

# ── Account defaults (15.11.2026) ─────────────────────────────
# The owner sets the period and whether deletion is automatic at all, per
# event and as a default for new events. "No automatic deletion" is the
# event's ``auto_delete = 0``, never a period of 0 days: a 0 left in an old
# row means "no period" everywhere (``_retention_deadline`` ignores it).
# The default is applied when an event is created (db.create_event), not
# read later: switching the default on must not start deleting galleries
# that were created while it was off.
MIN_DAYS, MAX_DAYS = 1, 3650


def read_days(raw: Any) -> Optional[int]:
    """A period from a form or a tool: 1..3650 days, anything else is None."""
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return days if MIN_DAYS <= days <= MAX_DAYS else None


def default_days() -> int:
    """The period a new event gets: the account default, else 30."""
    return read_days(db.get_setting("retention_days_default")) or db.DEFAULT_RETENTION_DAYS


def account_auto_default() -> bool:
    """Do new events delete automatically? Off only when the owner said so."""
    return (db.get_setting("retention_auto_default") or "1").strip() != "0"


def set_kind(event_id: int, kind: str) -> None:
    if kind not in KINDS:
        raise ValueError("unknown kind")
    db.update_event(int(event_id), kind=kind)


def set_auto_delete(event_id: int, on: Optional[bool]) -> None:
    """True: delete after the period, also for a client gallery. None: the kind decides."""
    db.update_event(int(event_id), auto_delete=None if on is None else (1 if on else 0))


def kind_of(event: Dict[str, Any]) -> str:
    """The kind; a folder has the kind of its parent."""
    from app import folders
    parent = folders.parent_of(event)
    source = parent or event
    kind = str(source.get("kind") or "booth")
    return kind if kind in KINDS else "booth"


def protected(event: Dict[str, Any]) -> bool:
    """Client gallery, assigned to a client, or carrying an old id - here or on the parent."""
    from app import clients, folders
    if kind_of(event) == "client":
        return True
    for ev in (event, folders.parent_of(event)):
        if not ev:
            continue
        if ev.get("foreign_id") or clients.clients_of_event(int(ev["id"])):
            return True
    return False


def auto_delete(event: Dict[str, Any]) -> bool:
    """Does the period apply to this gallery at all?"""
    flag = event.get("auto_delete")
    if flag is not None and str(flag) != "":
        return bool(int(flag))
    # A folder without its own switch follows a parent that was switched
    # off by hand: "keep this gallery" means its folders too.
    from app import folders
    parent = folders.parent_of(event)
    if parent and parent.get("auto_delete") is not None and str(parent["auto_delete"]) == "0":
        return False
    return not protected(event)


def purge_allowed(event: Dict[str, Any]) -> bool:
    """The hard stop, asked again right before each deletion by the run."""
    fresh = db.get_event_by_id(int(event["id"])) or event
    if protected(fresh):
        return fresh.get("auto_delete") is not None and int(fresh["auto_delete"]) == 1
    return auto_delete(fresh)


def info(event: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the admin shows at a gallery: kind, whether the period applies, and when."""
    if not event:
        return {"kind": "booth", "auto": True, "protected": False, "deadline": ""}
    when = deadline(event)
    return {"kind": kind_of(event), "auto": auto_delete(event), "protected": protected(event),
            "deadline": when.strftime("%d.%m.%Y") if when else ""}


def _retention_deadline(event: Dict[str, Any], now: dt.datetime) -> Optional[dt.datetime]:
    """Ab wann ein Event fällig ist: Aufbewahrungsdauer ab Eventdatum, und das
    Ablaufdatum der Galerie — das frühere gewinnt nicht, das spätere zählt.

    Begründung: Wer ein Ablaufdatum in der Zukunft setzt, will die Bilder bis
    dahin behalten, auch wenn die Standard-Aufbewahrung kürzer ist.
    """
    from app import gallery_settings as gsm, schedule

    if not auto_delete(event):
        return None
    deadlines = []
    try:
        days = int(event.get("retention_days") or 0)
    except (TypeError, ValueError):
        days = 0
    if days > 0:                          # 0 or less is "no period", never "now"
        base = event.get("date") or event.get("created_at") or ""
        try:
            start = dt.datetime.fromisoformat(str(base)[:19])
        except ValueError:
            start = now
        deadlines.append(start + dt.timedelta(days=days))
    expires = schedule.expires_at(gsm.for_event(event))
    if expires:
        deadlines.append(expires.replace(tzinfo=None) if expires.tzinfo else expires)
    return max(deadlines) if deadlines else None


def deadline(event: Dict[str, Any]) -> Optional[dt.datetime]:
    """When this event's recordings are due — the date the privacy page names."""
    return _retention_deadline(event, dt.datetime.now())


def due(now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    """Events, deren Aufbewahrung abgelaufen ist und die noch Aufnahmen haben."""
    now = now or dt.datetime.now()
    out = []
    for event in db.list_events() or []:
        event = dict(event)
        deadline = _retention_deadline(event, now)
        if not deadline or deadline > now:
            continue
        if not db.count_media(int(event["id"])):
            continue
        out.append({**event, "deadline": deadline.isoformat(timespec="minutes")})
    return out


def sweep(now: Optional[dt.datetime] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Alles löschen, was länger liegt als vereinbart. Für den täglichen Lauf."""
    init_schema()
    events = due(now)
    if dry_run:
        return {"dry_run": True, "events": [e["slug"] for e in events], "files": 0, "bytes": 0}
    files = size = 0
    done = []
    for event in events:
        if not purge_allowed(event):
            # Never reached through due(); kept as the last line in case a
            # caller ever hands the run a list of its own.
            continue
        result = purge_event(int(event["id"]), reason="retention", actor="system")
        files += result.get("files", 0)
        size += result.get("bytes", 0)
        done.append(event["slug"])
    return {"dry_run": False, "events": done, "files": files, "bytes": size}


def main() -> int:                        # pragma: no cover - Kommandozeile
    """Täglicher Lauf: ``python -m app.retention [--dry-run]``.

    Als geplante Aufgabe einrichten (Coolify: Scheduled Task, täglich nachts).
    Ohne diesen Lauf bleibt die Aufbewahrungsdauer ein Versprechen im Formular.
    """
    import argparse

    from app import create_app

    parser = argparse.ArgumentParser(description="Aufbewahrungsfristen durchsetzen")
    parser.add_argument("--dry-run", action="store_true", help="nur zeigen, was fällig wäre")
    args = parser.parse_args()

    app = create_app()
    with app.test_request_context():
        app.test_client().get("/healthz")   # Schema sicherstellen wie im Betrieb
        result = sweep(dry_run=args.dry_run)
    if args.dry_run:
        print(f"fällig: {', '.join(result['events']) or 'nichts'}")
    else:
        print(f"geleert: {', '.join(result['events']) or 'nichts'} · "
              f"{result['files']} Dateien · {round(result['bytes'] / 1e6, 1)} MB")
    return 0


if __name__ == "__main__":                # pragma: no cover
    raise SystemExit(main())
