"""Sub-galleries ("folders"): a gallery can hold galleries, one level deep.

A wedding has a ceremony, a reception and a party; a school has classes. The
photographer delivers one gallery with folders, and the client opens one
address. A folder is an ordinary event with ``parent_id`` set, so every route,
upload path and file layout works for it unchanged.

Rules (page 4, section 13):

* Two levels. A folder has no folders, a gallery with folders is not moved
  under another one, nothing is its own parent. That also rules out cycles.
* A folder inherits every gallery setting it does not set itself; the merge
  happens in ``gallery_settings.for_event`` and nowhere else. Silent mode,
  expiry and moderation are never looser than the parent's.
* Whoever may open the parent (published, password, link key, client code)
  may open its folders. A folder can add its own password or be unpublished
  on its own; it cannot open what the parent keeps closed.
"""
from __future__ import annotations

import re
import secrets
from typing import Any, Dict, List, Optional

from app import db


def _slugify(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[äöüß]", lambda m: {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}[m.group()], text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40]


def _ensure() -> None:
    from app import ecosystem
    ecosystem.init_schema()


def parent_of(event: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    pid = (event or {}).get("parent_id")
    if not pid:
        return None
    parent = db.get_event_by_id(int(pid))
    # A parent that itself has a parent cannot exist through this module;
    # if the database says otherwise, the extra level is ignored.
    return parent


def children_of(event_id: int) -> List[Dict[str, Any]]:
    _ensure()
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM events WHERE parent_id=? ORDER BY COALESCE(date, ''), lower(name), id",
            (int(event_id),))]
    finally:
        con.close()
    return rows


def has_children(event_id: int) -> bool:
    return bool(children_of(event_id))


def set_parent(child_id: int, parent_id: Optional[int]) -> None:
    """Move a gallery under another one, or back to the top with ``None``."""
    _ensure()
    child = db.get_event_by_id(int(child_id))
    if not child:
        raise ValueError("no such gallery")
    if parent_id in (None, "", 0):
        new_parent = None
    else:
        parent = db.get_event_by_id(int(parent_id))
        if not parent:
            raise ValueError("no such parent gallery")
        if int(parent["id"]) == int(child["id"]):
            raise ValueError("a gallery cannot be its own folder")
        if parent.get("parent_id"):
            raise ValueError("folders go one level deep")
        if has_children(int(child["id"])):
            raise ValueError("a gallery with folders cannot become a folder")
        new_parent = int(parent["id"])
    con = db.get_db()
    try:
        con.execute("UPDATE events SET parent_id=? WHERE id=?", (new_parent, int(child["id"])))
        con.commit()
    finally:
        con.close()


def create_child(parent_id: int, name: str) -> int:
    """A new folder under ``parent_id``: published, no own settings, no password.

    Published, because the parent decides who sees anything; an unpublished
    parent keeps the folder closed. The address is the parent's plus the
    folder name, so a client can read where they are.
    """
    _ensure()
    parent = db.get_event_by_id(int(parent_id))
    if not parent:
        raise ValueError("no such parent gallery")
    if parent.get("parent_id"):
        raise ValueError("folders go one level deep")
    name = (name or "").strip()[:200]
    if not name:
        raise ValueError("name required")
    base = f"{parent['slug']}-{_slugify(name) or 'ordner'}"[:80]
    slug, n = base, 2
    while db.slug_taken(slug):
        slug, n = f"{base}-{n}", n + 1
    eid = db.create_event(name=name, slug=slug, upload_token=secrets.token_urlsafe(32),
                          retention_days=int(parent.get("retention_days") or db.DEFAULT_RETENTION_DAYS),
                          status="live")
    con = db.get_db()
    try:
        con.execute("UPDATE events SET parent_id=?, published=1, date=?, kind=? WHERE id=?",
                    (int(parent["id"]), parent.get("date"), parent.get("kind") or "booth", eid))
        con.commit()
    finally:
        con.close()
    return eid


def apply_parent(event: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """For guest routes: a folder of an unpublished parent counts as unpublished.

    Only for the copy a public route works with — never written back, and
    never used in the admin, where the folder's own switch must stay visible.
    """
    if not event or not event.get("parent_id"):
        return event
    parent = parent_of(event)
    if parent is None or not parent.get("published"):
        event["published"] = 0
    return event
