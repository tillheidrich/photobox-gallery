"""Trash with a grace period (no. 61).

Account settings:

* ``trash_days`` - 0 (default) deletes at once, exactly as before. 1-90 keeps
  a picture deleted by hand (admin, assistant) that many days in the trash.
* ``trash_guest_deletes`` - "1" sends a guest's "delete my run" to the trash
  too. Off by default: a guest who deletes expects it gone, and the operator
  decides whether a grace period is fair to promise.

A trashed picture leaves the ``media`` table: its row is kept as JSON in
``media_trash`` and its files move to ``data/trash/<media id>/``, outside
every public prefix. So nothing that lists, counts, zips or serves media
has to know about the trash - gallery, ZIPs, counts, MCP listings and
``/data/...`` simply no longer see it. Restoring puts the row back under the
same id (AUTOINCREMENT never hands an id out twice) and the files back
where they were.

Only the real purge (by hand or in the maintenance run) writes the deletion
log, because only then is anything deleted. "Fotos löschen, Event behalten"
and the retention period stay immediate and take the event's trash with
them.

The booth tombstone (``deleted_uids``) is written when a picture goes to the
trash, so a box cannot upload it again while it lies there; restoring lifts
it.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import db

MAX_DAYS = 90
PATH_KEYS = ("path_original", "path_webp", "path_thumb", "path_poster")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def init_schema() -> None:
    con = db.get_db()
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS media_trash (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            row_json TEXT NOT NULL,
            files_json TEXT NOT NULL DEFAULT '{}',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            actor TEXT NOT NULL DEFAULT 'admin',
            reason TEXT NOT NULL DEFAULT 'admin',
            note TEXT NOT NULL DEFAULT '',
            trashed_at TEXT NOT NULL,
            purge_after TEXT NOT NULL)""")
        con.execute("CREATE INDEX IF NOT EXISTS media_trash_event ON media_trash(event_id)")
        con.execute("CREATE INDEX IF NOT EXISTS media_trash_due ON media_trash(purge_after)")
        con.commit()
    finally:
        con.close()


# ── Settings ─────────────────────────────────────────────────────────

def days() -> int:
    raw = str(db.get_setting("trash_days") or "0").strip()
    return int(raw) if raw.isdigit() and int(raw) <= MAX_DAYS else 0


def enabled() -> bool:
    return days() > 0


def for_guests() -> bool:
    return enabled() and (db.get_setting("trash_guest_deletes") or "0") == "1"


def save_settings(trash_days: Any, guests: bool) -> bool:
    raw = str(trash_days or "0").strip()
    if not raw.isdigit() or int(raw) > MAX_DAYS:
        return False
    db.set_setting("trash_days", str(int(raw)))
    db.set_setting("trash_guest_deletes", "1" if guests else "0")
    return True


# ── Moving in and out ────────────────────────────────────────────────

def _root() -> Path:
    from app import media
    return media.data_dir() / "trash"


def move(row: Dict[str, Any], *, actor: str = "admin", reason: str = "admin", note: str = "") -> Optional[int]:
    """Put one media row and its files into the trash. Returns the entry id."""
    from app import media
    init_schema()
    row = dict(row)
    mid = int(row["id"])
    target = _root() / str(mid)
    target.mkdir(parents=True, exist_ok=True)
    moved: Dict[str, str] = {}
    size = 0
    for key in PATH_KEYS:
        rel = row.get(key)
        if not rel or rel in moved:
            continue
        src = (media.data_dir() / rel).resolve()
        if not (src.is_file() and src.is_relative_to(media.data_dir().resolve())):
            continue
        dest = target / f"{key}__{src.name}"
        size += src.stat().st_size
        shutil.move(str(src), str(dest))
        moved[rel] = dest.relative_to(media.data_dir()).as_posix()
    # Scaled downloads and watermarked previews are made again on demand.
    try:
        media.clear_download_cache(mid)
    except Exception:                       # noqa: BLE001
        pass
    now = _utcnow()
    con = db.get_db()
    try:
        cur = con.execute(
            "INSERT INTO media_trash(media_id, event_id, row_json, files_json, size_bytes, actor, reason, note,"
            " trashed_at, purge_after) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid, int(row["event_id"]), json.dumps(row, default=str), json.dumps(moved), size,
             str(actor)[:20], str(reason)[:40], str(note or "")[:300],
             now.isoformat(), (now + timedelta(days=max(days(), 1))).isoformat()))
        con.execute("DELETE FROM media WHERE id=?", (mid,))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def get(entry_id: int) -> Optional[Dict[str, Any]]:
    init_schema()
    con = db.get_db()
    try:
        r = con.execute("SELECT * FROM media_trash WHERE id=?", (int(entry_id),)).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def restore(entry_id: int) -> bool:
    """Put the picture back where it was. False when the event is gone."""
    from app import media
    entry = get(entry_id)
    if not entry:
        return False
    row = json.loads(entry["row_json"])
    if not db.get_event_by_id(int(row["event_id"])):
        return False
    for rel, trashed in json.loads(entry["files_json"] or "{}").items():
        src = media.data_dir() / trashed
        dest = media.data_dir() / rel
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
    con = db.get_db()
    try:
        cols = [c for c in db._table_cols(con.cursor(), "media") if c in row]
        con.execute(f"INSERT INTO media ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [row[c] for c in cols])
        # The moderation trigger holds every new row of a moderated event;
        # a restored one comes back in the state it left.
        con.execute("UPDATE media SET hidden=?, review_state=? WHERE id=?",
                    (int(row.get("hidden") or 0), row.get("review_state"), int(row["id"])))
        if row.get("uid"):
            con.execute("DELETE FROM deleted_uids WHERE uid=?", (row["uid"],))
        con.execute("DELETE FROM media_trash WHERE id=?", (int(entry_id),))
        con.commit()
    finally:
        con.close()
    _remove_dir(int(row["id"]))
    return True


def purge(entry_id: int, *, actor: str = "admin", log: bool = True) -> Dict[str, int]:
    """Delete for good: files, entry, and one line in the deletion log."""
    from app import media, retention
    entry = get(entry_id)
    if not entry:
        return {"files": 0, "bytes": 0}
    row = json.loads(entry["row_json"])
    files = 0
    for trashed in json.loads(entry["files_json"] or "{}").values():
        try:
            (media.data_dir() / trashed).unlink()
            files += 1
        except FileNotFoundError:
            pass
    if row.get("storage") == "remote" and row.get("remote_path"):
        try:
            from app import storage
            storage.queue_remote_delete(row["remote_path"])
        except Exception:                   # noqa: BLE001
            pass
    con = db.get_db()
    try:
        con.execute("DELETE FROM media_trash WHERE id=?", (int(entry_id),))
        con.commit()
    finally:
        con.close()
    _remove_dir(int(row["id"]))
    if log:
        event = db.get_event_by_id(int(row["event_id"])) or {}
        retention.record(scope="media", reason=entry["reason"] or "admin", actor=actor,
                         event_slug=event.get("slug", ""), session_uid=row.get("session_uid") or "",
                         files=files, size=int(entry["size_bytes"] or 0),
                         note=("aus dem Papierkorb" + (f" · {entry['note']}" if entry.get("note") else "")))
    return {"files": files, "bytes": int(entry["size_bytes"] or 0)}


def _remove_dir(media_id: int) -> None:
    d = _root() / str(int(media_id))
    try:
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    except OSError:
        pass


# ── Lists and housekeeping ───────────────────────────────────────────

def entries(event_id: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
    init_schema()
    con = db.get_db()
    try:
        sql = ("SELECT t.*, e.name AS event_name, e.slug AS event_slug FROM media_trash t "
               "LEFT JOIN events e ON e.id = t.event_id")
        args: List[Any] = []
        if event_id is not None:
            sql += " WHERE t.event_id=?"
            args.append(int(event_id))
        sql += " ORDER BY t.trashed_at DESC, t.id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 5000)))
        out = []
        for r in con.execute(sql, args):
            d = dict(r)
            row = json.loads(d.pop("row_json") or "{}")
            d["filename"] = row.get("filename") or ""
            d["kind"] = row.get("kind") or "photo"
            files = json.loads(d.get("files_json") or "{}")
            d["thumb_rel"] = files.get(row.get("path_thumb") or "") or files.get(row.get("path_webp") or "") or ""
            out.append(d)
        return out
    finally:
        con.close()


def usage() -> Dict[str, int]:
    init_schema()
    con = db.get_db()
    try:
        r = con.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM media_trash").fetchone()
        return {"count": int(r[0] or 0), "bytes": int(r[1] or 0)}
    finally:
        con.close()


def purge_due(now: Optional[datetime] = None, *, dry_run: bool = False) -> Dict[str, int]:
    """For the maintenance run: purge everything past its date."""
    init_schema()
    stamp = (now or _utcnow()).isoformat()
    con = db.get_db()
    try:
        ids = [int(r[0]) for r in con.execute("SELECT id FROM media_trash WHERE purge_after <= ?", (stamp,))]
    finally:
        con.close()
    if dry_run:
        return {"purged": 0, "due": len(ids), "bytes": 0}
    total = 0
    for i in ids:
        total += purge(i, actor="system")["bytes"]
    return {"purged": len(ids), "due": len(ids), "bytes": total}


def purge_event(event_id: int) -> int:
    """An event's photos are deleted at once: its trash goes with them, unlogged
    (the caller writes one line for the whole event)."""
    ids = [e["id"] for e in entries(event_id=event_id, limit=5000)]
    for i in ids:
        purge(i, log=False)
    return len(ids)
