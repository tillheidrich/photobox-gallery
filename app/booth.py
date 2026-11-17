"""Booth-Zustand: Heartbeat auswerten und zu einer Ampel verdichten.

Der Betreiber steht auf einem anderen Event und will nicht Zahlen lesen,
sondern eine Farbe sehen. Grün heißt „läuft", Gelb heißt „schau mal drauf",
Rot heißt „da muss jemand ran".

Die Schwellen stehen bewusst hier an einer Stelle und nicht verstreut im
Template, damit sie später konfigurierbar gemacht werden können.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Schwellen
STALE_WARN_MIN = 3      # ab hier gelb
STALE_CRIT_MIN = 10     # ab hier rot — nur während eines laufenden Events, sonst grau
DISK_WARN_GB = 10.0
DISK_CRIT_GB = 2.0
QUEUE_WARN = 20

GREEN, YELLOW, RED, UNKNOWN = "green", "yellow", "red", "unknown"

# Felder, die wir aus dem Heartbeat übernehmen. Alles andere wird verworfen —
# ein Booth soll uns nicht beliebige Daten in die Datenbank schreiben können.
ALLOWED_FIELDS = {
    "agent_version": str,
    "booth_running": bool,
    "booth_version": str,
    "disk_free_gb": float,
    "disk_total_gb": float,
    "queue_pending": int,
    "queue_failed": int,
    "last_photo_at": str,
    "printer_name": str,
    "printer_status": str,
    "prints_today": int,
    "prints_pending": int,
    "prints_held": int,
    "printer_hint": str,
    "printers_total": int,
    "printers_ready": int,
    "sheets_left": int,
    "sheets": list,
    "update": dict,
    "cpu_percent": float,
    "ram_percent": float,
    "uptime_min": int,
    "battery_percent": int,
    "network": str,
    "camera_state": str,
    "camera_backend": str,
    # Camera battery: "57" (percent) or "ac" (mains adapter); absent = unknown.
    "camera_battery": str,
    "profile_version": int,
    "sessions_total": int,
    # The booth's counters of the evening (boothcore/abendzahlen.py): numbers
    # only, cleaned by app.stats.clean_booth_stats.
    "booth_stats": "booth_stats",
    # The booth's self-check (only what is not ok) and its open operator
    # messages. Both have their own cleaner below: free text from a device
    # gets capped and shaped, never stored as it came.
    "doctor": "doctor",
    "notices": "notices",
    # The two queues entry by entry, so the booth page can show and act on
    # single prints and single failed uploads. Cleaned like the doctor.
    "print_jobs": "print_jobs",
    "failed_uploads": "failed_uploads",
    # Why uploads gave up, counted, in plain words ("kein Event zugewiesen").
    "failed_reasons": "failed_reasons",
    # The second upload target as the booth reports it (app/second_target.py):
    # type, host, user, path, "password stored", last check, the public key
    # for sealing. Its own cleaner, which has no place for a password.
    "second_target": "second_target",
    # Sent / waiting / failed per upload target.
    "upload_targets": "upload_targets",
}

PRINT_JOB_STATES = ("queued", "printing", "held")
MAX_QUEUE_ENTRIES = 20

DOCTOR_STATES = ("warn", "fail")
NOTICE_LEVELS = ("warnung", "kritisch")
MAX_FINDINGS = 20
MAX_NOTICES = 10


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_doctor(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    findings = []
    for f in (raw.get("findings") or [])[:100]:
        if isinstance(f, dict) and f.get("state") in DOCTOR_STATES:
            findings.append({"name": _text(f.get("name"), 80), "state": f["state"],
                             "message": _text(f.get("message"), 300)})
        if len(findings) >= MAX_FINDINGS:
            break
    worst = raw.get("worst") if raw.get("worst") in ("ok", "warn", "fail") else (
        "fail" if any(f["state"] == "fail" for f in findings) else "warn" if findings else "ok")
    try:
        at = float(raw.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    return {"worst": worst, "at": at, "findings": findings}


def _clean_notices(raw: Any) -> list:
    out = []
    for n in (raw if isinstance(raw, list) else [])[:100]:
        if isinstance(n, dict) and n.get("stufe") in NOTICE_LEVELS and n.get("titel"):
            try:
                at = float(n.get("at") or 0)
            except (TypeError, ValueError):
                at = 0.0
            out.append({"stufe": n["stufe"], "titel": _text(n["titel"], 160),
                        "text": _text(n.get("text"), 300), "quelle": _text(n.get("quelle"), 20), "at": at})
        if len(out) >= MAX_NOTICES:
            break
    return out


def _number(value: Any, cast=int, default=0):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _run_id(value: Any) -> str:
    text = str(value or "")[:64]
    return text if text.replace(".", "").replace("-", "").isalnum() else ""


def _clean_print_jobs(raw: Any) -> list:
    out = []
    for j in (raw if isinstance(raw, list) else [])[:100]:
        if not isinstance(j, dict) or j.get("state") not in PRINT_JOB_STATES:
            continue
        job_id = _number(j.get("id"))
        if job_id <= 0:
            continue
        slot = _number(j.get("slot"), default=None) if j.get("slot") is not None else None
        out.append({"id": job_id, "run": _run_id(j.get("run")), "created": _number(j.get("created"), float, 0.0),
                    "copies": max(1, min(99, _number(j.get("copies"), default=1))), "state": j["state"],
                    "reason": _text(j.get("reason"), 200), "slot": slot, "deferred": bool(j.get("deferred"))})
        if len(out) >= MAX_QUEUE_ENTRIES:
            break
    return out


def _clean_failed_uploads(raw: Any) -> list:
    out = []
    for f in (raw if isinstance(raw, list) else [])[:100]:
        if not isinstance(f, dict):
            continue
        item_id = _number(f.get("id"))
        if item_id <= 0:
            continue
        # Booths from 17.11.2026 send "reason" (plain words) and neither a
        # file name nor the raw error; older ones send "file" and "error".
        out.append({"id": item_id, "file": _text(f.get("file"), 120), "target": _text(f.get("target"), 40),
                    "kind": _text(f.get("kind"), 20), "variant": _text(f.get("variant"), 20),
                    "run": _run_id(f.get("run")), "has_event": bool(f.get("has_event")),
                    "reason": _text(f.get("reason"), 80), "attempts": max(0, _number(f.get("attempts"))),
                    "error": _text(f.get("error"), 200), "created": _number(f.get("created"), float, 0.0)})
        if len(out) >= MAX_QUEUE_ENTRIES:
            break
    return out


def _clean_failed_reasons(raw: Any) -> list:
    out = []
    for r in (raw if isinstance(raw, list) else [])[:10]:
        if not isinstance(r, dict):
            continue
        reason, count = _text(r.get("reason"), 80), max(0, _number(r.get("count")))
        if reason and count:
            out.append({"reason": reason, "count": count})
        if len(out) >= 5:
            break
    return out


def sanitize_payload(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Nur bekannte Felder in erwarteten Typen übernehmen."""
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key, caster in ALLOWED_FIELDS.items():
        if key not in raw or raw[key] is None:
            continue
        val = raw[key]
        try:
            if caster == "doctor":
                out[key] = _clean_doctor(val)
            elif caster == "notices":
                out[key] = _clean_notices(val)
            elif caster == "print_jobs":
                out[key] = _clean_print_jobs(val)
            elif caster == "failed_uploads":
                out[key] = _clean_failed_uploads(val)
            elif caster == "failed_reasons":
                out[key] = _clean_failed_reasons(val)
            elif caster == "second_target":
                from app import second_target
                out[key] = second_target.clean_report(val)
            elif caster == "upload_targets":
                from app import second_target
                out[key] = second_target.clean_counts(val)
            elif caster == "booth_stats":
                from app import stats as stats_mod
                cleaned = stats_mod.clean_booth_stats(val)
                if cleaned:
                    out[key] = cleaned
            elif caster is bool:
                out[key] = bool(val) if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes")
            elif caster is str:
                out[key] = str(val)[:120]
            elif caster is int:
                out[key] = int(float(val))
            elif caster is list:
                # Strukturen wie die Blattzahlen je Drucker: übernommen, aber
                # gedeckelt — eine Box soll den Status nicht als Ablage benutzen.
                out[key] = list(val)[:16] if isinstance(val, (list, tuple)) else []
            elif caster is dict:
                out[key] = dict(val) if isinstance(val, dict) else {}
            else:
                out[key] = round(float(val), 2)
        except (TypeError, ValueError):
            continue
    return out


def _minutes_since(ts: Optional[str]) -> Optional[float]:
    """Alter eines UTC-Zeitstempels in Minuten (SQLite liefert 'YYYY-MM-DD HH:MM:SS')."""
    if not ts:
        return None
    text = str(ts).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(text[:26], fmt).replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 60.0)
        except ValueError:
            continue
    return None


def knapp_ab(gesamt: int) -> int:
    """From how many left a SELPHY tray or ribbon counts as low: about one in six, at least 2.

    The booth's rule (``Booth._knapp_ab`` in ``booth/boothcore/app.py``),
    repeated here because the gallery does not import the booth. Tray 18
    warns at 3, ribbon 36 at 5.
    """
    return max(2, round(int(gesamt or 0) * 0.15))


def evaluate(status: Optional[Dict[str, Any]], last_heartbeat_at: Optional[str],
             event_running: bool = True) -> Dict[str, Any]:
    """Ampel plus die Gründe dafür — die Gründe zeigen wir im Dashboard an.

    ``findings`` carries the same reasons with their level, so a page can put
    the red ones first. Silence is red only while an event is running; a box
    that stands in the cupboard between two weddings is grey, not an alarm.
    """
    status = status or {}
    age = _minutes_since(last_heartbeat_at)

    if age is None:
        return {"health": UNKNOWN, "reasons": ["Noch kein Lebenszeichen"],
                "findings": [{"level": UNKNOWN, "source": "heartbeat", "text": "Noch kein Lebenszeichen"}],
                "age_min": None, "is_stale": True}

    if age >= STALE_CRIT_MIN and not event_running:
        text = "Offline — gerade läuft kein Event mit dieser Box"
        return {"health": UNKNOWN, "reasons": [text],
                "findings": [{"level": UNKNOWN, "source": "heartbeat", "text": text}],
                "age_min": age, "is_stale": True}

    reasons: list = []
    findings: list = []
    level = GREEN

    def bump(new_level: str, reason: str, source: str = "status") -> None:
        nonlocal level
        if reason in reasons:                    # the same words from two sources once
            return
        reasons.append(reason)
        findings.append({"level": new_level, "source": source, "text": reason})
        if new_level == RED or level == RED:
            level = RED
        elif new_level == YELLOW:
            level = YELLOW

    if age >= STALE_CRIT_MIN:
        bump(RED, f"Seit {int(age)} min nichts gehört", "heartbeat")
    elif age >= STALE_WARN_MIN:
        bump(YELLOW, f"Seit {int(age)} min nichts gehört", "heartbeat")

    # What the booth found about itself. Its words, not ours: the self-check
    # already says what to do ("Datei fehlt: logo.png"), the gallery only
    # sorts it into the light.
    for f in (status.get("doctor") or {}).get("findings") or []:
        if isinstance(f, dict) and f.get("state") in DOCTOR_STATES:
            bump(RED if f["state"] == "fail" else YELLOW,
                 f"{f.get('name')}: {f.get('message')}".strip(": "), "doctor")
    for n in status.get("notices") or []:
        if isinstance(n, dict) and n.get("stufe") in NOTICE_LEVELS:
            bump(RED if n["stufe"] == "kritisch" else YELLOW, str(n.get("titel") or ""), "notice")

    if "booth_running" in status and not status["booth_running"]:
        bump(RED, "Booth-Software läuft nicht")

    cam = (status.get("camera_state") or "").lower()
    if cam and cam != "ready":
        bump(RED, "Kamera nicht verbunden (aus, Kabel oder falscher Modus?)")

    free = status.get("disk_free_gb")
    if isinstance(free, (int, float)):
        if free < DISK_CRIT_GB:
            bump(RED, f"Nur noch {free:.1f} GB frei")
        elif free < DISK_WARN_GB:
            bump(YELLOW, f"Wenig Speicher: {free:.1f} GB frei")

    failed = status.get("queue_failed") or 0
    if failed:
        bump(RED, f"{failed} Upload(s) fehlgeschlagen")

    pending = status.get("queue_pending") or 0
    if pending > QUEUE_WARN:
        bump(YELLOW, f"{pending} Fotos in der Warteschlange")

    printer = (status.get("printer_status") or "").lower()
    if printer in ("error", "fehler", "jam", "out_of_paper"):
        bump(RED, f"Drucker meldet: {status.get('printer_status')}")
    elif printer in ("offline", "warning", "low_paper"):
        bump(YELLOW, f"Drucker: {status.get('printer_status')}")

    # Wartende Drucke sind der Fall, den man am Abend sehen muss: Sie sind nicht
    # verloren, aber sie kommen auch nicht heraus, bis jemand Papier nachlegt.
    haengt = int(status.get("prints_held") or 0)
    if haengt:
        hinweis = (status.get("printer_hint") or "").strip()
        bump(RED, f"{haengt} Druck(e) warten auf Papier oder Farbband"
                  + (f" — {hinweis}" if hinweis else ""))

    # Zwei Drucker eingerichtet, einer antwortet nicht: läuft weiter, fällt aber auf.
    gesamt, bereit = int(status.get("printers_total") or 0), int(status.get("printers_ready") or 0)
    if gesamt > 1 and bereit < gesamt:
        bump(YELLOW, f"Nur {bereit} von {gesamt} Druckern bereit")

    # Was der Drucker selbst meldet, wiegt schwerer als jede Schätzung.
    for blatt in (status.get("sheets") or []):
        if not isinstance(blatt, dict):
            continue
        wo = f"Drucker {blatt.get('slot', '?')}" + (f" ({blatt['label']})" if blatt.get("label") else "")
        if blatt.get("leer"):
            bump(RED, f"{wo}: Papier oder Farbband leer")
        elif blatt.get("quelle") == "gemeldet" and int(blatt.get("prozent") or 100) <= 10:
            bump(YELLOW, f"{wo}: noch {int(blatt['prozent'])} % Vorrat (gemeldet)")
        elif blatt.get("quelle") == "gezählt" and (isinstance(blatt.get("fach"), dict)
                                                  or isinstance(blatt.get("farbband"), dict)):
            # SELPHY: tray and ribbon counted apart. "gesamt" is the tray
            # size (18), so "rest <= 10" was yellow for half of every tray.
            # Each part gets the booth's own rule and its own name.
            for teil, name, einheit in (("fach", "Papierfach", "Blatt"), ("farbband", "Farbband", "Drucke")):
                zahl = blatt.get(teil)
                if not isinstance(zahl, dict):
                    continue
                rest = int(zahl.get("rest") or 0)
                if 0 < rest <= knapp_ab(int(zahl.get("gesamt") or 0)):
                    bump(YELLOW, f"{wo}: {name} noch etwa {rest} {einheit} (gezählt)")
        elif blatt.get("quelle") == "gezählt" and 0 < int(blatt.get("rest") or 0) <= 10:
            bump(YELLOW, f"{wo}: noch etwa {int(blatt['rest'])} Blätter (gezählt)")

    rest = int(status.get("sheets_left") or 0)
    if rest and rest <= 10 and not (status.get("sheets") or []):
        bump(YELLOW, f"Noch etwa {rest} Blätter in der Kassette")

    if not reasons:
        reasons.append("Alles in Ordnung")

    rank = {RED: 0, YELLOW: 1}
    findings.sort(key=lambda f: rank.get(f["level"], 2))
    return {"health": level, "reasons": reasons, "findings": findings, "age_min": age,
            "is_stale": age >= STALE_WARN_MIN}


# A booth that went quiet this long ago is no longer "at the event" when its
# event has no date: the light turns grey instead of staying red overnight.
UNDATED_EVENT_HOURS = 6


def event_running(device: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """Is this booth at work right now — so that silence is an alarm?

    The gallery has no start and end time for an event, only a date. Running
    means: the booth is linked to an event and that event is dated today, or
    yesterday until 8 in the morning (weddings end after midnight). An event
    without a date counts as running while the booth reported in the last
    ``UNDATED_EVENT_HOURS`` hours.
    """
    device = device or {}
    if not device.get("event_id"):
        return False
    from app import db
    event = db.get_event_by_id(device["event_id"])
    if not event:
        return False
    local = now or datetime.now()
    raw = str(event.get("date") or "").strip()[:10]
    if raw:
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            day = None
        if day:
            today = local.date()
            return day == today or (day == today.fromordinal(today.toordinal() - 1) and local.hour < 8)
    age = _minutes_since(device.get("last_heartbeat_at"))
    return age is not None and age < UNDATED_EVENT_HOURS * 60


_event_running = event_running      # light() has a parameter of the same name


def age_text(minutes: Optional[float]) -> str:
    """How old the last report is, in the words the operator reads at night."""
    if minutes is None:
        return "noch nie gemeldet"
    seconds = int(round(minutes * 60))
    if minutes < STALE_WARN_MIN:
        return f"vor {seconds} s" if seconds < 60 else f"vor {int(minutes)} min"
    if minutes < 120:
        return f"seit {int(minutes)} min nichts gehört"
    if minutes < 48 * 60:
        return f"seit {int(minutes // 60)} h nichts gehört"
    return f"seit {int(minutes // (60 * 24))} Tagen nichts gehört"


def _paper_text(status: Dict[str, Any]) -> str:
    parts = []
    for s in status.get("sheets") or []:
        if not isinstance(s, dict):
            continue
        who = str(s.get("label") or f"Drucker {s.get('slot', '?')}")
        if s.get("leer"):
            parts.append(f"{who}: leer")
        elif s.get("quelle") == "gemeldet" and s.get("prozent") is not None:
            parts.append(f"{who}: {int(s['prozent'])} %")
        elif s.get("quelle") == "gezählt" and s.get("rest") is not None:
            parts.append(f"{who}: noch etwa {int(s['rest'])} Blatt")
        else:
            parts.append(f"{who}: unbekannt")
    if not parts and status.get("sheets_left"):
        parts.append(f"noch etwa {int(status['sheets_left'])} Blatt")
    return " · ".join(parts)


def light(device: Optional[Dict[str, Any]], status: Optional[Dict[str, Any]] = None,
          event_running: Optional[bool] = None) -> Dict[str, Any]:
    """The operator's light for one booth — the one function behind the admin
    page and the MCP tool ``list_booths``, so both always say the same.

    ``status`` defaults to the stored last heartbeat, ``event_running`` to
    :func:`event_running` for the device.
    """
    device = device or {}
    if status is None:
        from app import db
        status = db.get_device_status(device["id"]) if device.get("id") else {}
    status = status or {}
    if event_running is None:
        event_running = _event_running(device)
    verdict = evaluate(status, device.get("last_heartbeat_at"), event_running=event_running)
    # A box that said goodbye on purpose (app/silence_alarm.py) is silent
    # because somebody switched it off: grey with that sentence, not red.
    going_down = ""
    if device.get("id") and verdict.get("is_stale"):
        try:
            from app import silence_alarm
            note = silence_alarm.settings(device["id"])
            going_down = silence_alarm.goodbye_text(note)
            if going_down and not note.get("down_expect_back"):
                verdict = {**verdict, "health": UNKNOWN, "reasons": [going_down],
                           "findings": [{"level": UNKNOWN, "source": "heartbeat", "text": going_down}]}
        except Exception:                   # noqa: BLE001 — the light never fails on the note
            going_down = ""
    doctor = status.get("doctor") or {}
    doctor_text = ""
    if doctor.get("at"):
        # The booth's own clock; the self-check flags it if it drifts (> 2 min).
        mins = max(0.0, (time.time() - float(doctor["at"])) / 60.0)
        doctor_text = "gerade eben" if mins < 1 else f"vor {int(mins)} min" if mins < 120 else \
            f"vor {int(mins // 60)} h"
    return {
        "health": verdict["health"],
        "findings": verdict["findings"],
        "reasons": verdict["reasons"],
        "age_min": verdict["age_min"],
        "age_seconds": None if verdict["age_min"] is None else int(round(verdict["age_min"] * 60)),
        "age_text": age_text(verdict["age_min"]),
        "stale": verdict["is_stale"],
        "event_running": bool(event_running),
        "paper_text": _paper_text(status),
        "print_queue": {"pending": int(status.get("prints_pending") or 0),
                        "held": int(status.get("prints_held") or 0)},
        "upload_queue": {"pending": int(status.get("queue_pending") or 0),
                         "failed": int(status.get("queue_failed") or 0)},
        "doctor_worst": doctor.get("worst") or "",
        "doctor_at": doctor.get("at") or 0,
        "doctor_text": doctor_text,
        "going_down": going_down,
    }


def evaluate_fresh(status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Bewertung für einen gerade eingetroffenen Heartbeat (Alter = 0)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return evaluate(status, now)


def disk_percent_used(status: Optional[Dict[str, Any]]) -> Optional[int]:
    status = status or {}
    total, free = status.get("disk_total_gb"), status.get("disk_free_gb")
    if not isinstance(total, (int, float)) or not isinstance(free, (int, float)) or total <= 0:
        return None
    return max(0, min(100, round((total - free) / total * 100)))


def humanize_age(minutes: Optional[float]) -> str:
    if minutes is None:
        return "—"
    if minutes < 1:
        return "gerade eben"
    if minutes < 60:
        return f"vor {int(minutes)} Min."
    if minutes < 60 * 24:
        return f"vor {int(minutes // 60)} Std."
    return f"vor {int(minutes // (60 * 24))} Tg."
