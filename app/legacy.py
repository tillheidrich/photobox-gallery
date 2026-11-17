"""The same links after a move from another gallery service.

Galleries that lived elsewhere were shared as ``https://<service>/<account>/<id>``
and single pictures as ``…?file=<id>``. Those links sit in mails, chats and
printed cards. After the move the domain changes and nothing else: this
gallery answers ``/<account>/<id>`` for the one account name set in the
admin (setting ``legacy_account``) and leads to the gallery or the picture.

* The id is stored on the event (``events.foreign_id``) and the picture
  (``media.foreign_id``), unique, set in the admin or in one go with
  ``python -m app.import_ids file.csv``.
* Only the configured account counts, and no name that is the first part of
  an existing route can be configured. The route's converter refuses every
  other first segment before the view runs, so ``/lang/en`` stays a 405 and
  ``/foo/bar`` a 404 - nothing that worked before changes.
* Nothing here talks to the old service. No login, no download from there.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Dict, Optional

from werkzeug.routing import BaseConverter, ValidationError

from app import db

ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

#: First path segments the gallery uses itself, today or as prefixes. The
#: url map is checked as well; this list covers prefixes without a rule of
#: their own and names reserved for the client door.
RESERVED = {"admin", "api", "static", "gallery", "data", "kunde", "client", "p", "l", "s", "g", "mcp",
            "oauth", "booth", "box", "schau", "kiosk", "slideshow", "meine", "lang", "notify", "setup",
            "screen", "finden", "branding", "bewertung", "impressum", "datenschutz", "faq", "about",
            "ueber", "healthz", "well-known", ".well-known"}


def init_schema(cur) -> None:
    ecols = db._table_cols(cur, "events")
    if "foreign_id" not in ecols:
        cur.execute("ALTER TABLE events ADD COLUMN foreign_id TEXT;")
    mcols = db._table_cols(cur, "media")
    if "foreign_id" not in mcols:
        cur.execute("ALTER TABLE media ADD COLUMN foreign_id TEXT;")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_events_foreign_id ON events(foreign_id) "
                "WHERE foreign_id IS NOT NULL;")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_media_foreign_id ON media(event_id, foreign_id) "
                "WHERE foreign_id IS NOT NULL;")


def _ensure() -> None:
    from app import ecosystem
    ecosystem.init_schema()


def _first_segments() -> set:
    try:
        from flask import current_app
        out = set()
        for rule in current_app.url_map.iter_rules():
            first = rule.rule.lstrip("/").split("/", 1)[0]
            if first and "<" not in first:
                out.add(first.lower())
        return out
    except RuntimeError:            # no app: the fixed list alone
        return set()


def valid_account(name: str) -> bool:
    name = (name or "").strip().lower()
    return bool(ACCOUNT_RE.match(name)) and name not in RESERVED and name not in _first_segments()


def account() -> str:
    try:
        return (db.get_setting("legacy_account") or "").strip().lower()
    except Exception:               # noqa: BLE001 — before the first schema: no account, no match
        return ""


def set_account(name: str) -> None:
    name = (name or "").strip().lower()
    if name and not valid_account(name):
        raise ValueError("account name not allowed")
    db.set_setting("legacy_account", name)


class AccountConverter(BaseConverter):
    """Matches only the configured account name; everything else falls through."""

    regex = r"[A-Za-z0-9][A-Za-z0-9._-]{1,39}"

    def to_python(self, value: str) -> str:
        wanted = account()
        if not wanted or value.lower() != wanted:
            raise ValidationError()
        return value


class ForeignIdConverter(BaseConverter):
    regex = r"[A-Za-z0-9_-]{4,64}"


def _clean_id(value: str) -> str:
    value = (value or "").strip()
    if value and not ID_RE.match(value):
        raise ValueError("id may hold letters, digits, - and _ (4 to 64)")
    return value


def set_event_id(event_id: int, foreign_id: str) -> None:
    """Set or clear (empty) the old id of a gallery; another gallery's id is refused."""
    _ensure()
    fid = _clean_id(foreign_id)
    con = db.get_db()
    try:
        if fid:
            other = con.execute("SELECT id FROM events WHERE foreign_id=? AND id != ?",
                                (fid, int(event_id))).fetchone()
            if other:
                raise ValueError("id already used by another gallery")
        con.execute("UPDATE events SET foreign_id=? WHERE id=?", (fid or None, int(event_id)))
        con.commit()
    finally:
        con.close()


def set_media_id(media_id: int, foreign_id: str) -> None:
    _ensure()
    fid = _clean_id(foreign_id)
    con = db.get_db()
    try:
        row = con.execute("SELECT event_id FROM media WHERE id=?", (int(media_id),)).fetchone()
        if not row:
            raise ValueError("no such picture")
        if fid and con.execute("SELECT 1 FROM media WHERE event_id=? AND foreign_id=? AND id != ?",
                               (row["event_id"], fid, int(media_id))).fetchone():
            raise ValueError("id already used in this gallery")
        con.execute("UPDATE media SET foreign_id=? WHERE id=?", (fid or None, int(media_id)))
        con.commit()
    finally:
        con.close()


def event_by_id(foreign_id: str) -> Optional[Dict[str, Any]]:
    _ensure()
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM events WHERE foreign_id=?", (foreign_id,)).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def media_by_id(event_id: int, foreign_id: str) -> Optional[Dict[str, Any]]:
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM media WHERE event_id=? AND foreign_id=?",
                          (int(event_id), foreign_id)).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


# ── Import ────────────────────────────────────────────────────

def import_csv(path: Any) -> Dict[str, Any]:
    """Set old ids from a CSV in one go and say what happened.

    Columns: ``kennung`` and ``slug`` or ``titel`` (the gallery's name);
    optionally ``datei`` (file name in that gallery) and ``datei_kennung``
    (the old id of that picture). Separator comma or semicolon.

    Counts: ``set`` galleries given their id, ``missing`` rows whose gallery
    was not found (or whose title fits several), ``double`` ids that appear
    twice in the file or belong to another gallery, ``files`` pictures given
    their id. Nothing is half-done: a row either sets its id or is reported.
    """
    _ensure()
    text = Path(path).read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else ",", delimiters=",;")
    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    report: Dict[str, Any] = {"set": 0, "missing": 0, "double": 0, "files": 0, "lines": []}
    seen: Dict[str, int] = {}
    events = db.list_events() or []
    by_slug = {e["slug"]: e for e in events}
    by_title: Dict[str, list] = {}
    for e in events:
        by_title.setdefault((e["name"] or "").strip().lower(), []).append(e)
    for n, row in enumerate(rows, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        fid = row.get("kennung", "")
        slug, title = row.get("slug", ""), row.get("titel", "")
        event = by_slug.get(slug) if slug else None
        if not event and slug:
            event, _c, _a = db.resolve_event_slug(slug)
        if not event and title:
            hits = by_title.get(title.lower(), [])
            event = hits[0] if len(hits) == 1 else None
        if not fid or not ID_RE.match(fid):
            report["missing"] += 1
            report["lines"].append(f"Zeile {n}: keine gültige Kennung")
            continue
        if fid in seen and seen[fid] != (event or {}).get("id"):
            report["double"] += 1
            report["lines"].append(f"Zeile {n}: Kennung {fid} doppelt (schon Zeile {seen.get(fid + '#line')})")
            continue
        if not event:
            report["missing"] += 1
            report["lines"].append(f"Zeile {n}: Galerie nicht gefunden ({slug or title})")
            continue
        try:
            set_event_id(int(event["id"]), fid)
        except ValueError:
            report["double"] += 1
            report["lines"].append(f"Zeile {n}: Kennung {fid} gehört schon einer anderen Galerie")
            continue
        # A gallery that moved here is a client gallery: no deletion by date.
        db.update_event(int(event["id"]), kind="client")
        seen[fid] = int(event["id"])
        seen[fid + "#line"] = n
        report["set"] += 1
        fname, file_id = row.get("datei", ""), row.get("datei_kennung", "")
        if fname and file_id:
            con = db.get_db()
            try:
                m = con.execute("SELECT id FROM media WHERE event_id=? AND filename=? ORDER BY id LIMIT 1",
                                (int(event["id"]), fname)).fetchone()
            finally:
                con.close()
            try:
                if not m:
                    raise ValueError("no such file")
                set_media_id(int(m["id"]), file_id)
                report["files"] += 1
            except ValueError as e:
                report["lines"].append(f"Zeile {n}: Datei {fname}: {e}")
    return report
