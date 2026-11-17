"""Is a gallery "live" right now (no. 59)?

The same notion of a running event as the booth's silence alarm
(``booth.event_running``): the event is dated today, or yesterday until
8 in the morning (weddings end after midnight). On top, something must have
arrived lately - a gallery of a running event that got nothing for an hour
is not live, it is a gallery. Undated events count by the arrivals alone.

Only visible pictures count (not hidden, not waiting for approval, not the
booth's full-size originals), so the badge never announces a picture a
guest cannot see.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app import db

#: How recent the last arrival must be.
RECENT_MINUTES = 45

_VISIBLE = "COALESCE(hidden,0)=0 AND COALESCE(variant,'') != 'original'"


def _dated_today(event: Dict[str, Any], local: datetime) -> Optional[bool]:
    raw = str(event.get("date") or "").strip()[:10]
    if not raw:
        return None
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return None
    today = local.date()
    return day == today or (day == today - timedelta(days=1) and local.hour < 8)


def latest(event_id: int) -> Dict[str, Any]:
    con = db.get_db()
    try:
        r = con.execute(f"SELECT MAX(id) AS last_id, MAX(created_at) AS last_at FROM media "
                        f"WHERE event_id=? AND {_VISIBLE}", (int(event_id),)).fetchone()
        return {"last_id": int(r["last_id"] or 0), "last_at": r["last_at"]}
    finally:
        con.close()


def new_since(event_id: int, since: int) -> int:
    con = db.get_db()
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM media WHERE event_id=? AND id>? AND {_VISIBLE}",
                               (int(event_id), int(since))).fetchone()[0])
    finally:
        con.close()


def is_live(event: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    local = now or datetime.now()
    dated = _dated_today(event, local)
    if dated is False:
        return False
    last_at = latest(int(event["id"]))["last_at"]
    if not last_at:
        return False
    try:
        arrived = datetime.fromisoformat(str(last_at).replace("Z", "")).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - arrived <= timedelta(minutes=RECENT_MINUTES)
