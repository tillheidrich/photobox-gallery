"""Freigabe-Links — das Klarbild-Muster für die Galerie.

Grundsatz: Auf Fotobox-Bildern sind Kinder, Kollegen, Gäste. Ein Link, der ewig
gilt und den jeder weiterleiten kann, ist deshalb der falsche Standard.

* jeder Link hat ein **Ablaufdatum** (Standard 30 Tage), kann **widerrufen** und mit
  einer **Passphrase** geschützt werden, Suchmaschinen bleiben draußen;
* abgelaufen und widerrufen werden **getrennt** angezeigt — der Empfänger soll
  verstehen, warum nichts kommt;
* Zählung: Aufrufe, Downloads, wiederkehrende Besucher. Die IP wird nur **gehasht**
  gespeichert (mit Server-Geheimnis und Link-ID gesalzen, nach 90 Tagen gelöscht),
  vom User-Agent bleiben nur Browser und System („Safari · iPhone“).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from werkzeug.security import check_password_hash, generate_password_hash

from app import db

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
MAX_DAYS = 3650


def init_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS share_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            media_ids TEXT,                     -- JSON-Liste; NULL = ganze Galerie
            title TEXT, note TEXT,
            expires_at TEXT, revoked_at TEXT,
            allow_download INTEGER NOT NULL DEFAULT 1,
            password_hash TEXT,
            views INTEGER NOT NULL DEFAULT 0, downloads INTEGER NOT NULL DEFAULT 0,
            visitors INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS share_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            share_id INTEGER NOT NULL REFERENCES share_links(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, ip_hash TEXT, device TEXT, hour TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(share_id, kind, ip_hash, hour)
        );""")
    cur.execute("CREATE TABLE IF NOT EXISTS auth_attempts (key TEXT NOT NULL, at REAL NOT NULL)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_auth_attempts ON auth_attempts(key, at)")


def new_slug(length: int = 12) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def read_days(value: Any, fallback: int = 30) -> int:
    if value is None or value == "":
        return fallback
    if not isinstance(value, (int, str)):
        raise ValueError("Gültigkeit muss eine Zahl von Tagen sein.")
    try:
        n = int(str(value).strip())
    except ValueError as e:
        raise ValueError("Gültigkeit muss eine Zahl von Tagen sein.") from e
    if n < 0 or n > MAX_DAYS:
        raise ValueError(f"Gültigkeit zwischen 0 und {MAX_DAYS} Tagen.")
    return n


def create(event_id: int, *, media_ids: Optional[List[int]] = None, title: str = "", note: str = "",
           days: Any = None, allow_download: bool = True, password: str = "") -> Dict[str, Any]:
    d = read_days(days, int(db.get_setting("share_default_days") or 30))
    expires = (datetime.utcnow() + timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S") if d > 0 else None
    ids = sorted({int(i) for i in (media_ids or [])})[:500] or None
    con = db.get_db()
    cur = con.execute(
        """INSERT INTO share_links(slug, event_id, media_ids, title, note, expires_at, allow_download, password_hash)
           VALUES (?,?,?,?,?,?,?,?)""",
        (new_slug(), event_id, json.dumps(ids) if ids else None, (title or "").strip()[:200] or None,
         (note or "").strip()[:2000] or None, expires, 1 if allow_download else 0,
         generate_password_hash(password.strip()) if password and password.strip() else None))
    con.commit()
    row = dict(con.execute("SELECT * FROM share_links WHERE id=?", (cur.lastrowid,)).fetchone())
    con.close()
    return row


def get(slug: str) -> Optional[Dict[str, Any]]:
    if not (6 <= len(slug or "") <= 32 and slug.isalnum()):
        return None
    con = db.get_db()
    row = con.execute("SELECT * FROM share_links WHERE slug=?", (slug,)).fetchone()
    con.close()
    return dict(row) if row else None


def status(link: Optional[Dict[str, Any]]) -> str:
    if not link:
        return "not-found"
    if link.get("revoked_at"):
        return "revoked"
    if link.get("expires_at") and link["expires_at"] < datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"):
        return "expired"
    return "ok"


def check_password(link: Dict[str, Any], password: str) -> bool:
    return bool(link.get("password_hash") and password and check_password_hash(link["password_hash"], password))


def _secret() -> bytes:
    from flask import current_app
    return str(current_app.secret_key).encode()


def proof(link: Dict[str, Any]) -> str:
    """Kurzlebiger Nachweis „Passphrase geprüft“ für ein Cookie — gilt nur für diesen Link."""
    exp = int(time.time()) + 6 * 3600
    mac = hmac.new(_secret(), f"{link['id']}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{mac}"


def proof_valid(link: Dict[str, Any], value: Optional[str]) -> bool:
    try:
        exp_s, mac = (value or "").split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return False
    good = hmac.new(_secret(), f"{link['id']}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return exp > time.time() and hmac.compare_digest(good, mac)


def media_for(link: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What this link shows. A whole-gallery link includes the open folders.

    Same rule as password and client access (app/folders.py): what the
    parent opens, its folders open - unless a folder is stricter. A folder
    with its own password, unpublished, silent or expired stays out. Each
    folder picture carries ``folder`` (its name) for the page and the ZIP.
    A link to chosen pictures stays exactly those pictures.
    """
    items = db.list_media(link["event_id"]) or []
    if link.get("media_ids"):
        wanted = set(json.loads(link["media_ids"]))
        return [m for m in items if m["id"] in wanted]
    from app import folders, gallery_settings as gsm, silence
    for child in folders.children_of(int(link["event_id"])):
        if (not child.get("published") or child.get("password_hash") or silence.is_silent(child)
                or gsm.is_expired(gsm.for_event(child))):
            continue
        for m in db.list_media(int(child["id"])) or []:
            items.append({**m, "folder": child["name"]})
    return items


def has_media(link: Dict[str, Any], media_id: int) -> bool:
    return any(m["id"] == media_id for m in media_for(link))


def device(ua: str) -> Optional[str]:
    s = ua or ""
    if not s:
        return None
    browser = ("Edge" if "Edg/" in s else "Opera" if "OPR/" in s else "Chrome" if "Chrome/" in s
               else "Firefox" if "Firefox/" in s else "Safari" if "Safari/" in s else "Browser")
    system = ("iPhone" if "iPhone" in s else "iPad" if "iPad" in s else "Android" if "Android" in s
              else "Mac" if "Macintosh" in s else "Windows" if "Windows" in s else "Linux" if "Linux" in s else "")
    return f"{browser} · {system}" if system else browser


def track(link: Dict[str, Any], kind: str, ip: Optional[str], ua: Optional[str]) -> None:
    try:
        ip_hash = hashlib.sha256(f"{_secret().decode()}|{link['id']}|{ip}".encode()).hexdigest()[:22] if ip else None
        hour = datetime.utcnow().strftime("%Y-%m-%dT%H")
        con = db.get_db()
        cur = con.execute("INSERT OR IGNORE INTO share_views(share_id, kind, ip_hash, device, hour) VALUES (?,?,?,?,?)",
                          (link["id"], kind, ip_hash, device(ua or ""), hour))
        fresh = cur.rowcount > 0
        first_visit = fresh and kind == "view" and con.execute(
            "SELECT COUNT(*) FROM share_views WHERE share_id=? AND kind='view' AND ip_hash=?",
            (link["id"], ip_hash)).fetchone()[0] == 1
        con.execute("""UPDATE share_links SET last_seen_at=datetime('now'),
                         views=views+?, downloads=downloads+?, visitors=visitors+? WHERE id=?""",
                    (1 if kind == "view" else 0, 1 if kind == "download" else 0, 1 if first_visit else 0, link["id"]))
        con.execute("DELETE FROM share_views WHERE created_at < datetime('now','-90 days')")
        con.commit()
        con.close()
    except Exception:
        pass     # Zählen ist Nebensache — nie die Auslieferung stoppen


def list_for_event(event_id: int) -> List[Dict[str, Any]]:
    con = db.get_db()
    rows = [dict(r) for r in con.execute("SELECT * FROM share_links WHERE event_id=? ORDER BY id DESC", (event_id,))]
    for r in rows:
        r["status"] = status(r)
        r["count"] = len(json.loads(r["media_ids"])) if r.get("media_ids") else None
        r["devices"] = [dict(x) for x in con.execute(
            "SELECT device, COUNT(*) n FROM share_views WHERE share_id=? AND kind='view' GROUP BY device ORDER BY n DESC LIMIT 4",
            (r["id"],))]
    con.close()
    return rows


def revoke(link_id: int, event_id: int) -> bool:
    con = db.get_db()
    cur = con.execute("UPDATE share_links SET revoked_at=datetime('now') WHERE id=? AND event_id=? AND revoked_at IS NULL",
                      (link_id, event_id))
    con.commit()
    con.close()
    return cur.rowcount > 0


def too_often(key: str, limit: int = 10, window: float = 900) -> bool:
    """Fehlversuche zählen — in der Datenbank, damit alle Worker dieselbe Zahl sehen.

    Records an attempt on every call. For a door where a correct answer must
    never count against the operator, use :func:`retry_after`,
    :func:`record_failure` and :func:`forget` instead.
    """
    now = time.time()
    con = db.get_db()
    try:
        _purge(con, key, window, now)
        con.execute("INSERT INTO auth_attempts(key, at) VALUES (?, ?)", (key, now))
        n = con.execute("SELECT COUNT(*) FROM auth_attempts WHERE key=? AND at >= ?", (key, now - window)).fetchone()[0]
        con.commit()
    finally:
        con.close()
    return n > limit


# Rows older than this are of no use to any window in the gallery.
_KEEP_SECONDS = 86400


def _purge(con, key: str, window: float, now: float) -> None:
    # Only this key's expired rows, plus anything older than a day: a door
    # with a short window must not wipe the history of a door with a long one
    # (recovery counts an hour, the login a quarter of that).
    con.execute("DELETE FROM auth_attempts WHERE key=? AND at < ?", (key, now - window))
    con.execute("DELETE FROM auth_attempts WHERE at < ?", (now - _KEEP_SECONDS,))


def retry_after(key: str, limit: int, window: float) -> int:
    """Seconds until the next attempt is allowed; 0 if it is allowed now.

    Does not record anything — checking costs the caller no attempt.
    """
    now = time.time()
    con = db.get_db()
    try:
        times = [r[0] for r in con.execute(
            "SELECT at FROM auth_attempts WHERE key=? AND at >= ? ORDER BY at", (key, now - window))]
    finally:
        con.close()
    if len(times) < limit:
        return 0
    # Free again once enough of the recorded attempts have aged out of the
    # window to leave room for one more.
    free_at = times[len(times) - limit] + window
    return max(1, int(free_at - now + 0.999))


def record_failure(key: str, window: float = 900) -> None:
    now = time.time()
    con = db.get_db()
    try:
        _purge(con, key, window, now)
        con.execute("INSERT INTO auth_attempts(key, at) VALUES (?, ?)", (key, now))
        con.commit()
    finally:
        con.close()


def forget(key: str) -> None:
    """A correct answer wipes the slate for this key."""
    con = db.get_db()
    try:
        con.execute("DELETE FROM auth_attempts WHERE key=?", (key,))
        con.commit()
    finally:
        con.close()


def wait_message(seconds: int, key: str = "auth.locked") -> str:
    """One sentence that says when the door opens again, in whole minutes."""
    from app.i18n import t
    return t(key, minutes=max(1, (int(seconds) + 59) // 60))
