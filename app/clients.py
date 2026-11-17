"""Client access: one code opens every gallery of one client.

A photographer's client (a couple, a company, a school) collects galleries
over the years. A password per gallery means a drawer full of passwords; the
client wants one door with all of their galleries behind it. That door is
this module.

Rules (business_logic/current/models/galerie-sichtbarkeit-und-zugang.md,
section "Kundenzugang"):

* The code is the key for the galleries assigned to the client: it replaces
  their gallery password. It changes nothing else - publication, silent mode,
  expiry, moderation and the download switches apply as they do for anyone.
* The code is shown once, when it is created. Only an HMAC of it is stored
  (keyed with a pepper of this installation), so a copy of the database does
  not open anything. Forgotten means: create a new one.
* Guessing is braked like the gallery password: ten failures per address in
  fifteen minutes. Codes have twelve characters from a 31-letter alphabet
  (about 59 bits), so the brake is a second line, not the first.
* The client's session lives in its own keys and never touches the admin's.
  It ends on logout, after inactivity, when the client is blocked and when a
  new code is made.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any, Dict, Iterable, List, Optional

from app import db

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LEN = 12
SESSION_KEY = "client"
IDLE_MINUTES_DEFAULT = 120
BRAKE_LIMIT = 10
BRAKE_WINDOW = 15 * 60
NAME_MAX = 120
NOTE_MAX = 2000


def init_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            note TEXT,
            code_hash TEXT,
            code_version INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            last_seen_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_events (
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            last_seen_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(client_id, event_id)
        );""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_client_events_event ON client_events(event_id)")


def _ensure() -> None:
    from app import ecosystem
    ecosystem.init_schema()


# ── Codes ─────────────────────────────────────────────────────

def _pepper() -> bytes:
    """A random key of this installation for the code HMAC.

    Not the Flask secret: rotating that one logs everybody out, which is
    fine, but must not silently invalidate every client code. Created once;
    ``INSERT OR IGNORE`` keeps two workers from each writing their own.
    """
    con = db.get_db()
    try:
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('client_code_pepper', ?)",
                    (secrets.token_hex(32),))
        con.commit()
        row = con.execute("SELECT value FROM settings WHERE key='client_code_pepper'").fetchone()
    finally:
        con.close()
    return str(row["value"]).encode()


def normalize(code: str) -> str:
    """What the client typed, without dashes, spaces and case."""
    return "".join(c for c in str(code or "").lower() if c in ALPHABET)[:64]


def pretty(code: str) -> str:
    raw = normalize(code)
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def _hash(code: str) -> str:
    return hmac.new(_pepper(), normalize(code).encode(), hashlib.sha256).hexdigest()


def new_code(client_id: int) -> str:
    """Make a new code, store its hash, end every session of the old one.

    Returns the code in readable form. It is not stored anywhere else.
    """
    _ensure()
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    con = db.get_db()
    try:
        con.execute("UPDATE clients SET code_hash=?, code_version=code_version+1 WHERE id=?",
                    (_hash(code), int(client_id)))
        con.commit()
    finally:
        con.close()
    return pretty(code)


def find_by_code(code: str) -> Optional[Dict[str, Any]]:
    """The active client this code belongs to.

    Every stored hash is compared, without stopping at a hit, so the time
    does not tell which row matched or how far the comparison got.
    """
    raw = normalize(code)
    if len(raw) != CODE_LEN:
        return None
    _ensure()
    wanted = _hash(raw)
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM clients WHERE code_hash IS NOT NULL")]
    finally:
        con.close()
    found = None
    for r in rows:
        if hmac.compare_digest(str(r["code_hash"]), wanted) and found is None:
            found = r
    if found and not int(found.get("active") or 0):
        return None
    return found


# ── Clients ───────────────────────────────────────────────────

def create(name: str, note: str = "") -> int:
    _ensure()
    name = (name or "").strip()[:NAME_MAX]
    if not name:
        raise ValueError("name required")
    con = db.get_db()
    try:
        cur = con.execute("INSERT INTO clients(name, note) VALUES (?, ?)",
                          (name, (note or "").strip()[:NOTE_MAX] or None))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def get(client_id: Any) -> Optional[Dict[str, Any]]:
    _ensure()
    try:
        cid = int(client_id)
    except (TypeError, ValueError):
        return None
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def update(client_id: int, *, name: Optional[str] = None, note: Optional[str] = None,
           active: Optional[bool] = None) -> None:
    _ensure()
    fields: Dict[str, Any] = {}
    if name is not None and name.strip():
        fields["name"] = name.strip()[:NAME_MAX]
    if note is not None:
        fields["note"] = note.strip()[:NOTE_MAX] or None
    if active is not None:
        fields["active"] = 1 if active else 0
    if not fields:
        return
    con = db.get_db()
    try:
        con.execute(f"UPDATE clients SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                    (*fields.values(), int(client_id)))
        con.commit()
    finally:
        con.close()


def list_all() -> List[Dict[str, Any]]:
    _ensure()
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM client_events ce WHERE ce.client_id=c.id) AS event_count "
            "FROM clients c ORDER BY c.active DESC, lower(c.name)")]
    finally:
        con.close()
    for r in rows:
        r["has_code"] = bool(r.pop("code_hash", None))
    return rows


def event_ids(client_id: int) -> List[int]:
    _ensure()
    con = db.get_db()
    try:
        return [int(r[0]) for r in con.execute(
            "SELECT event_id FROM client_events WHERE client_id=? ORDER BY event_id", (int(client_id),))]
    finally:
        con.close()


def set_events(client_id: int, ids: Iterable[Any]) -> None:
    """Replace the client's galleries; the visit marks of kept ones stay."""
    _ensure()
    wanted = set()
    for i in ids:
        try:
            wanted.add(int(i))
        except (TypeError, ValueError):
            continue
    con = db.get_db()
    try:
        known = {int(r[0]) for r in con.execute("SELECT id FROM events")}
        wanted &= known
        have = {int(r[0]) for r in con.execute(
            "SELECT event_id FROM client_events WHERE client_id=?", (int(client_id),))}
        for eid in have - wanted:
            con.execute("DELETE FROM client_events WHERE client_id=? AND event_id=?", (int(client_id), eid))
        for eid in wanted - have:
            con.execute("INSERT OR IGNORE INTO client_events(client_id, event_id) VALUES (?, ?)",
                        (int(client_id), eid))
        con.commit()
    finally:
        con.close()


def assign(client_id: int, event_id: int, on: bool = True) -> None:
    ids = set(event_ids(client_id))
    (ids.add if on else ids.discard)(int(event_id))
    set_events(client_id, ids)


def clients_of_event(event_id: int) -> List[int]:
    _ensure()
    con = db.get_db()
    try:
        return [int(r[0]) for r in con.execute(
            "SELECT client_id FROM client_events WHERE event_id=?", (int(event_id),))]
    finally:
        con.close()


# ── Session ───────────────────────────────────────────────────

def idle_seconds() -> int:
    raw = (db.get_setting("client_idle_minutes") or "").strip()
    minutes = int(raw) if raw.isdigit() and int(raw) > 0 else IDLE_MINUTES_DEFAULT
    return min(minutes, 7 * 24 * 60) * 60


def login(client: Dict[str, Any]) -> None:
    from flask import session
    session[SESSION_KEY] = {"id": int(client["id"]), "v": int(client.get("code_version") or 0),
                            "t": int(time.time())}
    con = db.get_db()
    try:
        con.execute("UPDATE clients SET last_seen_at=datetime('now') WHERE id=?", (int(client["id"]),))
        con.commit()
    finally:
        con.close()


def logout() -> None:
    from flask import g, session
    session.pop(SESSION_KEY, None)
    g.pop("_client", None)


def current() -> Optional[Dict[str, Any]]:
    """The client logged in in this browser, or ``None``.

    Checked on every call (once per request): still there, still active,
    same code generation, not idle for too long. Activity refreshes the
    clock at most once a minute, so the cookie is not rewritten on every
    thumbnail.
    """
    from flask import g, has_request_context, session
    if not has_request_context():
        return None
    if "_client" in g:
        return g._client
    data = session.get(SESSION_KEY)
    found = None
    if isinstance(data, dict):
        now = int(time.time())
        row = get(data.get("id"))
        if (row and int(row.get("active") or 0) and row.get("code_hash")
                and int(row.get("code_version") or 0) == int(data.get("v") or -1)
                and now - int(data.get("t") or 0) <= idle_seconds()):
            found = row
            if now - int(data.get("t") or 0) > 60:
                session[SESSION_KEY] = {**data, "t": now}
        else:
            session.pop(SESSION_KEY, None)
    g._client = found
    return found


def has_event(event_id: Any) -> bool:
    """Does the logged-in client hold this gallery?"""
    client = current()
    if not client:
        return False
    try:
        eid = int(event_id)
    except (TypeError, ValueError):
        return False
    from flask import g
    cache = g.get("_client_events")
    if cache is None:
        cache = set(event_ids(client["id"]))
        g._client_events = cache
    return eid in cache


def holds(event: Optional[Dict[str, Any]]) -> bool:
    """Does the logged-in client hold this gallery, directly or through its parent?"""
    if not event:
        return False
    return has_event(event.get("id")) or bool(event.get("parent_id") and has_event(event["parent_id"]))


def mark_visit(event_id: int) -> None:
    """Remember that the client looked at this gallery - for "new since"."""
    client = current()
    if not client or not has_event(event_id):
        return
    con = db.get_db()
    try:
        con.execute("UPDATE client_events SET last_seen_at=datetime('now') WHERE client_id=? AND event_id=?",
                    (int(client["id"]), int(event_id)))
        con.execute("UPDATE clients SET last_seen_at=datetime('now') WHERE id=?", (int(client["id"]),))
        con.commit()
    finally:
        con.close()


def _date_h(raw: Any) -> str:
    """08.11.2026 - as the folder tiles and the home page write a date."""
    raw = str(raw or "")[:10]
    parts = raw.split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 and all(p.isdigit() for p in parts) else raw


def overview(client_id: int) -> List[Dict[str, Any]]:
    """The client's galleries as tiles: cover, date, count, new since the last visit.

    Unpublished and silent galleries are not listed: the code opens a door,
    it does not show what the operator keeps closed.
    """
    from app import folders, gallery_settings as gsm, silence
    _ensure()
    folders._ensure()
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute(
            """SELECT e.*, ce.last_seen_at AS client_seen_at
                 FROM client_events ce JOIN events e ON e.id = ce.event_id
                WHERE ce.client_id=?""", (int(client_id),))]
        out = []
        for e in rows:
            if not e.get("published") or silence.is_silent(e):
                continue
            # A gallery made of folders holds its pictures in them: the tile
            # counted only the gallery's own (none) and said "0 photos".
            ids = [int(e["id"])] + [int(c["id"]) for c in con.execute(
                "SELECT * FROM events WHERE parent_id=? AND published=1", (e["id"],))
                if not silence.is_silent(dict(c))]
            marks = ",".join("?" * len(ids))
            count = con.execute(f"SELECT COUNT(*) FROM media WHERE event_id IN ({marks}) AND COALESCE(hidden,0)=0 "
                                "AND COALESCE(review_state,'') != 'pending'", ids).fetchone()[0]
            fresh = 0
            if e.get("client_seen_at"):
                fresh = con.execute(
                    f"SELECT COUNT(*) FROM media WHERE event_id IN ({marks}) AND COALESCE(hidden,0)=0 "
                    "AND COALESCE(review_state,'') != 'pending' AND created_at > ?",
                    (*ids, e["client_seen_at"])).fetchone()[0]
            cover = e.get("cover_path") or e.get("cover_original_path")
            if not cover:
                first = con.execute(
                    "SELECT path_thumb, path_webp FROM media WHERE event_id=? AND COALESCE(hidden,0)=0 "
                    "AND COALESCE(review_state,'') != 'pending' AND (path_thumb IS NOT NULL OR path_webp IS NOT NULL) "
                    "ORDER BY datetime(created_at) ASC, id ASC LIMIT 1", (e["id"],)).fetchone()
                cover = (first["path_thumb"] or first["path_webp"]) if first else None
            out.append({
                "id": e["id"], "name": e["name"], "slug": e["slug"],
                "date": (e.get("date") or "")[:10],
                "date_h": _date_h(e.get("date")),
                "cover_url": f"/data/{cover}" if cover else None,
                "count": int(count),
                "new": int(fresh),
                "never_seen": not e.get("client_seen_at"),
                "expired": gsm.is_expired(gsm.for_event(e)),
            })
    finally:
        con.close()
    out.sort(key=lambda x: (x["date"] or "", x["id"]), reverse=True)
    return out
