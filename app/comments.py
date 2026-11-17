"""Comments on a picture or a gallery, between a client and the photographer.

Rules (page 4, section 17):

* Off by default; the gallery setting ``comments`` switches them on.
* Written only by a client logged in with their access code, under the
  client's name. No anonymous comments, no e-mail addresses, no names typed
  by hand - the name is the client's.
* Plain text, at most ``BODY_MAX`` characters, at most ``BRAKE_LIMIT`` per
  client and hour. Rendered escaped everywhere (Jinja autoescape, JS
  ``textContent``), never as HTML.
* A client sees their own comments and the photographer's answers to them,
  not other clients' threads. The photographer answers and deletes in the
  admin.
* The operator is told through the notifier, if one is set up - which
  gallery and which client, never the text.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app import db

BODY_MAX = 1000
BRAKE_LIMIT = 30
BRAKE_WINDOW = 3600


def init_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            media_id INTEGER REFERENCES media(id) ON DELETE CASCADE,
            client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
            parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            from_operator INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_event ON comments(event_id, media_id)")


def _ensure() -> None:
    from app import ecosystem
    ecosystem.init_schema()


def clean_body(text: Any) -> str:
    body = str(text or "").replace("\r\n", "\n").strip()
    if not body or len(body) > BODY_MAX:
        raise ValueError("length")
    return body


def add(event_id: int, *, body: str, author: str, client_id: Optional[int] = None,
        media_id: Optional[int] = None, parent_id: Optional[int] = None, from_operator: bool = False) -> int:
    _ensure()
    con = db.get_db()
    try:
        cur = con.execute(
            "INSERT INTO comments(event_id, media_id, client_id, parent_id, author, body, from_operator)"
            " VALUES (?,?,?,?,?,?,?)",
            (int(event_id), media_id, client_id, parent_id, str(author)[:120], clean_body(body),
             1 if from_operator else 0))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def get(comment_id: int) -> Optional[Dict[str, Any]]:
    _ensure()
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM comments WHERE id=?", (int(comment_id),)).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def delete(comment_id: int) -> None:
    con = db.get_db()
    try:
        con.execute("DELETE FROM comments WHERE id=? OR parent_id=?", (int(comment_id), int(comment_id)))
        con.commit()
    finally:
        con.close()


def for_event(event_id: int) -> List[Dict[str, Any]]:
    """Every comment of a gallery, oldest first, with the picture's file name."""
    _ensure()
    con = db.get_db()
    try:
        return [dict(r) for r in con.execute(
            "SELECT c.*, m.filename AS media_name FROM comments c LEFT JOIN media m ON m.id=c.media_id "
            "WHERE c.event_id=? ORDER BY c.id", (int(event_id),))]
    finally:
        con.close()


def for_client(event_id: int, client_id: int) -> List[Dict[str, Any]]:
    """The client's own comments and the photographer's answers to them."""
    rows = for_event(event_id)
    mine = {r["id"] for r in rows if r.get("client_id") == int(client_id) and not r["from_operator"]}
    return [r for r in rows if r["id"] in mine or (r["from_operator"] and r.get("parent_id") in mine)]


def media_with_comments(event_id: int, client_id: Optional[int] = None) -> Set[int]:
    rows = for_client(event_id, client_id) if client_id else for_event(event_id)
    return {int(r["media_id"]) for r in rows if r.get("media_id")}
