"""Selections that stay: a client's named picks per gallery, and their export.

Likes hang on a browser and "Meine Auswahl" on ids in the address - both are
gone with the next phone. A client with an access code (app/clients.py) can
keep named selections per gallery instead ("Album", "Abzüge"), and the
photographer reads them in the admin and exports the file names for
Lightroom's text filter.

Rules (page 4, section 15):

* Only a logged-in client, only in a gallery the client may open, only
  pictures the client can see there (not hidden, not waiting for approval).
* ``selection_max`` in the gallery settings caps each selection; empty means
  no cap.
* Without client access nothing changes for anybody.
* Exports name the picture as the photographer knows it: the original file
  name without extension (``app/dateinamen.py``, ``stamm``), the booth's
  original variant where there is one.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

from app import db

NAME_MAX = 60
PER_CLIENT_EVENT = 20


def init_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_selections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT,
            UNIQUE(client_id, event_id, name)
        );""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_selection_items (
            selection_id INTEGER NOT NULL REFERENCES client_selections(id) ON DELETE CASCADE,
            media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(selection_id, media_id)
        );""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_client_selections_event ON client_selections(event_id)")


def _ensure() -> None:
    from app import ecosystem
    ecosystem.init_schema()


def cap(event: Dict[str, Any]) -> int:
    from app import gallery_settings as gsm
    raw = str(gsm.for_event(event).get("selection_max") or "")
    return int(raw) if raw.isdigit() else 0


def get(selection_id: Any) -> Optional[Dict[str, Any]]:
    _ensure()
    try:
        sid = int(selection_id)
    except (TypeError, ValueError):
        return None
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM client_selections WHERE id=?", (sid,)).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def create(client_id: int, event_id: int, name: str) -> int:
    """A named selection; an existing one of the same name is returned as it is."""
    _ensure()
    name = " ".join((name or "").split())[:NAME_MAX]
    if not name:
        raise ValueError("name required")
    con = db.get_db()
    try:
        row = con.execute("SELECT id FROM client_selections WHERE client_id=? AND event_id=? AND name=?",
                          (int(client_id), int(event_id), name)).fetchone()
        if row:
            return int(row["id"])
        n = con.execute("SELECT COUNT(*) FROM client_selections WHERE client_id=? AND event_id=?",
                        (int(client_id), int(event_id))).fetchone()[0]
        if n >= PER_CLIENT_EVENT:
            raise ValueError("too many selections")
        cur = con.execute("INSERT INTO client_selections(client_id, event_id, name) VALUES (?,?,?)",
                          (int(client_id), int(event_id), name))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def delete(selection_id: int) -> None:
    con = db.get_db()
    try:
        con.execute("DELETE FROM client_selections WHERE id=?", (int(selection_id),))
        con.commit()
    finally:
        con.close()


def media_ids(selection_id: int) -> List[int]:
    con = db.get_db()
    try:
        return [int(r[0]) for r in con.execute(
            "SELECT media_id FROM client_selection_items WHERE selection_id=? ORDER BY added_at, media_id",
            (int(selection_id),))]
    finally:
        con.close()


def toggle(selection_id: int, media_id: int, on: bool) -> int:
    """Add or remove one picture; returns the new count. Caller checks the cap."""
    con = db.get_db()
    try:
        if on:
            con.execute("INSERT OR IGNORE INTO client_selection_items(selection_id, media_id) VALUES (?,?)",
                        (int(selection_id), int(media_id)))
        else:
            con.execute("DELETE FROM client_selection_items WHERE selection_id=? AND media_id=?",
                        (int(selection_id), int(media_id)))
        con.execute("UPDATE client_selections SET updated_at=datetime('now') WHERE id=?", (int(selection_id),))
        con.commit()
        return int(con.execute("SELECT COUNT(*) FROM client_selection_items WHERE selection_id=?",
                               (int(selection_id),)).fetchone()[0])
    finally:
        con.close()


def for_client(client_id: int, event_id: int) -> List[Dict[str, Any]]:
    _ensure()
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT id, name FROM client_selections WHERE client_id=? AND event_id=? ORDER BY id",
            (int(client_id), int(event_id)))]
    finally:
        con.close()
    for r in rows:
        r["media_ids"] = media_ids(r["id"])
        r["count"] = len(r["media_ids"])
    return rows


def for_event(event_id: int) -> List[Dict[str, Any]]:
    """Every client's selections of one gallery, for the photographer."""
    _ensure()
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute(
            """SELECT s.id, s.name, s.updated_at, s.created_at, c.id AS client_id, c.name AS client_name,
                      (SELECT COUNT(*) FROM client_selection_items i WHERE i.selection_id=s.id) AS count
                 FROM client_selections s JOIN clients c ON c.id=s.client_id
                WHERE s.event_id=? ORDER BY lower(c.name), s.id""", (int(event_id),))]
    finally:
        con.close()
    return rows


# ── Export ────────────────────────────────────────────────────

def files(selection_id: int) -> List[Dict[str, Any]]:
    """The pictures of a selection as the photographer knows them."""
    from app import dateinamen, ecosystem
    out = []
    for mid in media_ids(selection_id):
        row = db.get_media(mid)
        if not row:
            continue
        source = row
        if row.get("uid") and row.get("variant") == "web":
            source = ecosystem.media_by_uid(row["uid"], "original") or row
        out.append({"id": mid, "file": source.get("filename") or "", "stem": dateinamen.stamm(source),
                    "tag": row.get("color_tag") or ""})
    return out


FORMATS = ("comma", "or", "csv", "txt")


def export(selection_id: int, fmt: str) -> str:
    items = files(selection_id)
    stems = [i["stem"] for i in items if i["stem"]]
    if fmt == "comma":
        return ", ".join(stems) + "\n"
    if fmt == "or":
        # Lightroom Classic, library filter "Text": "Filename" "contains" -
        # several words separated by " OR " match any of them.
        return " OR ".join(stems) + "\n"
    if fmt == "txt":
        return "".join(s + "\n" for s in stems)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["datei", "name_ohne_endung", "galerie_id", "farbe"])
        for i in items:
            w.writerow([i["file"], i["stem"], i["id"], i["tag"]])
        return buf.getvalue()
    raise ValueError("unknown format")
