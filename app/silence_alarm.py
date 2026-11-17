"""Warn the owner by mail when a box stops answering — only when switched on.

Off by default, per box (admin → Boxen → the box, card "Warnung, wenn die
Box schweigt"). Guests may switch a rented box off on purpose; a mail every
time that happens would teach the owner to ignore the mail.

What it does
* One mail when a box has been silent for ``minutes`` (5–240, default 15),
  one mail when it reports again ("wieder da nach 42 min"), nothing between.
  The state lives in the table ``device_silence_alarm`` (additive).
* A box that says goodbye (``POST /api/v2/booth/going-down``, its own device
  key) is not reported: "bewusst heruntergefahren um 23:41". If it said it
  would come back (restart, update) and is still silent after the window,
  it is reported after all.
* "Only while an event is running" (default on) uses
  :func:`app.booth.event_running` — the same rule that turns the light red:
  the box's assigned event is dated today, or yesterday until 08:00 local
  time; an event without a date counts while the box reported in the last
  six hours.
* Optional quiet hours (HH:MM–HH:MM, local server time): the silence mail
  waits until they end; if the box is back by then, nothing is sent.

Who sends: every Gunicorn worker runs :func:`check` about once a minute in a
daemon thread (started by the first request under a real server, as the
maintenance run is). A state change is one conditional UPDATE; only the
worker whose UPDATE changed the row sends the mail, so two workers send one
mail. ``python -m app.silence_alarm`` runs one check from cron instead
(``PHOTOBOX_SILENCE_CHECK=off`` stops the thread).

The gallery cannot tell a dead box from a dead internet line at the venue;
the mail says so.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DEFAULT_MINUTES = 15
MIN_MINUTES, MAX_MINUTES = 5, 240
CHECK_EVERY_S = 60
#: A heartbeat that arrives this soon after a goodbye was already on its way;
#: it does not mean the box is back.
GOODBYE_GRACE_S = 10
REASONS = ("quit", "restart", "update", "error", "signal",
           "windows_close", "windows_logoff", "windows_shutdown")
EXPECTED_BACK = {"restart", "update", "error"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_silence_alarm (
    device_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    minutes INTEGER NOT NULL DEFAULT 15,
    recipient TEXT NOT NULL DEFAULT '',
    only_event INTEGER NOT NULL DEFAULT 1,
    quiet_from TEXT NOT NULL DEFAULT '',
    quiet_to TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'ok',
    alarm_at TEXT,
    alarm_last_seen TEXT,
    back_at TEXT,
    last_mail_at TEXT,
    last_mail_kind TEXT,
    down_at TEXT,
    down_reason TEXT,
    down_expect_back INTEGER
)
"""

_EXTRA_COLUMNS = {"last_mail_error": "TEXT"}
_schema_ready = [False]
_schema_lock = threading.Lock()


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _fmt(t: dt.datetime) -> str:
    """SQLite's own format (``datetime('now')``), so strings compare in time order."""
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _parse(ts: Optional[str]) -> Optional[dt.datetime]:
    text = str(ts or "").strip().replace("T", " ").replace("Z", "")[:19]
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return dt.datetime.strptime(text[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return None


def _local(t: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """UTC (naive) to the server's local time (naive). The server's TZ decides."""
    if t is None:
        return None
    return t.replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)


def ensure_schema(con=None) -> None:
    if _schema_ready[0] and con is None:
        return
    from app import db
    own = con is None
    con = con or db.get_db()
    try:
        with _schema_lock:
            con.execute(_SCHEMA)
            cols = {r[1] for r in con.execute("PRAGMA table_info(device_silence_alarm)")}
            for name, kind in _EXTRA_COLUMNS.items():
                if name not in cols:
                    con.execute(f"ALTER TABLE device_silence_alarm ADD COLUMN {name} {kind}")
            con.commit()
            _schema_ready[0] = True
    finally:
        if own:
            con.close()


def _row(con, device_id: int) -> Optional[Dict[str, Any]]:
    r = con.execute("SELECT * FROM device_silence_alarm WHERE device_id=?", (int(device_id),)).fetchone()
    return dict(r) if r else None


DEFAULTS = {"enabled": 0, "minutes": DEFAULT_MINUTES, "recipient": "", "only_event": 1,
            "quiet_from": "", "quiet_to": ""}


def settings(device_id: int) -> Dict[str, Any]:
    """The box's alarm settings and state; defaults for a box never configured."""
    from app import db
    ensure_schema()
    con = db.get_db()
    try:
        row = _row(con, device_id)
    finally:
        con.close()
    return {**DEFAULTS, "device_id": int(device_id), "state": "ok", **(row or {})}


def _valid_hhmm(text: str) -> bool:
    try:
        h, m = text.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59 and len(m) == 2
    except (ValueError, AttributeError):
        return False


def save_settings(device_id: int, *, enabled: bool, minutes: Any = DEFAULT_MINUTES,
                  recipient: str = "", only_event: bool = True,
                  quiet_from: str = "", quiet_to: str = "") -> List[str]:
    """Store the admin's choice. Returns the problems (nothing stored if any).

    The recipient is one address typed by the admin, or empty for the
    account's notification address (``admin_email``). Nothing a box sends can
    reach this; the gallery never mails an address it got from a device.
    """
    from app import db, emailer
    problems: List[str] = []
    try:
        minutes = int(str(minutes).strip() or DEFAULT_MINUTES)
    except ValueError:
        problems.append("Minuten: bitte eine Zahl")
        minutes = DEFAULT_MINUTES
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        problems.append(f"Minuten: {MIN_MINUTES} bis {MAX_MINUTES}")
    recipient = str(recipient or "").strip()
    if recipient and not emailer.valid_address(recipient):
        problems.append("Empfänger: keine gültige E-Mail-Adresse (eine Adresse)")
    quiet_from, quiet_to = str(quiet_from or "").strip(), str(quiet_to or "").strip()
    if bool(quiet_from) != bool(quiet_to) or (quiet_from and not (_valid_hhmm(quiet_from)
                                                                 and _valid_hhmm(quiet_to))):
        problems.append("Ruhezeit: beide Zeiten als HH:MM, oder beide leer")
    if problems:
        return problems
    ensure_schema()
    con = db.get_db()
    try:
        con.execute("INSERT OR IGNORE INTO device_silence_alarm(device_id) VALUES (?)", (int(device_id),))
        con.execute(
            "UPDATE device_silence_alarm SET enabled=?, minutes=?, recipient=?, only_event=?, "
            "quiet_from=?, quiet_to=? WHERE device_id=?",
            (1 if enabled else 0, minutes, recipient, 1 if only_event else 0,
             quiet_from, quiet_to, int(device_id)))
        if not enabled:
            # Switched off during an alarm: no "back" mail later either.
            con.execute("UPDATE device_silence_alarm SET state='ok', alarm_at=NULL, "
                        "alarm_last_seen=NULL, back_at=NULL WHERE device_id=?", (int(device_id),))
        con.commit()
    finally:
        con.close()
    return []


# ── What the box tells us ─────────────────────────────────────────────

def note_going_down(device_id: int, reason: str, expect_back: Optional[bool] = None,
                    now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """The box says it is going down on purpose. Stored whether or not the
    alarm is on: the box page shows it either way."""
    from app import db
    reason = str(reason or "").strip().lower()
    if reason not in REASONS:
        reason = "signal"
    if expect_back is None:
        expect_back = reason in EXPECTED_BACK
    now = now or _utcnow()
    ensure_schema()
    con = db.get_db()
    try:
        con.execute("INSERT OR IGNORE INTO device_silence_alarm(device_id) VALUES (?)", (int(device_id),))
        con.execute("UPDATE device_silence_alarm SET down_at=?, down_reason=?, down_expect_back=? "
                    "WHERE device_id=?", (_fmt(now), reason, 1 if expect_back else 0, int(device_id)))
        con.commit()
    finally:
        con.close()
    return {"reason": reason, "expect_back": bool(expect_back)}


def note_heartbeat(device_id: int, now: Optional[dt.datetime] = None) -> None:
    """Called with every heartbeat (``db.record_heartbeat``). Two cheap UPDATEs
    that touch nothing unless the box was silent or had said goodbye."""
    from app import db
    now = now or _utcnow()
    ensure_schema()
    con = db.get_db()
    try:
        # The first report after an alarm: the time the "back" mail names.
        con.execute("UPDATE device_silence_alarm SET back_at=? WHERE device_id=? "
                    "AND state='alarm' AND back_at IS NULL", (_fmt(now), int(device_id)))
        # Back after a goodbye — unless this report was already on its way.
        con.execute("UPDATE device_silence_alarm SET down_at=NULL, down_reason=NULL, down_expect_back=NULL "
                    "WHERE device_id=? AND down_at IS NOT NULL AND down_at<=?",
                    (int(device_id), _fmt(now - dt.timedelta(seconds=GOODBYE_GRACE_S))))
        con.commit()
    finally:
        con.close()


# ── The check ─────────────────────────────────────────────────────────

def _in_quiet(row: Dict[str, Any], local: dt.datetime) -> bool:
    a, b = row.get("quiet_from") or "", row.get("quiet_to") or ""
    if not (_valid_hhmm(a) and _valid_hhmm(b)):
        return False
    fa = int(a[:2]) * 60 + int(a[3:])
    fb = int(b[:2]) * 60 + int(b[3:])
    m = local.hour * 60 + local.minute
    return fa <= m < fb if fa <= fb else (m >= fa or m < fb)


def _claim(con, sql: str, args: tuple) -> bool:
    """One conditional UPDATE. SQLite serialises writers: of two workers
    running the same check, one sees rowcount 1 and sends the mail."""
    cur = con.execute(sql, args)
    con.commit()
    return cur.rowcount == 1


def check(now: Optional[dt.datetime] = None, sender=None) -> List[Dict[str, Any]]:
    """Look at every box with the alarm on; send what is due. Returns what was sent."""
    from app import booth as booth_mod, db
    now = now or _utcnow()
    local = _local(now)
    ensure_schema()
    sent: List[Dict[str, Any]] = []
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM device_silence_alarm WHERE enabled=1 OR state='alarm'")]
        for row in rows:
            device = db.get_device_by_id(row["device_id"])
            if not device:
                continue
            if not row.get("enabled"):
                con.execute("UPDATE device_silence_alarm SET state='ok', alarm_at=NULL, alarm_last_seen=NULL, "
                            "back_at=NULL WHERE device_id=?", (row["device_id"],))
                con.commit()
                continue
            last = _parse(device.get("last_heartbeat_at"))
            if row["state"] == "alarm":
                seen = _parse(row.get("alarm_last_seen"))
                back = _parse(row.get("back_at")) or (last if last and seen and last > seen else None)
                if not back:
                    continue
                if _claim(con, "UPDATE device_silence_alarm SET state='ok', alarm_at=NULL, alarm_last_seen=NULL, "
                               "back_at=NULL, last_mail_at=?, last_mail_kind='back' "
                               "WHERE device_id=? AND state='alarm' AND alarm_at IS ?",
                          (_fmt(now), row["device_id"], row.get("alarm_at"))):
                    gone = int(round((back - seen).total_seconds() / 60)) if seen else None
                    sent.append(_deliver(con, "back", device, row, now, seen, gone, sender))
                continue
            if last is None:
                continue                    # never reported: nothing to miss
            minutes = int(row.get("minutes") or DEFAULT_MINUTES)
            if (now - last).total_seconds() < minutes * 60:
                continue
            down = _parse(row.get("down_at"))
            if down:
                if not row.get("down_expect_back"):
                    continue                # switched off on purpose
                if (now - down).total_seconds() < minutes * 60:
                    continue                # announced restart, still within the window
            if row.get("only_event") and not booth_mod.event_running(device, now=local):
                continue
            if _in_quiet(row, local):
                continue
            if _claim(con, "UPDATE device_silence_alarm SET state='alarm', alarm_at=?, alarm_last_seen=?, "
                           "back_at=NULL, last_mail_at=?, last_mail_kind='silent' "
                           "WHERE device_id=? AND state='ok'",
                      (_fmt(now), _fmt(last), _fmt(now), row["device_id"])):
                gone = int((now - last).total_seconds() // 60)
                sent.append(_deliver(con, "silent", device, row, now, last, gone, sender))
    finally:
        con.close()
    return sent


# ── The mail ──────────────────────────────────────────────────────────

REASON_TEXT = {
    "de": {"quit": "an der Box beendet", "restart": "Neustart", "update": "Update wird eingespielt",
           "error": "Fehler, startet neu", "signal": "vom System beendet",
           "windows_close": "Fenster geschlossen", "windows_logoff": "Windows-Abmeldung",
           "windows_shutdown": "Windows fährt herunter"},
    "en": {"quit": "quit at the box", "restart": "restart", "update": "installing an update",
           "error": "error, restarting", "signal": "ended by the system",
           "windows_close": "window closed", "windows_logoff": "Windows sign-out",
           "windows_shutdown": "Windows shutting down"},
}


def _lang() -> str:
    from app import db
    return "en" if (db.get_setting("app_language") or "de").strip().lower().startswith("en") else "de"


def _battery_text(value: Any, lang: str) -> str:
    if value in (None, ""):
        return ""
    if str(value).lower() == "ac":
        return "Netzteil" if lang == "de" else "mains power"
    return f"{value} %"


def compose(kind: str, device: Dict[str, Any], status: Dict[str, Any], *, last_seen: Optional[dt.datetime],
            minutes: Optional[int], lang: str, link: str) -> Dict[str, str]:
    """Subject and body. Plain text, the facts the owner needs on the phone."""
    from app import booth as booth_mod
    name = device.get("name") or f"#{device.get('id')}"
    at = _local(last_seen)
    when = at.strftime("%d.%m.%Y %H:%M") if at else "—"
    de = lang == "de"
    if kind == "back":
        subject = (f"Box „{name}“ ist wieder da (nach {minutes} min)" if de
                   else f"Box \"{name}\" is back (after {minutes} min)")
        body = ((f"Die Box „{name}“ meldet sich wieder — wieder da nach {minutes} min.\n"
                 f"Zuletzt gehört vor der Pause: {when}.\n") if de else
                (f"The box \"{name}\" is reporting again — back after {minutes} min.\n"
                 f"Last heard before the gap: {when}.\n"))
        if link:
            body += f"\n{'Zustand der Box' if de else 'Box status'}: {link}\n"
        return {"subject": subject, "body": body}
    facts = []
    label = (lambda d, e: d if de else e)
    if status.get("camera_state"):
        facts.append(f"{label('Kamera', 'Camera')}: {status['camera_state']}")
    battery = _battery_text(status.get("camera_battery"), lang)
    if battery:
        facts.append(f"{label('Kamera-Akku', 'Camera battery')}: {battery}")
    if status.get("printer_status"):
        facts.append(f"{label('Drucker', 'Printer')}: {status['printer_status']}")
    paper = booth_mod._paper_text(status)
    if paper:
        facts.append(f"{label('Papier', 'Paper')}: {paper}")
    if status.get("prints_pending") or status.get("prints_held"):
        facts.append(label(f"Drucke: {int(status.get('prints_pending') or 0)} offen, "
                           f"{int(status.get('prints_held') or 0)} warten",
                           f"Prints: {int(status.get('prints_pending') or 0)} pending, "
                           f"{int(status.get('prints_held') or 0)} held"))
    if "queue_pending" in status or "queue_failed" in status:
        facts.append(label(f"Uploads: {int(status.get('queue_pending') or 0)} warten, "
                           f"{int(status.get('queue_failed') or 0)} fehlgeschlagen",
                           f"Uploads: {int(status.get('queue_pending') or 0)} waiting, "
                           f"{int(status.get('queue_failed') or 0)} failed"))
    if de:
        subject = f"Box „{name}“ antwortet nicht (seit {minutes} min)"
        body = (f"Die Box „{name}“ hat sich seit {minutes} min nicht gemeldet.\n"
                f"Letzter Kontakt: {when}.\n\n"
                "Ob die Box selbst steht oder nur die Internetverbindung vor Ort weg ist, "
                "kann die Galerie nicht unterscheiden (Box oder Internetverbindung).\n\n"
                + ("Zuletzt bekannt:\n" + "".join(f"  · {f}\n" for f in facts) + "\n" if facts else "")
                + "Vor Ort prüfen:\n  · Läuft der Box-PC, ist die Fotobox-Oberfläche zu sehen?\n"
                  "  · Strom: Steckdosenleiste, Netzteil\n"
                  "  · Internet: WLAN/Router oder mobiler Hotspot\n"
                  "  · Kamera eingeschaltet und angeschlossen\n")
        if link:
            body += f"\nZustand der Box: {link}\n"
        body += "\nEine weitere Mail kommt erst, wenn die Box wieder da ist.\n"
    else:
        subject = f"Box \"{name}\" is not answering (for {minutes} min)"
        body = (f"The box \"{name}\" has not reported for {minutes} min.\n"
                f"Last contact: {when}.\n\n"
                "The gallery cannot tell a stopped box from a lost internet connection at the venue "
                "(box or internet connection).\n\n"
                + ("Last known:\n" + "".join(f"  · {f}\n" for f in facts) + "\n" if facts else "")
                + "Check on site:\n  · Is the booth PC running and showing the booth screen?\n"
                  "  · Power: power strip, power supply\n"
                  "  · Internet: Wi-Fi/router or mobile hotspot\n"
                  "  · Camera switched on and connected\n")
        if link:
            body += f"\nBox status: {link}\n"
        body += "\nThe next mail comes only when the box is back.\n"
    return {"subject": subject, "body": body}


def _recipient(row: Dict[str, Any]) -> str:
    from app import db
    return (row.get("recipient") or db.get_setting("admin_email") or "").strip()


def box_link(device_id: int) -> str:
    """Only from the configured base address — never from a request's Host."""
    from app import db
    base = db.base_url()
    return f"{base}/admin/devices/{int(device_id)}/booth#schweigen" if base else ""


def _deliver(con, kind: str, device: Dict[str, Any], row: Dict[str, Any], now: dt.datetime,
             last_seen: Optional[dt.datetime], minutes: Optional[int], sender) -> Dict[str, Any]:
    from app import db, emailer, notifier
    lang = _lang()
    link = box_link(device["id"])
    mail = compose(kind, device, db.get_device_status(device["id"]), last_seen=last_seen,
                   minutes=minutes, lang=lang, link=link)
    to = _recipient(row)
    error = ""
    if not to:
        error = "keine Empfängeradresse"
    elif sender is None and not emailer.smtp_configured():
        error = "kein Postausgang eingerichtet"
    else:
        try:
            (sender or emailer.send_text_mail)(to_email=to, subject=mail["subject"], body=mail["body"])
        except Exception as e:              # noqa: BLE001 — no retry storm: the state already moved
            error = str(e)[:200]
    con.execute("UPDATE device_silence_alarm SET last_mail_error=? WHERE device_id=?",
                (error or None, device["id"]))
    con.commit()
    if error:
        log.warning("silence alarm %s for box %s: mail not sent: %s", kind, device["id"], error)
    else:
        log.info("silence alarm %s for box %s mailed", kind, device["id"])
    try:
        notifier.send_webhook({"event": "booth_silent" if kind == "silent" else "booth_back",
                               "device": device.get("name"), "device_id": device["id"],
                               "last_contact": _fmt(last_seen) if last_seen else None,
                               "minutes": minutes, "status_url": link})
    except Exception:                       # noqa: BLE001
        log.debug("webhook for silence alarm failed", exc_info=True)
    return {"kind": kind, "device_id": device["id"], "to": to, "error": error, **mail}


# ── What the admin sees ───────────────────────────────────────────────

def goodbye_text(row: Optional[Dict[str, Any]], lang: str = "de") -> str:
    """ "bewusst heruntergefahren um 23:41 (an der Box beendet)" or "". """
    row = row or {}
    down = _local(_parse(row.get("down_at")))
    if not down:
        return ""
    if row.get("down_reason") == "error":
        # The booth ended with an error and its watcher starts it again
        # after a pause: nothing about that was on purpose.
        if lang == "en":
            return f"stopped with an error at {down:%H:%M}, restarting"
        return f"mit Fehler beendet um {down:%H:%M}, startet neu"
    reason = REASON_TEXT.get(lang, REASON_TEXT["de"]).get(row.get("down_reason") or "", "")
    if lang == "en":
        text = f"shut down on purpose at {down:%H:%M}" + (f" ({reason})" if reason else "")
        return text + (", expected back" if row.get("down_expect_back") else "")
    text = f"bewusst heruntergefahren um {down:%H:%M}" + (f" ({reason})" if reason else "")
    return text + (", kommt wieder" if row.get("down_expect_back") else "")


def view(device_id: int) -> Dict[str, Any]:
    """Settings, state and one line of text for the box page and the light."""
    s = settings(device_id)
    line = ""
    if s.get("state") == "alarm":
        seen = _local(_parse(s.get("alarm_last_seen")))
        line = "Warnung verschickt: antwortet nicht" + (f" seit {seen:%H:%M}" if seen else "")
        if s.get("last_mail_error"):
            line += f" (Mail nicht zugestellt: {s['last_mail_error']})"
    elif s.get("last_mail_kind") == "back" and s.get("last_mail_at"):
        at = _local(_parse(s["last_mail_at"]))
        line = f"Zuletzt: „wieder da“ gemeldet um {at:%d.%m. %H:%M}" if at else ""
    return {**s, "goodbye": goodbye_text(s), "line": line, "recipient_default": _recipient({}),
            "minutes_min": MIN_MINUTES, "minutes_max": MAX_MINUTES}


def dashboard_lines() -> List[Dict[str, Any]]:
    """Boxes in alarm, and boxes that said goodbye: one line each for the dashboard."""
    from app import db
    ensure_schema()
    con = db.get_db()
    try:
        # The card is "the last 24 hours": a goodbye from last week is no news.
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM device_silence_alarm WHERE state='alarm' OR down_at>=?",
            (_fmt(_utcnow() - dt.timedelta(hours=24)),))]
    finally:
        con.close()
    out = []
    for row in rows:
        device = db.get_device_by_id(row["device_id"])
        if not device:
            continue
        if row["state"] == "alarm":
            seen = _local(_parse(row.get("alarm_last_seen")))
            out.append({"device_id": device["id"], "name": device.get("name"), "level": "error",
                        "text": "antwortet nicht" + (f" seit {seen:%H:%M}" if seen else "")
                                + " — Warnung verschickt"})
        else:
            out.append({"device_id": device["id"], "name": device.get("name"), "level": "info",
                        "text": goodbye_text(row)})
    return out


# ── Running it ────────────────────────────────────────────────────────

_thread_started = [False]
_thread_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("PHOTOBOX_SILENCE_CHECK", "on").strip().lower() not in ("off", "0", "no")


def _loop(app) -> None:
    while True:
        try:
            with app.app_context():
                check()
        except Exception:                   # noqa: BLE001 — the loop must outlive one bad run
            log.exception("silence alarm check failed")
        time.sleep(CHECK_EVERY_S)


def on_request(app) -> None:
    """Before-request hook: start the per-worker check thread once.

    Only under a real server (as ``maintenance.on_request``): a command-line
    tool using the test client must not leave a thread behind. The thread
    does not depend on further requests — a box that falls silent is exactly
    the moment no request comes.
    """
    from flask import request
    if _thread_started[0] or not enabled():
        return
    if app.testing or not request.environ.get("SERVER_SOFTWARE"):
        return
    with _thread_lock:
        if _thread_started[0]:
            return
        _thread_started[0] = True
    threading.Thread(target=_loop, args=(app,), name="silence-alarm", daemon=True).start()


def main() -> int:                          # pragma: no cover - command line
    from app import create_app
    app = create_app()
    with app.test_request_context():
        app.test_client().get("/healthz")
        for item in check():
            print(f"{item['kind']}: box {item['device_id']} → {item['to'] or '-'} {item['error']}")
    return 0


if __name__ == "__main__":                  # pragma: no cover
    raise SystemExit(main())
