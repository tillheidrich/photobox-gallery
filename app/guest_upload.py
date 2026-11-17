"""Rules of the guest upload that are more than the on/off switch (no. 58).

* **Upload PIN** per event: 4–8 digits, stored only as a salted hash in
  ``events.upload_pin_hash`` (never in ``gallery_json``, which is shown to
  assistants). Unset = no PIN, the upload works as it always did.
* **Grace period** (``upload_grace_days`` in the gallery settings): uploads
  stay open until the end of the N-th day after the event date, then the
  form closes with a notice. Empty = no window, as before. An event without
  a date has no window either: there is nothing to count from.

The table sign (a printable card with QR and PIN) lives in the admin
blueprint; it only reads from here.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional

from werkzeug.security import check_password_hash, generate_password_hash

from app import db

#: Failed PIN attempts per event and address before a pause, and the pause.
#: A table of guests typing the right PIN is never slowed down: only
#: failures count.
PIN_TRIES = 10
PIN_WINDOW = 15 * 60


def valid_pin(pin: str) -> bool:
    pin = str(pin or "").strip()
    return pin.isdigit() and 4 <= len(pin) <= 8


def set_pin(event_id: int, pin: str) -> None:
    if not valid_pin(pin):
        raise ValueError("PIN: 4 bis 8 Ziffern")
    _store(event_id, generate_password_hash(str(pin).strip()))


def clear_pin(event_id: int) -> None:
    _store(event_id, None)


def _store(event_id: int, value: Optional[str]) -> None:
    from app import ecosystem
    ecosystem.init_schema()
    con = db.get_db()
    try:
        con.execute("UPDATE events SET upload_pin_hash=? WHERE id=?", (value, int(event_id)))
        con.commit()
    finally:
        con.close()


def has_pin(event: Optional[Dict[str, Any]]) -> bool:
    return bool((event or {}).get("upload_pin_hash"))


def check_pin(event: Dict[str, Any], pin: str) -> bool:
    """Constant-time compare through werkzeug's hash check."""
    stored = (event or {}).get("upload_pin_hash") or ""
    if not stored or not pin:
        return False
    return check_password_hash(stored, str(pin).strip())


def closes_at(event: Dict[str, Any], gs: Dict[str, Any]) -> Optional[datetime]:
    """When the upload closes, or ``None`` when it does not close by date."""
    days = str(gs.get("upload_grace_days") or "").strip()
    raw = str((event or {}).get("date") or "").strip()[:10]
    if not days.isdigit() or not raw:
        return None
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return None
    return datetime.combine(day + timedelta(days=int(days) + 1), time.min)


def is_open(event: Dict[str, Any], gs: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    until = closes_at(event, gs)
    return until is None or (now or datetime.now()) < until
