"""MCP tools to run and supervise an event remotely (17.11.2026).

The owner's rule: everything must be controllable via MCP. An audit of the
admin against the tools found that the things needed on an event evening
from a phone were missing: the dashboard, "Als gelesen markieren", the
backup button, the booth's queues and discarding failed uploads. Each tool
here does what its admin page does, through the same function:

* ``get_dashboard`` — admin.dashboard (booth.light, ecosystem error cards,
  storage_stats.overview, maintenance/backup status, pending review counts);
* ``acknowledge_booth_messages`` — admin.device_logs_ack;
* ``start_backup_now`` — admin.backup_now (two-step like the button's
  "Wirklich?"), its state readable in ``get_dashboard.backup_now``;
* ``get_booth_queues`` / ``discard_failed_uploads`` — the queue cards of
  admin_booth.sync_device and the job its "Verwerfen" buttons send;
* ``get_silence_alarm`` — the alarm settings without the recipient unless
  the access has ``secure`` (the address is a destination).

The rules of app/mcp_more.py hold: secrets never leave, booth text is data,
and every call needing more than ``read`` is in the change log.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import current_app

from app import booth as booth_status
from app import db, ecosystem
from app.mcp import _granted, allowed, tool

MASK = "••••"


def _booth(booth_id: Any) -> dict:
    try:
        device = db.get_device_by_id(int(booth_id))
    except (TypeError, ValueError):
        device = None
    if not device:
        raise ValueError(f"Keine Box mit der Nummer {booth_id}")
    return device


def _cut(text: Any, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


# ── Dashboard ────────────────────────────────────────────────────────

def _booth_rows() -> List[Dict[str, Any]]:
    errors = {int(r["device_id"]): r for r in ecosystem.recent_error_devices(since_hours=24, limit=200)}
    acked = {int(r["device_id"]) for r in ecosystem.acknowledged_error_devices(since_hours=24)}
    rows = []
    for d in db.list_devices() or []:
        status = db.get_device_status(d["id"]) or {}
        light = booth_status.light(d, status)       # the same light the admin pages show
        e = errors.get(int(d["id"])) or {}
        rows.append({
            "id": d["id"], "name": d["name"], "event_id": d.get("event_id"),
            "last_contact": d.get("last_heartbeat_at"), "age_text": light["age_text"],
            "health": light["health"], "stale": light["stale"], "reasons": light["reasons"],
            "event_running": light["event_running"], "paper_text": light["paper_text"],
            "print_queue": light["print_queue"], "upload_queue": light["upload_queue"],
            "messages": {
                "unacknowledged_errors": int(e.get("errors") or 0),
                "unacknowledged_warnings": int(e.get("warnings") or 0),
                "incidents": int(e.get("incidents") or 0),
                "reasons_text": _cut(e.get("reasons_text"), 300),
                "last_message": _cut(e.get("last_message"), 200),
                "last_at": e.get("last_at") or None,
                "acknowledged_at": ecosystem.logs_acknowledged_at(d["id"]) or None,
                # "since <time> no new errors" on the dashboard
                "all_acknowledged": int(d["id"]) in acked and not e,
            },
        })
    return rows


def _storage() -> Dict[str, Any]:
    from app import storage_stats
    overview = storage_stats.overview()
    disk = overview.get("disk") or {}
    return {"used": disk.get("used"), "total": disk.get("total"), "free": disk.get("free"),
            "percent_used": disk.get("percent_used"), "warning": bool(overview.get("warning")),
            "warn_percent": overview.get("warn_percent"),
            "media_bytes": (overview.get("totals") or {}).get("total_bytes")}


def _maintenance() -> Dict[str, Any]:
    from app import maintenance
    st = maintenance.status()
    # The report itself stays out: it names paths and the off-site address.
    return {k: st.get(k) for k in ("enabled", "last_run", "age_hours", "stale", "errors")}


def _backup() -> Dict[str, Any]:
    from app import backup
    st = backup.status()

    def item(b: Optional[dict]) -> Optional[dict]:
        if not b:
            return None
        return {k: b.get(k) for k in ("name", "created_at", "age_hours", "size", "media_included")}

    return {"enabled": st.get("enabled"), "count": st.get("count"), "stale": st.get("stale"),
            "last": item(st.get("last")), "newest_with_media": item(st.get("newest_with_media")),
            "error": _cut(st.get("error"), 200) or None, "offsite": st.get("offsite"),
            "offsite_failed": bool(st.get("offsite_error"))}


@tool("get_dashboard",
      "Alles vom Dashboard in einem Aufruf: je Box Zustand (health, stale, reasons, letzter Kontakt), die "
      "aktuellen Meldungen mit Anzahl ungelesener Fehler/Warnungen, letzter Meldung und ob alles als gelesen "
      "markiert ist; Summe der ungelesenen Meldungen; Speicherplatz des Servers (used/total/free in Bytes); "
      "Wartung, Sicherungen und „Sicherung jetzt“ (backup_now: state running/done/failed); wartende Bilder "
      "zur Freigabe je Event. Als gelesen markieren: acknowledge_booth_messages; Sicherung: start_backup_now.",
      scope="read", untrusted=True)
def _get_dashboard() -> dict:
    from app import maintenance
    ecosystem.init_schema()
    booths = _booth_rows()
    names = {int(e["id"]): e["name"] for e in (db.list_events() or [])}
    pending = [{"event_id": eid, "name": names.get(eid, "?"), "pending": n}
               for eid, n in sorted(ecosystem.pending_counts().items()) if n]
    now = maintenance.backup_now_status()
    return {"booths": booths,
            "unacknowledged": {"errors": sum(b["messages"]["unacknowledged_errors"] for b in booths),
                               "warnings": sum(b["messages"]["unacknowledged_warnings"] for b in booths),
                               "booths": sum(1 for b in booths if b["messages"]["unacknowledged_errors"])},
            "storage": _storage(), "maintenance": _maintenance(), "backup": _backup(),
            "backup_now": {k: now.get(k) for k in ("state", "at", "name")} | (
                {"error": _cut(now.get("error"), 200)} if now.get("error") else {}),
            "pending_review": pending}


@tool("acknowledge_booth_messages",
      "Die Meldungen einer Box als gelesen markieren (wie „Als gelesen markieren“ im Admin): Fehler bis jetzt "
      "zählen nicht mehr, die Zeilen bleiben in booth_diagnostics.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="manage")
def _acknowledge(booth_id: int) -> dict:
    ecosystem.init_schema()
    device = _booth(booth_id)
    stamp = ecosystem.acknowledge_logs(device["id"])
    return {"booth_id": device["id"], "acknowledged_at": stamp}


def _backup_preview() -> dict:
    from app import backup, maintenance
    return {"effect": "Eine Sicherung wird jetzt im Hintergrund angelegt — dieselbe wie nachts (Datenbank, "
                      "je nach Plan auch Medien, Aufräumen alter Sicherungen, Kopie außer Haus, falls "
                      "eingerichtet). Den Stand zeigt get_dashboard unter backup_now.",
            "backup_enabled": backup.enabled(), "last_backup_now": maintenance.backup_now_status().get("state") or ""}


_BACKUP_OUTCOME = {"started": "Sicherung läuft im Hintergrund; Stand in get_dashboard.backup_now.",
                   "running": "Es läuft schon eine Sicherung; Stand in get_dashboard.backup_now.",
                   "off": "Sicherungen sind auf diesem Server abgeschaltet (PHOTOBOX_BACKUP=off)."}


@tool("start_backup_now",
      "Eine Sicherung jetzt anlegen (wie „Sicherung jetzt anlegen“ auf dem Dashboard).",
      scope="manage", confirm=_backup_preview)
def _start_backup_now() -> dict:
    from app import maintenance
    outcome = maintenance.start_backup_now(current_app._get_current_object())
    return {"outcome": outcome, "text": _BACKUP_OUTCOME.get(outcome, outcome)}


# ── Queues ───────────────────────────────────────────────────────────

#: What of the separate sync program's report a chat gets: the figures of the
#: page, not its log or file names (a file name can carry a guest's text).
_REPORT_FIELDS = ("updated_at", "version", "booth_running", "paused", "autostart", "queue", "eta_seconds",
                  "speed_bps", "disk_free_gb", "target_names")


def _dicts(value: Any) -> List[dict]:
    return [v for v in (value or []) if isinstance(v, dict)]


@tool("get_booth_queues",
      "Die Warteschlangen einer Box wie auf der Sync-Seite im Admin: wartende Drucke (prints, Nummern für "
      "booth_discard_prints), fehlgeschlagene Uploads mit Ziel und Grund (failed_uploads, Nummern für "
      "discard_failed_uploads), Gründe zusammengefasst, Zähler je Ziel (upload_targets: gesendet/wartend/"
      "fehlgeschlagen) und der Bericht des Sync-Programms. Neu versuchen: send_booth_job retry_failed.",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="read", untrusted=True)
def _get_queues(booth_id: int) -> dict:
    device = _booth(booth_id)
    status = db.get_device_status(device["id"]) or {}
    report = ecosystem.sync_report(device["id"])
    return {"booth": {"id": device["id"], "name": device["name"]},
            "status_at": device.get("last_heartbeat_at"),
            "queue_pending": status.get("queue_pending"), "queue_failed": status.get("queue_failed"),
            "prints": _dicts(status.get("print_jobs")),
            "prints_pending": status.get("prints_pending"), "prints_held": status.get("prints_held"),
            "failed_uploads": _dicts(status.get("failed_uploads")),
            "failed_reasons": _dicts(status.get("failed_reasons")),
            "upload_targets": status.get("upload_targets") or {},
            "sync_program": {k: report.get(k) for k in _REPORT_FIELDS if k in report} or None}


def _failed_ids(ids: Any) -> Optional[List[int]]:
    if ids is None:
        return None
    if not isinstance(ids, list) or not ids or len(ids) > 100:
        raise ValueError("ids: eine Liste mit 1 bis 100 Nummern aus get_booth_queues.failed_uploads")
    try:
        return [int(i) for i in ids]
    except (TypeError, ValueError):
        raise ValueError("ids: nur Nummern aus get_booth_queues.failed_uploads") from None


def _choice(ids: Any, all: Any) -> Optional[List[int]]:
    chosen = _failed_ids(ids)
    if chosen is None and all is not True:
        raise ValueError("Welche? ids (Nummern aus get_booth_queues) oder all=true für alle")
    if chosen is not None and all is True:
        raise ValueError("Entweder ids oder all=true, nicht beides")
    return chosen


def _discard_failed_preview(booth_id: int, ids: Any = None, all: Any = None) -> dict:
    device = _booth(booth_id)
    chosen = _choice(ids, all)
    failed = _dicts((db.get_device_status(device["id"]) or {}).get("failed_uploads"))
    if chosen is not None:
        failed = [f for f in failed if f.get("id") in chosen]
    return {"booth": device["name"], "failed_uploads": failed,
            "effect": ("Diese fehlgeschlagenen Uploads" if chosen is not None else
                       "Alle fehlgeschlagenen Uploads")
                      + " werden an der Box verworfen und nicht mehr versucht. Die Bilder bleiben auf der Box; "
                        "was die Galerie schon hat, bleibt. Die Box holt den Auftrag beim nächsten Kontakt ab."}


@tool("discard_failed_uploads",
      "Fehlgeschlagene Uploads an der Box verwerfen: ids (aus get_booth_queues.failed_uploads) oder all=true. "
      "Neu versuchen statt verwerfen: send_booth_job mit retry_failed.",
      {"type": "object", "required": ["booth_id"],
       "properties": {"booth_id": {"type": "integer"},
                      "ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 100},
                      "all": {"type": "boolean", "description": "true = alle fehlgeschlagenen"}}},
      scope="control", destructive=True, confirm=_discard_failed_preview)
def _discard_failed(booth_id: int, ids: Any = None, all: Any = None) -> dict:
    device = _booth(booth_id)
    chosen = _choice(ids, all)
    # The booth's own rule (sync agent): "all" only with confirm=all, never by default.
    payload = {"ids": chosen} if chosen is not None else {"confirm": "all"}
    job_id = ecosystem.add_job(device["id"], "discard_failed_uploads", payload)
    return {"job_id": job_id, "booth_id": device["id"], "payload": payload,
            "hint": "Die Box holt den Auftrag beim nächsten Kontakt ab; das Ergebnis steht in get_job bzw. "
                    "booth_diagnostics.jobs."}


# ── Silence alarm, read-only ─────────────────────────────────────────

@tool("get_silence_alarm",
      "Die Warnung, wenn eine Box nicht mehr antwortet, nur lesen: an/aus, Minuten, nur während eines Events, "
      "Ruhezeit, Zustand. Den Empfänger zeigt sie nur mit dem Recht secure (sonst •••• oder leer = Konto-Adresse). "
      "Ändern: set_silence_alarm (Recht secure).",
      {"type": "object", "required": ["booth_id"], "properties": {"booth_id": {"type": "integer"}}},
      scope="read")
def _get_silence_alarm(booth_id: int) -> dict:
    from app import silence_alarm
    device = _booth(booth_id)
    current = silence_alarm.settings(device["id"])
    out = {k: current.get(k) for k in ("device_id", "enabled", "minutes", "recipient", "only_event",
                                       "quiet_from", "quiet_to", "state")}
    if not allowed("secure", _granted()) and out.get("recipient"):
        out["recipient"] = MASK
    return out
