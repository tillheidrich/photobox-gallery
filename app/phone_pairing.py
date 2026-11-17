"""Pairing a booth from the phone: a device-authorisation flow.

The booth knows only the gallery's address. It makes a random verifier
(never shown, never logged, never in the QR code), sends its SHA-256 and gets
a short public request code back. Its admin page shows a QR code for
``/admin/devices/pair?c=<code>``; the operator scans it, logs in (second
factor included), sees which booth asks and confirms. The booth, polling
with the verifier, then collects address and device key — once.

States of a request::

    open ──confirm──> confirmed ──booth collects──> collected
      │ └──decline──> declined
      └──10 minutes──> expired   (a new request of the same booth replaces it)

The code alone never yields a key: collecting needs the verifier, and a wrong
verifier counts against the brake of the pair endpoints. The key is never put
into a page; the admin sees only the booth's name and the request code.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Dict, Optional, Tuple

from app import db
from app.ecosystem import PAIR_ALPHABET, _pair_random

#: How long an open request waits for the operator. Short on purpose (S-2):
#: a request link that travels by chat should be dead before it is read.
REQUEST_MINUTES = 5
#: After confirmation the booth must collect within this time; its poll runs
#: every few seconds, so anything slower is not the booth that asked.
COLLECT_MINUTES = 2
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def clean_code(code: str) -> str:
    out = "".join(ch for ch in str(code or "").upper() if ch in PAIR_ALPHABET)
    return out if len(out) == 8 else ""


def open_request(verifier_hash: str, booth_name: str, version: str, install_id: str, ip: str,
                 user_agent: str = "") -> Dict[str, Any]:
    """A new request. An open request of the same booth (``install_id``) is replaced."""
    verifier_hash = str(verifier_hash or "").lower()
    if not _HEX64.match(verifier_hash):
        raise ValueError("verifier_hash")
    install_id = re.sub(r"[^A-Za-z0-9_-]", "", str(install_id or ""))[:64]
    con = db.get_db()
    try:
        cleanup(con)
        if install_id:
            con.execute("DELETE FROM pairing_requests WHERE install_id=? AND state='open'", (install_id,))
        for _ in range(5):
            code = _pair_random()
            exists = con.execute("SELECT 1 FROM pairing_requests WHERE code=? UNION "
                                 "SELECT 1 FROM device_pairings WHERE code=?", (code, code)).fetchone()
            if not exists:
                break
        else:
            raise RuntimeError("kein freier Code")
        ua = " ".join(str(user_agent or "").split())[:200]
        con.execute("""INSERT INTO pairing_requests(code, verifier_hash, install_id, booth_name, version, ip,
                                                    user_agent, expires_at)
                       VALUES (?,?,?,?,?,?,?, datetime('now', ?))""",
                    (code, verifier_hash, install_id, str(booth_name or "").strip()[:60],
                     str(version or "").strip()[:40], str(ip or "")[:45], ua, f"+{REQUEST_MINUTES} minutes"))
        con.commit()
    finally:
        con.close()
    return {"code": code, "pretty": f"{code[:4]}-{code[4:]}", "expires_in": REQUEST_MINUTES * 60}


def get_request(code: str) -> Optional[Dict[str, Any]]:
    code = clean_code(code)
    if not code:
        return None
    con = db.get_db()
    row = con.execute("""SELECT *, expires_at <= datetime('now') AS expired
                         FROM pairing_requests WHERE code=?""", (code,)).fetchone()
    con.close()
    return dict(row) if row else None


def state_of(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return "unknown"
    # A confirmed request that nobody collected in time is as dead as an
    # open one that nobody confirmed.
    if row["state"] in ("open", "confirmed") and row.get("expired"):
        return "expired"
    return str(row["state"])


def decide(code: str, *, device_id: Optional[int], by: str) -> bool:
    """Confirm (with the device the key belongs to) or decline (``device_id=None``). Once.

    Confirming for an existing box replaces its key at once (S-2): the box
    that is running with it is disconnected, and nobody — neither that box
    nor whoever opened the request — learns the replaced key. The key the
    requester gets is made only when it collects (see :func:`collect`).
    """
    code = clean_code(code)
    con = db.get_db()
    try:
        cur = con.execute("""UPDATE pairing_requests SET state=?, device_id=?, decided_by=?, decided_at=datetime('now'),
                                    expires_at=CASE WHEN ? THEN datetime('now', ?) ELSE expires_at END
                             WHERE code=? AND state='open' AND expires_at > datetime('now')""",
                          ("confirmed" if device_id else "declined", device_id, str(by or "")[:120],
                           1 if device_id else 0, f"+{COLLECT_MINUTES} minutes", code))
        con.commit()
        ok = (cur.rowcount or 0) == 1
    finally:
        con.close()
    if ok and device_id:
        db.regenerate_device_key(int(device_id))
    return ok


def collect(code: str, verifier: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """What the booth's poll gets: ``(state, device)``.

    ``wrong`` for an unknown code or a verifier that does not match (the
    caller counts it on the brake); ``paired`` with the device exactly once.
    """
    row = get_request(code)
    if not row:
        return "wrong", None
    given = hashlib.sha256(str(verifier or "").encode("utf-8")).hexdigest()
    if not hmac.compare_digest(given, str(row["verifier_hash"])):
        return "wrong", None
    state = state_of(row)
    if state != "confirmed":
        return state, None
    con = db.get_db()
    try:
        cur = con.execute("""UPDATE pairing_requests SET state='collected', collected_at=datetime('now')
                             WHERE code=? AND state='confirmed' AND expires_at > datetime('now')""",
                          (row["code"],))
        con.commit()
        if (cur.rowcount or 0) != 1:
            return "collected", None
    finally:
        con.close()
    if not row.get("device_id") or not db.get_device_by_id(int(row["device_id"])):
        return "declined", None
    # A fresh key, made now and handed out once. Never the stored one.
    device = dict(db.regenerate_device_key(int(row["device_id"])))
    return "paired", device


def cleanup(con=None) -> int:
    """Expired and finished requests go after a day; called on every new request and by maintenance."""
    own = con is None
    con = con or db.get_db()
    try:
        cur = con.execute("""DELETE FROM pairing_requests
                             WHERE expires_at < datetime('now', '-1 day')
                                OR (state != 'open' AND COALESCE(decided_at, created_at) < datetime('now', '-1 day'))""")
        gone = cur.rowcount or 0
        if own:
            con.commit()
        return gone
    finally:
        if own:
            con.close()
