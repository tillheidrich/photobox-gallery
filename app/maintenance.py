"""Was regelmäßig laufen muss, damit Versprechen keine Versprechen bleiben.

Drei Aufgaben, die bisher zwar programmiert waren, aber niemand aufgerufen hat:

1. **Auslagern** — Originale wandern nach der eingestellten Frist auf das NAS
   (``storage.offload_pending``). Ohne Lauf bleibt alles auf dem VPS liegen.
2. **Ferngelöschtes wirklich löschen** — was auf dem NAS liegt und hier gelöscht
   wurde, steht in einer Warteschlange (``storage.process_remote_deletions``).
   Ohne Lauf war „gelöscht" nur auf dem VPS wahr, nicht auf dem NAS.
3. **Aufbewahrungsfristen durchsetzen** — abgelaufene Galerien leeren und die
   Löschung belegen (``retention.sweep``).

Since 12.11.2026 a fourth task runs first: the backup (``backup.scheduled``,
app/backup.py), so that retention and the offload act on a saved state.

Als geplante Aufgabe einrichten, täglich nachts:

    python -m app.maintenance

Since 11.11.2026 the gallery also starts this run itself, at most once per
``PHOTOBOX_MAINTENANCE_HOURS`` (default 24) across all workers — see
``maybe_start`` below and docs/MAINTENANCE.md. A scheduled task stays the
better option where it exists; both together run once a day, not twice.

Der Lauf ist absichtlich langweilig: Er meldet, was er getan hat, und bricht
nicht ab, wenn eine der drei Aufgaben klemmt — ein nicht erreichbares NAS darf
nicht verhindern, dass Fristen eingehalten werden.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

TASKS = ("backup", "offload", "remote_deletions", "retention")

log = logging.getLogger(__name__)

# Since 11.11.2026 the gallery also starts the run itself (gap L3: production
# had no cron job, so retention was a promise in a form). Settings keys, all
# times naive UTC in ISO format so that string comparison is time order:
LAST_RUN_KEY = "maintenance_last_run"         # end of the last completed run
LAST_REPORT_KEY = "maintenance_last_report"   # its report as JSON
CLAIM_KEY = "maintenance_claimed_at"          # start of the last run, by anyone
STALE_HOURS = 36                              # one missed night plus slack
_CHECK_EVERY_S = 600                          # per process: look at most every 10 min
_next_check = [0.0]
_check_lock = threading.Lock()


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def enabled() -> bool:
    """``PHOTOBOX_MAINTENANCE=off`` leaves the run to an external scheduler only."""
    return os.environ.get("PHOTOBOX_MAINTENANCE", "on").strip().lower() not in ("off", "0", "no")


def interval_hours() -> float:
    try:
        return max(1.0, float(os.environ.get("PHOTOBOX_MAINTENANCE_HOURS", "") or 24))
    except ValueError:
        return 24.0


def claim(now: Optional[dt.datetime] = None) -> bool:
    """Take today's run. True for exactly one caller per interval.

    One UPDATE with the condition in its WHERE clause: SQLite serialises
    writers, so of several Gunicorn workers (or a worker and a cron job)
    only one sees a changed row. No file lock, so it works the same on
    every platform and in every container layout that shares the database.
    """
    from app import db

    now = now or _utcnow()
    cutoff = _iso(now - dt.timedelta(hours=interval_hours()))
    con = db.get_db()
    try:
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, '')", (CLAIM_KEY,))
        cur = con.execute(
            "UPDATE settings SET value=?, updated_at=datetime('now') "
            "WHERE key=? AND (value IS NULL OR value='' OR value<=?)",
            (_iso(now), CLAIM_KEY, cutoff))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def run_and_record(dry_run: bool = False) -> Dict[str, Any]:
    """Run once and remember it: the dashboard shows it, and a run from cron
    keeps the gallery from starting a second one the same day."""
    from app import db

    started = _utcnow()
    if not dry_run:
        db.set_setting(CLAIM_KEY, _iso(started))
    report = run(dry_run=dry_run)
    if not dry_run:
        db.set_setting(LAST_RUN_KEY, _iso(_utcnow()))
        db.set_setting(LAST_REPORT_KEY, json.dumps(report, ensure_ascii=False)[:4000])
    return report


def maybe_start(app, *, background: bool = True, now: Optional[dt.datetime] = None) -> bool:
    """Start the daily run if it is due. Cheap enough to call on every request.

    In the background by default: a sweep of a large event or an NAS
    offload must not hold up the guest whose request happened to be first.
    If the worker dies mid-run, the claim stays and the next try is a day
    later; the dashboard shows the missing run after 36 hours.
    """
    if not enabled():
        return False
    try:
        if not claim(now):
            return False
    except Exception:                       # noqa: BLE001 — maintenance never breaks a request
        log.exception("maintenance: claim failed")
        return False

    def _job():
        try:
            with app.app_context():
                report = run_and_record()
            log.info("maintenance: %s", as_text(report).replace("\n", " | "))
        except Exception:                   # noqa: BLE001
            log.exception("maintenance run failed")

    if background:
        threading.Thread(target=_job, name="daily-maintenance", daemon=True).start()
    else:
        _job()
    return True


def on_request(app) -> None:
    """Before-request hook: look at most every ten minutes per process.

    Only under a real server (Gunicorn, Werkzeug: both set SERVER_SOFTWARE).
    Command-line tools use the test client to create the schema; a daemon
    thread started there would die with the tool and leave a claim behind.
    """
    from flask import request

    if app.testing or not request.environ.get("SERVER_SOFTWARE"):
        return
    mono = time.monotonic()
    with _check_lock:
        if mono < _next_check[0]:
            return
        _next_check[0] = mono + _CHECK_EVERY_S
    maybe_start(app)


def status(now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """For the dashboard: when did it last run, and is that too long ago?"""
    from app import db

    now = now or _utcnow()
    raw = (db.get_setting(LAST_RUN_KEY) or "").strip()
    try:
        last = dt.datetime.fromisoformat(raw) if raw else None
    except ValueError:
        last = None
    age = (now - last).total_seconds() / 3600 if last else None
    try:
        report = json.loads(db.get_setting(LAST_REPORT_KEY) or "{}")
    except ValueError:
        report = {}
    errors = [k for k in report if k.endswith("_error")]
    return {"last_run": raw or None, "age_hours": round(age, 1) if age is not None else None,
            "stale": age is None or age > STALE_HOURS, "errors": errors,
            "enabled": enabled(), "report": report}


# ── Backup on demand (dashboard button) ─────────────────────────────────

BACKUP_NOW_KEY = "backup_now"                 # JSON {state, at, name?, error?}
_BACKUP_NOW_STALE_MIN = 30                    # a "running" mark older than this is a dead worker


def backup_now_status() -> Dict[str, Any]:
    """What the button last did: ``state`` is running, done, failed, or "" (never pressed)."""
    from app import db
    try:
        return json.loads(db.get_setting(BACKUP_NOW_KEY) or "{}")
    except ValueError:
        return {}


def start_backup_now(app, *, background: bool = True) -> str:
    """Write one backup now, whatever ``PHOTOBOX_MAINTENANCE`` says.

    Same function as the nightly run (``_backup``: create, prune, copy off
    the server, record the report), so the card, the pruning and the
    off-site copy behave as at night. One at a time: a second press while
    one runs answers "running". Returns ``started``, ``running`` or ``off``
    (``PHOTOBOX_BACKUP=off`` is respected).
    """
    from app import backup, db

    if not backup.enabled():
        return "off"
    now = _utcnow()
    old = backup_now_status()
    try:
        began = dt.datetime.fromisoformat(old.get("at", "")) if old.get("state") == "running" else None
    except ValueError:
        began = None
    if began and (now - began).total_seconds() < _BACKUP_NOW_STALE_MIN * 60:
        return "running"
    db.set_setting(BACKUP_NOW_KEY, json.dumps({"state": "running", "at": _iso(now)}))

    def _job():
        try:
            with app.app_context():
                out = _backup(False)
                error = out.get("backup_error")
                made = out.get("backup") if isinstance(out.get("backup"), dict) else {}
                db.set_setting(BACKUP_NOW_KEY, json.dumps({
                    "state": "failed" if error else "done", "at": _iso(_utcnow()),
                    "name": made.get("name", ""), "error": error or ""}))
        except Exception as e:              # noqa: BLE001
            log.exception("backup on demand failed")
            try:
                with app.app_context():
                    db.set_setting(BACKUP_NOW_KEY, json.dumps(
                        {"state": "failed", "at": _iso(_utcnow()), "error": str(e)[:200]}))
            except Exception:               # noqa: BLE001
                pass

    if background:
        threading.Thread(target=_job, name="backup-now", daemon=True).start()
    else:
        _job()
    return "started"


def run(dry_run: bool = False) -> Dict[str, Any]:
    from app import retention, storage

    report: Dict[str, Any] = {"dry_run": dry_run}
    # The backup comes first: retention may empty galleries and the offload
    # may delete originals locally, and both should happen with last
    # night's state safely in an archive (the offload checks for that).
    report.update(_backup(dry_run))
    try:
        report["offload"] = 0 if dry_run else storage.offload_pending()
        waiting = storage.last_offload_stats.get("waiting_for_backup") if not dry_run else 0
        failed = storage.last_offload_stats.get("verify_failed") if not dry_run else 0
        if waiting:
            report["offload_waiting_for_backup"] = waiting
        if failed:
            report["offload_verify_failed"] = failed
    except Exception as e:
        report["offload_error"] = str(e)[:200]
    try:
        report["remote_deletions"] = 0 if dry_run else storage.process_remote_deletions()
    except Exception as e:
        report["remote_deletions_error"] = str(e)[:200]
    try:
        # Pairing requests from the phone that nobody confirmed (or that are done).
        from app import phone_pairing
        report["pairing_requests_removed"] = 0 if dry_run else phone_pairing.cleanup()
    except Exception as e:
        report["pairing_requests_error"] = str(e)[:200]
    try:
        # Sign-ups for new-photo mails nobody confirmed within the link's 7 days.
        from app import db
        report["subscriptions_removed"] = 0 if dry_run else db.purge_unconfirmed_subscriptions()
    except Exception as e:
        report["subscriptions_error"] = str(e)[:200]
    try:
        result = retention.sweep(dry_run=dry_run)
        report["retention"] = {"events": result["events"], "files": result["files"],
                               "bytes": result["bytes"]}
    except Exception as e:
        report["retention_error"] = str(e)[:200]
    try:
        # Trash (app/trash.py): what is past its grace period is deleted now.
        from app import trash
        report["trash"] = trash.purge_due(dry_run=dry_run)
    except Exception as e:                  # noqa: BLE001
        report["trash_error"] = str(e)[:200]
    try:
        # Image statistics for "Aussortieren" (app/sortout.py), a small batch
        # per run so a big archive is caught up over a few nights.
        from app import sortout
        report["sortout"] = {"done": 0} if dry_run else sortout.backfill(limit=300)
    except Exception as e:                  # noqa: BLE001
        report["sortout_error"] = str(e)[:200]
    return report


def _backup(dry_run: bool) -> Dict[str, Any]:
    """The nightly backup (app/backup.py). Its own errors never stop the run."""
    from app import backup, db

    if dry_run:
        return {"backup": "skipped (dry run)"}
    if not backup.enabled():
        return {"backup": "off"}
    out: Dict[str, Any] = {}
    try:
        made = backup.scheduled()
        out["backup"] = {k: made[k] for k in ("name", "size", "media_included", "removed", "offsite")
                         if k in made}
        if made.get("offsite_error"):
            out["backup_offsite_error"] = made["offsite_error"]
        if made.get("integrity") not in (None, "ok"):
            out["backup_error"] = f"integrity_check: {made['integrity']}"[:200]
    except Exception as e:                  # noqa: BLE001 — disk full, permissions: reported, run goes on
        out["backup_error"] = str(e)[:200]
    try:
        db.set_setting(backup.LAST_REPORT_KEY, json.dumps({
            "at": _iso(_utcnow()), "error": out.get("backup_error"),
            "offsite": (out.get("backup") or {}).get("offsite") if isinstance(out.get("backup"), dict) else None,
            "offsite_error": out.get("backup_offsite_error")}))
    except Exception:                       # noqa: BLE001
        log.exception("maintenance: could not record the backup report")
    return out


def as_text(report: Dict[str, Any]) -> str:
    lines = []
    if report.get("dry_run"):
        lines.append("Probelauf — es wurde nichts verändert.")
    b = report.get("backup")
    if isinstance(b, dict):
        lines.append(f"Sicherung: {b.get('name')} · {round((b.get('size') or 0) / 1e6, 1)} MB · "
                     + ("mit Medien" if b.get("media_included") else "nur Datenbank")
                     + f" · außer Haus: {b.get('offsite', 'off')}"
                     + (f" (Fehler: {report['backup_offsite_error']})" if report.get("backup_offsite_error") else ""))
    elif report.get("backup_error"):
        lines.append(f"Sicherung: Fehler: {report['backup_error']}")
    elif b:
        lines.append(f"Sicherung: {b}")
    if report.get("offload_waiting_for_backup"):
        lines.append(f"Auslagerung wartet auf eine Sicherung: {report['offload_waiting_for_backup']} Originale")
    if report.get("offload_verify_failed"):
        lines.append(f"Auslagerung: Prüfung nach dem Hochladen fehlgeschlagen bei "
                     f"{report['offload_verify_failed']} Originalen (lokal behalten)")
    lines.append(f"Ausgelagert: {report.get('offload', 0)} Originale"
                 + (f" (Fehler: {report['offload_error']})" if report.get("offload_error") else ""))
    lines.append(f"Auf dem Auslagerungsziel gelöscht: {report.get('remote_deletions', 0)}"
                 + (f" (Fehler: {report['remote_deletions_error']})" if report.get("remote_deletions_error") else ""))
    ret = report.get("retention") or {}
    events = ", ".join(ret.get("events") or []) or "nichts"
    lines.append(f"Aufbewahrung: {events} · {ret.get('files', 0)} Dateien · "
                 f"{round((ret.get('bytes') or 0) / 1e6, 1)} MB"
                 + (f" (Fehler: {report['retention_error']})" if report.get("retention_error") else ""))
    tr = report.get("trash") or {}
    if tr.get("due") or report.get("trash_error"):
        lines.append(f"Papierkorb geleert: {tr.get('purged', 0)} Bilder"
                     + (f" (Fehler: {report['trash_error']})" if report.get("trash_error") else ""))
    return "\n".join(lines)


def main() -> int:                        # pragma: no cover - Kommandozeile
    import argparse

    from app import create_app

    parser = argparse.ArgumentParser(description="Wartung: auslagern, fernlöschen, Fristen durchsetzen")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    app = create_app()
    with app.test_request_context():
        app.test_client().get("/healthz")
        report = run_and_record(dry_run=args.dry_run)
    print(as_text(report))
    return 0


if __name__ == "__main__":                # pragma: no cover
    raise SystemExit(main())
