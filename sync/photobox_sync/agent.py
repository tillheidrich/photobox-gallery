"""Agent: Heartbeat, Profil, Assets, Aufträge, Löschungen und Upload-Schleife.

Der Server ruft nie die Box an — der Agent fragt nach. Eine Anfrage erledigt alles:

    POST /api/v2/booth/sync  ->  Status, Profilversion, Auftragsergebnisse, Löschungen
                             <-  Profil (falls neuer), Assets, Aufträge, bestätigte Löschungen

Der Agent weiß nichts über Kameras oder dslrBooth. Wer ihn benutzt, gibt ihm
Funktionen mit (Status liefern, Profil übernehmen, Aufträge ausführen).
Aufträge kommen aus einer festen Liste. Kein Server-Shell-Befehl, nie.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import reasons
from .outbox import Outbox
from .targets import (GalleryTarget, HostKeys, PermanentError, TemporaryError, build_extra_targets,
                      storage_targets)

log = logging.getLogger("photobox.sync")


def job_ids(payload: Any) -> Optional[List[int]]:
    """``ids`` of a job payload as a list of ints; None when the job names none (= all).

    Anything that is not a whole number is refused, not skipped: a job that
    meant three files must not quietly act on two.
    """
    raw = (payload or {}).get("ids") if isinstance(payload, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) > 500:
        raise ValueError("ids muss eine Liste sein (höchstens 500)")
    out = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().isdigit():
            raise ValueError(f"ungültige Kennung: {value!r}")
        out.append(int(value))
    return out


# Diagnose: wie viele Meldungen pro Sync mitgehen und wie viele offline gepuffert werden
MAX_LOGS_PER_SYNC = 50
MAX_LOGS_BACKLOG = 500

# One list, not two: this must equal the keys of JOB_TYPES in app/ecosystem.py
# (both test suites compare them). A job the gallery cannot issue is dead code here.
ALLOWED_JOBS = {"sync_now", "test_capture", "test_print", "retry_failed",
                "reset_print_counter", "release_prints", "paper_changed", "ribbon_changed",
                "fetch_update", "install_update",
                "restart_app", "lock", "unlock", "retry_mail", "doctor",
                # Fernwartung des Sync-Programms: prüfen, neu einlesen, einstellen, Ordner suchen.
                # Weiterhin kein Befehl von außen — nur diese feste Liste.
                "check_targets", "rescan", "set_sync_config", "browse_remote",
                # Kamera, Drucker, Ablage der Box — die Box prüft gegen ihre eigene Liste.
                "set_local_config",
                # Live remote control (MCP right "control"): the booth checks the
                # action against its own list and its local switch.
                "remote_control",
                # Queues from the gallery's booth page: one print or all of
                # them ("all" only with confirm), failed uploads given up.
                "discard_prints", "discard_failed_uploads",
                # The booth's second upload target from the gallery (17.11.2026):
                # refused unless allowed at the booth; the password arrives
                # sealed to the booth's own key (boothcore/second_target.py).
                "set_second_target", "check_second_target", "backfill_second_target",
                # A changed SFTP host key, accepted on purpose (fingerprint in the payload).
                "accept_second_target_host_key"}

# Auftragsergebnisse sind meist ein Satz. Eine Ordnerliste ist es nicht, deshalb
# so viel Platz, dass eine Auswahl vollständig zurückkommt.
MAX_JOB_RESULT = 4000
#: A remote-control answer may carry a small picture (live image, screenshot).
MAX_RESULT_BY_TYPE = {"remote_control": 400_000}


#: How many executed jobs the agent remembers. The gallery re-delivers a job
#: after a minute without a result and gives up after ten, so this covers far
#: more than any window in which the same job can come back.
JOB_MEMORY = 200

INTERRUPTED = "interrupted: the booth stopped while running this job; not repeated"

#: Failed gallery uploads go back into the queue on their own, at most this
#: often, and only while the gallery answers. The daily cap per file is in
#: outbox.MAX_ATTEMPTS_PER_DAY. Local config ``retry_failed_auto: false``
#: switches it off.
AUTO_RETRY_EVERY = 3600


class JobLedger:
    """Executed jobs and their results, on disk.

    The gallery delivers a job again when no result came back in time — the
    answer carrying it may have been lost, or the booth restarted before it
    could report (results used to live only in memory). Re-delivery is only
    safe because of this file: a known id is answered from here and nothing
    runs twice, ``install_update`` and ``test_print`` included.

    An entry is written *before* the handler runs. If the process dies inside
    the handler, the id is still known afterwards and is reported as
    interrupted instead of being run again.

    Ids are numbers of one gallery, so entries are keyed by its address too.
    """

    def __init__(self, path: Optional[Path]):
        self.path = path
        self._lock = threading.Lock()
        self.entries: List[Dict[str, Any]] = []
        if path is not None:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
            except (OSError, ValueError, AttributeError):
                self.entries = []

    def _find(self, server: str, job_id: Any) -> Optional[Dict[str, Any]]:
        for e in self.entries:
            if e.get("server") == server and e.get("id") == job_id:
                return e
        return None

    def known(self, server: str, job_id: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            e = self._find(server, job_id)
            return dict(e) if e else None

    def started(self, server: str, job_id: Any, job_type: str) -> None:
        with self._lock:
            self.entries.append({"server": server, "id": job_id, "type": job_type,
                                 "state": "started", "ok": False, "result": INTERRUPTED,
                                 "sent": False, "at": time.time()})
            self._save()

    def finished(self, server: str, job_id: Any, ok: bool, result: str) -> None:
        with self._lock:
            e = self._find(server, job_id)
            if e is None:
                e = {"server": server, "id": job_id, "at": time.time()}
                self.entries.append(e)
            e.update({"state": "done", "ok": bool(ok), "result": result, "sent": False})
            self._save()

    def unsent(self, server: str) -> List[Dict[str, Any]]:
        """Results that have not reached this gallery yet — after a restart, too."""
        with self._lock:
            return [{"id": e["id"], "ok": bool(e.get("ok")), "result": e.get("result", "")}
                    for e in self.entries if e.get("server") == server and not e.get("sent")]

    def mark_sent(self, job_ids: List[Any], server: Optional[str] = None) -> None:
        if not job_ids:
            return
        ids = set(job_ids)
        with self._lock:
            for e in self.entries:
                if e.get("id") in ids and (server is None or e.get("server") == server):
                    e["sent"] = True
            self._save()

    def _save(self) -> None:
        # Bounded: unsent results are never dropped, the oldest sent ones are.
        if len(self.entries) > JOB_MEMORY:
            unsent = [e for e in self.entries if not e.get("sent")]
            sent = [e for e in self.entries if e.get("sent")]
            keep = max(0, JOB_MEMORY - len(unsent))
            self.entries = sorted(unsent + (sent[-keep:] if keep else []), key=lambda e: e.get("at", 0))
        if self.path is None:
            return
        try:
            tmp = Path(self.path).with_suffix(".tmp")
            tmp.write_text(json.dumps({"entries": self.entries}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:                       # a full disk must not stop the job itself
            log.warning("Job memory not written: %s", e)


class Agent:
    def __init__(self, *, outbox: Outbox, local_config: Callable[[], dict], assets_dir: Optional[Path] = None,
                 status: Callable[[], Dict[str, Any]] = lambda: {},
                 profile_version: Callable[[], int] = lambda: 0,
                 apply_profile: Callable[[dict], bool] = lambda p: False,
                 pending_deletions: Callable[[], List[str]] = lambda: [],
                 ack_deletions: Callable[[List[str]], None] = lambda uids: None,
                 on_uploaded: Callable[[dict], None] = lambda item: None,
                 logs: Callable[[], List[dict]] = lambda: [],
                 report: Callable[[], Dict[str, Any]] = lambda: {},
                 event_claim: Callable[[], Dict[str, Any]] = lambda: {},
                 event_claimed: Callable[[Dict[str, Any]], None] = lambda ev: None,
                 templates: Callable[[], List[dict]] = lambda: [],
                 templates_acked: Callable[[List[str]], None] = lambda namen: None,
                 templates_pending: Callable[[], List[str]] = lambda: [],
                 session_gone: Callable[[str], None] = lambda session_uid: None,
                 profile_seed: Callable[[], Dict[str, Any]] = lambda: {},
                 profile_seed_answered: Callable[[str], None] = lambda answer: None,
                 job_memory: Optional[Path] = None, host_keys: Optional[HostKeys] = None):
        self.outbox = outbox
        #: The SFTP servers' host keys (trust on first use, then pinned; targets.HostKeys).
        self.host_keys = host_keys or HostKeys(None)
        # The gallery refused a run because it was deleted there. The agent
        # drops the run from its queues; this lets the booth drop its copy.
        self.session_gone = session_gone
        self._gone: set = set()
        #: Runs deleted in the gallery that were removed here; confirmed in the
        #: next sync call so the gallery stops listing them.
        self._deleted_seen: List[str] = []
        self.local_config = local_config
        # Vorlagen, die an der Box gebaut wurden, reisen im Heartbeat mit — ein
        # eigener Upload-Weg wäre ein zweiter, der ausfallen kann.
        self.templates = templates
        self.templates_acked = templates_acked
        #: Template files the booth changed and has not got acknowledged yet:
        #: _ensure_asset leaves them alone (see there).
        self.templates_pending = templates_pending
        self._kept_noted: set = set()
        #: Wird gesetzt, wenn die Box Updates annehmen kann (siehe boothcore/updater.py).
        self.update_offer: Optional[Callable[[Dict[str, Any]], None]] = None
        self.assets_dir = assets_dir
        self.status = status
        self.profile_version = profile_version
        self.apply_profile = apply_profile
        self.pending_deletions = pending_deletions
        self.ack_deletions = ack_deletions
        self.on_uploaded = on_uploaded
        self.logs = logs
        self.report = report            # alles, was die Fernwartung sehen soll
        # Ein Event, das ohne Netz an der Box angelegt wurde. Es reist bei jedem
        # Sync mit, bis die Galerie es quittiert — ein verlorener Zettel hieße:
        # die Bilder des Abends landen in keiner Galerie.
        self.event_claim = event_claim
        self.event_claimed = event_claimed
        # After a new pairing the booth offers its own profile as the starting
        # point; the gallery takes it only while its own is untouched (see
        # app/blueprints/booth_v2.py). Sent until the gallery has answered once.
        self.profile_seed = profile_seed
        self.profile_seed_answered = profile_seed_answered
        self.job_handlers: Dict[str, Callable[[dict], Any]] = {
            "sync_now": lambda p: self.outbox.reset_backoff() or "ok",
            # Without "ids": every failed upload, as before.
            "retry_failed": lambda p: self.outbox.retry_ids(job_ids(p)),
            "discard_failed_uploads": self._job_discard_failed,
        }
        self.online = False
        #: Clock of the hourly automatic retry (tests move it).
        self.clock = time.time
        self._auto_retry_at: Optional[float] = None
        self.last_sync: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_upload: Optional[dict] = None
        self.poll_seconds = 30
        self.fast_seconds = 0.0          # > 0 while the gallery drives this booth (remote control)
        self._fast_thread: Optional[threading.Thread] = None
        # Next to the queue unless told otherwise: whoever has an outbox on disk
        # gets a job memory on disk as well.
        if job_memory is None and getattr(outbox, "path", None):
            job_memory = Path(outbox.path).parent / "jobs.json"
        self.ledger = JobLedger(job_memory)
        self._acks: list = self.ledger.unsent(self._server())
        self._log_backlog: list = []       # nicht zugestellte Meldungen, beim nächsten Kontakt erneut
        self._sync_lock = threading.Lock()
        self._resync = False           # nach Aufträgen gleich wieder melden, nicht 30 s warten
        self._speed_samples: List[tuple] = []
        self._stop = threading.Event()
        #: Ziele, für die gerade ein Upload-Thread läuft. Ohne diese Liste
        #: startet die nächste Runde einen zweiten für dieselben Dateien.
        self._laufend: set = set()
        self._laufend_lock = threading.Lock()

    def _server(self) -> str:
        try:
            return str((self.local_config().get("server") or {}).get("url") or "").rstrip("/")
        except Exception:                          # noqa: BLE001 — no config, no gallery
            return ""

    # ── Ziele ─────────────────────────────────────────────
    def gallery(self) -> Optional[GalleryTarget]:
        s = self.local_config().get("server") or {}
        if not s.get("url") or not s.get("api_key"):
            return None
        return GalleryTarget(s["url"], s["api_key"], s.get("verify_tls", True),
                             limit_kbps=float(s.get("limit_kbps") or 0))

    def targets(self):
        """Alle Ziele: Galerie (wenn eingerichtet), zweite Ablage, Stick, NAS/Online-Galerie.

        Ohne Galerie bleiben die lokalen Ziele — die Box läuft dann standalone.
        """
        cfg = self.local_config()
        out = []
        g = self.gallery()
        if g:
            out.append(g)
        out.extend(build_extra_targets(storage_targets(cfg.get("storage"))
                                       + list(cfg.get("targets") or []), self.host_keys))
        return out

    def target_names(self) -> List[str]:
        return [t.name for t in self.targets()]

    def start(self) -> None:
        threading.Thread(target=self._sync_loop, name="sync", daemon=True).start()
        threading.Thread(target=self._upload_loop, name="upload", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # ── Sync ──────────────────────────────────────────────
    def sync_once(self) -> bool:
        with self._sync_lock:                  # Schleife, „Jetzt synchronisieren“ und Einstellungen nie parallel
            return self._sync_once()

    def _sync_once(self) -> bool:
        g = self.gallery()
        if not g:
            self.online = False
            return False
        deletions = self.pending_deletions()
        seen, self._deleted_seen = self._deleted_seen, []
        acks, self._acks = self._acks, []
        entries = (self._log_backlog + list(self.logs() or []))[-MAX_LOGS_PER_SYNC:]
        self._log_backlog = []
        payload = {"status": {**self.status(), **self.outbox_status()}, "profile_version": self.profile_version(),
                   "job_results": acks, "deleted": deletions, "logs": entries}
        if seen:
            payload["deleted_seen"] = seen
        try:
            anspruch = self.event_claim() or {}
        except Exception:
            anspruch = {}
        if anspruch:
            payload["event_claim"] = anspruch
        try:
            neue_vorlagen = self.templates() or []
        except Exception:
            neue_vorlagen = []
        if neue_vorlagen:
            payload["templates"] = neue_vorlagen
        try:
            saat = self.profile_seed() or {}
        except Exception:
            saat = {}
        if saat:
            payload["profile_seed"] = saat
        try:
            payload["sync"] = self.report()
        except Exception as e:                 # ein kaputter Bericht darf den Heartbeat nicht kosten
            log.warning("Bericht nicht erstellt: %s", e)
        try:
            resp = g.sync(payload)
        except (TemporaryError, PermanentError) as e:
            if self.online:
                log.info("Offline: %s", e)
            self.online, self.last_error = False, str(e)
            self._acks = acks + self._acks       # beim nächsten Kontakt erneut melden
            self._deleted_seen = (seen + self._deleted_seen)[-1000:]
            self._log_backlog = (entries + self._log_backlog)[-MAX_LOGS_BACKLOG:]
            return False
        self.ledger.mark_sent([a.get("id") for a in acks], self._server())
        if deletions:
            self.ack_deletions(resp.get("deleted_ack", deletions))
        self._apply_gallery_deletions(resp.get("deleted_sessions"))
        if not self.online:
            log.info("Online — Warteschlange wird abgearbeitet")
            self.outbox.reset_backoff()
        self.online, self.last_sync, self.last_error = True, time.time(), None
        self._auto_retry_failed()
        if saat:
            # An older gallery does not know the offer and says nothing: that
            # counts as an answer too, the booth does not offer it forever.
            try:
                self.profile_seed_answered(str(resp.get("profile_seed") or "ignored"))
            except Exception as e:
                log.warning("Antwort zum Startprofil nicht verarbeitet: %s", e)
        if anspruch and resp.get("event_claimed"):
            try:
                self.event_claimed(resp["event_claimed"])
                log.info("Event %r ist in der Galerie angekommen", resp["event_claimed"].get("name", ""))
            except Exception as e:
                log.error("Quittung zum Event nicht verarbeitet: %s", e)
        if neue_vorlagen:
            bestaetigt = resp.get("templates_ack")
            # Keine Quittung heißt: Diese Galerie kann das noch nicht — dann
            # bleibt der Zettel liegen, statt die Vorlage zu verlieren.
            if isinstance(bestaetigt, list) and bestaetigt:
                try:
                    self.templates_acked(bestaetigt)
                    log.info("%d Vorlage(n) in der Galerie angekommen", len(bestaetigt))
                except Exception as e:
                    log.error("Quittung zu Vorlagen nicht verarbeitet: %s", e)
        angebot_update = resp.get("update")
        if angebot_update and self.update_offer:
            try:
                self.update_offer(angebot_update)
            except Exception as e:                # ein Angebot darf den Sync nicht kosten
                log.warning("Update-Angebot nicht notiert: %s", e)
        profile = resp.get("profile")
        if profile:
            try:
                if self.apply_profile(profile):
                    log.info("Neues Profil v%s übernommen", profile.get("version"))
            except Exception as e:
                log.error("Profil nicht übernommen: %s", e)
        if self.assets_dir is not None:
            for asset in resp.get("assets", []):
                self._ensure_asset(g, asset)
        jobs = resp.get("jobs", [])
        for job in jobs:
            self._run_job(job)
        self.poll_seconds = int(resp.get("poll_seconds") or 30)
        self._resync = bool(jobs)        # Ergebnis sofort melden, sonst sieht die Ferne 30 s lang nichts
        self._fast(resp.get("fast_seconds"))
        return True

    def _auto_retry_failed(self) -> None:
        """The gallery answers: give its failed files another round, hourly at most.

        Only the gallery: a second target (FTP/SFTP, NAS, stick) fails for
        reasons this heartbeat knows nothing about — a stick that is out, a
        password that is wrong — and the gallery answering says nothing about
        them. They keep the remote job ``retry_failed``.
        """
        try:
            if self.local_config().get("retry_failed_auto") is False:
                return
        except Exception:                          # noqa: BLE001 — no config, default behaviour
            pass
        now = self.clock()
        if self._auto_retry_at is not None and now - self._auto_retry_at < AUTO_RETRY_EVERY:
            return
        self._auto_retry_at = now
        try:
            n = self.outbox.retry_failed_capped(GalleryTarget.name)
        except Exception as e:                     # noqa: BLE001 — the next hour tries again
            log.warning("Automatic retry of failed uploads not done: %s", e)
            return
        if n:
            log.info("Fehlgeschlagene Uploads: %d Datei(en) für die Galerie erneut eingereiht "
                     "(automatisch, höchstens stündlich)", n)

    def goodbye(self, reason: str, expect_back: bool, limit: float = 2.0) -> bool:
        """Tell the gallery we go down on purpose, so its silence alarm stays quiet.

        Best effort and never longer than ``limit`` seconds in total: a
        shutdown must not wait for a slow venue network. No gallery set up,
        no network, a timeout — then nothing is said and the gallery warns
        after its window, as after a power cut.
        """
        g = self.gallery()
        if not g:
            return False
        result: List[bool] = []

        def say() -> None:
            try:
                result.append(g.going_down(reason, expect_back, timeout=max(0.2, limit - 0.3)))
            except Exception:                  # noqa: BLE001 — leaving never fails on this
                result.append(False)

        t = threading.Thread(target=say, name="goodbye", daemon=True)
        t.start()
        t.join(limit)
        ok = bool(result and result[0])
        log.info("Abmeldung bei der Galerie (%s): %s", reason, "angekommen" if ok else "nicht angekommen")
        return ok

    def _apply_gallery_deletions(self, uids: Any) -> None:
        """Runs deleted in the gallery: remove them here, confirm next time.

        An older gallery does not send the list; then nothing happens.
        """
        if not isinstance(uids, list):
            return
        for uid in uids[:500]:
            uid = str(uid or "").strip()
            if not uid or len(uid) > 64 or not uid.replace(".", "").isalnum():
                continue
            self.forget_session(uid)
            if uid not in self._deleted_seen:
                self._deleted_seen.append(uid)

    #: Failed uploads listed in each heartbeat, so the gallery can show them
    #: with their reason and offer retry/discard per file.
    FAILED_IN_HEARTBEAT = 20

    def outbox_status(self) -> Dict[str, Any]:
        c = self.outbox.counts()
        out: Dict[str, Any] = {"queue_pending": c["pending"], "queue_failed": c["failed"]}
        # Per target, sent / waiting / failed: whether the second target got
        # what the gallery got, without opening the booth (17.11.2026).
        try:
            out["upload_targets"] = self.outbox.counts_by_target()
        except Exception:                    # noqa: BLE001 — numbers never cost the heartbeat
            pass
        if c["failed"]:
            # No file names and no raw errors in the heartbeat: a file name can
            # carry a guest's name or text (naming patterns), a raw error a
            # path with it. The operator sees why in plain words (reasons.py);
            # the booth's own admin page keeps file and raw error.
            out["failed_uploads"] = [
                {"id": f["id"], "target": f["target"],
                 "kind": f["kind"], "variant": f["variant"], "run": f["run"],
                 "has_event": bool(f["event_slug"]), "reason": reasons.explain(f["error"]),
                 "attempts": f["attempts"], "created": f["created"]}
                for f in self.outbox.failed_list(self.FAILED_IN_HEARTBEAT)]
            out["failed_reasons"] = reasons.summary(f["error"] for f in self.outbox.failed_list(500))
        return out

    def _job_discard_failed(self, p: dict) -> str:
        """Give up on failed uploads. "All" only with ``confirm: "all"`` — two steps, never one."""
        ids = job_ids(p)
        if ids is None and str((p or {}).get("confirm") or "") != "all":
            raise ValueError("Alle verwerfen nur mit Bestätigung (confirm: all)")
        return f"{self.outbox.discard_failed(ids)} Uploads verworfen"

    def _ensure_asset(self, g: GalleryTarget, asset: Dict[str, Any]) -> None:
        name = asset.get("file")
        sha = asset.get("sha256", "")
        if not name or "/" in name or "\\" in name or ".." in name:
            return
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        target = self.assets_dir / name
        if target.exists() and (not sha or sha256_file(target) == sha):
            self._kept_noted.discard(name)
            return
        # A template edited at the booth and not yet acknowledged by the
        # gallery: the gallery's checksum is that of its older copy (an answer
        # to a heartbeat built before the edit, or a gallery that did not take
        # the upload). Downloading it would replace the edit, and the next
        # push would carry the old copy back. Once acknowledged, the gallery
        # lists the booth's version and this file is followed again.
        try:
            waiting = set(self.templates_pending() or [])
        except Exception:
            waiting = set()
        if target.exists() and name in waiting:
            if name not in self._kept_noted:
                self._kept_noted.add(name)
                log.info("Vorlage %s an der Box geändert, wartet auf den Upload — "
                         "die ältere Fassung der Galerie wird nicht geladen", name)
            return
        try:
            g.download(asset["url"], target)
            if sha and sha256_file(target) != sha:
                target.unlink(missing_ok=True)
                raise TemporaryError("Prüfsumme stimmt nicht")
            log.info("Asset geladen: %s", name)
        except Exception as e:
            log.warning("Asset %s nicht geladen: %s", name, e)

    def _ack(self, jid: Any, ok: bool, result: str) -> None:
        self._acks = [a for a in self._acks if a.get("id") != jid]
        self._acks.append({"id": jid, "ok": bool(ok), "result": result})

    def _run_job(self, job: Dict[str, Any]) -> None:
        jtype, jid = job.get("type"), job.get("id")
        server = self._server()
        known = self.ledger.known(server, jid) if jid is not None else None
        if known:
            # Delivered again (lost answer, restart): answer, do not run twice.
            self._ack(jid, bool(known.get("ok")), str(known.get("result") or ""))
            return
        handler = self.job_handlers.get(jtype)
        if jtype not in ALLOWED_JOBS or not handler:
            ok, result = False, "nicht unterstützt"
        else:
            if jid is not None:
                self.ledger.started(server, jid, str(jtype))
            try:
                limit = MAX_RESULT_BY_TYPE.get(str(jtype), MAX_JOB_RESULT)
                ok, result = True, str(handler(job.get("payload") or {}))[:limit]
            except Exception as e:
                ok, result = False, str(e)[:MAX_JOB_RESULT]
        if jid is not None:
            self.ledger.finished(server, jid, ok, result)
        self._ack(jid, ok, result)

    # ── Live remote control ───────────────────────────────
    #: Never faster than this, whatever the gallery says; never slower than 10 s.
    FAST_MIN, FAST_MAX = 0.5, 10.0

    def _fast(self, seconds: Any) -> None:
        """The gallery opened a remote-control session: poll the small job door until it says 0.

        No long poll: the gallery's two workers must not be held. One thread
        at most; it ends itself when the gallery answers 0 (or the agent stops).
        """
        try:
            self.fast_seconds = max(0.0, float(seconds or 0))
        except (TypeError, ValueError):
            self.fast_seconds = 0.0
        if self.fast_seconds <= 0:
            return
        t = getattr(self, "_fast_thread", None)
        if t is not None and t.is_alive():
            return
        self._fast_thread = threading.Thread(target=self._fast_loop, name="jobs-fast", daemon=True)
        self._fast_thread.start()

    def poll_jobs_once(self) -> bool:
        """One round at the small door: send results, run what came, note the next interval."""
        g = self.gallery()
        if not g:
            self.fast_seconds = 0.0
            return False
        with self._sync_lock:                  # never next to a sync: one list of results, one ledger
            acks, self._acks = self._acks, []
            try:
                resp = g.jobs({"job_results": acks})
            except (TemporaryError, PermanentError) as e:
                self._acks = acks + self._acks
                log.info("Fernsteuerung: Galerie nicht erreicht: %s", e)
                return False
            self.ledger.mark_sent([a.get("id") for a in acks], self._server())
            for job in resp.get("jobs", []) or []:
                self._run_job(job)
            try:
                self.fast_seconds = max(0.0, float(resp.get("fast_seconds") or 0))
            except (TypeError, ValueError):
                self.fast_seconds = 0.0
        return True

    def _fast_loop(self) -> None:
        log.info("Fernsteuerung: schneller Abruf an")
        while not self._stop.is_set() and (self.fast_seconds > 0 or self._acks):
            ok = self.poll_jobs_once()
            if self._acks and ok:
                continue                       # an answer is waiting: deliver it at once
            wait = min(self.FAST_MAX, max(self.FAST_MIN, self.fast_seconds or self.FAST_MIN))
            self._stop.wait(wait if ok else 5)
        log.info("Fernsteuerung: schneller Abruf aus")

    def _sync_loop(self) -> None:
        while not self._stop.is_set():
            ok = self.sync_once()
            if ok and self._resync:
                self._resync = False
                self._stop.wait(1)
                continue
            self._stop.wait(self.poll_seconds if ok else 15)

    # ── Upload ────────────────────────────────────────────
    def paused(self, target_name: str = "") -> bool:
        """Anhalten, ohne etwas zu verlieren.

        Am Eventabend ist die Leitung manchmal für anderes gebraucht. Pausiert
        wird deshalb der Upload, nie die Warteschlange: die Dateien stehen
        weiter an und gehen raus, sobald es weitergeht.
        """
        cfg = self.local_config()
        if cfg.get("paused"):
            return True
        if not target_name:
            return False
        for t in cfg.get("targets") or []:
            if (t.get("name") or t.get("host")) == target_name:
                return bool(t.get("paused"))
        return False

    def _send_one(self, target, item: Dict[str, Any]) -> str:
        """Ein Upload. Rückgabe: ok | gone | stalled | failed (gone: deleted in the gallery)."""
        if item.get("session_uid") in self._gone:
            return "gone"                         # dropped in the meantime; cancel_session took it
        if self.outbox.status_of(item["id"]) != "pending":
            # Taken out of the queue after this batch was read: the run was
            # deleted on the booth (cancel_session). Its files are gone; trying
            # them anyway gave "Datei fehlt" on every other target.
            return "gone"
        try:
            answer = target.upload(item)
        except TemporaryError as e:
            self.outbox.mark_retry(item["id"], str(e))
            return "stalled"                      # Ziel offline/Stick raus: nur dieses Ziel pausiert
        except PermanentError as e:
            self._gescheitert(item, str(e))
            return "failed"
        except Exception as e:
            self._gescheitert(item, f"{type(e).__name__}: {e}")
            return "failed"
        if isinstance(answer, dict) and answer.get("gone"):
            # Deleted in the gallery: delivered as far as the queue is concerned,
            # and nothing else of that run needs to go anywhere any more.
            self.forget_session(str(item.get("session_uid") or item["uid"]))
            return "gone"
        self.outbox.mark_done(item["id"])
        self.last_upload = {"file": Path(item["path"]).name, "target": item["target"], "at": time.time()}
        try:
            self.on_uploaded(item)
        except Exception:
            pass
        return "ok"

    def forget_session(self, session_uid: str) -> None:
        """A run is gone at the gallery: drop it from every queue, tell the booth.

        No deletion slip is written here — the gallery already knows, and a
        slip would only travel back to where the deletion came from.
        """
        if not session_uid:
            return
        self._gone.add(session_uid)
        if len(self._gone) > 500:                  # only needed for the batch in flight
            self._gone = {session_uid}
        try:
            self.outbox.cancel_session(session_uid)
        except Exception as e:                     # noqa: BLE001 — the next round tries again
            log.warning("Queue for %s not cleared: %s", session_uid, e)
        try:
            self.session_gone(session_uid)
        except Exception as e:                     # noqa: BLE001
            log.warning("Local copy of %s not removed: %s", session_uid, e)
        log.info("Run %s was deleted in the gallery — removed here too", session_uid)

    def _gescheitert(self, item: Dict[str, Any], grund: str) -> None:
        """Endgültig gescheitert — und das soll man sehen.

        Nach acht Versuchen steht ein Eintrag auf ``failed`` und wird nie
        wieder angefasst. Bis zum 19.09.2026 stand darüber keine Zeile im
        Protokoll: Eine Datei verschwand lautlos aus dem Betrieb, und das
        einzige Signal war ein Zähler im Herzschlag, den niemand ansieht.
        """
        self.outbox.mark_retry(item["id"], grund, permanent=True)
        if self.outbox.status_of(item["id"]) == "failed":
            log.warning("Aufgegeben: %s an %s — %s",
                        Path(item["path"]).name, item["target"], grund)

    def _drain_target(self, target, items: List[Dict[str, Any]]) -> int:
        """Ein Ziel, seine Dateien der Reihe nach. Der erste Netzfehler beendet die Runde."""
        done = 0
        for item in items:
            if self._stop.is_set():
                break
            started = time.monotonic()
            result = self._send_one(target, item)
            if result == "stalled":
                break
            if result == "ok":
                done += 1
                self._note_speed(int(item.get("bytes") or 0), time.monotonic() - started)
        return done

    def _note_speed(self, size: int, seconds: float) -> None:
        """Gleitendes Mittel der letzten Uploads — daraus wird die Restzeit."""
        if size <= 0 or seconds <= 0:
            return
        self._speed_samples.append((size, seconds))
        del self._speed_samples[:-20]

    def speed_bps(self) -> float:
        total = sum(b for b, _ in self._speed_samples)
        secs = sum(s for _, s in self._speed_samples)
        return total / secs if secs > 0 else 0.0

    def eta_seconds(self) -> Optional[int]:
        pending = self.outbox.pending_by_target()
        if not pending:
            return 0                       # nichts offen — nur dann heißt es „alles übertragen"
        speed = self.speed_bps()
        if speed <= 0:
            return None
        waiting = sum(v["bytes"] for v in pending.values())
        return max(1, int(waiting / speed))

    def _upload_loop(self) -> None:
        while not self._stop.is_set():
            items = self.outbox.due(limit=60)
            if not items or self.paused():
                self.outbox.wake.clear()
                self.outbox.wake.wait(timeout=10)
                continue
            targets = {t.name: t for t in self.targets()}
            by_target: Dict[str, List[Dict[str, Any]]] = {}
            for item in items:
                if targets.get(item["target"]) is None:
                    self.outbox.mark_retry(item["id"], "Ziel nicht konfiguriert")
                elif not self.paused(item["target"]):
                    by_target.setdefault(item["target"], []).append(item)
            # Ziele laufen nebeneinander: Ein hängendes NAS darf die Online-Galerie nicht
            # aufhalten, und drei Ziele brauchen nicht die dreifache Zeit.
            results: Dict[str, int] = {}
            threads = []
            for name, group in by_target.items():
                # Ein Ziel, ein Thread. ``join(timeout=300)`` gibt die Runde
                # auf, ohne den Thread zu stoppen; der Eintrag steht dann
                # weiter auf „pending" und kam in der nächsten Runde noch
                # einmal dran — für dieselbe Datei lief danach ein zweiter
                # Upload parallel zum ersten. Beide schreiben in der Galerie
                # in dieselbe ``.part``-Datei. Gefunden am 19.09.2026.
                with self._laufend_lock:
                    if name in self._laufend:
                        continue
                    self._laufend.add(name)

                def arbeite(n=name, g=group):
                    try:
                        results[n] = self._drain_target(targets[n], g)
                    finally:
                        with self._laufend_lock:
                            self._laufend.discard(n)

                th = threading.Thread(target=arbeite, name=f"up-{name}", daemon=True)
                threads.append(th)
                th.start()
            for th in threads:
                th.join(timeout=300)
            progressed = any(results.values())
            if progressed and not self.online:
                self.outbox.reset_backoff()
            if not progressed:
                time.sleep(2)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
