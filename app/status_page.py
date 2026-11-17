"""A read-only status page per booth, for rentals (``/status/<token>``).

The owner rents a booth out. During the rental the customer, or the owner on
his phone without logging into the admin, opens one link and sees whether the
booth is fine. The page is built for a link that leaks: it names the booth,
not the event, and shows nothing that leads to guests, their photos or the
booth's machine.

* Off by default, per booth. Switching it on creates a random token
  (``secrets``, 192 bit); only its SHA-256 is stored, in a table of its own
  so that no ``SELECT * FROM devices`` (admin pages, MCP tools) ever carries
  it. The full link is shown once. A new link replaces the old one;
  switching off deletes the row.
* A plain SHA-256 is enough here, unlike a password: the token is random and
  long, so there is nothing to guess from the hash, and the lookup stays one
  indexed query.
* The words of the light come from :func:`app.booth.light`, the function
  behind the admin page and the MCP tool ``list_booths``. Free text the booth
  sends (printer hint, labels, notice titles) passes :func:`_shareable`, which
  drops a line that looks like an address, a path or a key, and only the
  notices of camera, printer and disk are shown: the self-check's notices
  name files on the booth PC.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date
from typing import Any, Dict, List, Optional

from app import db

TABLE = "booth_status_pages"
TOKEN_BYTES = 24
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")

#: Misses per address and window: requests whose token opens no page. A valid
#: link is never counted, so any number of phones may refresh once a minute
#: behind one proxy address. Guessing a 192-bit token cannot succeed; the brake
#: is against someone hammering the gallery with wrong tokens. Over the limit,
#: further wrong tokens answer 429 instead of 404 until the window has passed.
RATE_LIMIT = 120
RATE_WINDOW = 900
REFRESH_SECONDS = 60

#: Notices of these sources are about things a rental customer can see and
#: fix or report. "box" (the self-check) names files and settings of the PC.
NOTICE_SOURCES = ("kamera", "drucker", "platte")

_PRINTER_WORDS = re.compile(r"Druck|Papier|Farbband|Blatt|Blätter|Kassette")
# One line is dropped whole if any of these is in it. Better a missing hint
# than an e-mail address, an IP or a path on a page anyone with the link reads.
_NOT_SHAREABLE = re.compile(
    r"@|https?://|\b\d{1,3}(?:\.\d{1,3}){3}\b|[0-9a-fA-F]{0,4}:[0-9a-fA-F]{0,4}:[0-9a-fA-F:]+"
    r"|[A-Za-z]:[\\/]|\\|(?:^|\s)/[^\s/]+/|\b(?=\w*\d)[A-Za-z0-9_-]{20,}")


def init_schema(cur) -> None:
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")


def rate_key(addr: Optional[str]) -> str:
    return f"statuspage:{addr or '-'}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _ready() -> None:
    from app import ecosystem
    ecosystem.init_schema()


def enable(device_id: int) -> str:
    """Switch on or make a new link. Returns the token; it is not kept anywhere."""
    _ready()
    token = secrets.token_urlsafe(TOKEN_BYTES)
    con = db.get_db()
    try:
        con.execute(f"""INSERT INTO {TABLE}(device_id, token_hash) VALUES (?, ?)
                        ON CONFLICT(device_id) DO UPDATE SET token_hash=excluded.token_hash,
                                                             created_at=datetime('now')""",
                    (int(device_id), _hash(token)))
        con.commit()
    finally:
        con.close()
    return token


def disable(device_id: int) -> None:
    _ready()
    con = db.get_db()
    try:
        con.execute(f"DELETE FROM {TABLE} WHERE device_id=?", (int(device_id),))
        con.commit()
    finally:
        con.close()


def is_enabled(device_id: int) -> bool:
    _ready()
    con = db.get_db()
    try:
        return con.execute(f"SELECT 1 FROM {TABLE} WHERE device_id=?", (int(device_id),)).fetchone() is not None
    finally:
        con.close()


def created_at(device_id: int) -> Optional[str]:
    _ready()
    con = db.get_db()
    try:
        row = con.execute(f"SELECT created_at FROM {TABLE} WHERE device_id=?", (int(device_id),)).fetchone()
    finally:
        con.close()
    return row["created_at"] if row else None


def device_for(token: str) -> Optional[Dict[str, Any]]:
    """The booth behind a token, or None for anything that is not a live link.

    A deactivated booth (``devices.active = 0``; its API key is refused too) has
    no status page, whatever its link says.
    """
    if not _TOKEN_RE.match(token or ""):
        return None
    _ready()
    con = db.get_db()
    try:
        row = con.execute(f"SELECT device_id FROM {TABLE} WHERE token_hash=?", (_hash(token),)).fetchone()
    finally:
        con.close()
    device = db.get_device_by_id(row["device_id"]) if row else None
    return device if device and device.get("active") else None


# ── What the page shows ───────────────────────────────────────

def _shareable(text: Any) -> str:
    line = " ".join(str(text or "").split())[:200]
    return "" if not line or _NOT_SHAREABLE.search(line) else line


def relative(minutes: Optional[float]) -> str:
    """'vor 2 Minuten' / '2 minutes ago', in the page's language."""
    from app.i18n import t
    if minutes is None:
        return ""
    if minutes < 1:
        return t("statuspage.ago.now")
    return t("statuspage.ago", span=span(minutes))


def span(minutes: float) -> str:
    """'25 Minuten' / '3 hours': the length of a silence."""
    from app.i18n import t
    m = int(minutes)
    if m < 120:
        return t("statuspage.minute" if m == 1 else "statuspage.minutes", n=max(m, 1))
    if m < 48 * 60:
        return t("statuspage.hours", n=m // 60)
    return t("statuspage.days", n=m // (60 * 24))


def _printer(status: Dict[str, Any], light: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    present = (status.get("printer_status") or status.get("printers_total") or status.get("sheets")
               or status.get("sheets_left") or status.get("prints_held"))
    if not present:
        return None
    # The light's own words about printer and paper ("Drucker 1: Papierfach noch
    # etwa 3 Blatt (gezählt)", "2 Druck(e) warten auf Papier oder Farbband").
    problems = [f["text"] for f in light.get("findings") or []
                if f.get("source") == "status" and _PRINTER_WORDS.search(f.get("text") or "")]
    lines = [x for x in (_shareable(p) for p in problems) if x]
    stock = [x for x in (_shareable(p) for p in (light.get("paper_text") or "").split(" · ")) if x]
    return {"ok": not problems, "lines": lines, "stock": stock}


def _battery(status: Dict[str, Any]) -> str:
    raw = str(status.get("camera_battery") or "").strip().lower()
    return raw if raw == "ac" or (raw.isdigit() and int(raw) <= 100) else ""


def _today(status: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Runs and prints of the booth's reported day, from the evening counters."""
    from app import stats
    raw = stats.clean_booth_stats(status.get("booth_stats"))
    if not raw:
        return None
    z = stats.zusammenfassung(raw.get("today") or {})
    return {"is_today": raw["day"] == date.today().isoformat(), "day": raw["day"],
            "runs": z["runs_total"], "prints": z["prints"]}


def view(device: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the template shows — and nothing more. Built in the request's language."""
    from app import booth
    from app.i18n import t
    status = db.get_device_status(int(device["id"])) or {}
    light = booth.light(device, status)
    age = light["age_min"]
    never = age is None
    stale = bool(light["stale"]) and not never
    health = light["health"]
    if never:
        sentence = t("statuspage.never")
    elif stale:
        sentence = t("statuspage.no_connection", span=span(age))
    else:
        sentence = t(f"statuspage.light.{health}")

    cam_state = str(status.get("camera_state") or "").lower()
    camera = None if never or not cam_state else (cam_state == "ready")
    printer = None if never else _printer(status, light)
    notices = []
    for n in status.get("notices") or []:
        if isinstance(n, dict) and n.get("quelle") in NOTICE_SOURCES:
            line = _shareable(n.get("titel"))
            if line and line not in notices:
                notices.append(line)
    # Yellow or red with nothing on the page that says why: say that much
    # without naming the self-check's findings (they name files on the PC).
    explained = notices or camera is False or (printer and not printer["ok"])
    other = health in ("yellow", "red") and not stale and not never and not explained
    return {
        "name": device.get("name") or "",
        "health": health,
        "sentence": sentence,
        "never": never,
        "stale": stale,
        "last_contact": relative(age),
        "camera": camera,
        # "57" or "ac" from the heartbeat; nothing when the camera cannot tell.
        "camera_battery": _battery(status) if not never else "",
        "camera_battery_low": _battery(status).isdigit() and int(_battery(status)) <= 20,
        "printer": printer,
        "notices": notices,
        "other_issue": other,
        "today": None if never else _today(status),
        "refresh": REFRESH_SECONDS,
    }
