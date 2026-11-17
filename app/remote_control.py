"""Live remote control of a booth from the gallery (MCP extra right ``control``).

Why a second, faster way to the booth: jobs used to reach a booth when it
next reported — every 30 seconds, every 5 while jobs wait. Fine for "print a
test page", useless for "start a run now and tell me what came out".

How it works, without a new dependency and without holding gunicorn's two
sync workers:

* A **control session** per booth is open while a ``control`` tool was used
  in the last :data:`SESSION_SECONDS`. The sync answer then carries
  ``fast_seconds``; the booth's agent polls ``POST /api/v2/booth/jobs`` (one
  small query, no status, no profile) every that many seconds until an answer
  says 0, then falls back to its normal interval. No long poll: a request
  that waits would hold one of the two workers.
* A control tool **waits** for the booth's answer for at most
  :data:`WAIT_SECONDS`, and only one call in the whole gallery waits at a time
  (a slot in the database, shared by every worker). With two workers one is
  therefore always free for the booth's poll; a second waiting call returns
  at once with the job id ("angenommen") and ``get_job`` reads it later.
* A control job not picked up within :data:`JOB_TTL_SECONDS` expires: an
  order "start a run" must not fire when the booth comes back online an hour
  later. Executed ids are remembered on the booth (``JobLedger``), so a job
  never runs twice. At most :data:`RATE_PER_MINUTE` control jobs per booth.
* The booth executes only what ``ACTIONS`` lists, through the same methods the
  kiosk uses, and only when "Fernsteuerung durch die Galerie erlauben" is on
  at the booth (off by default).
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from app import db

SESSION_SECONDS = 300
FAST_POLL_SECONDS = 1.5
JOB_TTL_SECONDS = 60
WAIT_SECONDS = 15.0
RATE_PER_MINUTE = 30
#: The booth counts as listening when its last fast poll is this recent.
LISTENING_SECONDS = 6.0

#: The actions a ``remote_control`` job may carry. Identical keys in
#: booth/boothcore/fernsteuerung.py (each suite compares the two files).
ACTIONS = {
    "status": "Zustand: Kamera, Drucker, Sperre, laufende Durchgänge",
    "trigger": "Durchgang an der Box auslösen wie mit dem Buzzer (Countdown auf dem Bildschirm)",
    "start_run": "Durchgang ohne Bildschirm anlegen (Modus, Format, Vorlage)",
    "shot": "Eine Aufnahme im angelegten Durchgang",
    "clip": "Clip aufnehmen (Boomerang/Video)",
    "finish": "Durchgang fertigstellen (Look)",
    "run": "Ganzer Durchgang ohne Bildschirm: anlegen, alle Aufnahmen, fertigstellen, optional drucken",
    "abort": "Laufenden Durchgang abbrechen",
    "set_text": "Gruß des Gastes zum Durchgang setzen",
    "print": "Durchgang drucken (Kopien, Kontingente gelten)",
    "reprint": "Aus der Galerie der Box nachdrucken (Kontingente gelten)",
    "lock": "Box sperren",
    "unlock": "Box entsperren",
    "restart_app": "App neu starten",
    "paper_changed": "Papier nachgelegt",
    "ribbon_changed": "Farbband gewechselt",
    "reset_print_counter": "Druckzähler zurücksetzen",
    "release_prints": "Wartende Drucke erneut",
    "discard_prints": "Wartende Drucke verwerfen (zweistufig)",
    "test_print": "Testdruck",
    "test_capture": "Testfoto",
    "doctor": "Selbstprüfung",
    "logs": "Letzte Meldungen der Box",
    "live_image": "Ein Livebild der Kamera (klein)",
    "screenshot": "Bildschirmfoto des Kiosk-Fensters, wenn die Box das kann",
    "list_runs": "Die letzten Durchgänge an der Box",
    "delete_run": "Durchgang an der Box löschen (zweistufig)",
    "quit": "Box-Programm beenden (zweistufig)",
    "restart_pc": "PC der Box neu starten (zweistufig; nie während eines Durchgangs oder Drucks)",
    "shutdown_pc": "PC der Box herunterfahren (zweistufig; nie während eines Durchgangs oder Drucks)",
    "set_language": "Sprache des Kiosks setzen, bis zum Neustart der Box (zweistufig; lang, leer = Profil)",
}
#: Need the two-step confirm in MCP. The kiosk language is on the list too:
#: it changes what every next guest reads, so it is asked for twice like the rest.
DESTRUCTIVE_ACTIONS = frozenset({"delete_run", "quit", "discard_prints", "restart_pc", "shutdown_pc",
                                 "set_language"})


def init_schema(cur) -> None:
    """Additive. Called from ``ecosystem.init_schema``."""
    cur.execute("""CREATE TABLE IF NOT EXISTS remote_sessions (
        device_id INTEGER PRIMARY KEY, until REAL NOT NULL, opened_by TEXT NOT NULL DEFAULT '',
        last_poll REAL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS mcp_wait_slot (
        id INTEGER PRIMARY KEY CHECK (id = 1), until REAL NOT NULL DEFAULT 0, holder TEXT NOT NULL DEFAULT '')""")
    cur.execute("INSERT OR IGNORE INTO mcp_wait_slot (id, until) VALUES (1, 0)")


def _con():
    con = db.get_db()
    init_schema(con)
    return con


# ── Sessions ──────────────────────────────────────────────────

def open_session(device_id: int, by: str) -> float:
    until = time.time() + SESSION_SECONDS
    con = _con()
    try:
        con.execute("INSERT INTO remote_sessions (device_id, until, opened_by) VALUES (?,?,?) "
                    "ON CONFLICT(device_id) DO UPDATE SET until=excluded.until, opened_by=excluded.opened_by",
                    (int(device_id), until, str(by or "")[:120]))
        con.commit()
    finally:
        con.close()
    return until


def close_session(device_id: int) -> bool:
    con = _con()
    try:
        cur = con.execute("UPDATE remote_sessions SET until=0 WHERE device_id=? AND until > ?",
                          (int(device_id), time.time()))
        con.commit()
        return (cur.rowcount or 0) > 0
    finally:
        con.close()


def session(device_id: int) -> Dict[str, Any]:
    con = _con()
    try:
        row = con.execute("SELECT * FROM remote_sessions WHERE device_id=?", (int(device_id),)).fetchone()
    finally:
        con.close()
    now = time.time()
    if not row:
        return {"active": False, "until": None, "listening": False}
    return {"active": row["until"] > now, "until": row["until"] if row["until"] > now else None,
            "listening": bool(row["last_poll"] and now - row["last_poll"] < LISTENING_SECONDS),
            "opened_by": row["opened_by"]}


def fast_seconds(device_id: int) -> float:
    """What the booth is told: poll this often, or 0 = back to normal."""
    return FAST_POLL_SECONDS if session(device_id)["active"] else 0


def note_poll(device_id: int) -> None:
    con = _con()
    try:
        con.execute("UPDATE remote_sessions SET last_poll=? WHERE device_id=?", (time.time(), int(device_id)))
        con.commit()
    finally:
        con.close()


# ── Jobs ──────────────────────────────────────────────────────

def add_control_job(device_id: int, action: str, args: Optional[dict], by: str) -> int:
    """One ``remote_control`` job, bound to the booth and its current event."""
    from app import ecosystem
    if action not in ACTIONS:
        raise ValueError("Unbekannte Aktion. Möglich: " + ", ".join(sorted(ACTIONS)))
    device = db.get_device_by_id(int(device_id))
    if not device:
        raise ValueError(f"Keine Box mit der Nummer {device_id}")
    con = _con()
    try:
        n = con.execute("SELECT COUNT(*) FROM device_jobs WHERE device_id=? AND type='remote_control' "
                        "AND created_at > datetime('now', '-60 seconds')", (int(device_id),)).fetchone()[0]
    finally:
        con.close()
    if int(n or 0) >= RATE_PER_MINUTE:
        raise ValueError(f"Zu viele Fernsteuer-Aufträge an diese Box (höchstens {RATE_PER_MINUTE} je Minute). "
                         "Bitte kurz warten.")
    event = db.get_event_by_id(device["event_id"]) if device.get("event_id") else None
    payload = {"action": action, "args": dict(args or {}), "event": (event or {}).get("slug", ""),
               "by": str(by or "")[:120]}
    open_session(int(device_id), by)
    return ecosystem.add_job(int(device_id), "remote_control", payload)


def _acquire_wait_slot(holder: str, seconds: float) -> bool:
    now = time.time()
    con = _con()
    try:
        cur = con.execute("UPDATE mcp_wait_slot SET until=?, holder=? WHERE id=1 AND until < ?",
                          (now + seconds + 2, holder, now))
        con.commit()
        return (cur.rowcount or 0) == 1
    finally:
        con.close()


def _release_wait_slot(holder: str) -> None:
    con = _con()
    try:
        con.execute("UPDATE mcp_wait_slot SET until=0 WHERE id=1 AND holder=?", (holder,))
        con.commit()
    finally:
        con.close()


def wait_for(device_id: int, job_id: int, seconds: float = WAIT_SECONDS) -> Dict[str, Any]:
    """The job's state after waiting at most ``seconds`` — or at once, if another call already waits."""
    from app import ecosystem
    holder = f"{device_id}:{job_id}:{time.time()}"
    waited = _acquire_wait_slot(holder, seconds) if seconds > 0 else False
    try:
        deadline = time.time() + (seconds if waited else 0)
        while True:
            row = ecosystem.job_result(int(device_id), int(job_id))
            if row.get("state") in ("done", "expired") or time.time() >= deadline:
                break
            time.sleep(0.15)
    finally:
        if waited:
            _release_wait_slot(holder)
    return job_view(row, waited=waited)


def job_view(row: Dict[str, Any], waited: bool = True) -> Dict[str, Any]:
    """What a tool returns about a control job. The result text comes from the booth: untrusted."""
    if not row:
        return {"state": "unknown"}
    out: Dict[str, Any] = {"job_id": row.get("id"), "state": row.get("state")}
    try:
        payload = json.loads(row.get("payload_json") or "{}")
        out["action"] = payload.get("action")
    except ValueError:
        pass
    if row.get("state") == "done":
        out["ok"] = bool(row.get("ok"))
        raw = row.get("result") or ""
        try:
            out["result"] = json.loads(raw)
        except ValueError:
            out["result"] = raw
    elif row.get("state") == "expired":
        out["ok"] = False
        out["result"] = "Die Box hat nicht rechtzeitig geantwortet; der Auftrag ist verfallen und wird nicht mehr ausgeführt."
    else:
        out["hint"] = ("angenommen, Box hat noch nicht geantwortet — mit get_job nachsehen"
                       if waited else "angenommen; ein anderer Aufruf wartet gerade schon — mit get_job nachsehen")
    return out
