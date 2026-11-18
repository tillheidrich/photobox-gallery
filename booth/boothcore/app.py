"""Photobox Booth — Prozess, Dienste und HTTP-API für die Kiosk-Oberfläche."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import time as time_module
from concurrent.futures import Future
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from . import __version__, camera_sdk_bundle, errors, plugins, profile_rules, redact, share, source_offer
from .media import vorlagen_kern as templates
from .abendzahlen import Abendzahlen
from .camera import CameraService, create_backend
from .camera.base import check_photo
from .camera.service import create_video_backend, rotate_image
from .dauerbetrieb import Dauerbetrieb
from . import meldungen as meldungen_mod
from .aufraeumen import Aufraeumer
from . import medienablage
from . import guest_share
from . import drawing
from . import waechter
from . import window as window_mod
from .meldungen import Meldungen, aus_diagnose

#: Operator messages the kiosk shows in its own language (keys
#: "<code>.title" and "<code>.text" in i18n.js and app/booth_texts.py). The
#: German sentences in _zustand_melden stay for mails, the log and the gallery.
OPERATOR_CODES = ("op.camera_down", "op.printer_none", "op.printer_missing", "op.prints_held",
                  "op.disk_full", "op.disk_low", "op.paper_low", "op.paper_out",
                  "op.tray_low", "op.tray_out", "op.ribbon_low", "op.ribbon_out", "op.flash_dark",
                  "op.camera_battery")
from .config import Store, data_dir
from .delivery.mail import MailSender, attachment_names, valid_address
from .diagnostics import LogBuffer
from .webhooks import Webhooks
from .library import Library
from . import usb_backup
from . import layoutbilder as layoutbilder_mod
from .media import aufnahmezeit, boomerang, chroma, compose, looks, luts, motion_frames
from .printing import PrintService, create_printer, printer_configs
from .printing.service import PrintJob
from .session import Session, new_session
from .pinbrake import PinBrake
from photobox_sync.agent import Agent, job_ids
from photobox_sync.outbox import Outbox

log = logging.getLogger("booth")
WEB = Path(__file__).parent / "web"
# A wrong PIN still costs a moment; the real brake is PinBrake.
_WRONG_PIN_PAUSE = 0.8
# Data-minimal mode: an unfinished run never lives shorter than this, however
# small the configured delete period — a guest needs a moment between shots.
_IDLE_FLOOR_SECONDS = 10
# Written into every run folder started in data-minimal mode, so that a later
# start can tell those leftovers apart from a normal booth's runs.
_EPHEMERAL_MARKER = ".datensparsam"
MEDIA_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm")


_MOTION_SEEN: Dict[str, Tuple[float, int, bool]] = {}


def _motion_file(p: Path) -> bool:
    """Is this media file a frame for boomerang/video (``"kind": "motion_frame"``)?

    Asked on every state poll (``templates``), so the answer is kept per
    file until its size or time changes.
    """
    try:
        st = p.stat()
    except OSError:
        return False
    seen = _MOTION_SEEN.get(str(p))
    if seen and seen[:2] == (st.st_mtime, st.st_size):
        return seen[2]
    try:
        hit = motion_frames.is_motion_frame(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        hit = False
    _MOTION_SEEN[str(p)] = (st.st_mtime, st.st_size, hit)
    return hit


def _lut_label(key: str) -> str:
    """``natural-portrait`` -> ``Natural Portrait``."""
    return " ".join(w.upper() if w.isdigit() else w.capitalize() for w in key.replace("_", "-").split("-") if w)


#: Modes whose set-up offers motion frames ("Rahmen"). The GIF follows the
#: boomerang (18.11.2026) and takes the frames made for it.
FRAME_MODES = ("boomerang", "video", "gif")

class Booth:
    """Hält alle Dienste. Genau eine Instanz pro Prozess."""

    def __init__(self, base: Optional[Path] = None, start_threads: bool = True):
        self.data = base or data_dir()
        for sub in ("sessions", "assets", "plugins", "logs", "luts"):
            (self.data / sub).mkdir(parents=True, exist_ok=True)
        self.assets_dir = self.data / "assets"
        self.sessions_dir = self.data / "sessions"
        self.luts_dir = self.data / "luts"
        self.logbuf = LogBuffer()
        self._setup_logging()
        self.store = Store(self.data)
        #: Windows-Einstellungen für den Dauerbetrieb. Das Gedächtnis liegt
        #: neben dem Profil, damit „Zurücksetzen“ einen Neustart übersteht.
        self.dauerbetrieb = Dauerbetrieb(self.data / "dauerbetrieb.json")
        #: Shared by the admin login and the lock on the booth (see pinbrake.py).
        #: Its count lives in the data folder, so a restart is no fresh start.
        self.pin_brake = PinBrake(path=self.data / "pin_brake.json")
        #: Address the web interface listens on; __main__ sets it from --host.
        #: The self-diagnosis needs it to tell a kiosk from a booth on the network.
        self.listen_host = "127.0.0.1"
        #: time.monotonic() of the kiosk's last /api/state on this PC; None = not yet.
        self.kiosk_seen: Optional[float] = None
        self.plugins = plugins.load(self.data / "plugins")
        from .camera import service as _camsvc
        for d in plugins.safe_call(self.plugins, "booth_register_cameras") or []:
            if isinstance(d, dict):
                _camsvc.EXTRA_BACKENDS.update(d)
        cam_cfg = self.store.local["camera"]
        self.camera = CameraService(lambda: create_backend(self.store.local["camera"]),
                                    rotate=cam_cfg.get("rotate", 0),
                                    video_factory=lambda: create_video_backend(self.store.local["camera"]))
        # The shot's values read from the camera, kept while the live view has
        # its own: a crash must not leave the next start reading those as the shot's.
        self.camera.shot_values_file = self.store.local_path.parent / "preview_shot.json"
        # Raw camera movies and their frame folders never outlive the process
        # that recorded them (a movie that arrived after its caller gave up,
        # a crash mid-clip): gone at start.
        self._sweep_movie_folder()
        loc = self.store.local
        # Beim Start einmal nachsehen, was für ein Drucker da hängt und was
        # drin ist. Eingetragenes bleibt stehen; nur Lücken werden gefüllt.
        self._drucker_erkennen(loc)
        pcfgs = printer_configs(loc)
        backends = []
        for i, pc in enumerate(pcfgs):
            try:
                backends.append(create_printer(pc, self.data if i == 0 else self.data / f"printer{i + 1}"))
            except Exception as e:
                log.error("Drucker %s nicht verfügbar: %s", i + 1, e)
                backends.append(None)
        self.printer = PrintService(backends, self.data / "print_counter.txt", on_printed=self._on_printed,
                                    on_failed=self._on_print_failed,
                                    on_error=self._on_print_error,
                                    routing=loc.get("print_routing", "balance"), options=pcfgs,
                                    korrektur_fuer=self._druckkorrektur)
        # Prints accepted while the paper was out (print.queue_while_empty):
        # how long they may wait, and the allowance back when one is dropped.
        self.printer.deferred_max_age = lambda: 3600.0 * float(
            (self.profile.get("print") or {}).get("queue_max_hours") or 6)
        self.printer.on_dropped = self._print_dropped
        self.outbox = Outbox(self.data / "outbox.sqlite")
        self.library = Library(self.data / "library.sqlite")
        #: Numbers of the evening per event and day (abendzahlen.py), in the
        #: same file as the library, survive a restart.
        self.zahlen = Abendzahlen(self.data / "library.sqlite")
        #: Camera counters seen at the last heartbeat; the camera service
        #: counts since its start, the evening's counters want the increase.
        self._kamera_stand = {"errors": 0, "reconnects": 0}
        self.mail = MailSender(self.data / "mail.sqlite", lambda: self.profile.get("mail") or {},
                               start_worker=start_threads)
        # Mails über die Galerie: Die Box kennt sie ohnehin, und so braucht sie
        # kein eigenes Postfach. Scheitert der Aufruf, bleibt die Mail in der
        # Warteschlange der Box und wird später erneut versucht.
        self.mail.set_relay(self._relay_mail)
        #: Was die Box dem Betreiber zu sagen hat. Geht über dieselbe
        #: Warteschlange wie die Gastmails — die Box ist oft offline, und
        #: eine Meldung, die morgen ankommt, ist besser als keine.
        self.meldungen = Meldungen(lambda: self.profile.get("meldungen") or {},
                                   mail_enqueue=self.mail.enqueue)
        #: Dauerbetrieb: alte Aufnahmen von der Platte nehmen, aber nur
        #: bestätigte. Ohne Ziel gibt es keine zweite Kopie — dann ist diese
        #: Platte das Original, und ein Original löscht man nicht nach Plan.
        self.aufraeumer = Aufraeumer(
            sessions_dir=self.sessions_dir, library=self.library, outbox=self.outbox,
            config=lambda: self.profile.get("aufraeumen") or {},
            hat_ziele=lambda: bool(self.store.local["server"].get("url")
                                   or self.store.local.get("targets")
                                   or (self.store.local.get("storage") or {}).get("folder")),
            # A profile from the gallery cannot switch on deleting unconfirmed
            # runs (R2-N1); a booth without a gallery sets its profile itself.
            unbestaetigt_erlaubt=lambda: (not self.store.local["server"].get("url")
                                          or bool(self.store.local.get("aufraeumen_ohne_bestaetigung"))))
        self.webhooks = Webhooks(lambda: {**(self.profile.get("webhooks") or {}),
                                          "booth_name": self.store.local.get("booth_name", "")},
                                 start_worker=start_threads)
        self.trigger: Optional[dict] = None          # Auslösen von außen, die Oberfläche holt es ab
        #: Kiosk language set by remote control ("" = the profile's first); kept in memory only.
        self.kiosk_lang = ""
        self._wipe_timers: Dict[str, threading.Timer] = {}   # datensparsamer Betrieb
        # Data-minimal mode, runs that have not reached finalize yet: one timer
        # each, restarted by every activity of that run (see _arm_idle_wipe).
        self._idle_timers: Dict[str, threading.Timer] = {}
        # Print allowance: check, submit and booking in one step (print_uid).
        self._print_lock = threading.Lock()
        self.remote_locked = False
        #: Set by pairing: report version -1 until the (possibly new) gallery
        #: has sent its profile, even if its version number equals ours.
        self._profile_refresh = False
        self._threads = start_threads
        #: An open pairing request from the phone: address, code, verifier,
        #: deadline. Memory only — a restart simply needs a new request.
        self._phone_pair: Optional[dict] = None
        from photobox_sync.targets import HostKeys
        # SFTP host keys: trust on first use, then pinned (targets.HostKeys, A-3c).
        self.host_keys = HostKeys(self.data / "sftp_host_keys.json")
        self.agent = Agent(outbox=self.outbox, local_config=lambda: self.store.local, assets_dir=self.assets_dir,
                           host_keys=self.host_keys,
                           templates=self.templates_to_push, templates_acked=self.templates_acked,
                           templates_pending=lambda: list(self.store.local.get("template_push") or []),
                           status=self._status_payload,
                           profile_version=lambda: -1 if self._profile_refresh else int(self.store.profile.get("version", 0)),
                           apply_profile=self._apply_server_profile, pending_deletions=self.library.pending_deletions,
                           ack_deletions=self.library.ack_deletions,
                           event_claim=lambda: self.store.local.get("event_claim") or {},
                           event_claimed=self._event_angekommen,
                           session_gone=self.deleted_in_gallery,
                           profile_seed=self.profile_seed, profile_seed_answered=self.profile_seed_answered,
                           on_uploaded=lambda item: plugins.safe_call(self.plugins, "booth_uploaded", item=item),
                           logs=lambda: self.logbuf.take(50))
        # Neue Fassungen kommen über die Galerie — sie liegt ohnehin dahinter.
        from .updater import Updater, aufraeumen as _update_aufraeumen
        self.updater = Updater(self.data, __version__)
        _update_aufraeumen(self.data)
        self.agent.update_offer = self.updater.merke
        self.agent.job_handlers.update({
            # Not under a guest (R2-U2): the same rule as live control.
            "test_capture": lambda p: self._when_idle(lambda: str(self.test_capture())),
            "test_print": lambda p: self.test_print(p),
            "reset_print_counter": lambda p: self.printer.reset_counter() or "ok",
            "release_prints": lambda p: f"{self.printer.release_held()} wartende Drucke erneut",
            # „Papier nachgelegt" heißt zweierlei: Der Zähler fängt von vorn an,
            # und was wegen Papiermangel liegen blieb, darf wieder los.
            "paper_changed": lambda p: (f"{self.paper_changed(p.get('slot'))} Drucker auf voll, "
                                        f"{self.printer.release_held()} wartende Drucke erneut"),
            # The same for the ink ribbon (SELPHY): its own count, same release.
            "ribbon_changed": lambda p: (f"Farbband neu bei {self.printer.ribbon_changed(p.get('slot'))} "
                                         f"Drucker(n), {self.printer.release_held()} wartende Drucke erneut"),
            "discard_prints": self.discard_prints,
            "restart_app": lambda p: self._when_idle(self.restart_on_purpose),
            "lock": lambda p: self._when_idle(lambda: self._set_locked(True)),
            "unlock": lambda p: self._set_locked(False),
            "retry_mail": lambda p: f"{self.mail.retry_failed()} E-Mails erneut",
            "doctor": lambda p: self._doctor_summary(),
            # Update: holen und einspielen sind zwei Schritte. Holen darf immer,
            # einspielen beendet die Box — das entscheidet der Betreiber.
            "fetch_update": lambda p: str(self.updater.hole(
                self.store.local["server"].get("api_key", ""),
                bool(self.store.local["server"].get("verify_tls", True)),
                gallery_url=self.store.local["server"].get("url", ""))),
            "install_update": lambda p: str(self.updater.einspielen()),
            "set_local_config": self.remote_local_config,
            # Live control from the gallery: refused unless allowed here.
            "remote_control": lambda p: self.fernsteuerung.handle(p),
        })
        from .fernsteuerung import Fernsteuerung
        self.fernsteuerung = Fernsteuerung(self)
        # The second upload target from the gallery: refused unless allowed
        # here (remote_targets.allowed); the password arrives sealed.
        from .second_target import SecondTarget
        self.second_target = SecondTarget(self)
        self.agent.job_handlers.update({
            "set_second_target": lambda p: self.second_target.handle_set(p),
            "check_second_target": lambda p: self.second_target.handle_check(p),
            "backfill_second_target": lambda p: self.second_target.handle_backfill(p),
            "accept_second_target_host_key": lambda p: self.second_target.handle_accept_host_key(p),
        })
        self.sessions: Dict[str, Session] = {}
        self.last_photo_at: Optional[str] = None
        self.sessions_total = 0
        #: Process start, for the camera grace period (CAMERA_START_GRACE_S).
        self._gestartet = time.monotonic()
        self.admin_tokens: Dict[str, float] = {}
        # Tokens handed out for the factory PIN. They open exactly one door:
        # choosing a new PIN. Everything else answers 403 until that is done.
        self.pin_change_tokens: set = set()
        self.profile_rev = 0
        self._event_slug = self.profile["event"].get("slug", "")
        self._extra_looks = {}
        for d in plugins.safe_call(self.plugins, "booth_register_looks") or []:
            if isinstance(d, dict):
                self._extra_looks.update(d)
        self.camera.desired_settings = self._shot_settings()
        self.camera.preview_settings = self._preview_exposure()
        from .layoutbilder import Layoutbilder
        #: Pictures of the layouts on the set-up screen (layoutbilder.py).
        self.layoutbilder = Layoutbilder(self)
        if start_threads:
            self.layoutbilder.vorbereiten()
            # Only the real start: --doctor opens the same data folder while
            # the booth may be running, and must not delete its current run.
            self._clear_ephemeral_leftovers()
            self.camera.start()
            from . import lifecycle
            lifecycle.before_exit(self.camera.stop)
            # The goodbye to the gallery goes first, capped at 2 s: its
            # silence alarm must not warn about a box switched off on purpose.
            lifecycle.before_exit(self.say_goodbye, first=True)
            lifecycle.watch_console_events(self._on_console_event)
            threading.Thread(target=self._usb_loop, name="usb-auto", daemon=True).start()
            # A kopplung.txt from a USB stick pairs a booth without a keyboard;
            # the short code needs the network once, so not on the start path.
            threading.Thread(target=self.pair_from_file, name="pairing-file", daemon=True).start()
            self.agent.start()

    # ── Infrastruktur ─────────────────────────────────────
    def _setup_logging(self) -> None:
        root = logging.getLogger()
        for old in [h for h in root.handlers if isinstance(h, LogBuffer)]:
            root.removeHandler(old)             # bei einer zweiten Instanz gilt der neue Puffer
        root.addHandler(self.logbuf)            # Warnungen/Fehler gehen zusätzlich an die Galerie
        root.setLevel(logging.INFO)
        if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            return
        fh = RotatingFileHandler(self.data / "logs" / "booth.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)

    def _apply_profile(self, profile: dict) -> bool:
        changed = self.store.apply_profile(profile)
        if changed:
            self.on_profile_changed()
        return changed

    def shutdown(self, code: int = 0) -> None:
        """End the program in order: the camera session is closed first (lifecycle.py)."""
        from . import lifecycle
        lifecycle.leave(code)

    def say_goodbye(self) -> bool:
        """Tell the gallery why we end (quit, restart, update, Windows) — at most 2 s.

        Its silence alarm (gallery, app/silence_alarm.py) then shows
        "bewusst heruntergefahren" instead of warning the owner.
        """
        from . import lifecycle
        reason, expect_back = lifecycle.why(lifecycle.exit_code())
        try:
            return self.agent.goodbye(reason, expect_back, limit=2.0)
        except Exception:                                # noqa: BLE001 — leaving never fails on this
            log.exception("Abmeldung bei der Galerie")
            return False

    def _on_console_event(self, reason: str) -> None:
        """Windows: console closed, sign-out or shutdown. Leave in order, saying why."""
        self.end_session(reason)

    def end_session(self, reason: str) -> None:
        """The session or the watcher ends (console event, ``end:<reason>`` from
        the watcher, the own window closed by Windows, the watcher gone).

        One way out for all of them: the first reason is the one the gallery
        hears (lifecycle.announce), the goodbye goes once (lifecycle.leave is
        a latch), the camera is handed back, exit 0 - nothing restarts.
        """
        from . import lifecycle
        lifecycle.announce(reason, False)
        window_mod.ended_on_purpose(self.data)   # not a failure of the own window either
        self.shutdown(0)

    def profile_seed(self) -> dict:
        """This booth's profile as a starting point for a gallery it was just paired with.

        Without the event (the gallery decides that), the version and every
        secret (PIN, mail password, tokens): a starting point, not a backup.
        """
        if not self.store.local.get("profile_seed_pending"):
            return {}
        seed = json.loads(json.dumps(self.profile))
        seed.pop("event", None)
        seed.pop("version", None)
        for gruppe, feld in (*_PROFILE_SECRETS, ("wifi_share", "password")):
            if isinstance(seed.get(gruppe), dict):
                seed[gruppe][feld] = ""
        return seed

    def profile_seed_answered(self, answer: str) -> None:
        self.store.save_local({"profile_seed_pending": False})
        if answer == "taken":
            log.info("Die Galerie hat die Einstellungen dieser Box als Startprofil übernommen")
        elif answer == "kept":
            log.warning("Die Galerie hat schon ein eigenes Profil für diese Box; es gilt, "
                        "Einstellungen an der Box werden ersetzt (festhalten: Verwaltung → Alle Einstellungen)")

    def _apply_server_profile(self, profile: dict) -> bool:
        """A profile that came from the gallery. Waits while the booth holds its own."""
        self._profile_refresh = False
        # The gallery sends the template files this profile names with every
        # sync (assets_for_profile), held or not: deleted here, they come back.
        # The templates page needs to know which those are.
        von_galerie = [str(x) for x in (profile.get("collage_templates") or [])][:200]
        if von_galerie != list(self.store.local.get("gallery_templates") or []):
            self.store.save_local({"gallery_templates": von_galerie})
        if self.store.local.get("profile_hold"):
            return False
        return self._apply_profile(self._keep_local_pin(profile))

    # ── Event an der Box ──────────────────────────────────
    #
    # Der Betreiber steht im Festzelt, die Galerie ist nicht erreichbar, und
    # das Event ist nirgends angelegt. Dann legt die Box es selbst an: Name,
    # Slug und vor allem der Signaturschlüssel entstehen hier. Damit stimmen
    # die QR-Codes vom ersten Foto an — die Galerie holt das Event später nach.

    def _slug_aus(self, name: str) -> str:
        import re
        text = (name or "").lower()
        text = re.sub(r"[äöüß]",
                      lambda m: {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}[m.group()], text)
        text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60]
        return text or secrets.token_hex(3)

    def event_anlegen(self, name: str, galerie: str = "privat") -> dict:
        """Ein Event an der Box anlegen — mit oder ohne Netz.

        Mit Netz fragt die Box die Galerie und übernimmt deren Antwort. Ohne
        Netz gilt der lokale Stand sofort, und der Zettel im ``local.json``
        sorgt dafür, dass er bei der nächsten Verbindung nachgereicht wird.
        """
        name = str(name or "").strip()[:80]
        if len(name) < 2:
            raise HTTPException(400, "Das Event braucht einen Namen")
        # „privat" heißt: Jeder Gast sieht über seinen QR-Code nur den eigenen
        # Durchgang. „gemeinsam" heißt: Es gibt zusätzlich eine Event-Galerie,
        # in der alle alles sehen. Das ist eine Entscheidung, keine
        # Voreinstellung — deshalb reist sie mit dem Zettel zur Galerie.
        galerie = "gemeinsam" if str(galerie).lower() == "gemeinsam" else "privat"
        claim = {"name": name, "slug": self._slug_aus(name),
                 "share_key": secrets.token_urlsafe(24), "galerie": galerie,
                 "angelegt": dt.datetime.now().isoformat(timespec="seconds")}
        antwort = self._gallery_json("POST", "/api/v2/booth/events", claim)
        if antwort and antwort.get("event"):
            event = antwort["event"]
            self._event_setzen(event.get("slug", claim["slug"]), event.get("name", name), claim["share_key"])
            self.store.save_local({"event_claim": None})
            return {"event": event, "online": True}
        # Ohne Galerie: Der Zettel bleibt liegen, bis die Quittung kommt.
        self.store.save_local({"event_claim": claim})
        self._event_setzen(claim["slug"], name, claim["share_key"])
        log.info("Event %r ohne Verbindung angelegt — wird beim nächsten Sync nachgetragen", name)
        return {"event": {"slug": claim["slug"], "name": name, "current": True}, "online": False}

    def event_waehlen(self, kennung) -> dict:
        """Auf ein Event der Galerie wechseln. Das geht nur mit Verbindung."""
        ziel = {"event_id": kennung} if str(kennung).isdigit() else {"slug": str(kennung)}
        antwort = self._gallery_json("POST", "/api/v2/booth/events/select", ziel)
        if not antwort or not antwort.get("event"):
            raise HTTPException(503, "Galerie nicht erreichbar")
        self.store.save_local({"event_claim": None})
        self.agent.sync_once()                 # Profil samt Signaturschlüssel sofort holen
        return {"event": antwort["event"], "online": True}

    def events_liste(self) -> dict:
        """Was die Galerie kennt — und was die Box gerade macht."""
        antwort = self._gallery_json("GET", "/api/v2/booth/events")
        eigenes = self.profile.get("event") or {}
        return {"online": bool(antwort), "events": (antwort or {}).get("events", []),
                "current": {"slug": eigenes.get("slug", ""), "name": eigenes.get("name", "")},
                "claim": self.store.local.get("event_claim") or {}}

    def _event_setzen(self, slug: str, name: str, share_key: str) -> None:
        profil = json.loads(json.dumps(self.profile))
        event = {**(profil.get("event") or {}), "slug": slug, "name": name, "share_key": share_key}
        # Die Adresse der Galerie steht in den lokalen Einstellungen, auch wenn
        # sie gerade nicht erreichbar ist. Ohne sie zeigte der QR-Code ins Leere.
        if not event.get("base_url"):
            event["base_url"] = (self.store.local.get("server") or {}).get("url", "")
        profil["event"] = event
        self._apply_profile(profil)

    def _event_angekommen(self, event: dict) -> None:
        """Quittung der Galerie: Zettel weg, Slug übernehmen."""
        slug = str(event.get("slug") or "")
        if slug:
            self._event_setzen(slug, str(event.get("name") or ""),
                               str(event.get("share_key") or (self.profile.get("event") or {}).get("share_key", "")))
        self.store.save_local({"event_claim": None})

    def _gallery_json(self, method: str, pfad: str, body: Optional[dict] = None) -> Optional[dict]:
        """Ein Aufruf an die Galerie. Gibt None zurück, wenn sie nicht antwortet."""
        import httpx

        srv = self.store.local.get("server") or {}
        url, key = (srv.get("url") or "").rstrip("/"), srv.get("api_key") or ""
        if not url or not key:
            return None
        try:
            r = httpx.request(method, f"{url}{pfad}", json=body, timeout=12,
                              headers={"X-API-Key": key}, verify=srv.get("verify_tls", True))
            if r.status_code >= 400:
                log.info("Galerie antwortet mit %s auf %s", r.status_code, pfad)
                return None
            return r.json()
        except Exception as e:
            log.info("Galerie nicht erreichbar (%s): %s", pfad, e)
            return None

    # ── Kopplung mit der Galerie ──────────────────────────
    def pair(self, text: str) -> dict:
        """Redeem a pairing code from the gallery admin: address and key in one go.

        Accepts what the gallery shows — gallery address or name plus the short
        code, or the long PBX1 code (parsing and the exchange are the sync
        package's, shared with the stand-alone sync program). Writes
        ``server.url``/``server.api_key`` to local.json. No restart: the sync
        loop, uploads, mail relay and updates read the server settings at every
        call, so the next round already talks to the new gallery. The code is
        never logged; a refusal raises ``PairingError`` (a ValueError) with a
        sentence and a ``reason``.
        """
        from photobox_sync.pairing import PairingError, fetch_key, parse_input

        srv = self.store.local.get("server") or {}
        try:
            data = parse_input(text, known_url=srv.get("url", ""))
        except PairingError:
            raise
        except ValueError as e:
            raise PairingError(str(e), "malformed") from None
        if data["mode"] == "fetch":
            got = fetch_key(data["url"], data["code"], verify_tls=bool(srv.get("verify_tls", True)),
                            user_agent="photobox-booth")
            data = {"url": got.get("url") or data["url"], "api_key": got["api_key"],
                    "name": got.get("name", ""), "gallery_name": got.get("gallery_name", "")}
        return self._store_pairing(data["url"], data["api_key"], data.get("name", ""), data.get("gallery_name", ""))

    def _store_pairing(self, url: str, api_key: str, name: str = "", gallery_name: str = "") -> dict:
        """Write address and key to local.json and let the sync use them at its next round (no restart)."""
        srv = self.store.local.get("server") or {}
        url = str(url).rstrip("/")
        changed = (url != (srv.get("url") or "").rstrip("/") or api_key != srv.get("api_key"))
        self.store.save_local({"server": {"url": url, "api_key": api_key,
                                          "device_name": str(name or "")[:60],
                                          "gallery_name": str(gallery_name or "")[:80]}})
        if changed:
            self._profile_refresh = True
            # The operator's settings at this booth are offered as the starting
            # profile (owner, 16.11.2026: the first sync after pairing replaced
            # controls.mouse_start, start.gallery_style and access.lock_icon
            # with the gallery's defaults).
            self.store.save_local({"profile_seed_pending": True})
        log.info("Mit der Galerie %s gekoppelt (Box „%s“)", url, name or "ohne Namen")
        if self._threads:
            threading.Thread(target=self.agent.sync_once, name="sync-after-pairing", daemon=True).start()
        return self.pairing_status()

    # ── Kopplung mit dem Handy (device flow, app/phone_pairing.py in the gallery) ──
    def phone_pair_start(self, url: str = "") -> dict:
        """Open a pairing request at the gallery and return the QR code for the operator's phone.

        The verifier stays in this process (never logged, never in the QR);
        the gallery stores only its SHA-256. A new start replaces an open request.
        """
        import base64
        import hashlib

        import httpx
        from photobox_sync.pairing import PairingError

        srv = self.store.local.get("server") or {}
        url = str(url or srv.get("url") or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        if not url or "." not in url.split("://", 1)[1] and "localhost" not in url and "127.0.0.1" not in url:
            raise PairingError("Bitte die Adresse der Galerie eintragen, zum Beispiel meine-galerie.de", "malformed")
        install_id = self.store.local.get("install_id") or secrets.token_hex(12)
        patch: dict = {"install_id": install_id}
        if not srv.get("api_key"):
            patch["server"] = {"url": url}          # remembered for the next attempt
        self.store.save_local(patch)
        verifier = secrets.token_urlsafe(32)
        body = {"verifier_hash": hashlib.sha256(verifier.encode("utf-8")).hexdigest(),
                "booth_name": self.store.local.get("booth_name") or "Fotobox",
                "version": f"photobox-booth {__version__}", "install_id": install_id}
        try:
            r = httpx.post(f"{url}/api/v2/pair/request", json=body, timeout=15,
                           headers={"User-Agent": "photobox-booth"}, verify=bool(srv.get("verify_tls", True)))
        except httpx.HTTPError as e:
            raise PairingError(f"{url} ist nicht erreichbar ({e.__class__.__name__}) — Adresse und Netz prüfen",
                               "unreachable")
        if r.status_code == 429:
            raise PairingError("Zu viele Versuche — bitte ein paar Minuten warten", "too_many")
        if r.status_code == 404:
            raise PairingError("Diese Galerie kennt das Koppeln mit dem Handy noch nicht — "
                               "bitte den Kopplungscode nehmen", "refused")
        if r.status_code >= 400:
            raise PairingError(f"Galerie antwortet mit {r.status_code}", "refused")
        try:
            data = r.json()
            code, link = str(data["code"]), str(data["url"])
            seconds = int(data.get("expires_in") or 600)
        except (ValueError, KeyError, TypeError):
            raise PairingError("Die Galerie hat keine Anfrage geschickt", "refused")
        self._phone_pair = {"url": url, "code": code, "verifier": verifier, "until": time.time() + seconds}
        img = compose.qr_image(link, 480)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        log.info("Kopplung mit dem Handy angefragt bei %s", url)
        return {"state": "open", "code": f"{code[:4]}-{code[4:]}", "link": link, "seconds": seconds,
                "qr": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")}

    def phone_pair_poll(self) -> dict:
        """One look at the gallery: still open, declined, expired — or paired (then stored)."""
        import httpx

        req = getattr(self, "_phone_pair", None)
        if not req:
            return {"state": "none"}
        left = int(req["until"] - time.time())
        if left <= 0:
            self._phone_pair = None
            return {"state": "expired"}
        srv = self.store.local.get("server") or {}
        try:
            r = httpx.post(f"{req['url']}/api/v2/pair/status", json={"code": req["code"], "verifier": req["verifier"]},
                           timeout=10, headers={"User-Agent": "photobox-booth"},
                           verify=bool(srv.get("verify_tls", True)))
        except httpx.HTTPError:
            return {"state": "open", "seconds": left, "offline": True}
        if r.status_code == 429:
            return {"state": "open", "seconds": left, "offline": True}
        if r.status_code == 404:
            self._phone_pair = None
            return {"state": "unknown"}
        try:
            data = r.json()
        except ValueError:
            return {"state": "open", "seconds": left, "offline": True}
        state = str(data.get("state") or "")
        if state == "paired" and data.get("api_key"):
            self._phone_pair = None
            url = str(data.get("url") or req["url"])
            if not url.startswith(("http://", "https://")):
                url = req["url"]
            return {"state": "paired", "pairing": self._store_pairing(url, str(data["api_key"]),
                                                                       str(data.get("device_name") or ""),
                                                                       str(data.get("gallery_name") or ""))}
        if state == "open":
            return {"state": "open", "seconds": left}
        self._phone_pair = None
        return {"state": state or "unknown"}

    def phone_pair_cancel(self) -> None:
        self._phone_pair = None

    def pair_from_file(self) -> bool:
        """``kopplung.txt``/``pairing.txt`` in the data folder (or next to the .exe), read once at start.

        Same rules as the stand-alone sync program: only while no key is set,
        deleted after success, kept with the reason in the log otherwise.
        """
        from photobox_sync.pairing import FILE_NAMES, redeem_file

        folders = [self.data]
        if getattr(sys, "frozen", False):
            folders.append(Path(sys.executable).parent)
        if (self.store.local.get("server") or {}).get("api_key"):
            if any((Path(d) / n).is_file() for d in folders for n in FILE_NAMES):
                log.info("Kopplungsdatei liegt bereit, die Box ist aber schon gekoppelt — nicht eingelöst. "
                         "Neu koppeln in der Verwaltung unter „Galerie-Server“.")
            return False
        return redeem_file(folders, self.pair, log)

    def pairing_status(self) -> dict:
        """What the card "Galerie-Server" shows: which gallery, which box, which event, what is missing."""
        from .doctor import event_problems

        srv = self.store.local.get("server") or {}
        ev = self.profile.get("event") or {}
        problems = event_problems(self.profile, self.store.local)
        return {"paired": bool(srv.get("url") and srv.get("api_key")), "url": srv.get("url", ""),
                "gallery_name": srv.get("gallery_name", ""), "device_name": srv.get("device_name", ""),
                "online": bool(self.agent.online),
                "event": {"slug": ev.get("slug", ""), "name": ev.get("name", ""),
                          "base_url": ev.get("base_url", ""), "published": ev.get("published")},
                "warnings": [text for state, text in problems if state == "warn"],
                "notes": [text for state, text in problems if state != "warn"]}

    def _relay_mail(self, payload: dict) -> None:
        """Eine Mail von der Galerie verschicken lassen."""
        import httpx

        srv = self.store.local.get("server") or {}
        url, key = (srv.get("url") or "").rstrip("/"), srv.get("api_key") or ""
        if not url or not key:
            raise RuntimeError("keine Galerie eingerichtet — Versand über SMTP einstellen")
        r = httpx.post(f"{url}/api/v2/booth/mail", json=payload,
                       headers={"X-API-Key": key}, timeout=30,
                       verify=srv.get("verify_tls", True))
        if r.status_code == 409:
            raise RuntimeError("Aufnahme noch nicht in der Galerie — später erneut")
        if r.status_code >= 400:
            raise RuntimeError(f"Galerie antwortet mit {r.status_code}: {r.text[:120]}")

    def _status_payload(self) -> dict:
        import psutil
        du = shutil.disk_usage(self.data)
        pr = self.printer.status() if self.printer else {"state": "none"}
        st = {
            "agent_version": f"photobox-booth {__version__}", "booth_running": True,
            "booth_version": f"photobox-booth {__version__}",
            "disk_free_gb": round(du.free / 1e9, 2), "disk_total_gb": round(du.total / 1e9, 2),
            "last_photo_at": self.last_photo_at or "",
            "printer_name": str(pr.get("name") or pr.get("backend") or ""), "printer_status": pr.get("state", "unknown"),
            "prints_today": self.printer.printed if self.printer else 0,
            # Was der Betreiber aus der Ferne wissen muss, ohne davorzustehen:
            # Wie viele Drucke hängen, woran, wie viele Blätter sind noch in der
            # Kassette und wie viele Drucker antworten überhaupt.
            "prints_pending": int(pr.get("pending") or 0),
            "prints_held": int(pr.get("held") or 0),
            "printer_hint": str(pr.get("held_reason") or pr.get("last_error") or "")[:200],
            "printers_total": len(pr.get("stations") or []),
            "printers_ready": sum(1 for st in (pr.get("stations") or [])
                                  if st.get("state") in ("ready", "printing")),
            "sheets_left": self.sheets_left(),
            # Je Drucker: was noch da ist und woher die Zahl kommt (gemeldet,
            # gezählt, unbekannt). Aus der Ferne ist genau das die Frage.
            "sheets": self.sheets(),
            # Each print that is not out yet, so the gallery's booth page can
            # show and discard single ones (job ``discard_prints``).
            "print_jobs": self.print_queue_view(self.PRINT_QUEUE_IN_HEARTBEAT),
            "cpu_percent": psutil.cpu_percent(interval=None), "ram_percent": psutil.virtual_memory().percent,
            "uptime_min": int((time.time() - psutil.boot_time()) / 60),
            "camera_state": self.camera.state, "camera_backend": self.camera.backend.name if self.camera.backend else "",
            "profile_version": self.store.profile.get("version", 0), "sessions_total": self.sessions_total,
            # Liegt eine neue Fassung bereit? Der Betreiber soll das aus der
            # Ferne sehen, ohne an der Box zu stehen.
            "update": self.updater.stand(),
        }
        # Camera battery (Canon SDK only): percent or "ac"; absent = unknown.
        akku = getattr(self.camera, "battery", None)
        if akku is not None:
            st["camera_battery"] = str(akku)
        st["booth_stats"] = self._abendzahlen_melden()
        try:
            # Type, host, user, path, "password stored" and the last check —
            # never the password; the public key only while it is allowed here.
            st["second_target"] = self.second_target.report()
        except Exception as e:                       # noqa: BLE001 — the report never costs the heartbeat
            log.warning("Zweites Ziel nicht gemeldet: %s", type(e).__name__)
        self._zustand_melden(st)
        # After _zustand_melden: what it just raised travels in this heartbeat.
        st["doctor"] = self._heartbeat_doctor()
        st["notices"] = self._heartbeat_notices()
        self._vielleicht_aufraeumen(st)
        return st

    #: Prints listed in each heartbeat. An evening with more waiting than
    #: this has a bigger problem than the list; the count is sent as well.
    PRINT_QUEUE_IN_HEARTBEAT = 20

    def print_queue_view(self, limit: int = 50) -> list:
        """The print queue for the operator: what waits, since when, why."""
        if not self.printer:
            return []
        return [{**j, "reason": redact.text(j["reason"])} for j in self.printer.queue_view(limit)]

    def discard_prints(self, payload: Optional[dict] = None) -> str:
        """Throw prints away: the ``ids`` named, or all of them with ``confirm: "all"``.

        "All" never happens by accident: without ids it needs the confirmation
        word, which only the second step of a page (or the assistant's
        confirmed tool) sends. Each guest gets the sheet back.
        """
        payload = payload or {}
        ids = job_ids(payload)
        if ids is None:
            if str(payload.get("confirm") or "") != "all":
                raise ValueError("Alle Drucke verwerfen nur mit Bestätigung (confirm: all)")
            return f"{self.printer.discard_waiting()} wartende Drucke verworfen"
        weg = sum(1 for i in ids if self.printer.discard_one(i))
        rest = len(ids) - weg
        return f"{weg} Druck(e) verworfen" + (f", {rest} waren schon gedruckt oder weg" if rest else "")

    def _abendzahlen_melden(self) -> dict:
        """Camera errors since the last heartbeat into the counters, then today's and the event's sums."""
        slug = self._zahlen_event()
        stats = getattr(self.camera, "stats", None) or {}
        for name in ("errors", "reconnects"):
            jetzt = int(stats.get(name) or 0)
            vorher = int(self._kamera_stand.get(name) or 0)
            # A smaller value: the camera service started again, its count did too.
            zuwachs = jetzt - vorher if jetzt >= vorher else jetzt
            self._kamera_stand[name] = jetzt
            if zuwachs > 0:
                self.zahlen.zaehle(slug, f"camera.{name}", zuwachs)
        try:
            return self.zahlen.bericht(slug)
        except Exception as e:                       # noqa: BLE001 — numbers cost no heartbeat
            log.warning("Abendzahlen nicht lesbar: %s", e)
            return {}

    #: Nicht öfter als einmal pro halbe Stunde. Der Zustandsbericht läuft je
    #: nach Einstellung alle paar Sekunden; Aufräumen ist nichts, was dabei
    #: jedes Mal mitlaufen soll.
    AUFRAEUM_ABSTAND = 1800.0

    #: Camera battery thresholds in percent (fixed, no profile setting).
    AKKU_WARN = 20
    AKKU_KRITISCH = 10

    def _vielleicht_aufraeumen(self, st: dict) -> None:
        letzte = float(self.aufraeumer.zuletzt.get("at") or 0)
        knapp = float(st.get("disk_free_gb") or 0) < 2.0
        if not knapp and (time.time() - letzte) < self.AUFRAEUM_ABSTAND:
            return
        try:
            self.aufraeumer.lauf(frei_gb=float(st.get("disk_free_gb") or 0))
        except Exception as e:                      # pragma: no cover - Systemfehler
            log.warning("Aufräumen fehlgeschlagen: %s", e)

    #: Seconds after process start in which a camera still "starting" raises
    #: no notice. The Canon SDK needs a few seconds to find the body (3 s find
    #: timeout, reconnect backoff up to 8 s) and a camera that was just
    #: powered or woken from USB sleep takes longer; seen 17.11.2026: critical
    #: notice at 1 s, connected at 2 s. 90 s covers a slow wake without hiding
    #: a camera that really is not coming.
    CAMERA_START_GRACE_S = 90.0

    def _kamera_melden(self, state) -> None:
        m = self.meldungen
        if state == "ready":
            m.entwarnung("kamera")
            return
        if state == "starting" and (time.monotonic() - self._gestartet) < self.CAMERA_START_GRACE_S:
            return                                   # still connecting: not an alarm yet
        m.melden("kamera", meldungen_mod.KRITISCH, "Die Kamera antwortet nicht",
                 f"Zustand: {state}. Ohne Kamera nimmt die Box nichts auf. "
                 "Kabel und Stromversorgung prüfen, dann die Kamera aus und wieder an.",
                 quelle="kamera", code="op.camera_down", werte={"state": str(state)})

    def _drucker_melden(self, gesamt: int, bereit: int, hinweis: str) -> None:
        m = self.meldungen
        if gesamt and not bereit:
            m.melden("drucker", meldungen_mod.KRITISCH, "Kein Drucker ist bereit",
                     (hinweis or "Kein Grund gemeldet.") +
                     " Die Box nimmt weiter auf, aber es kommt nichts heraus.", quelle="drucker",
                     code="op.printer_none", werte={"hint": hinweis})
        elif gesamt and bereit < gesamt:
            m.melden("drucker", meldungen_mod.WARNUNG, "Ein Drucker fehlt",
                     f"{bereit} von {gesamt} Druckern bereit. " + hinweis,
                     quelle="drucker", code="op.printer_missing",
                     werte={"ready": str(bereit), "total": str(gesamt), "hint": hinweis})
        else:
            m.entwarnung("drucker")

    def _geraete_abgleichen(self) -> None:
        """Re-judge camera and printer right now, for the kiosk and the admin page.

        Between two heartbeats the status chip follows the live camera state
        while the notice used to wait for the next report; seen 17.11.2026:
        chip green next to "camera not answering". Cheap: no disk, no CPU.
        """
        try:
            self._kamera_melden(self.camera.state)
            pr = self.printer.status() if self.printer else {}
            stationen = pr.get("stations") or []
            self._drucker_melden(len(stationen),
                                 sum(1 for s in stationen if s.get("state") in ("ready", "printing")),
                                 str(pr.get("held_reason") or pr.get("last_error") or "")[:200])
        except Exception as e:                       # noqa: BLE001 — never costs the status call
            log.debug("Geräte-Abgleich nicht möglich: %s", e)

    def _zustand_melden(self, st: dict) -> None:
        """Aus dem Zustandsbericht Meldungen machen — und Behobenes entwarnen.

        Hängt bewusst am Zustandsbericht statt an einem eigenen Faden: Der
        wird ohnehin regelmäßig zusammengestellt, kennt alle Zahlen und kostet
        nichts obendrauf. Jede Regel kennt beide Richtungen — eine Box, die
        nach dem Papierwechsel weiter „kein Papier“ meldet, wird ignoriert,
        und damit auch die nächste echte Meldung.
        """
        from .doctor import DISK_FAIL_GB, DISK_WARN_GB
        m = self.meldungen

        self._kamera_melden(st.get("camera_state"))

        # Camera battery: a warning at 20 %, critical at 10 %; gone on the
        # mains adapter, above 20 % or when the backend cannot tell.
        akku = getattr(self.camera, "battery", None)
        if isinstance(akku, int) and not isinstance(akku, bool) and akku <= self.AKKU_WARN:
            m.melden("kamera_akku", meldungen_mod.KRITISCH if akku <= self.AKKU_KRITISCH else meldungen_mod.WARNUNG,
                     "Kamera-Akku fast leer",
                     f"Noch {akku} %. Akku tauschen oder das Netzteil anschließen, sonst geht die Kamera "
                     "mitten im Abend aus.", quelle="kamera", code="op.camera_battery",
                     werte={"percent": str(akku)})
        else:
            m.entwarnung("kamera_akku")

        hinweis = str(st.get("printer_hint") or "")
        self._drucker_melden(int(st.get("printers_total") or 0), int(st.get("printers_ready") or 0), hinweis)

        haengen = int(st.get("prints_held") or 0)
        if haengen:
            m.melden("drucke_haengen", meldungen_mod.WARNUNG,
                     f"{haengen} Druck(e) warten", hinweis
                     + " Ist das behoben, „Papier nachgelegt“ drücken – fehlte kein Papier, "
                       "„Wartende Drucke erneut“.", quelle="drucker",
                     code="op.prints_held", werte={"n": str(haengen), "hint": hinweis})
        else:
            m.entwarnung("drucke_haengen")

        frei = float(st.get("disk_free_gb") or 0)
        if frei < DISK_FAIL_GB:
            m.melden("platte", meldungen_mod.KRITISCH, "Die Platte ist voll",
                     f"Nur noch {frei:.1f} GB frei. Weitere Aufnahmen gehen verloren.", quelle="platte",
                     code="op.disk_full", werte={"gb": f"{frei:.1f}"})
        elif frei < DISK_WARN_GB:
            m.melden("platte", meldungen_mod.WARNUNG, "Der Platz wird knapp",
                     f"Noch {frei:.1f} GB frei — für einen langen Abend zu wenig.", quelle="platte",
                     code="op.disk_low", werte={"gb": f"{frei:.1f}"})
        else:
            m.entwarnung("platte")

        # Only a stock the booth can count can run out. Without a cassette
        # size and without counted sheets, sheets_left() is 0 meaning "not
        # known" — until 12.11.2026 that raised "paper is out" as critical at
        # every heartbeat, a permanent false alarm.
        # Printers with tray and ribbon (SELPHY) have their own notices below;
        # their 18-sheet tray would sit under this threshold all evening.
        blatt = self._blatt_je_kassette()
        if isinstance(blatt, (int, float)) and 0 < blatt <= 25:
            m.melden("blatt", meldungen_mod.WARNUNG, f"Noch etwa {int(blatt)} Blatt",
                     "Gezählt, nicht gemeldet — die Zahl kann abweichen. Nachschub bereitlegen.",
                     quelle="drucker", code="op.paper_low", werte={"n": str(int(blatt))})
        elif isinstance(blatt, (int, float)) and blatt <= 0:
            m.melden("blatt", meldungen_mod.KRITISCH, "Das Papier ist rechnerisch alle",
                     "Die Box hat so viele Blatt gedruckt, wie in eine Kassette passen.",
                     quelle="drucker", code="op.paper_out")
        else:
            m.entwarnung("blatt")

        # Tray and ribbon, each on its own: the operator has to know which of
        # the two to reach for. The printer with the least left speaks.
        for key, teil, knapp_code, leer_code, knapp_titel, leer_titel, leer_text in (
                ("fach", "fach", "op.tray_low", "op.tray_out", "Papierfach: noch etwa {n} Blatt",
                 "Das Papierfach ist rechnerisch leer",
                 "So viele Blatt passen ins Fach. Papier nachlegen, dann „Papier nachgelegt“ drücken."),
                ("farbband", "farbband", "op.ribbon_low", "op.ribbon_out", "Farbband: noch etwa {n} Drucke",
                 "Das Farbband ist rechnerisch verbraucht",
                 "So viele Drucke hält ein Farbband. Farbband wechseln, dann „Farbband gewechselt“ drücken.")):
            stand = [s[teil] for s in self.sheets() if isinstance(s.get(teil), dict)]
            if not stand:
                m.entwarnung(key)
                continue
            knappst = min(stand, key=lambda x: int(x.get("rest") or 0))
            rest, gesamt = int(knappst.get("rest") or 0), int(knappst.get("gesamt") or 0)
            if rest <= 0:
                m.melden(key, meldungen_mod.KRITISCH, leer_titel,
                         "Gezählt, nicht gemeldet — die Zahl kann abweichen. " + leer_text,
                         quelle="drucker", code=leer_code)
            elif rest <= self._knapp_ab(gesamt):
                m.melden(key, meldungen_mod.WARNUNG, knapp_titel.format(n=rest),
                         "Gezählt, nicht gemeldet — die Zahl kann abweichen. Nachschub bereitlegen.",
                         quelle="drucker", code=knapp_code, werte={"n": str(rest)})
            else:
                m.entwarnung(key)

    @staticmethod
    def _knapp_ab(gesamt: int) -> int:
        """From how many left a tray or ribbon counts as low: about one in six, at least 2.

        Tray 18 warns at 3, ribbon 36 at 5 — a few prints of warning, which
        at a SELPHY's pace is a few minutes to fetch the next pack.
        """
        return max(2, round(int(gesamt or 0) * 0.15))

    def _blatt_je_kassette(self) -> Optional[int]:
        """Sheets left for the single per-cassette notice; None = nothing to say.

        Printers that count tray and ribbon are left out — they have their own
        notices. If every counted printer is one of those, there is nothing
        left for this one.
        """
        gezaehlt = [s for s in self.sheets() if s.get("quelle") == "gezählt"]
        if gezaehlt:
            einzeln = [s for s in gezaehlt if "fach" not in s]
            return sum(int(s.get("rest") or 0) for s in einzeln) if einzeln else None
        return self.sheets_left() if self._vorrat_bekannt() else None

    def _vorrat_bekannt(self) -> bool:
        """Does the booth know its paper stock — counted per printer, or a cassette size in the profile?"""
        if any(s.get("quelle") == "gezählt" for s in self.sheets()):
            return True
        return int((self.profile.get("print") or {}).get("sheets_per_cassette") or 0) > 0

    def _drucker_erkennen(self, loc: dict) -> None:
        """Modell und Medium aus dem Druckernamen ableiten — still, beim Start.

        Das ist der Unterschied zwischen „Blattzahl bitte nachschlagen und
        eintragen" und „die Box weiß es". Erkanntes wird gespeichert, damit es
        auch im Formular steht; was schon eingetragen war, bleibt.
        """
        from .printing.service import uebernehmen

        geaendert = False
        for schluessel in ("printer", "printer2"):
            cfg = loc.get(schluessel)
            if not isinstance(cfg, dict) or (cfg.get("backend") or "auto") == "none":
                continue
            try:
                ergebnis = uebernehmen(cfg, self.data)
            except Exception as e:                # noqa: BLE001 — Erkennen darf keinen Start kosten
                log.warning("Drucker nicht erkannt: %s", e)
                continue
            if ergebnis["geaendert"]:
                loc[schluessel] = ergebnis["config"]
                geaendert = True
                log.info("Drucker erkannt (%s): %s", schluessel,
                         ", ".join(f"{k}={ergebnis['config'][k]}" for k in ergebnis["geaendert"]))
        liste = loc.get("printers")
        if isinstance(liste, list):
            for i, cfg in enumerate(liste):
                if not isinstance(cfg, dict) or (cfg.get("backend") or "auto") == "none":
                    continue
                try:
                    ergebnis = uebernehmen(cfg, self.data if i == 0 else self.data / f"printer{i + 1}")
                except Exception as e:            # noqa: BLE001
                    log.warning("Drucker %s nicht erkannt: %s", i + 1, e)
                    continue
                if ergebnis["geaendert"]:
                    liste[i] = ergebnis["config"]
                    geaendert = True
                    log.info("Drucker %s erkannt: %s", i + 1, ", ".join(ergebnis["geaendert"]))
        if geaendert:
            try:
                self.store.save_local({k: loc[k] for k in ("printer", "printer2", "printers") if k in loc})
            except Exception as e:                # noqa: BLE001
                log.warning("Erkannte Druckerdaten nicht gespeichert: %s", e)

    def drucker_suchen(self) -> dict:
        """Auf Zuruf noch einmal nachsehen — nach einem Druckerwechsel im Betrieb."""
        loc = self.store.local
        self._drucker_erkennen(loc)
        from .printing.service import erkennen

        return {"printer": erkennen(loc.get("printer") or {}, self.data),
                "printers": [erkennen(c, self.data if i == 0 else self.data / f"printer{i + 1}")
                             for i, c in enumerate(loc.get("printers") or []) if isinstance(c, dict)]}

    def sheets(self) -> list:
        """Restblätter je Drucker, mit Angabe, woher die Zahl kommt."""
        return self.printer.sheets() if self.printer else []

    def paper_changed(self, slot=None) -> int:
        """Papier gewechselt — die Zählung dieses Druckers fängt von vorn an."""
        if not self.printer:
            return 0
        anzahl = self.printer.paper_changed(slot)
        log.info("Papier gewechselt (%s Drucker)", anzahl)
        return anzahl

    def sheets_left(self) -> int:
        """Restblätter der Kassette, so gut die Box das wissen kann.

        Messen kann sie es nicht — Fotodrucker sagen es nicht. Sie zählt
        deshalb mit, was sie selbst gedruckt hat, seit der Zähler zuletzt auf
        null stand. 0 heißt: keine Vorgabe eingetragen.
        """
        if not self.printer:
            return 0
        # Steht das Modell in der Einrichtung, kennt jeder Drucker seine eigene
        # Kassette — das ist genauer als ein Wert für die ganze Box.
        je_drucker = [s for s in self.printer.sheets() if s.get("quelle") == "gezählt"]
        if je_drucker:
            return sum(int(s.get("rest") or 0) for s in je_drucker)
        proKassette = int((self.profile.get("print") or {}).get("sheets_per_cassette") or 0)
        if not proKassette:
            return 0
        return max(0, proKassette - int(self.printer.printed))

    def _restart_later(self) -> str:
        """Beenden — und dafür sorgen, dass etwas die Box wieder hochbringt.

        Aus dem Quellcode genügt das Beenden: ``start-booth.bat`` hat eine
        Schleife und startet neu. Die .exe hat die nicht — dort trug der
        Aufgabenplaner sie direkt ein, ohne Wiederholung. „Neustart" in der
        Verwaltung und der Fernauftrag ``restart_app`` schalteten die Box
        damit endgültig aus, bis jemand physisch doppelklickt. Mitten im
        Event. Ist die Box eingefroren, startet sie sich deshalb selbst.
        """
        def raus():
            from . import waechter
            # Under the watcher (the .exe since 15.11.2026) exit code 3 is the
            # restart; a successor started here as well would be a second booth.
            if getattr(sys, "frozen", False) and not waechter.watched():
                try:
                    from . import lifecycle
                    subprocess.Popen([sys.executable] + sys.argv[1:], **lifecycle.respawn_kwargs())
                except Exception:                # noqa: BLE001
                    log.exception("Neustart konnte nicht angestoßen werden")
            self.shutdown(3)                     # Starter-Skript startet neu
        threading.Timer(2.0, raus).start()
        return "Neustart in 2 s"

    def _when_idle(self, action):
        """Run ``action`` only while no guest run is open and nothing prints (R2-U2)."""
        from .fernsteuerung import nothing_running
        nothing_running(self)
        return action()

    def restart_on_purpose(self) -> str:
        """Admin "Neustart", remote ``restart_app``: exit 3 (the watcher restarts at once).

        A restart somebody asked for is no failure of the own window; a dead
        web view restarting the booth (window.py, ``on_stuck``) is, and goes
        straight to ``_restart_later``.
        """
        window_mod.ended_on_purpose(self.data)
        return self._restart_later()

    def quit_on_purpose(self, delay: float = 1.0) -> str:
        """Admin "Beenden", ``--quit``: exit 0, the watcher stays down, nothing restarts."""
        window_mod.ended_on_purpose(self.data)
        threading.Timer(delay, lambda: self.shutdown(0)).start()
        return f"Beenden in {delay:.0f} s"

    def _set_locked(self, value: bool) -> str:
        self.remote_locked = value
        return "gesperrt" if value else "entsperrt"

    def _druckkorrektur(self, index: int) -> dict:
        """Brightness/contrast/saturation of the printer at station ``index``, from the live settings.

        Read at each print rather than at start: these are the one printer
        setting Till adjusts in the evening, from afar, after a look at a
        test sheet — a restart in the middle of an event is not the price.
        """
        cfgs = printer_configs(self.store.local)
        return cfgs[index] if 0 <= index < len(cfgs) else {}

    def remote_local_config(self, payload: Dict[str, Any]) -> str:
        """Job ``set_local_config``: camera, printer and storage choices from the gallery.

        The gallery checked the list already; it is checked here again,
        because this is the side whose server, keys and paths are at stake.
        Takes effect after a restart, like a change in the booth's own admin.
        """
        from .config import clean_remote_local, nest_remote_local
        clean, refused = clean_remote_local(payload or {})
        if not clean:
            return "nichts übernommen" + (": " + "; ".join(refused) if refused else "")
        self.store.save_local(nest_remote_local(self.store.local, clean))
        log.info("Lokale Einstellungen aus der Ferne: %s", ", ".join(sorted(clean)))
        sofort = {k for k in clean if k.startswith("printers.")}
        answer = "übernommen: " + ", ".join(sorted(clean))
        if clean.keys() - sofort:
            answer += " (wirkt nach einem Neustart)"
        if sofort:
            answer += " (Druckkorrektur gilt ab dem nächsten Druck)"
        if refused:
            answer += "; abgelehnt: " + "; ".join(refused)
        return answer

    @property
    def locked(self) -> bool:
        return self.remote_locked or bool(self.profile["lock"].get("active")) or self.locked_by_schedule()

    def locked_by_schedule(self, jetzt: Optional[time_module.struct_time] = None) -> bool:
        """Liegt gerade eine geplante Pause?

        Gebucht ist von 19 bis 1 Uhr, aufgebaut wird um 14 — dazwischen soll
        niemand das Farbband leer drucken. Ein Fenster über Mitternacht ist
        deshalb der Normalfall und kein Sonderfall: ``from`` 19:00, ``until``
        01:00 heißt „abends bis nachts", nicht „nie".
        """
        plan = (self.profile.get("lock") or {}).get("schedule") or {}
        if not plan.get("enabled"):
            return False
        von, bis = str(plan.get("from") or ""), str(plan.get("until") or "")
        if not von and not bis:
            return False
        jetzt = jetzt or time_module.localtime()
        tage = [int(t) for t in (plan.get("days") or []) if str(t).isdigit()]
        # Ohne Tagesangabe gilt der Plan täglich.
        minute = jetzt.tm_hour * 60 + jetzt.tm_min

        def _minuten(wert: str, standard: int) -> int:
            try:
                stunde, _, rest = wert.partition(":")
                return int(stunde) * 60 + int(rest or 0)
            except ValueError:
                return standard

        start, ende = _minuten(von, 0), _minuten(bis, 24 * 60)
        ueber_mitternacht = ende <= start
        heute = jetzt.tm_wday
        # Bei einem Fenster über Mitternacht gehört die Nacht zum Vortag.
        tag_des_fensters = heute if (not ueber_mitternacht or minute >= start) else (heute - 1) % 7
        if tage and tag_des_fensters not in tage:
            return True                          # heute gar nicht gebucht
        offen = (start <= minute < ende) if not ueber_mitternacht else (minute >= start or minute < ende)
        return not offen

    def on_profile_changed(self) -> None:
        self.profile_rev += 1
        slug = self.profile["event"].get("slug", "")
        if slug != self._event_slug:
            log.info("Neues Event: %s — Druckzähler zurückgesetzt", slug or "(keins)")
            self._event_slug = slug
            self.printer.reset_counter()
        wanted = self._shot_settings()
        if wanted != self.camera.desired_settings:
            threading.Thread(target=self._apply_camera_settings, args=(wanted,), daemon=True).start()
        self.camera.set_preview_settings(self._preview_exposure())
        # Templates, style, texts or formats may have changed: the layout
        # pictures whose key changed are rendered again, between runs.
        if self._threads and getattr(self, "layoutbilder", None) is not None:
            self.layoutbilder.vorbereiten()

    #: The live frame last brightened for the screen: (seq, setting, bytes).
    #: Two clients (kiosk and admin) share one computation per frame.
    _screen_cache: tuple = (None, None, b"")
    _screen_lock = threading.Lock()

    def screen_frame(self, frame: bytes, seq: int) -> bytes:
        """A live frame as the screens get it: with ``flash.preview_gain`` applied while the flash is in use.

        Only the frames served to screens (``/api/live.mjpg``, ``/api/live.jpg``);
        the camera's frames for clips and every captured picture stay as they are.
        """
        fl = self.profile.get("flash") or {}
        if not fl.get("enabled"):
            return frame
        setting = "auto" if fl.get("preview_gain_auto") else fl.get("preview_gain") or 0
        if self.camera.preview_at_end:
            # ISO and shutter are at their limits and the frame is still too
            # dark: software is the only rung left, whatever the profile says.
            setting = "auto"
        if setting == 0:
            return frame
        with self._screen_lock:
            cseq, csetting, cdata = self._screen_cache
            if cseq == seq and csetting == setting and cdata:
                return cdata
        from .camera import exposure
        data = exposure.brighten_jpeg(frame, setting)
        with self._screen_lock:
            self._screen_cache = (seq, setting, data)
        return data

    def _shot_settings(self) -> dict:
        """The profile's exposure for the shot (``camera.iso/aperture/shutter/wb``); empty = the camera's."""
        cam = self.profile.get("camera") or {}
        return {k: cam[k] for k in ("iso", "aperture", "shutter", "wb") if cam.get(k)}

    def _preview_exposure(self) -> dict:
        """The live view's own exposure — only while the booth shoots with flash.

        With ``flash.enabled`` off nothing is switched at all: the camera's
        and the profile's values serve live view and shot alike.
        """
        if not (self.profile.get("flash") or {}).get("enabled"):
            return {}
        from .camera import exposure
        cam = self.profile.get("camera") or {}
        out = {k: str(cam.get(f"preview_{k}")) for k in ("iso", "aperture", "shutter") if cam.get(f"preview_{k}")}
        if out.get("iso", "").lower() == "auto":
            # The ladder's limits; a value the booth does not know falls back
            # to the default rather than to "no limit".
            iso_max = str(cam.get("preview_iso_max") or "")
            out["iso_max"] = iso_max if iso_max in exposure.ISO_MAX_CHOICES else exposure.ISO_MAX_DEFAULT
            if not out.get("shutter"):
                floor = str(cam.get("preview_shutter_min") or "")
                out["shutter_min"] = floor if floor in exposure.SHUTTER_MIN_CHOICES else exposure.SHUTTER_MIN_DEFAULT
        return out

    def _apply_camera_settings(self, wanted: dict) -> None:
        try:
            res = self.camera.apply_settings(wanted)
            log.info("Kamera-Einstellungen: gesetzt %s, nicht gesetzt %s", res.get("applied"), res.get("failed"))
        except Exception as e:
            log.warning("Kamera-Einstellungen fehlgeschlagen: %s", e)

    @property
    def profile(self) -> dict:
        return self.store.profile

    # ── Vorlagen, die hier entstanden sind ───────────────

    def push_template(self, name: str) -> None:
        """Eine an der Box gebaute Vorlage zum Hochladen vormerken.

        Ohne Galerie bleibt der Zettel einfach liegen — die Vorlage wirkt
        trotzdem, sie steht ja hier auf der Platte.
        """
        offen = [x for x in (self.store.local.get("template_push") or []) if x != name]
        self.store.save_local({"template_push": (offen + [name])[-20:]})

    def templates_to_push(self, limit: int = 5) -> List[dict]:
        """Was beim nächsten Sync mitgehen soll, mit Inhalt."""
        aus = []
        for name in (self.store.local.get("template_push") or [])[:limit]:
            p = self.assets_dir / str(name)
            if not p.is_file():
                continue
            try:
                aus.append({"file": p.name, "template": compose.load_template(p)})
            except Exception as exc:                 # noqa: BLE001
                log.warning("Vorlage %s nicht lesbar, wird nicht hochgeladen: %s", name, exc)
        return aus

    def templates_acked(self, namen: List[str]) -> None:
        offen = [x for x in (self.store.local.get("template_push") or []) if x not in set(namen or [])]
        self.store.save_local({"template_push": offen})

    # ── Vorlagen, Hintergründe, Looks, Startmedien ───────
    def templates(self) -> List[dict]:
        """Die Collagen-Vorlagen dieser Box, in der Reihenfolge der Auswahl.

        Drei Quellen: eigene Vorlagen als JSON-Datei, die mitgelieferten, und
        was ein Plugin beisteuert. Sind eigene hinterlegt, gelten nur die —
        sonst stünde neben der Kundenvorlage weiter unser Raster. Ohne eigene
        entscheidet "collage_builtins", welche der mitgelieferten erscheinen;
        leer heißt alle.
        """
        out = []
        for asset_file in self.profile.get("collage_templates") or []:
            p = self.assets_dir / str(asset_file)
            if p.suffix == ".json" and p.exists() and not _motion_file(p):
                try:
                    out.append(compose.load_template(p))
                except Exception as e:
                    log.warning("Vorlage %s fehlerhaft: %s", p.name, e)
        gewaehlt = [str(x) for x in (self.profile.get("collage_builtins") or [])]
        if self.profile.get("collage_exact"):
            # Exactly the ticked layouts (an event's "Layouts für dieses Event"):
            # own templates first, then the built-ins, in the order ticked; no
            # plugin adds to it. Nothing usable: the catalogue, never an empty choice.
            nach_id = {t["id"]: t for t in compose.BUILTIN_TEMPLATES}
            genau = out + [nach_id[i] for i in gewaehlt if i in nach_id]
            if genau:
                return self._plain_qr(genau)
            return [t for t in compose.BUILTIN_TEMPLATES if not t.get("plain")]
        for extra in plugins.safe_call(self.plugins, "booth_register_templates") or []:
            out.extend(extra or [])
        if out:
            return out
        if gewaehlt:
            nach_id = {t["id"]: t for t in compose.BUILTIN_TEMPLATES}
            gefiltert = [nach_id[i] for i in gewaehlt if i in nach_id]
            if gefiltert:
                return self._plain_qr(gefiltert)
        # "All" is the catalogue as it was: the plain layouts (15.11.2026)
        # come only where they were ticked.
        return [t for t in compose.BUILTIN_TEMPLATES if not t.get("plain")]

    def _plain_qr(self, vorlagen: List[dict]) -> List[dict]:
        """Show the hidden QR code of the plain layouts when ``collage_plain_qr`` is on."""
        if not self.profile.get("collage_plain_qr"):
            return vorlagen
        return [dict(t, qr=dict(t["qr"], hidden=False)) if t.get("plain") and isinstance(t.get("qr"), dict)
                else t for t in vorlagen]

    def template(self, tid: Optional[str]) -> Optional[dict]:
        return next((t for t in self.templates() if t.get("id") == tid), None)

    def _layouts_chosen(self) -> bool:
        """Did the operator choose layouts (exact list, own templates, ticked built-ins)?"""
        p = self.profile
        return bool(p.get("collage_exact") or p.get("collage_templates") or p.get("collage_builtins"))

    def photo_templates(self) -> List[dict]:
        """The layouts with exactly one photo, offered in photo mode (18.11.2026).

        Only from layouts the operator chose: the shipped catalogue as a whole
        is no choice, and a booth that never chose any keeps the plain photo.
        """
        if not self._layouts_chosen():
            return []
        # Only what was chosen: an own template, or a built-in that is ticked.
        # When the event's own templates are not on the booth yet, templates()
        # falls back to the catalogue; none of that may turn up here.
        builtin = {str(t["id"]) for t in compose.BUILTIN_TEMPLATES}
        ticked = {str(x) for x in (self.profile.get("collage_builtins") or [])}
        return [t for t in self.templates() if compose.template_info(t)["shots"] == 1
                and (str(t.get("id")) not in builtin or str(t.get("id")) in ticked)]

    def collage_choice(self) -> List[dict]:
        """The collage's layouts: those with two photos or more, else all (never empty)."""
        alle = self.templates()
        if not self.photo_templates():
            return alle
        mehr = [t for t in alle if compose.template_info(t)["shots"] >= 2]
        return mehr or alle

    @staticmethod
    def _format_of(tpl: dict) -> str:
        """The format nearest the layout's first slot: the shape the plain photo keeps."""
        ratio = float(compose.template_info(tpl)["slot_aspect"] or 1.0)
        return min(compose.ASPECTS, key=lambda k: abs(compose.ASPECTS[k] - ratio))

    # ── Frames for boomerang and video ("Rahmen") ────────
    def motion_frame_builtins(self) -> List[dict]:
        """The shipped frames (media/motion_frames.json), read once."""
        cached = getattr(self, "_motion_builtins", None)
        if cached is None:
            cached = motion_frames.builtins(Path(motion_frames.__file__).with_suffix(".json"))
            self._motion_builtins = cached
        return cached

    def own_motion_frames(self) -> List[Tuple[str, dict]]:
        """(file, frame) of every readable frame file among the media.

        Asked on every state poll while frames are on: kept until a JSON file
        in the media folder changes.
        """
        files = [p_ for p_ in sorted(self.assets_dir.glob("*.json")) if _motion_file(p_)]
        stamp = []
        for p_ in files:
            try:
                st = p_.stat()
                stamp.append((p_.name, st.st_mtime, st.st_size))
            except OSError:
                pass
        cached = getattr(self, "_own_motion", None)
        if cached and cached[0] == stamp:
            return list(cached[1])
        out = []
        for p_ in files:
            try:
                data = json.loads(p_.read_text(encoding="utf-8"))
                out.append((p_.name, motion_frames.check(data)[0]))
            except (OSError, ValueError) as exc:     # FrameError is a ValueError
                log.warning("Rahmen %s nicht nutzbar: %s", p_.name, exc)
        self._own_motion = (stamp, out)
        return list(out)

    def motion_frame_offer(self, mode: str, fmt: Optional[str] = None) -> dict:
        """What the set-up screen of ``mode`` offers: frames (id, frame), "Ohne Rahmen", default.

        Off (the default): no frames, and the run is exactly as before.
        ``fmt`` keeps only the frames of that format (the guest chose one).
        """
        cfg = self.profile.get(mode) or {}
        if mode not in FRAME_MODES or not cfg.get("frames"):
            return {"enabled": False, "frames": [], "none": True, "default": motion_frames.NONE_ID,
                    "guest_choice": False}
        builtins = {f["id"]: f for f in self.motion_frame_builtins()}
        own_files, ids, none = motion_frames.choice_ids(cfg, list(builtins))
        own = dict(self.own_motion_frames())
        frames = [(name, own[name]) for name in own_files if name in own]
        frames += [(i, builtins[i]) for i in ids]
        # A GIF takes the frames made for the boomerang (it follows its set-up).
        kind = "boomerang" if mode == "gif" else mode
        frames = [(i, f) for i, f in frames if kind in (f.get("modes") or motion_frames.MODES)
                  and (not fmt or f.get("format") == fmt)]
        if not frames:
            none = True                 # never an empty choice: without a frame is always possible
        offered = [i for i, _f in frames] + ([motion_frames.NONE_ID] if none else [])
        return {"enabled": True, "frames": frames, "none": none,
                "default": motion_frames.default_choice(cfg, offered),
                "guest_choice": cfg.get("frame_guest_choice", True) is not False}

    def pick_motion_frame(self, mode: str, wanted: Optional[str]) -> Tuple[str, Optional[dict]]:
        """(id, frame) for a run: the guest's pick when offered, else the default; never fails."""
        offer = self.motion_frame_offer(mode)
        if not offer["enabled"]:
            return "", None
        frames = dict(offer["frames"])
        choice = str(wanted or "") if offer["guest_choice"] else ""
        if choice not in frames and not (choice == motion_frames.NONE_ID and offer["none"]):
            choice = offer["default"]
        return (choice, frames[choice]) if choice in frames else ("", None)

    # ── Which layouts the guests see, chosen at the box ──
    def own_template_files(self) -> List[Tuple[str, dict]]:
        """(file, template) of every readable template file in the media folder."""
        out = []
        for p_ in sorted(self.assets_dir.glob("*.json")):
            if _motion_file(p_):
                continue                              # a frame for boomerang/video, not a print layout
            try:
                out.append((p_.name, compose.load_template(p_)))
            except Exception as exc:                  # noqa: BLE001 — a broken file does not block the rest
                log.warning("Vorlage %s unlesbar: %s", p_.name, exc)
        return out

    def layout_visibility(self) -> dict:
        """Who decides the layouts here, and whether the switches at the box may change them.

        The gallery builds the box's profile from its "Box-Profil" and lets the
        assigned event's "Layouts für dieses Event" win over it
        (ecosystem.profile_for_box); it sends that profile whole whenever its
        version for this box changes, and it replaces the box's own keys -
        unless the box holds its settings ("festhalten", profile_hold), then
        nothing from the gallery applies. ``event.layouts_fixed`` says the
        event set the layouts.
        """
        server = bool((self.store.local.get("server") or {}).get("url"))
        hold = bool(self.store.local.get("profile_hold"))
        event = self.profile.get("event") or {}
        source = "box" if not server else ("event" if event.get("layouts_fixed") else "gallery")
        return {"mode": "exact" if self.profile.get("collage_exact") else "auto", "source": source,
                "event": str(event.get("name") or ""), "hold": hold, "server": server,
                "locked": source == "event" and not hold}

    def layout_overview(self) -> dict:
        """Every layout the box knows, as the templates page shows its tiles."""
        angeboten = {str(t.get("id")) for t in self.templates()}
        aktiv = [str(x) for x in (self.profile.get("collage_templates") or [])]
        von_galerie = set(self.store.local.get("gallery_templates") or [])
        server = bool((self.store.local.get("server") or {}).get("url"))
        lb = getattr(self, "layoutbilder", None)
        bilder = lb.urls()["t"] if lb else {}
        layouts = []

        def kachel(kind: str, ident: str, tpl: dict, **extra) -> dict:
            info = compose.template_info(tpl)
            # The picture's address carries the cache key (everything that
            # changes the picture, the template's content first): the page
            # may keep it for good, and a changed template gets a new one.
            v = lb.schluessel("t", str(tpl.get("id") or ident), tpl) if lb else ""
            from urllib.parse import urlencode
            q = urlencode({"kind": kind, "id": ident, "v": v})
            return {"kind": kind, "id": str(tpl.get("id") or ident), "name": str(tpl.get("name") or ident),
                    "shots": info["shots"], "orientation": info["orientation"], "paper": info["paper"],
                    "plain": bool(tpl.get("plain")), "template": tpl, "picture_key": v,
                    "picture": "/api/admin/templates/picture?" + q,
                    "thumb": bilder.get(str(tpl.get("id") or "")) or "", **extra}

        for datei, tpl in self.own_template_files():
            galerie = server and datei in von_galerie
            layouts.append(kachel("own", datei, tpl, key="t:" + datei, file=datei,
                                  visible=datei in aktiv and str(tpl.get("id")) in angeboten,
                                  origin="gallery" if galerie else "box", deletable=not galerie))
        eigene_ids = {x["id"] for x in layouts if x["visible"]}
        for tpl in compose.BUILTIN_TEMPLATES:
            ident = str(tpl["id"])
            layouts.append(kachel("builtin", ident, tpl, key="b:" + ident, file="",
                                  visible=ident in angeboten and ident not in eigene_ids,
                                  origin="builtin", deletable=False))
        return {"layouts": layouts, "visibility": self.layout_visibility()}

    def set_layout_visibility(self, dateien: List[str], ids: List[str]) -> dict:
        """Exactly these layouts for the guests: own template files and built-in ids, in this order."""
        vis = self.layout_visibility()
        if vis["locked"]:
            raise ValueError("locked:Die Layouts gibt das Event „%s“ vor (Galerie → Event → Layouts). "
                             "An der Box ändern geht nur mit „Einstellungen dieser Box festhalten“."
                             % (vis["event"] or "?"))
        bekannt = {d for d, _t in self.own_template_files()}
        gebaut = {str(t["id"]) for t in compose.BUILTIN_TEMPLATES}
        dateien = list(dict.fromkeys(str(d) for d in dateien if str(d) in bekannt))
        ids = list(dict.fromkeys(str(i) for i in ids if str(i) in gebaut))
        if not dateien and not ids:
            raise ValueError("empty:Mindestens ein Layout muss sichtbar bleiben.")
        self._apply_profile({**json.loads(json.dumps(self.profile)), "collage_exact": True,
                             "collage_templates": dateien, "collage_builtins": ids})
        aus = self.layout_overview()
        if vis["server"] and not vis["hold"]:
            aus["warning"] = ("Gilt ab sofort. Die Galerie ersetzt diese Auswahl, sobald sie dieser Box ein neues "
                              "Profil schickt (wenn dort an der Box oder am Event etwas geändert wird). Damit sie "
                              "bleibt: „Einstellungen dieser Box festhalten“.")
        return aus

    # ── Which frames the guests see, chosen at the box ───
    def frame_visibility(self) -> dict:
        """Like ``layout_visibility``, for the frames: ``event.frames_fixed`` locks the switches."""
        vis = self.layout_visibility()
        event = self.profile.get("event") or {}
        source = "box" if not vis["server"] else ("event" if event.get("frames_fixed") else "gallery")
        return {**vis, "source": source, "locked": source == "event" and not vis["hold"], "mode": ""}

    def frame_overview(self) -> dict:
        """Every frame the box knows, per mode, as the templates page shows its tiles."""
        from urllib.parse import urlencode
        builtins = self.motion_frame_builtins()
        own = self.own_motion_frames()
        modes = {}
        for mode in motion_frames.MODES:
            cfg = self.profile.get(mode) or {}
            offer = self.motion_frame_offer(mode)
            shown = {i for i, _f in offer["frames"]} if offer["enabled"] else set()
            tiles = []
            for kind, ident, frame in [("own", d, f) for d, f in own] + [("builtin", f["id"], f) for f in builtins]:
                if mode not in (frame.get("modes") or motion_frames.MODES):
                    continue
                key = self.layoutbilder.schluessel("m", ident, frame)
                tiles.append({"kind": kind, "id": ident, "name": frame.get("name") or ident,
                              "format": frame["format"], "visible": ident in shown,
                              "canvas": list(motion_frames.FORMATS[frame["format"]]),
                              "picture": "/api/admin/frames/picture?" + urlencode({"id": ident, "v": key})})
            modes[mode] = {"enabled": bool(cfg.get("frames")), "none": cfg.get("frame_none", True) is not False,
                           "frames": tiles}
        return {"modes": modes, "visibility": self.frame_visibility()}

    def set_frame_visibility(self, mode: str, ids: List[str], none: bool) -> dict:
        """Exactly these frames (own files and built-in ids) for ``mode``; switches frames on."""
        vis = self.frame_visibility()
        if vis["locked"]:
            raise ValueError("locked:Die Rahmen gibt das Event „%s“ vor (Galerie → Event → Rahmen für Boomerang "
                             "& Video). An der Box ändern geht nur mit „Einstellungen dieser Box festhalten“."
                             % (vis["event"] or "?"))
        if mode not in motion_frames.MODES:
            raise ValueError("mode:Nur boomerang oder video.")
        own = [d for d, _f in self.own_motion_frames()]
        builtin = [f["id"] for f in self.motion_frame_builtins()]
        wanted = list(dict.fromkeys(str(i) for i in ids))
        dateien = [i for i in wanted if i in own]
        gebaut = [i for i in wanted if i in builtin]
        if not dateien and not gebaut and not none:
            raise ValueError("empty:Mindestens ein Rahmen oder „Ohne Rahmen“ muss sichtbar bleiben.")
        profile = json.loads(json.dumps(self.profile))
        cfg = profile.setdefault(mode, {})
        if not dateien and not gebaut:
            cfg.update({"frames": False, "frame_none": True})        # only "Ohne Rahmen" = no frames
        else:
            cfg.update({"frames": True, "frame_templates": dateien, "frame_builtins": gebaut, "frame_none": bool(none)})
        self._apply_profile(profile)
        aus = self.frame_overview()
        if vis["server"] and not vis["hold"]:
            aus["warning"] = ("Gilt ab sofort. Die Galerie ersetzt diese Auswahl, sobald sie dieser Box ein neues "
                              "Profil schickt. Damit sie bleibt: „Einstellungen dieser Box festhalten“.")
        return aus

    def _existing_assets(self, names) -> List[str]:
        return [str(b) for b in (names or []) if (self.assets_dir / str(b)).exists()]

    def backgrounds(self) -> List[str]:
        return self._existing_assets(self.profile.get("backgrounds"))

    def start_media(self) -> List[str]:
        """Start media the kiosk can show: files on this booth and the built-in backgrounds."""
        from .config import builtin_start_id
        out = []
        for m in self.profile["start"].get("media") or []:
            if builtin_start_id(m) or (str(m).lower().endswith(MEDIA_EXT) and (self.assets_dir / str(m)).exists()):
                out.append(str(m))
        return out

    def phase_media(self) -> List[str]:
        """Files of ``phase_media`` that are on this booth; a missing one is no medium."""
        names = {str((c or {}).get("media") or "").strip()
                 for c in (self.profile.get("phase_media") or {}).values() if isinstance(c, dict)}
        return sorted(m for m in self._existing_assets(n for n in names if n) if m.lower().endswith(MEDIA_EXT))

    # ── LUTs (.cube) ──────────────────────────────────────
    def lut_folders(self) -> List[Path]:
        """Wo LUTs herkommen: mitgeliefert, lokal abgelegt, aus der Galerie geladen."""
        return [luts.builtin_folder(), self.luts_dir, self.assets_dir]

    def lut_looks(self) -> Dict[str, "looks.LookFn"]:
        """``{"lut-golden-warm": fn}`` für alle LUTs, die diese Box hat."""
        cfg = self.profile.get("luts") or {}
        if not cfg.get("enabled", True):
            return {}
        strength = max(0.0, min(1.0, float(cfg.get("strength", 1.0) or 1.0)))
        show = [luts.canonical_key(luts.slugify(str(s)).removeprefix("lut-")) for s in (cfg.get("show") or [])]
        found: Dict[str, "looks.LookFn"] = {}
        for folder in self.lut_folders():
            try:
                found.update(luts.looks_from_folder(folder, strength))
            except OSError as e:
                log.warning("LUT-Ordner %s nicht lesbar: %s", folder, e)
        if show:
            found = {k: v for k, v in found.items() if k.removeprefix("lut-") in show}
        return found

    def lut_catalog(self) -> List[dict]:
        """Alle gefundenen LUTs für die Verwaltung — auch die gerade nicht aktiven."""
        out, seen = [], set()
        for folder in self.lut_folders():
            for key, lut in luts.load_folder(folder).items():
                if key in seen:
                    continue
                seen.add(key)
                out.append({"id": f"lut-{key}", "label": _lut_label(key), "size": lut.size,
                            "source": folder.name})
        return sorted(out, key=lambda d: d["label"].lower())

    def baked_lut(self) -> Optional["luts.Lut"]:
        """LUT, die fest auf jedes Bild kommt (bevor der Gast einen Filter wählt)."""
        cfg = self.profile.get("luts") or {}
        raw = str(cfg.get("baked") or "").strip()
        if not raw:                       # slugify("") ergibt "lut" — das wäre eine Suche nach nichts
            return None
        wanted = luts.canonical_key(luts.slugify(raw).removeprefix("lut-"))
        if not wanted:
            return None
        for folder in self.lut_folders():
            lut = luts.load_folder(folder).get(wanted)
            if lut:
                return lut
        log.warning("Feste LUT %s nicht gefunden", wanted)
        return None

    def look_names(self) -> List[str]:
        """Welche Filter der Gast sieht.

        Drei Schalter greifen hier ineinander: die Liste im Profil, eine
        Obergrenze („looks_max") und die Betriebsart. Bei „auto" und „off"
        bekommt der Gast gar keine Auswahl — dann ist die Liste einelementig,
        und die Oberfläche blendet den Streifen von selbst aus.
        """
        modus = str(self.profile.get("looks_mode") or "ask")
        if modus == "off":
            return ["original"]
        if modus == "auto":
            fest = self.canonical_look(str(self.profile.get("looks_auto") or "original"))
            return [fest]
        available = self.lut_looks()
        names = []
        for n in map(self.canonical_look, self.profile.get("looks", [])):
            if n not in names and (n in looks.LOOKS or n in self._extra_looks or n in available):
                names.append(n)
        names += [n for n in available if n not in names]
        names = names or ["original"]
        grenze = int(self.profile.get("looks_max") or 0)
        if grenze > 0:
            # „original" bleibt immer dabei: Der Gast muss den Filter wieder
            # loswerden können, den er eben ausprobiert hat.
            behalten = [n for n in names[:grenze]]
            if "original" in names and "original" not in behalten:
                behalten = ["original"] + behalten[:max(0, grenze - 1)]
            names = behalten
        return names

    @staticmethod
    def canonical_look(name: str) -> str:
        """Current id of a look; built-in LUTs renamed in 0.3 keep their old ids as aliases."""
        name = str(name or "")
        return luts.canonical_key(name) if name.startswith("lut-") else name

    def look_label(self, name: str) -> str:
        name = self.canonical_look(name)
        if name.startswith("lut-"):
            return _lut_label(name.removeprefix("lut-"))
        return looks.LABELS.get(name, name.replace("-", " ").title())

    def apply_look(self, name: str, img: Image.Image) -> Image.Image:
        name = self.canonical_look(name)
        fn = self._extra_looks.get(name) or (self.lut_looks().get(name) if name.startswith("lut-") else None)
        return fn(img) if fn else looks.apply(name, img)

    # ── Greenscreen ───────────────────────────────────────
    def chroma_backgrounds(self) -> List[str]:
        """Hintergründe für den Greenscreen: Asset-Dateien oder Farben (#rrggbb)."""
        out = []
        for b in (self.profile.get("chroma") or {}).get("backgrounds") or []:
            b = str(b)
            if b.startswith("#") or (self.assets_dir / b).exists():
                out.append(b)
        return out

    def chroma_active(self) -> bool:
        cfg = self.profile.get("chroma") or {}
        return bool(cfg.get("enabled")) and bool(self.chroma_backgrounds())

    def _chroma_background(self, s: Session) -> Optional[str]:
        """Welcher Hintergrund gilt: der vom Gast gewählte, sonst der erste."""
        available = self.chroma_backgrounds()
        if not available:
            return None
        cfg = self.profile.get("chroma") or {}
        wanted = s.chroma_background if cfg.get("guest_choice", True) else ""
        return wanted if wanted in available else available[0]

    def _apply_chroma(self, img: Image.Image, s: Session) -> Image.Image:
        if not self.chroma_active():
            return img
        name = self._chroma_background(s)
        if not name:
            return img
        cfg = self.profile.get("chroma") or {}
        background: object = name
        if not name.startswith("#"):
            path = self.assets_dir / name
            try:
                background = Image.open(path).convert("RGB")
            except (OSError, ValueError) as e:
                log.warning("Greenscreen-Hintergrund %s nicht lesbar: %s", name, e)
                return img
        try:
            return chroma.composite(img, background, fit=str(cfg.get("fit", "cover")),
                                    color=str(cfg.get("color", "auto")),
                                    tolerance=float(cfg.get("tolerance", 0.35)),
                                    softness=float(cfg.get("softness", 0.08)),
                                    spill=float(cfg.get("spill", 0.4)))
        except Exception as e:                       # lieber das Original als kein Foto
            log.warning("Greenscreen fehlgeschlagen: %s", e)
            return img
        finally:
            if isinstance(background, Image.Image):
                background.close()

    # ── Druck ─────────────────────────────────────────────
    def sheet_for(self, source: Path, uid: str = "") -> Path:
        """Aus der fertigen Aufnahme das Blatt machen, das in den Drucker geht.

        Bei Standardformat ohne Rand ist das ein reiner Durchreicher — dann wird
        nichts neu berechnet. Erst Streifen, Ränder oder ein anderes Papier
        erzeugen eine eigene Datei.
        """
        from .printing.layout import Layout, compose_sheet

        layout = Layout.from_profile(self.profile.get("print_layout"))
        einfach = (layout.per_sheet == 1 and layout.margin_mm == 0
                   and layout.paper == "10x15" and layout.fit == "cover")
        blatt = source
        if not einfach:
            target = source.with_name(f"{source.stem}-sheet.jpg")
            try:
                blatt = compose_sheet(source, layout, target)
            except Exception as e:               # noqa: BLE001 — lieber unpassend drucken als gar nicht
                log.warning("Blatt ließ sich nicht aufbauen (%s) — drucke die Aufnahme direkt", e)
                blatt = source
        return self._nachschaerfen(blatt)

    #: Wie viel Nachschärfen je Stufe: (Radius, Stärke, Schwelle) für Pillow.
    SCHAERFE = {"light": (1.2, 60, 3), "medium": (1.4, 110, 3), "strong": (1.6, 170, 2)}

    def _nachschaerfen(self, blatt: Path) -> Path:
        """Ein Hauch Schärfe vor dem Druck.

        Thermosublimation weicht Kanten auf — was am Bildschirm scharf aussieht,
        wirkt auf dem Abzug weich. dslrBooth nennt das „Druckschärfen" und hat
        es standardmäßig an. Zu viel davon sieht nach Handykamera aus, deshalb
        drei Stufen und „aus".

        Schlägt es fehl, wird ungeschärft gedruckt: Ein weiches Bild ist besser
        als keins.
        """
        stufe = str((self.profile.get("print") or {}).get("sharpen") or "light")
        werte = self.SCHAERFE.get(stufe)
        if not werte or not blatt or not Path(blatt).exists():
            return blatt
        from PIL import ImageFilter
        radius, staerke, schwelle = werte
        ziel = Path(blatt).with_name(f"{Path(blatt).stem}-scharf.jpg")
        try:
            with Image.open(blatt) as im:
                scharf = im.convert("RGB").filter(
                    ImageFilter.UnsharpMask(radius=radius, percent=staerke, threshold=schwelle))
                scharf.save(ziel, "JPEG", quality=95, subsampling=0)
            return ziel
        except Exception as e:                   # noqa: BLE001
            log.warning("Nachschärfen übersprungen: %s", e)
            return blatt

    # ── Zugang ────────────────────────────────────────────
    def current_pin(self) -> str:
        """Die geltende PIN: aus dem Profil, sonst die an der Box eingestellte.

        So kann der Betreiber sie aus der Ferne setzen, ohne dass eine Box ohne
        Galerie-Kontakt plötzlich ohne PIN dasteht.
        """
        return str((self.profile.get("access") or {}).get("pin") or
                   self.store.local.get("admin_pin") or "")

    def factory_pin_active(self) -> bool:
        """True while the PIN in force is the one printed in every manual."""
        from .config import FACTORY_PIN
        return self.current_pin() == FACTORY_PIN

    @staticmethod
    def _pin_fingerprint(pin: str) -> str:
        # local.json holds the booth PIN in clear anyway; this only has to
        # recognise the gallery's PIN again, not keep it secret.
        import hashlib
        return hashlib.sha256(("photobox-pin:" + str(pin)).encode()).hexdigest()

    def set_admin_pin(self, new_pin: str) -> None:
        """Store a new booth PIN (callers check it with weak_pin_reason first).

        A PIN that came in through the profile would keep winning over the
        local one (``current_pin``), so the operator's change would do nothing.
        It is dropped here, and its fingerprint is kept: the gallery's next
        profile, which still carries that same PIN, does not bring it back
        (``_keep_local_pin``). A different PIN set in the gallery later wins.
        """
        new_pin = str(new_pin)
        self.store.save_local({"admin_pin": new_pin})
        profile_pin = str((self.profile.get("access") or {}).get("pin") or "")
        if profile_pin and profile_pin != new_pin:
            self.store.save_local({"pin_overridden": self._pin_fingerprint(profile_pin)})
            profile = json.loads(json.dumps(self.profile))
            profile.setdefault("access", {})["pin"] = ""
            if self.store.apply_profile(profile):
                self.on_profile_changed()
            else:                                    # keep the promise even if the store refuses
                self.profile.setdefault("access", {})["pin"] = ""
        log.info("Admin-PIN an der Box geändert")

    def _keep_local_pin(self, profile: dict) -> dict:
        """A profile from the gallery never erases or resets the PIN set at the booth.

        Empty means "the booth's PIN applies" (``current_pin``). A masked or
        otherwise non-numeric value (••••) is never a PIN: it is read as empty.
        The PIN the operator replaced at the booth, arriving again unchanged,
        is read as empty too. Only a new PIN typed in the gallery replaces the
        booth's.
        """
        access = profile.get("access") if isinstance(profile, dict) else None
        if not isinstance(access, dict) or not access.get("pin"):
            return profile
        pin = str(access.get("pin"))
        replaced = str(self.store.local.get("pin_overridden") or "")
        if pin.isdigit() and pin.isascii() and not (replaced and self._pin_fingerprint(pin) == replaced):
            return profile
        profile = json.loads(json.dumps(profile))
        profile["access"]["pin"] = ""
        return profile

    def may_lock_on_booth(self) -> bool:
        return bool((self.profile.get("access") or {}).get("lock_on_booth", True))

    def set_locked(self, locked: bool) -> None:
        """Sperre an der Box umlegen. Die Galerie erfährt es beim nächsten Kontakt."""
        profile = json.loads(json.dumps(self.profile))
        profile.setdefault("lock", {})["active"] = bool(locked)
        if self.store.apply_profile(profile):
            self.on_profile_changed()

    # ── Datensparsamer Betrieb ────────────────────────────
    @property
    def ephemeral(self) -> bool:
        """Kita-/Schulmodus: drucken, löschen, fertig.

        Wird an den Stellen geprüft, die Daten *herausgeben* — nicht nur dort,
        wo die Oberfläche Knöpfe zeichnet. Ein ausgeblendeter Knopf ist kein
        Datenschutz.
        """
        return str((self.profile.get("privacy") or {}).get("mode", "normal")) == "print_only"

    def _privacy(self) -> dict:
        return self.profile.get("privacy") or {}

    # Video und Boomerang lassen sich nicht drucken. Im Kita-Modus gäbe es sie
    # nur, damit der Gast am Ende mit leeren Händen dasteht — also gar nicht.
    PRINTABLE_MODES = ("photo", "collage", "gif")

    def printable_modes(self) -> List[str]:
        modes = [m for m in self.profile.get("modes", []) if m in self.PRINTABLE_MODES]
        return modes or ["photo"]

    # ── Backup onto a USB stick ────────────────────────────
    usb_job = None
    _usb_seen = (0.0, [])

    def usb_drives(self) -> List[Dict[str, Any]]:
        """The sticks plugged in, looked up at most every three seconds."""
        at, known = self._usb_seen
        if time.monotonic() - at > 3 or not at:
            known = usb_backup.drives()
            self._usb_seen = (time.monotonic(), known)
        return known

    def usb_event_slug(self) -> str:
        return str((self.profile.get("event") or {}).get("slug") or "") or "ohne-event"

    def _usb_cfg(self) -> Dict[str, Any]:
        return ((self.store.local.get("storage") or {}).get("usb") or {})

    def _usb_target(self) -> Optional[Dict[str, Any]]:
        """Where the backup goes: the folder named in the settings if it is
        there, otherwise the first confirmed stick, otherwise the first stick
        the system shows (which then only gets the question, no pictures)."""
        named = str(self._usb_cfg().get("path") or "").strip()
        if named and Path(named).is_dir():
            return {"path": named, "label": named, "trusted": True}
        drives = [dict(d, trusted=self.usb_trusted(d["path"])) for d in self.usb_drives()]
        trusted = [d for d in drives if d["trusted"]]
        return (trusted or drives or [None])[0]

    #: Said when a stick is plugged in that nobody confirmed at the box (S-7).
    USB_UNTRUSTED = ("Neuer USB-Stick erkannt – bitte in der Verwaltung an der Box mit der PIN bestätigen. "
                     "Bis dahin wird nichts darauf gesichert.")

    def usb_trusted(self, path: str) -> bool:
        """Was this stick confirmed at the box? The id on the stick decides, not its label."""
        found = usb_backup.stick_id(Path(path))
        return bool(found) and any(found == str(t.get("id")) for t in (self._usb_cfg().get("trusted") or [])
                                   if isinstance(t, dict))

    def usb_trust(self, path: str) -> Dict[str, Any]:
        """Confirm a stick (admin with PIN, at the box): write an id onto it and remember it."""
        drive = next((d for d in self.usb_drives() if d["path"] == path), None)
        if drive is None:
            raise ValueError("Dieser USB-Stick steckt nicht in der Box.")
        new_id = usb_backup.mark_stick(Path(drive["path"]))
        storage = dict(self.store.local.get("storage") or {})
        usb = dict(storage.get("usb") or {})
        known = [t for t in (usb.get("trusted") or []) if isinstance(t, dict)][-19:]
        usb["trusted"] = known + [{"id": new_id, "label": str(drive.get("label") or "")[:60],
                                   "since": time.strftime("%Y-%m-%d %H:%M")}]
        storage["usb"] = usb
        self.store.save_local({"storage": storage})
        self._usb_seen = (0.0, [])
        self.usb_error = ""
        self.usb_mark_dirty()
        log.info("USB-Stick „%s“ an der Box für Sicherungen freigegeben", drive.get("label"))
        return {"path": drive["path"], "label": drive.get("label"), "trusted": True}

    def usb_skip(self, uid: str) -> bool:
        """Does this run stay off the stick? Withheld — or not finished yet.

        A run has no entry in the library until it is finished, and only then
        is it on record whether the guest chose "print only". Copied earlier,
        the shots of exactly those guests reached the stick.
        """
        if self.library.withheld(uid):
            return True
        if self.library.get(uid) is not None:
            return False
        # Not in the library: a run of an earlier evening is backed up all the
        # same; one that is being taken right now waits until it is finished.
        return uid in self.sessions

    def usb_start(self, scope: str, drive: str = "") -> Dict[str, Any]:
        self._usb_seen = (0.0, [])
        target = self._usb_target()
        if drive:
            target = next((d for d in self.usb_drives() if d["path"] == drive), None)
            if target is None:
                raise ValueError("Dieser USB-Stick steckt nicht mehr in der Box.")
        if target is None:
            raise ValueError("Es steckt kein USB-Stick in der Box.")
        if not (target.get("trusted") or self.usb_trusted(target["path"])):
            raise ValueError(self.USB_UNTRUSTED)
        if self.usb_job and self.usb_job.status()["state"] == "running":
            raise ValueError("Die Sicherung läuft schon.")
        wanted = [self.usb_event_slug()] if scope == "event" else None
        there = usb_backup.events(self.sessions_dir, self.usb_skip)
        if not any(wanted is None or e["slug"] in wanted for e in there):
            # Before the first picture of the day this is the normal state,
            # not a failure — and no reason to write to the stick.
            raise LookupError("Es gibt noch keine Aufnahmen zum Sichern.")
        self.usb_job = usb_backup.start(self.sessions_dir, Path(target["path"]), wanted,
                                        skip=self.usb_skip, retract=self.library.withheld,
                                        originals=self._usb_cfg().get("originals") is not False)
        self._usb_job_read = False
        log.info("USB-Sicherung gestartet: %s auf %s", scope, target["label"])
        return self.usb_job.status()

    # All day, by itself. The loop calls ``usb_auto_tick`` every few seconds;
    # a run that was just saved, a stick that was just plugged in, or ten
    # quiet minutes start an incremental backup of the running event.
    USB_AUTO_EVERY = 600.0
    _usb_dirty = True                       # nothing has reached a stick yet
    _usb_auto_stick = ""
    _usb_auto_at = 0.0
    _usb_job_read = True
    usb_last_ok = 0.0
    usb_error = ""

    def usb_mark_dirty(self) -> None:
        self._usb_dirty = True

    def _usb_read_job(self) -> None:
        """Take note of how the last backup ended (once)."""
        if self._usb_job_read or not self.usb_job:
            return
        st = self.usb_job.status()
        if st["state"] == "running":
            return
        self._usb_job_read = True
        if st["state"] == "done":
            self.usb_last_ok, self.usb_error = time.time(), ""
        else:
            self.usb_error = st.get("message") or "Die Sicherung ist fehlgeschlagen."
            self._usb_dirty = True          # try again at the next occasion
            log.warning("USB-Sicherung: %s", self.usb_error)

    def usb_auto_tick(self) -> bool:
        """One look by the loop. True when a backup was started."""
        self._usb_read_job()
        if not self._usb_cfg().get("enabled"):
            return False
        if str(self._usb_cfg().get("path") or "").strip():
            # A typed folder is served file by file by the sync agent
            # (storage target "usb"); copying by run as well would double it.
            return False
        if self.usb_job and self.usb_job.status()["state"] == "running":
            return False
        self._usb_seen = (0.0, [])
        target = self._usb_target()
        if target is None:
            self._usb_auto_stick = ""        # the next stick starts from scratch
            return False
        if not target.get("trusted"):
            # A stranger's stick at the venue gets nothing (S-7); the admin
            # page shows the question and the confirm button.
            if self.usb_error != self.USB_UNTRUSTED:
                log.warning("USB-Stick „%s“ ist nicht freigegeben – keine Sicherung", target.get("label"))
            self.usb_error = self.USB_UNTRUSTED
            self._usb_auto_stick = ""
            return False
        if self.usb_error == self.USB_UNTRUSTED:
            self.usb_error = ""
        new_stick = target["path"] != self._usb_auto_stick
        due = time.monotonic() - self._usb_auto_at > self.USB_AUTO_EVERY
        failed_recently = bool(self.usb_error) and time.monotonic() - self._usb_auto_at < 60
        if failed_recently or not (self._usb_dirty or new_stick or due):
            return False
        self._usb_dirty = False
        self._usb_auto_stick = target["path"]
        self._usb_auto_at = time.monotonic()
        # A reason to look is no reason to copy. A stick that drops out of the
        # drive list for one look (busy, being flushed) came back as a "new"
        # stick, and every such look started a full pass and logged it.
        try:
            if not usb_backup.has_work(self.sessions_dir, Path(target["path"]), [self.usb_event_slug()],
                                       skip=self.usb_skip, retract=self.library.withheld,
                                       originals=self._usb_cfg().get("originals") is not False):
                self.usb_error = ""          # the stick holds everything: an older failure is over
                return False
        except OSError as e:
            log.debug("USB-Sicherung: Stick nicht lesbar (%s)", e)
            return False
        try:
            self.usb_start("event", target["path"] if not self._usb_cfg().get("path") else "")
        except LookupError:
            self._usb_auto_stick = ""        # look again once there is a picture
            return False
        except ValueError:
            return False
        return True

    def _usb_loop(self) -> None:
        while True:
            time.sleep(15)
            try:
                self.usb_auto_tick()
            except Exception:                # noqa: BLE001 — the loop must outlive the evening
                log.exception("USB-Sicherung im Hintergrund")

    def run_print_only(self, run) -> bool:
        """Data-minimal for this run: the whole booth is, or the guest chose "Nur drucken".

        ``run`` is a Session or a run id. After a restart the run is gone from
        memory; the library row still knows which option was picked.
        """
        if self.ephemeral:
            return True
        if hasattr(run, "print_only"):
            return bool(run.print_only)
        uid = str(run or "")
        s = self.sessions.get(uid)
        if s is not None:
            return bool(s.print_only)
        return bool(uid) and self.library.is_print_only(uid)

    # ── Einwilligung je Durchgang ─────────────────────────
    def mode_prints(self, mode: str) -> bool:
        """Does a run of this mode get a print sheet at all?

        Photo, collage and GIF always do (a GIF prints its stills); a
        boomerang only with ``boomerang.print``; a video never. Where this is
        False, nothing may offer to print the run: no "Nur drucken" (the guest
        would be left with neither a print nor a gallery link) and no print
        button (finalize leaves ``print_file`` empty).
        """
        if mode in ("photo", "collage", "gif"):
            return True
        return mode == "boomerang" and self.boomerang_prints()

    def consent_options(self, mode: Optional[str] = None) -> List[str]:
        """The buttons of the "Wohin mit eurem Foto?" screen, or [] for no screen.

        "Nur drucken" only while printing actually works, and with ``mode``
        only for a mode that has a print sheet (18.11.2026: a video offered
        "Nur drucken"); a booth that is data-minimal as a whole has nothing
        to choose. One option is no choice, so the step is skipped then.
        """
        cfg = self.profile.get("consent") or {}
        if not cfg.get("enabled") or self.ephemeral:
            return []
        options = ["gallery"]
        printing = bool(self.profile["print"].get("enabled")) and self.printer.backend is not None
        if cfg.get("print_only", True) and printing and (mode is None or self.mode_prints(mode)):
            options.append("print")
        return options if len(options) >= 2 else []

    def consent_notice_only(self, mode: Optional[str] = None) -> bool:
        """The step is on, but there is nothing to choose (no printer, "Nur drucken" off,
        or a mode without a print sheet).

        The screen is skipped then; the note must not go with it. The kiosk
        shows it on the set-up screen and the run records "notice".
        """
        cfg = self.profile.get("consent") or {}
        return bool(cfg.get("enabled")) and not self.ephemeral and not self.consent_options(mode)

    def consent_note(self, lang: str) -> str:
        """The note under the buttons, exactly as the guest reads it."""
        cfg = self.profile.get("consent") or {}
        notes = cfg.get("note") or {}
        text = str(notes.get(lang) or notes.get("de") or "")
        try:
            days = str(int(cfg.get("days") or 0) or "")
        except (TypeError, ValueError):
            days = ""
        return text.replace("{tage}", days).replace("{days}", days)

    def consent_record(self, option: str, lang: str) -> Dict[str, Any]:
        """What is kept about a choice: option, which text, when. Nothing about the person."""
        import hashlib
        text = self.consent_note(lang)
        return {"option": option, "lang": lang,
                "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                "text_version": int(self.profile.get("version", 0) or 0),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def _schedule_wipe(self, uid: str) -> None:
        """Sicherheitsnetz: Auch wenn der Druck scheitert, ist nach kurzer Zeit alles weg."""
        seconds = max(10, int(self._privacy().get("delete_seconds", 120) or 120))
        # „Nochmal" setzt ``finalized`` zurück, also läuft finalize ein zweites
        # Mal als „erstes Mal" — und legte bisher einen zweiten Zeitgeber an,
        # ohne den ersten abzubrechen. Der alte feuerte dann mitten im neuen
        # Durchgang und nahm einem gerade eingereihten Druck die Datei weg.
        alt = self._wipe_timers.pop(uid, None)
        if alt is not None:
            alt.cancel()
        idle = self._idle_timers.pop(uid, None)          # finished now: this timer takes over
        if idle is not None:
            idle.cancel()
        timer = threading.Timer(seconds, lambda: self._wipe(uid, reason="Zeitablauf"))
        timer.daemon = True
        timer.start()
        self._wipe_timers[uid] = timer

    def _arm_idle_wipe(self, s: Session) -> None:
        """Data-minimal mode: a run nobody finishes is removed after the delete period.

        Measured from the last activity of this run, so a guest between two
        shots is not hurried, but a guest who walked away after two of four
        leaves nothing behind — also after the last guest of the evening, when
        no new run would ever trigger _gc_sessions. Once a run has been
        finalized, _schedule_wipe is in charge and this timer stays off.
        """
        if not self.run_print_only(s) or s.abgerechnet or s.deleted:
            return
        seconds = max(_IDLE_FLOOR_SECONDS, int(self._privacy().get("delete_seconds", 120) or 120))
        old = self._idle_timers.pop(s.uid, None)
        if old is not None:
            old.cancel()
        timer = threading.Timer(seconds, lambda: None)
        timer.function = lambda: self._wipe_unfinished(s.uid, timer)
        timer.daemon = True
        self._idle_timers[s.uid] = timer
        timer.start()

    def _wipe_unfinished(self, uid: str, timer: threading.Timer) -> None:
        s = self.sessions.get(uid)
        if s is None:
            return
        # Under the run's lock: a shot or finalize in progress finishes first
        # and re-arms or ends this timer; only the current timer may delete.
        with s.lock:
            if self._idle_timers.get(uid) is not timer:
                return
            self._idle_timers.pop(uid, None)
            if s.abgerechnet or s.deleted:
                return
            self._wipe(uid, reason="nicht abgeschlossen")

    def _clear_ephemeral_leftovers(self) -> None:
        """At start: remove every run folder a previous run left in data-minimal mode.

        A power cut takes the timers with it. Only folders carrying the
        marker are touched — a normal booth's runs stay where they are.
        """
        try:
            markers = list(self.sessions_dir.rglob(_EPHEMERAL_MARKER))
        except OSError as e:
            log.warning("Datensparsamer Betrieb: Reste nicht gesucht: %s", e)
            return
        for marker in markers:
            folder = marker.parent
            uid = folder.name
            try:
                if self.library.get(uid):
                    self.delete(uid)
                if folder.exists():
                    shutil.rmtree(folder, ignore_errors=True)
                message = f"Datensparsamer Betrieb: Durchgang {uid} gelöscht (Rest vom letzten Lauf)"
                log.info("%s", message)
                self.logbuf.add("info", message, source="privacy")
            except Exception as e:              # noqa: BLE001 — the start must go on
                log.warning("Durchgang %s ließ sich nicht löschen: %s", uid, e)

    def _wipe(self, uid: str, reason: str = "") -> None:
        """Alles zu diesem Durchgang entfernen — Dateien, Eintrag, Warteschlange."""
        timer = self._wipe_timers.pop(uid, None)
        if timer is not None:
            timer.cancel()
        if not self.library.get(uid) and uid not in self.sessions:
            return
        try:
            self.delete(uid)
            message = f"Datensparsamer Betrieb: Durchgang {uid} gelöscht ({reason or 'nach dem Druck'})"
            log.info("%s", message)
            # Beleg, der die Box verlässt: Der Puffer nimmt sonst nur Warnungen,
            # aber genau diese Zeile ist der Nachweis, auf den es hier ankommt.
            self.logbuf.add("info", message, source="privacy")
        except Exception as e:
            log.warning("Durchgang %s ließ sich nicht löschen: %s", uid, e)

    # ── Rahmen und Textzeilen ─────────────────────────────
    def _frame_overlay(self, key: str) -> Optional[Path]:
        name = str((self.profile.get("frame") or {}).get(key) or "")
        path = self.assets_dir / name if name else None
        return path if path and path.exists() else None

    def frame_on_photo(self) -> bool:
        """Whether apply_frame(…, "photo") changes a picture: a frame PNG or a text line for photos.

        Travels with each upload (meta ``frame``) so the gallery knows whether
        the unframed capture of a shot is a second picture or the same one.
        """
        cfg = self.profile.get("frame") or {}
        if not cfg.get("enabled") or not cfg.get("on_photo", True):
            return False
        lines = [l for l in (cfg.get("texts") or []) if str(l.get("target", "both")) in ("both", "photo")]
        return self._frame_overlay("overlay") is not None or bool(lines)

    def apply_frame(self, img: Image.Image, s: Session, target: str) -> Image.Image:
        """Rahmen-PNG und Textzeilen auf das fertige Bild — ``target``: photo | print."""
        cfg = self.profile.get("frame") or {}
        if not cfg.get("enabled") or not cfg.get(f"on_{target}", True):
            return img
        lines = [l for l in (cfg.get("texts") or [])
                 if str(l.get("target", "both")) in ("both", target)]
        overlay = self._frame_overlay("print_overlay" if target == "print" else "overlay")
        if target == "print" and overlay is None:
            overlay = self._frame_overlay("overlay")
        if overlay is None and not lines:
            return img
        return compose.apply_frame(img, overlay=overlay, lines=lines, values=self._texts(s))

    def prepare_shot(self, img: Image.Image, s: Session) -> Image.Image:
        """Was vor der Filterauswahl passiert: Greenscreen, dann die feste LUT."""
        out = self._apply_chroma(img, s)
        lut = self.baked_lut()
        if lut is not None:
            strength = float((self.profile.get("luts") or {}).get("baked_strength", 1.0) or 1.0)
            try:
                out = luts.apply_lut(out, lut, max(0.0, min(1.0, strength)))
            except Exception as e:
                log.warning("Feste LUT nicht angewendet: %s", e)
        return out

    # ── Working copies and the render pool (15.11.2026) ───
    # Measured on the booth PC (i5-6200U, 2 cores) with 6000×4000 camera
    # files: a GIF run of six shots stood 55 s on "Daten werden bearbeitet",
    # a photo 3.4 s. Every step decoded the 24 MP originals again — the GIF,
    # its print card, each of the 17 look previews (six originals each for a
    # GIF), the collage's film. What is delivered at 1080 px is now made from
    # one working copy per shot, decoded once at a reduced JPEG scale
    # (``Image.draft``) and kept for the newest runs; independent renders run
    # side by side in a small pool sized to the CPU.
    #: Long edge of a working copy: GIF frames and films (≤ 1080 wide, also
    #: from a 4:5 crop of a 3:2 picture), the GIF's print card, previews up to 1600.
    WORK_PX = 2880
    _render_pool = None
    _prep_cache = None
    _preview_cache = None
    _cache_lock = threading.Lock()

    def render_pool(self):
        """A few threads for independent renders. Never submit to it from one of its own tasks."""
        if self._render_pool is None:
            from concurrent.futures import ThreadPoolExecutor
            n = max(2, min(4, os.cpu_count() or 2))
            self._render_pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="render")
        return self._render_pool

    def _open_shot(self, path: Path, box: Optional[int] = None) -> Image.Image:
        """One camera file, turned by its EXIF and in RGB; with ``box`` a working copy of that long edge.

        For a working copy the JPEG is decoded at the smallest scale that
        still gives at least ``box`` (``draft``): a quarter of the pixels or
        fewer, which is most of the cost of a 24 MP file.
        """
        with Image.open(path) as im:
            if box:
                w, h = im.size
                f = box / max(w, h)
                if f < 1:
                    im.draft("RGB", (max(1, int(w * f)), max(1, int(h * f))))
            im = ImageOps.exif_transpose(im).convert("RGB")
        if box:
            im.thumbnail((box, box), Image.Resampling.BILINEAR)
        return im

    def _shots_sig(self, s: Session) -> tuple:
        out = []
        for p in s.shots:
            try:
                st = Path(p).stat()
                out.append((str(p), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(p), 0, 0))
        return tuple(out)

    def _prep_sig(self, s: Session) -> str:
        """What prepare_shot depends on besides the file: greenscreen and the fixed LUT."""
        lut = self.profile.get("luts") or {}
        active = self.chroma_active()
        return json.dumps([active, self._chroma_background(s) if active else None, self.profile.get("chroma"),
                           lut.get("baked"), lut.get("baked_strength")], sort_keys=True, default=str)

    #: Working copies kept, counted in pixels (about 3 bytes each): the
    #: shots of the newest runs, a GIF's six at 2160 px are 28 million.
    PREP_CACHE_PIXELS = 90_000_000

    def _prepared_one(self, s: Session, path: Path, box: int, prep_sig: str) -> Image.Image:
        """One shot as a prepared working copy (greenscreen, fixed LUT), made once.

        Shared by the previews of all looks, the GIF, its films and its print
        card. A reshoot is a new file (new key); another greenscreen or LUT
        setting too. A larger copy at hand is scaled down instead of decoding
        the original again. While one thread makes a copy, another asking for
        it waits for that one instead of decoding the file a second time.
        Returns the cached image itself: callers copy before changing it.
        """
        from concurrent.futures import Future
        try:
            st = Path(path).stat()
            fsig = (str(path), st.st_mtime_ns, st.st_size)
        except OSError:
            fsig = (str(path), 0, 0)
        key = (fsig, int(box), prep_sig)
        with self._cache_lock:
            if self._prep_cache is None:
                from collections import OrderedDict
                self._prep_cache = OrderedDict()
            hit = self._prep_cache.get(key)
            mine = None
            bigger = None
            if hit is None:
                mine = Future()
                self._prep_cache[key] = mine
                bigger = next((v for k, v in self._prep_cache.items()
                               if k[0] == fsig and k[2] == prep_sig and k[1] > int(box)
                               and isinstance(v, Image.Image)), None)
            else:
                self._prep_cache.move_to_end(key)
        if mine is None:
            return hit.result() if isinstance(hit, Future) else hit
        try:
            if bigger is not None:
                img = bigger.copy()
                img.thumbnail((int(box), int(box)), Image.Resampling.BILINEAR)
            else:
                img = self.prepare_shot(self._open_shot(Path(path), int(box)), s)
        except BaseException as e:
            with self._cache_lock:
                self._prep_cache.pop(key, None)
            mine.set_exception(e)
            raise
        with self._cache_lock:
            self._prep_cache[key] = img
            pixels = sum(v.width * v.height for v in self._prep_cache.values() if isinstance(v, Image.Image))
            for k in list(self._prep_cache):
                if pixels <= self.PREP_CACHE_PIXELS or k == key:
                    break
                v = self._prep_cache[k]
                if isinstance(v, Image.Image):
                    pixels -= v.width * v.height
                    del self._prep_cache[k]
        mine.set_result(img)
        return img

    def prepared_shots(self, s: Session, box: int) -> List[Image.Image]:
        """All shots of the run as prepared working copies (copies: callers may change them)."""
        sig = self._prep_sig(s)
        made = list(self.render_pool().map(lambda p: self._prepared_one(s, p, box, sig), list(s.shots)))
        return [im.copy() for im in made]

    def warm_shot(self, s: Session, path: Path) -> None:
        """Start the working copy of a GIF shot while the guests pose for the next one.

        By the last shot five of six are made, and finishing the GIF starts
        with the pictures already decoded. Never in the way of a shot: it runs
        in the render pool, and a failure only means it is made later.
        """
        if s.mode != "gif":
            return
        try:
            sig = self._prep_sig(s)
            self.render_pool().submit(self._prepared_one, s, path, self._gif_work_px(), sig)
        except Exception as e:                       # noqa: BLE001 — made at finalize then
            log.debug("Arbeitskopie von %s nicht vorbereitet: %s", path, e)

    def preview_jpeg(self, s: Session, look: str, size: int, drawn: bool = True) -> bytes:
        """A preview of the run in one look, as JPEG — made once per run, look and size.

        The result screen asks for every look the guest may choose (17 by
        default). Kept until the run, its shots, its drawing or the profile change.
        """
        import hashlib
        render_sig = hashlib.sha1(json.dumps(
            [self.profile, s.fmt, s.template, s.background, s.share_url, s.guest_text, s.event_name,
             s.drawing if drawn else None], sort_keys=True, default=str).encode()).hexdigest()
        key = (s.uid, str(look), int(size), bool(drawn), self._shots_sig(s), self._prep_sig(s), render_sig)
        with self._cache_lock:
            if self._preview_cache is None:
                from collections import OrderedDict
                self._preview_cache = OrderedDict()
            data = self._preview_cache.get(key)
        if data is None:
            img = self.render(s, look, size, drawn)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=84)
            data = buf.getvalue()
            with self._cache_lock:
                self._preview_cache[key] = data
                while len(self._preview_cache) > 80:
                    self._preview_cache.popitem(last=False)
        return data

    def _gif_work_px(self) -> int:
        """Long edge for the stills of a GIF: twice the widest film it feeds.

        A 4:5 film cut from a 3:2 picture keeps 2/3 of the long edge, still
        more than the 1080 it is made at; the print card's slots are smaller.
        """
        cfg = self.profile.get("gif") or {}
        widths = [int(cfg.get("max_width") or 1080), int((cfg.get("boomerang") or {}).get("max_width") or 1080)]
        for scale in (cfg.get("scale"), (cfg.get("boomerang") or {}).get("scale")):
            widths.append(boomerang.SCALE_TARGETS.get(str(scale or "native").lower(), 0))
        return 2 * max(widths)

    #: How often the heartbeat's copy of the self-check is renewed. Some checks
    #: talk to the camera or the network; every 30 s would be too often, and
    #: a finding five minutes old is still news to an operator who has none.
    DOCTOR_EVERY = 300.0
    #: Checks that say nothing to the gallery: it knows better than the booth
    #: whether the booth is online, and the port check is about this PC only.
    #: "Event" too: the gallery assigned (or did not assign) the event itself,
    #: and a booth waiting between two events must not turn its light yellow.
    DOCTOR_NOT_SENT = ("Galerie", "Weboberfläche", "Event")

    def _heartbeat_doctor(self) -> dict:
        """The last self-check for the heartbeat; renewed in the background.

        Never runs in the sync thread itself: a camera that hangs on its
        answer must not delay the heartbeat that would report it.
        """
        cached = getattr(self, "_doctor_cache", None) or {}
        running = getattr(self, "_doctor_thread", None)
        # The camera changed since the self-check looked at it: its finding
        # is about a camera that is no longer there (seen 16.11.2026: "edsdk:
        # nicht verbunden" next to "Kamera bereit" until the next run, five
        # minutes later). It leaves this heartbeat, and the check runs again now.
        camera_moved = bool(cached) and cached.get("camera_state", self.camera.state) != self.camera.state
        due = camera_moved or (time.time() - float(cached.get("at") or 0)) >= self.DOCTOR_EVERY
        if due and not (running and running.is_alive()):
            self._doctor_thread = threading.Thread(target=self._refresh_doctor, name="doctor-heartbeat", daemon=True)
            self._doctor_thread.start()
        if camera_moved:
            findings = [f for f in cached.get("findings") or [] if f.get("name") != "Kamera"]
            worst = ("fail" if any(f.get("state") == "fail" for f in findings)
                     else "warn" if findings else "ok")
            return {**cached, "findings": findings, "worst": worst}
        return cached

    def _refresh_doctor(self) -> None:
        from .doctor import Doctor, FAIL, WARN, summary
        try:
            checks = Doctor(self).run()
        except Exception as e:                      # pragma: no cover - the doctor catches per check
            log.warning("Selbstdiagnose für den Heartbeat fehlgeschlagen: %s", e)
            return
        sent = [c for c in checks if c.name not in self.DOCTOR_NOT_SENT]
        # The camera state the check judged, so a later change can retire it.
        kamera = next((c for c in checks if c.name == "Kamera"), None)
        gesehen = ((getattr(kamera, "detail", None) or {}).get("state") if kamera else None) or self.camera.state
        self._doctor_cache = {
            "camera_state": gesehen,
            "worst": summary(sent)["worst"], "at": time.time(),
            "findings": [{"name": c.name, "state": c.state, "message": c.message[:300]}
                         for c in sent if c.state in (FAIL, WARN)][:20],
        }

    def _heartbeat_notices(self) -> list:
        """Open operator messages (warnung/kritisch), without the internal key."""
        self._geraete_abgleichen()
        return [{"stufe": m["stufe"], "titel": str(m["titel"])[:160], "text": str(m.get("text") or "")[:300],
                 "quelle": m.get("quelle") or "", "at": m.get("at") or 0}
                for m in self.meldungen.offen()[:10]]

    def _doctor_summary(self) -> str:
        """Kurzfassung der Selbstdiagnose — passt in eine Auftragsantwort (300 Zeichen)."""
        from .doctor import Doctor, FAIL, WARN, summary
        checks = Doctor(self).run()
        bad = [c for c in checks if c.state in (FAIL, WARN)]
        if not bad:
            return "alles in Ordnung"
        return "; ".join(f"{c.name}: {c.message}" for c in bad)

    def take_trigger(self) -> Optional[dict]:
        """Anstehende Auslösung abholen — sie gilt nur einmal und nur kurz."""
        job, self.trigger = self.trigger, None
        if job and time.time() - job["at"] < 20:
            return job
        return None

    # ── Drucklimits ───────────────────────────────────────
    def print_allowance(self, uid: Optional[str]) -> dict:
        cfg = self.profile["print"]
        ok = bool(cfg.get("enabled")) and self.printer.backend is not None
        ev_limit, se_limit = int(cfg.get("limit_event", 0)), int(cfg.get("limit_session", 0))
        slug = self.profile["event"].get("slug", "")
        ev_left = None if not ev_limit else max(0, ev_limit - self.library.event_prints(slug))   # Zähler steigt schon beim Einreihen
        se_left = None if not (se_limit and uid) else max(0, se_limit - self.library.prints(uid))
        lefts = [x for x in (ev_left, se_left) if x is not None]
        max_now = min([int(cfg.get("copies_max", 1)) or 1] + lefts)
        pst = self.printer.status()
        # Wartezeit: was vor diesem Auftrag noch in der Schlange steht, mal der
        # Zeit je Blatt. Ein Sublimationsdrucker ist langsam genug, dass der Gast
        # es wissen will, bevor er auf „Drucken" tippt.
        per_print = float(cfg.get("seconds_per_print") or 0)
        wait = int(round(self.printer.pending * per_print)) if per_print else 0
        code = errors.printer_code(pst.get("state"))
        return {"enabled": ok, "event_left": ev_left, "session_left": se_left, "max_now": max(0, max_now),
                "printer_state": pst.get("state"), "error_code": code,
                # Paper out and queue_while_empty on: "Drucken" stays a button.
                "can_queue": code == "printer.error" and self._queue_room(uid) > 0,
                "wait_seconds": wait, "queue": self.printer.pending, **self.media_left()}

    def _queue_room(self, uid: Optional[str]) -> int:
        """How many more prints may be accepted while the paper is out (0 = none).

        Only with ``print.queue_while_empty``; never for a "Nur drucken" run,
        whose photo is meant to leave the booth with the print and must not
        wait on the disk for hours.
        """
        cfg = self.profile.get("print") or {}
        if not cfg.get("queue_while_empty"):
            return 0
        if uid and self.run_print_only(uid):
            return 0
        return max(0, int(cfg.get("queue_max") or 12) - self.printer.spool.waiting())

    def _print_dropped(self, row: dict) -> None:
        """A waiting print thrown away (operator, or too old): the guest gets the sheet back."""
        uid = str(row.get("session_uid") or "")
        if not uid or uid.startswith(self.TESTDRUCK_PREFIX):
            return
        if self.printer.spool.mark_quota_returned(int(row.get("id") or 0)):
            self.library.add_prints(uid, -int(row.get("copies") or 1))

    def media_left(self) -> dict:
        """Wie viele Blatt die Kassette noch hat — gezählt, nicht gemessen.

        Der Drucker meldet keinen Vorrat. Die Box weiß nur, was sie selbst
        gedruckt hat, seit der Betreiber zuletzt „Kassette gewechselt" gedrückt
        hat. Das reicht, um rechtzeitig zu warnen, und ist ehrlicher als eine
        erfundene Füllstandsanzeige.
        """
        per_cassette = int(self.profile["print"].get("sheets_per_cassette") or 0)
        if not per_cassette:
            return {"sheets_left": None}
        used = self.printer.printed % per_cassette if self.printer.printed else 0
        return {"sheets_left": max(0, per_cassette - used), "sheets_per_cassette": per_cassette}

    # ── Durchgang ─────────────────────────────────────────
    def start_session(self, mode: str, fmt: str, template_id: Optional[str], background: Optional[str],
                      chroma_background: str = "", consent: Optional[dict] = None,
                      motion_frame: Optional[str] = None, seconds: Any = None) -> Session:
        if self.locked:
            raise HTTPException(423, "locked")
        allowed = self.printable_modes() if self.ephemeral else self.profile["modes"]
        if mode not in allowed:
            raise HTTPException(400, "Modus nicht aktiv")
        record: Dict[str, Any] = {}
        options = self.consent_options(mode)
        if options:
            # With the step on, no run starts without a choice: a run without
            # one would silently land in the gallery.
            option = str((consent or {}).get("option") or "")
            if not option:
                raise HTTPException(400, "consent.missing")
            if option not in options:
                raise HTTPException(400, "consent.option")
            langs = self.profile.get("languages") or ["de"]
            lang = str((consent or {}).get("lang") or "")
            record = self.consent_record(option, lang if lang in langs else langs[0])
        elif self.consent_notice_only(mode):
            # One option is no choice; what is left is the note on the set-up
            # screen. The run says so, and so does the log: a consent step
            # that is on never drops without a trace.
            if str((consent or {}).get("option") or "") == "print":
                # "Nur drucken" for a run that cannot print (a kiosk page from
                # before 18.11.2026 still offers it for a video): refused, never
                # quietly turned into a gallery run the guest did not choose.
                raise HTTPException(400, "consent.option")
            langs = self.profile.get("languages") or ["de"]
            lang = str((consent or {}).get("lang") or "")
            record = self.consent_record("notice", lang if lang in langs else langs[0])
            log.info("Einwilligung: nur eine Möglichkeit, Hinweis gezeigt (consent notice shown)")
        if self.camera.state != "ready":
            raise HTTPException(503, errors.camera_code(self.camera.error) or "camera.off")
        tpl = None
        if mode == "collage":
            tpl = self.template(template_id) or self.collage_choice()[0]
        elif mode == "boomerang" and self.boomerang_prints():
            # A boomerang with its print sheet takes a collage layout too.
            tpl = self.template(template_id) or self.templates()[0]
        elif mode == "photo":
            # A one-photo layout the operator chose: it makes the result and the
            # sheet, and its shape wins over the format choice.
            einzel = self.photo_templates()
            if einzel:
                tpl = next((t for t in einzel if t.get("id") == template_id), einzel[0])
                fmt = self._format_of(tpl)
        if mode in FRAME_MODES:
            # The boomerang's (and since 18.11.2026 the GIF's) format is chosen
            # like the photo's: the guest picks among "formats" when
            # guest_choice.format is on (the default) or the mode's own
            # guest_choice is; anything else is the operator's <mode>.format.
            # The film's is always the operator's.
            clip = self.profile[mode]
            if not (mode in ("boomerang", "gif") and self.boomerang_format_choice(mode)
                    and fmt in (self.profile.get("formats") or [])):
                fmt = clip.get("format") or self.profile["default_format"]
        frame_id, frame = self.pick_motion_frame(mode, motion_frame) if mode in FRAME_MODES else ("", None)
        if frame is not None:
            # The frame's canvas is the film's shape (and the key frames' of a printed boomerang).
            fmt = frame["format"]
        if fmt not in compose.ASPECTS:
            fmt = self.profile["default_format"]
        available = self.chroma_backgrounds()
        chroma_bg = chroma_background if chroma_background in available else ""
        s = new_session(self.sessions_dir, mode, fmt, self.profile, tpl, background, chroma_bg)
        s.motion_frame, s.motion_frame_id = frame, frame_id
        # The guest's boomerang length, only from the offer: anything else
        # (an old kiosk page, a typo, a remote start without one) is the
        # pre-selected length, never a refused run.
        s.clip_choice_seconds = self._chosen_clip_seconds(mode, seconds)
        s.consent = record
        s.print_only = record.get("option") == "print"
        if self.run_print_only(s):
            s.share_url = ""                         # kein Link, der irgendwohin zeigen könnte
            try:
                (s.folder / _EPHEMERAL_MARKER).touch()
            except OSError as e:
                log.warning("Datensparsamer Betrieb: Markierung fehlt in %s: %s", s.uid, e)
        self.sessions[s.uid] = s
        self._arm_idle_wipe(s)
        self._gc_sessions()
        plugins.safe_call(self.plugins, "booth_session_started", session=s)
        self.webhooks.emit("session_started", {"uid": s.uid, "mode": s.mode, "format": s.fmt,
                                               "event": s.event_slug})
        return s

    def _gc_sessions(self) -> None:
        """Alte Durchgänge aufräumen — im datensparsamen Betrieb samt Dateien.

        Ein Durchgang, der ``finalize`` nie erreicht (Kamerafehler beim dritten
        von vier Bildern, Gast geht weg), wurde bisher nur aus der Liste
        genommen. Die fertigen Gesichtsaufnahmen blieben unbegrenzt im Ordner
        liegen — im Modus, der verspricht, dass nichts auf dem Gerät bleibt.
        """
        cutoff = time.time() - 3600
        for uid in [u for u, s in self.sessions.items() if s.created < cutoff]:
            s = self.sessions.pop(uid, None)
            if s is not None and not s.finalized and not s.deleted:
                # Nobody finished it and nobody pressed "Abbrechen": the guest
                # walked away. Counted here, at the latest an hour later.
                self.zahlen.zaehle(s.event_slug or self._zahlen_event(), "aborted.timeout")
            if s is None or not self.run_print_only(s):
                continue
            try:
                if s.folder and s.folder.exists():
                    shutil.rmtree(s.folder, ignore_errors=True)
                    log.info("Abgebrochener Durchgang aufgeräumt: %s", uid)
            except Exception as e:              # noqa: BLE001 — Aufräumen darf nichts anhalten
                log.warning("Durchgang %s nicht aufgeräumt: %s", uid, e)

    def get_session(self, uid: str) -> Session:
        s = self.sessions.get(uid)
        if not s or s.deleted:
            raise HTTPException(404, "Durchgang unbekannt")
        # Every request about a run is activity of that run.
        self._arm_idle_wipe(s)
        return s

    def run_failed(self, uid: str, step: str, exc: BaseException, replace: Any = None) -> None:
        """One log line for a shot, clip or finish that sends the kiosk to its error screen.

        Until 15.11.2026 only a camera exception left a line, without run or
        shot; a 409 ("all shots there", two runs feeding one session) or a 404
        left nothing, and the evening's only error screen had no trace.
        A run thrown away by the guest (410) is not a failure: INFO.
        """
        s = self.sessions.get(uid)
        mode = s.mode if s else "?"
        if step == "shot":
            try:
                nummer = int(replace) if replace else (len(s.shots) + 1 if s else 0)
            except (TypeError, ValueError):
                nummer = 0
            needed = s.shots_needed if s else 0
            was = f"Aufnahme {nummer}/{needed}"
        elif step == "clip":
            was = "Clip"
        else:
            was = "Fertigstellen"
        status = getattr(exc, "status_code", 500)
        code = getattr(exc, "detail", "") if isinstance(exc, HTTPException) else ""
        cause = exc.__cause__ or (None if isinstance(exc, HTTPException) else exc)
        detail = f"{code}" + (f" ({type(cause).__name__}: {cause})" if cause is not None else "")
        line = f"Durchgang {uid} ({mode}): {was} fehlgeschlagen — HTTP {status} {detail}".rstrip()
        if status == 410 or (s is not None and s.deleted):
            log.info("%s", line)
            return
        if status >= 500:
            log.error("%s", line)
        else:
            log.warning("%s", line)
        if status == 503 and step in ("shot", "clip"):
            self.zahlen.zaehle((s.event_slug if s else "") or self._zahlen_event(), "camera.errors")

    def take_shot(self, s: Session, retake: bool = False, ersetzt: Optional[int] = None) -> Path:
        """Eine Aufnahme machen — neu, statt der letzten, oder statt einer bestimmten.

        ``ersetzt`` ist die Nummer des Bildes (ab 1), das noch einmal gemacht
        werden soll: Auf einer Collage aus vier Bildern hat beim zweiten jemand
        geblinzelt, und deswegen soll niemand alle vier wiederholen müssen.
        """
        with s.lock:
            # Thrown away while this request waited for the lock (the guest
            # cancelled): no file may land in the folder that was just removed.
            if s.deleted:
                raise HTTPException(410, "Durchgang verworfen")
            stelle = None
            if ersetzt is not None:
                stelle = int(ersetzt) - 1
                if not 0 <= stelle < len(s.shots):
                    raise HTTPException(404, "Diese Aufnahme gibt es nicht")
            elif retake and s.shots:
                stelle = len(s.shots) - 1
            if stelle is not None:
                self.zahlen.zaehle(s.event_slug or self._zahlen_event(), "retakes")
            if stelle is None and len(s.shots) >= max(1, s.shots_needed):
                raise HTTPException(409, "Alle Aufnahmen vorhanden")
            # Der Dateiname hängt an der Stelle, nicht an der Reihenfolge der
            # Auslösung — sonst liegt das ersetzte Bild am Ende der Collage.
            nummer = (stelle + 1) if stelle is not None else (len(s.shots) + 1)
            target = s.folder / f"shot{nummer}.jpg"
            # Erst aufnehmen, dann ersetzen. Bis zum 19.09.2026 wurde das alte
            # Bild vorher gelöscht: Antwortete die Kamera dann nicht (USB-Wackler,
            # Zeitüberschreitung), war es weg, und der Gast fing von vorn an.
            roh = (s.folder / f"shot{nummer}.neu.jpg") if stelle is not None else target
            try:
                self.camera.capture(roh)
                check_photo(roh)
            except Exception as e:
                roh.unlink(missing_ok=True)
                # Logged once, with run, mode and shot, by run_failed (the HTTP layer).
                raise HTTPException(503, errors.camera_code(str(e)) or "camera.generic") from e
            try:
                # The camera's clock may be wrong (an EOS on a mains adapter
                # forgets it). Its date is corrected in place, never re-encoded.
                aufnahmezeit.stamp_original(roh, time.time())
            except Exception as e:                   # noqa: BLE001 — a date never costs a shot
                log.warning("Aufnahmezeit nicht ins Original geschrieben: %s", e)
            if stelle is not None:
                alt_datei = s.shots[stelle]
                if roh != target:
                    roh.replace(target)              # jetzt erst überschreiben
                if alt_datei != target:
                    alt_datei.unlink(missing_ok=True)
                s.shots[stelle] = target
            else:
                s.shots.append(target)
            s.finalized = False
            s.drawing = []                 # it belonged to the picture that was just replaced
            self.last_photo_at = dt.datetime.now().isoformat(timespec="seconds")
            self._check_flash_light(s, target, nummer)
            self.warm_shot(s, target)
            plugins.safe_call(self.plugins, "booth_shot_taken", session=s, path=target)
            self.webhooks.emit("shot_taken", {"uid": s.uid, "shot": len(s.shots),
                                              "needed": s.shots_needed})
            self._arm_idle_wipe(s)
            return target

    def _check_flash_light(self, s: Session, path: Path, nummer: int) -> None:
        """With the flash in use: was the picture lit? A near-black one is logged, counted and told.

        Seen on the booth PC (15.11.2026): pictures taken without the flash's
        light, and the M50 wrote Flash=9 ("fired") into a frame of mean 2/255,
        so the EXIF is no evidence — the pixels are, decoded at 1/8 scale.
        The picture is kept: a dark one may still be rescued, and the guest
        decides with "Nochmal". One bright picture with the flash ends the notice.
        """
        if not (self.profile.get("flash") or {}).get("enabled"):
            return
        from .camera import exposure
        mean = exposure.file_mean(path)
        if mean is None:
            return
        if mean >= exposure.DARK_MEAN:
            self.meldungen.entwarnung("blitz")
            return
        log.warning("Durchgang %s (%s): Aufnahme %s fast schwarz (Helligkeit %.1f/255) – Blitz nicht "
                    "ausgelöst oder zu schwach", s.uid, s.mode, nummer, mean)
        self.zahlen.zaehle(s.event_slug or self._zahlen_event(), "flash.dark")
        self.meldungen.melden("blitz", meldungen_mod.WARNUNG,
                              "Blitz hat nicht ausgelöst oder ist zu schwach – bitte prüfen",
                              "Ein Bild mit Blitz war fast schwarz. Blitz eingeschaltet, geladen, richtig "
                              "aufgesteckt? Akkus prüfen.", quelle="kamera", code="op.flash_dark")

    def boomerang_prints(self) -> bool:
        """``boomerang.print``: a boomerang run gets a sheet on the collage layouts (off by default)."""
        return bool((self.profile.get("boomerang") or {}).get("print"))

    def format_without_choice(self, mode: str) -> str:
        """The format of a run that names none: a boomerang's or film's own, else the photo's default."""
        if mode in FRAME_MODES:
            return str((self.profile.get(mode) or {}).get("format") or self.profile["default_format"])
        return self.profile["default_format"]

    def boomerang_format_choice(self, mode: str = "boomerang") -> bool:
        """May the guest choose the boomerang's format? Like the photo's, or by its own switch.

        Until 15.11.2026 only ``boomerang.guest_choice`` (off by default)
        opened it; the owner wants it "identisch wie bei den anderen".
        """
        general = (self.profile.get("guest_choice") or {}).get("format", True) is not False
        return bool(general or (self.profile.get(mode) or {}).get("guest_choice"))

    def record_clip(self, s: Session, encode_later: bool = False) -> dict:
        """Record a boomerang or film; ``encode_later``: answer as soon as the camera stops.

        The kiosk shows "recording" until this answers. Until 15.11.2026 the
        answer came after the film was encoded (three ping-pong passes through
        x264), so a 2 s boomerang showed "Bewegt euch!" for 8 s on a fast Mac
        and longer on the booth PC. With ``encode_later`` the film is made in
        its own thread while the kiosk shows "processing"; finalize waits for
        it. The remote control and the tests encode at once, as before.
        """
        cfg = self.profile[s.mode]
        with s.lock:
            if s.deleted:                            # cancelled while waiting, see take_shot
                raise HTTPException(410, "Durchgang verworfen")
            # A retake of a clip on the same run (kiosk "Nochmal") or a remote
            # /clip while the film of the take before is still being made:
            # wait for that film first. Two threads writing the same run's
            # files gave a corrupt or a rejected take.
            earlier = s.clip_job
            if earlier is not None and not earlier.done():
                log.info("Durchgang %s (%s): neue Aufnahme wartet auf den Film der vorigen", s.uid, s.mode)
                try:
                    earlier.result(timeout=300)
                except Exception:                    # noqa: BLE001 — that take is replaced anyway
                    pass
            seconds = min(self._clip_seconds(s), boomerang.MAX_FRAMES / max(1.0, float(cfg["fps"])))
            movie = None
            if self.clip_source(s.mode) == "camera" and not self._movie_paused():
                movie_seconds = min(seconds, self.CAMERA_MOVIE_MAX_SECONDS)
                try:
                    # A little longer than asked: the camera writes its first
                    # frames a moment after the start (EOS M50: 2.0 s gave a
                    # 1.3 s file); the clip keeps the last seconds of the file.
                    frames, elapsed, movie = self.camera.record_movie(
                        movie_seconds + self.CAMERA_MOVIE_LEAD, float(cfg["fps"]), self._movie_folder())
                    seconds = movie_seconds
                except Exception as e:               # noqa: BLE001 — the live view films instead
                    log.warning("Durchgang %s (%s): Film der Kamera nicht gestartet (%s) – Aufnahme aus dem "
                                "Live-Bild", s.uid, s.mode, str(e)[:200])
                    # Mode switch, record start, no card: the same next time.
                    # Until the pause ends the live view films at once.
                    self._movie_failed(str(e), started=False)
                    movie = None
                if movie is not None and len(frames) < 4:
                    # Too few live frames to fall back on: wait for the movie
                    # here, while the kiosk still shows "recording", so a
                    # failed one can still be recorded from the live view.
                    try:
                        movie.result(timeout=self._movie_timeout(seconds))
                    except Exception as e:           # noqa: BLE001
                        log.warning("Durchgang %s (%s): Film der Kamera fehlgeschlagen (%s), kein Live-Bild "
                                    "dabei – Aufnahme aus dem Live-Bild", s.uid, s.mode, str(e)[:200])
                        self._discard_when_done(movie)
                        self._movie_failed(str(e), started=True)
                        movie = None
            try:
                if movie is None:
                    frames, elapsed = self.camera.record_timed(seconds, float(cfg["fps"]))
            except Exception as e:
                raise HTTPException(503, errors.camera_code(str(e)) or "camera.generic") from e
            if movie is None and len(frames) < 4:
                raise HTTPException(503, "camera.generic") from RuntimeError(
                    f"nur {len(frames)} Live-Bilder in der Aufnahme")
            s.clip, s.poster, s.key_frames = None, None, []
            s.clip_seconds = float(elapsed or 0)
            s.clip_fps = boomerang.played_fps(len(frames), elapsed, cfg["fps"])
            log.info("Durchgang %s (%s): %d Live-Bilder in %.2f s aufgenommen (eingestellt %.1f s, %s/s), "
                     "Film mit %d Bildern/s%s", s.uid, s.mode, len(frames), s.clip_seconds, seconds,
                     cfg["fps"], s.clip_fps, ", dazu Film der Kamera" if movie is not None else "")
            self.last_photo_at = dt.datetime.now().isoformat(timespec="seconds")
            self._arm_idle_wipe(s)
            if encode_later:
                job: Future = Future()
                s.clip_job = job

                def work():
                    try:
                        job.set_result(self._build_clip(s, frames, movie))
                    except BaseException as exc:      # noqa: BLE001 — handed to finalize
                        job.set_exception(exc)

                threading.Thread(target=work, name=f"clip-{s.uid}", daemon=True).start()
            else:
                s.clip_job = None
                self._build_clip(s, frames, movie)
            return {**s.public(), "recorded_seconds": round(s.clip_seconds, 3), "frames": len(frames),
                    "fps": s.clip_fps}

    def motion_frame_size(self, frame: dict, cfg: dict) -> Tuple[int, int]:
        """A framed film's size: the frame's canvas, its short edge capped like a film's width.

        ``max_width`` (1080) caps the short edge, so a 9:16 frame stays
        1080×1920 and a 16:9 one 1920×1080; a fixed ``scale`` (720p, 1080p,
        1440p) sets the short edge, as it does for an unframed film. "native"
        does not apply: the frame is drawn at its size and the camera's
        picture is scaled into the window.
        """
        target = boomerang.SCALE_TARGETS.get(str(cfg.get("scale") or "native").lower(), 0)
        canvas = motion_frames.FORMATS[frame["format"]]
        short = target or min(int(cfg.get("max_width") or 1080), min(canvas))
        return motion_frames.output_size(frame, short)

    def _gif_in_frame(self, s: Session, frames: List[Image.Image], layers: dict) -> List[Image.Image]:
        """A GIF's pictures in the run's motion frame; the pictures unframed if it cannot be drawn."""
        try:
            with Image.open(layers["bottom"]) as bottom, Image.open(layers["top"]) as top:
                bottom, top = bottom.convert("RGB"), top.convert("RGBA")
                return [motion_frames.compose(fr, bottom, top, layers["box"]) for fr in frames]
        except Exception as e:                       # noqa: BLE001 — without the frame, not without the GIF
            log.warning("Durchgang %s: GIF mit Rahmen „%s“ nicht erzeugt (%s: %s) – es entsteht ohne Rahmen",
                        s.uid, s.motion_frame_id, e.__class__.__name__, str(e)[:200])
            s.motion_frame, s.motion_frame_id = None, ""
            return frames

    def _motion_layers(self, s: Session, cfg: dict) -> Optional[dict]:
        """The still layers of the run's frame, from the cache or rendered now; None if they cannot be."""
        try:
            return motion_frames.cached_layers(
                s.motion_frame, self.motion_frame_size(s.motion_frame, cfg), self.assets_dir, self._texts(s),
                self.data / "cache" / "rahmen", style_cfg=self.profile.get("collage_style"))
        except Exception as e:                       # noqa: BLE001 — without the frame, not without the film
            log.warning("Durchgang %s: Rahmen „%s“ nicht vorbereitet (%s: %s) – der Film entsteht ohne Rahmen",
                        s.uid, s.motion_frame_id, e.__class__.__name__, str(e)[:200])
            s.motion_frame, s.motion_frame_id = None, ""
            return None

    # ── Clip source "camera": a movie file the camera records itself ──
    #: Longest movie the booth asks the camera for (the film's own limit).
    CAMERA_MOVIE_MAX_SECONDS = 60.0
    #: Seconds recorded before the asked length (the camera's start delay).
    CAMERA_MOVIE_LEAD = 0.7

    # ── Length the guest chooses (boomerang.seconds_choices, 18.11.2026) ──
    #: Longest length offered when the camera films (source "camera"): its
    #: boomerang is turned round by ffmpeg's ``reverse`` filter, which holds
    #: every frame of the recording at 1080x1920 (about 3 MB each in yuv420p):
    #: 10 s at 20/s is ~620 MB in the ffmpeg process, 15 s would be ~930 MB
    #: on a small booth PC. The live view keeps JPEG bytes (~0.1 MB a frame)
    #: and decodes one picture at a time, so it may go up to 15 s.
    CAMERA_CHOICE_MAX_SECONDS = 10.0

    def clip_seconds_offer(self, mode: str) -> dict:
        """The lengths the set-up screen offers, and the pre-selected one.

        Only the boomerang, only with ``seconds_guest_choice`` on and two or
        more valid lengths (1-15 s, at most four, doubles once). A length
        whose single loop (forward and back) is longer than
        ``max_total_seconds`` is left out: its film could not keep the limit.
        ``choices`` empty means no choice: today's ``seconds`` and ``loops``.
        """
        cfg = self.profile.get(mode) or {}
        if mode != "boomerang" or not cfg.get("seconds_guest_choice"):
            return {"choices": [], "default": None}
        low, high, most = profile_rules.NUMBER_LISTS["boomerang.seconds_choices"]
        if self.clip_source(mode) == "camera":
            high = min(high, self.CAMERA_CHOICE_MAX_SECONDS)
        top = max(self._clip_total(cfg, "max_total_seconds", 30), self._clip_total(cfg, "min_total_seconds", 15))
        choices: List[float] = []
        for v in cfg.get("seconds_choices") or []:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                continue
            v = float(v)
            if low <= v <= high and 2 * v <= top + 1e-9 and v not in choices:
                choices.append(v)
        choices = choices[:most]
        if len(choices) < 2:
            return {"choices": [], "default": None}
        try:
            own = float(cfg.get("seconds"))
        except (TypeError, ValueError):
            own = None
        return {"choices": choices, "default": own if own in choices else choices[0]}

    @staticmethod
    def _clip_total(cfg: dict, key: str, fallback: float) -> float:
        try:
            value = float(cfg.get(key) or fallback)
        except (TypeError, ValueError):
            value = fallback
        return value if math.isfinite(value) and value > 0 else fallback

    def _chosen_clip_seconds(self, mode: str, wanted: Any) -> Optional[float]:
        """The guest's length checked against the offer: unknown or broken -> the pre-selected one."""
        offer = self.clip_seconds_offer(mode)
        if not offer["choices"]:
            return None
        try:
            value = float(wanted) if not isinstance(wanted, bool) else None
        except (TypeError, ValueError):
            value = None
        return value if value in offer["choices"] else offer["default"]

    def _clip_seconds(self, s: Session) -> float:
        """How long this run records: the guest's choice, else the profile's ``seconds``."""
        return float(s.clip_choice_seconds or self.profile[s.mode]["seconds"])

    def _clip_loops(self, s: Session) -> int:
        """Loops of this run's boomerang: by the 15-30 s rule after a choice, else ``loops`` as before."""
        cfg = self.profile[s.mode]
        if not s.clip_choice_seconds:
            return int(cfg.get("loops", 1))
        return boomerang.loops_for_total(s.clip_choice_seconds, self._clip_total(cfg, "min_total_seconds", 15),
                                         self._clip_total(cfg, "max_total_seconds", 30))

    def clip_source(self, mode: str) -> str:
        """``boomerang.source`` / ``video.source``: "live" (default) or "camera"."""
        value = str((self.profile.get(mode) or {}).get("source") or "live")
        return "camera" if value == "camera" else "live"

    def _movie_folder(self) -> Path:
        """Where the camera's raw movies land: out of every run folder, upload and backup.

        A file left by a crash (they are large: a 4K second is ~15 MB) is
        removed before the next recording.
        """
        folder = self.data / "cache" / "kamerafilm"
        folder.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 600
        for old in folder.iterdir():
            try:
                if old.is_file() and old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass
        return folder

    @staticmethod
    def _movie_timeout(seconds: float) -> float:
        # Mode switch, file event, download (~17 MB/s measured) and the way back.
        return 60.0 + 2.0 * float(seconds)

    # ── Circuit breaker of the camera movie ────────────────
    #: After a failed start (mode switch, record start, no card) the live view
    #: films directly for this long, or until the camera is connected afresh.
    #: Without it every run paid the failure path (bounded waits of several
    #: seconds) after the guest's "move now" had ended.
    CAMERA_MOVIE_PAUSE_S = 600.0
    #: Failures after a recording (no file, download, ffmpeg) in a row that pause too.
    CAMERA_MOVIE_LATE_FAILURES = 2

    @property
    def _movie_pause(self) -> dict:
        state = self.__dict__.get("_movie_pause_state")
        if state is None:
            state = self.__dict__.setdefault("_movie_pause_state", {
                "until": 0.0, "wall_until": "", "reason": "", "connections": -1, "late": 0,
                "lock": threading.Lock()})
        return state

    def _movie_paused(self) -> bool:
        """Is the camera movie paused (live view films directly)? Ends by time or by a fresh connection."""
        st = self._movie_pause
        with st["lock"]:
            if not st["until"]:
                return False
            if time.monotonic() >= st["until"] or getattr(self.camera, "connections", 0) != st["connections"]:
                log.info("Film der Kamera wird wieder versucht")
                st.update(until=0.0, wall_until="", reason="")
                return False
            return True

    def _movie_failed(self, reason: str, started: bool) -> None:
        """Count a failure of the camera movie; pause it at once (start) or after a few in a row (later)."""
        st = self._movie_pause
        with st["lock"]:
            if started:
                st["late"] += 1
                if st["late"] < self.CAMERA_MOVIE_LATE_FAILURES:
                    return
            st["late"] = 0
            if st["until"] and time.monotonic() < st["until"]:
                return
            st["until"] = time.monotonic() + self.CAMERA_MOVIE_PAUSE_S
            wall = dt.datetime.now() + dt.timedelta(seconds=self.CAMERA_MOVIE_PAUSE_S)
            st["wall_until"] = wall.isoformat(timespec="seconds")
            st["reason"] = str(reason)[:200]
            st["connections"] = getattr(self.camera, "connections", 0)
        log.warning("Film der Kamera pausiert bis %s – Live-Bild (%s)", wall.strftime("%H:%M"), str(reason)[:200])

    def _movie_succeeded(self) -> None:
        st = self._movie_pause
        with st["lock"]:
            st["late"] = 0

    def camera_movie_state(self) -> dict:
        """For /api/state camera.camera_movie: is the camera's own film paused, until when and why."""
        paused = self._movie_paused()
        st = self._movie_pause
        return {"paused": paused, "until": st["wall_until"] if paused else "",
                "reason": st["reason"] if paused else ""}

    @staticmethod
    def _discard_when_done(movie: Future) -> None:
        """A movie nobody waits for any more: delete its raw file whenever it arrives."""
        def discard(fut: Future) -> None:
            try:
                if fut.cancelled() or fut.exception() is not None:
                    return
                Path(fut.result()).unlink(missing_ok=True)
            except Exception:                        # noqa: BLE001 — the start-up sweep gets it
                pass
        movie.add_done_callback(discard)

    def _sweep_movie_folder(self) -> None:
        """At start: nothing in cache/kamerafilm belongs to a run any more (raw movies, frame folders)."""
        folder = self.data / "cache" / "kamerafilm"
        if not folder.is_dir():
            return
        for old in folder.iterdir():
            try:
                if old.is_dir():
                    shutil.rmtree(old, ignore_errors=True)
                else:
                    old.unlink()
            except OSError:
                pass

    def _build_clip(self, s: Session, frames: list, movie: Optional[Future]) -> Path:
        """The film from the camera's movie, or from the live frames when that fails or is off.

        Without a motion frame or a print sheet the film is made by ffmpeg in
        one pass straight from the movie (``camera_movie.make_film``); with
        one, the movie becomes frames that go through ``_encode_clip``. The
        parts are logged separately: waiting for the movie (file and
        download, on the camera thread), then ffmpeg. Never a dead end: a
        movie that cannot be used is replaced by the live frames, and without
        enough of them by the movie read once more as frames, then by a new
        recording from the live view.
        """
        if movie is None:
            return self._encode_clip(s, frames)
        cfg = self.profile[s.mode]
        raw: Optional[Path] = None
        want = min(self._clip_seconds(s), self.CAMERA_MOVIE_MAX_SECONDS,
                   boomerang.MAX_FRAMES / max(1.0, float(cfg["fps"])))
        live_fps = s.clip_fps
        from .media import camera_movie
        layout = cfg.get("layout") or ("crop" if s.mode == "boomerang" else "blur")
        try:
            t_wait = time.monotonic()
            try:
                raw = Path(movie.result(timeout=self._movie_timeout(want)))
            except Exception:
                self._discard_when_done(movie)
                raise
            waited = time.monotonic() - t_wait
            layers = self._motion_layers(s, cfg) if s.motion_frame else None
            if layers is not None:
                box, fill = (int(layers["box"][2]), int(layers["box"][3])), True
            else:
                box = camera_movie.target_size(s.fmt)
                fill = layout != "blur"
            t0 = time.monotonic()
            if layers is None and not (s.mode == "boomerang" and s.template is not None):
                target = s.folder / f"{s.mode}.mp4"
                part = self._clip_part(target)
                try:
                    _, poster, times = camera_movie.make_film(
                        raw, part, box=box, fill=fill, rotate=int(self.camera.rotate or 0), fps=cfg["fps"],
                        seconds=want, boomerang=s.mode == "boomerang", loops=self._clip_loops(s))
                    s.poster = compose.save_jpeg(poster, s.folder / "poster.jpg", quality=85, taken=s.created)
                    os.replace(part, target)
                finally:
                    self._drop_parts(part)
                # Only now: a failure above falls back with the live view's rate.
                s.clip_fps = int(times["fps"])
                s.clip, out = target, target
                log.info("Durchgang %s (%s): Film der Kamera %s → %dx%d, %d/s, %.1f s in einem Durchgang: "
                         "Warten auf den Film %.1f s, ffmpeg %s", s.uid, s.mode, raw.name, box[0], box[1],
                         s.clip_fps, want, waited, times)
            else:
                out = self._clip_from_movie_frames(s, raw, box, fill, layers, want, live_fps, t0, waited)
            s.clip_seconds = min(want, boomerang.MAX_FRAMES / max(1.0, float(s.clip_fps or cfg["fps"])))
            self._movie_succeeded()
            return out
        except Exception as e:                       # noqa: BLE001 — the guest gets the live-view clip
            s.clip_fps = live_fps
            self._movie_failed(f"{e.__class__.__name__}: {e}", started=True)
            if len(frames) >= 4:
                log.warning("Durchgang %s (%s): Film der Kamera nicht verwendbar (%s: %s) – Film aus dem Live-Bild",
                            s.uid, s.mode, e.__class__.__name__, str(e)[:200])
                return self._encode_clip(s, frames)
            return self._clip_without_live_frames(s, raw, e, want, live_fps)
        finally:
            if raw is not None:
                try:
                    raw.unlink()                     # huge, and never part of the run
                except OSError:
                    pass

    def _clip_from_movie_frames(self, s: Session, raw: Path, box, fill: bool, layers, want: float,
                                live_fps, t0: float, waited: float) -> Path:
        """The movie as frames through ``_encode_clip`` (frame, print sheet, or the second try)."""
        from .media import camera_movie
        cfg = self.profile[s.mode]
        movie_frames, fps = camera_movie.extract_frames(
            raw, self.data / "cache" / "kamerafilm" / f"frames-{s.uid}", box=box, fill=fill,
            rotate=int(self.camera.rotate or 0), fps=cfg["fps"], seconds=want)
        extracted = time.monotonic() - t0
        s.clip_fps = fps
        try:
            out = self._encode_clip(s, movie_frames, turn=False, movie_box=None if layers else box)
        except Exception:
            s.clip_fps = live_fps
            raise
        log.info("Durchgang %s (%s): Film der Kamera %s → %d Bilder %dx%d, %d/s: Warten auf den Film "
                 "%.1f s, Bilder auslesen %.1f s, kodieren %.1f s", s.uid, s.mode, raw.name,
                 len(movie_frames), box[0], box[1], fps, waited, extracted,
                 time.monotonic() - t0 - extracted)
        return out

    def _clip_without_live_frames(self, s: Session, raw: Optional[Path], error: Exception, want: float,
                                  live_fps) -> Path:
        """Movie unusable and fewer than 4 live frames: read the movie once more as frames, else film anew.

        Before the review of 17.11.2026 this re-raised and the guest got
        "camera.generic" for a clip that was on disk.
        """
        from .media import camera_movie
        cfg = self.profile[s.mode]
        if raw is not None and raw.is_file():
            try:
                box = camera_movie.target_size(s.fmt)
                fill = (cfg.get("layout") or ("crop" if s.mode == "boomerang" else "blur")) != "blur"
                out = self._clip_from_movie_frames(s, raw, box, fill, None, want, live_fps,
                                                   time.monotonic(), 0.0)
                s.clip_seconds = want
                log.warning("Durchgang %s (%s): Film der Kamera erst im zweiten Anlauf gelesen (%s)",
                            s.uid, s.mode, str(error)[:200])
                return out
            except Exception as e2:                  # noqa: BLE001 — the live view films once more
                error = e2
        log.warning("Durchgang %s (%s): Film der Kamera nicht verwendbar und kein Live-Bild (%s) – "
                    "neue Aufnahme aus dem Live-Bild", s.uid, s.mode, str(error)[:200])
        seconds = min(self._clip_seconds(s), boomerang.MAX_FRAMES / max(1.0, float(cfg["fps"])))
        frames, elapsed = self.camera.record_timed(seconds, float(cfg["fps"]))
        if len(frames) < 4:
            raise RuntimeError(f"nur {len(frames)} Live-Bilder in der neuen Aufnahme") from error
        s.clip_seconds = float(elapsed or 0)
        s.clip_fps = boomerang.played_fps(len(frames), elapsed, cfg["fps"])
        return self._encode_clip(s, frames)

    @staticmethod
    def _clip_part(target: Path) -> Path:
        """A name of its own to encode to, replaced onto ``target`` when whole (two takes never share one)."""
        return target.with_name(f"{target.stem}.tmp-{secrets.token_hex(4)}{target.suffix}")

    @staticmethod
    def _drop_parts(part: Path) -> None:
        """Remove the part and the encoder's side files of it (``<stem>.pass.mp4`` / ``.pass.txt``)."""
        for p_ in (part, part.with_name(part.stem + ".pass.mp4"), part.with_name(part.stem + ".pass.txt")):
            try:
                p_.unlink(missing_ok=True)
            except OSError:
                pass

    def _encode_clip(self, s: Session, frames: list, turn: bool = True,
                     movie_box: Optional[Tuple[int, int]] = None) -> Path:
        """The recorded frames as the film (and, if switched on, the boomerang's print frames).

        ``turn``: the frames still need the camera's rotation (live view);
        frames from the camera's movie come turned. ``movie_box``: their
        film size, which then replaces the width rules.
        """
        cfg = self.profile[s.mode]
        target = s.folder / f"{s.mode}.mp4"
        rotate = (lambda im: rotate_image(im, self.camera.rotate)) if turn else None
        if s.mode == "boomerang" and s.template is not None:
            # boomerang.print: the slots' pictures, as the film shows them.
            n = int(compose.template_info(s.template)["shots"] or 1)
            s.key_frames = [boomerang.print_frame(boomerang._decode(frames[i], rotate), s.fmt,
                                                  cfg.get("layout", "crop" if s.mode == "boomerang" else "blur"))
                            for i in boomerang.key_frame_indices(len(frames), n)]
        layers = self._motion_layers(s, cfg) if s.motion_frame else None
        if layers is not None:
            part = self._clip_part(target)
            try:
                _, poster = boomerang.make_clip(frames, part, fps=int(s.clip_fps or cfg["fps"]),
                                                boomerang=s.mode == "boomerang", loops=self._clip_loops(s),
                                                rotate=rotate,
                                                frame_layers=layers)
                s.poster = compose.save_jpeg(poster, s.folder / "poster.jpg", quality=85, taken=s.created)
                os.replace(part, target)
                s.clip = target
                return target
            except Exception as e:                   # noqa: BLE001 — a guest never loses a film to a frame
                log.warning("Durchgang %s: Film mit Rahmen „%s“ nicht erzeugt (%s: %s) – er entsteht ohne Rahmen",
                            s.uid, s.motion_frame_id, e.__class__.__name__, str(e)[:200])
                s.motion_frame, s.motion_frame_id = None, ""
            finally:
                self._drop_parts(part)
        # The camera's movie comes in the film's size already: its width is the width.
        width = movie_box[0] if movie_box else cfg.get("width", "auto")
        max_width = movie_box[0] if movie_box else int(cfg.get("max_width") or 1080)
        scale = "native" if movie_box else str(cfg.get("scale") or "native")
        # Encoded under a name of its own and moved into place when whole:
        # two takes of one run never write the same file (review 17.11.2026).
        part = self._clip_part(target)
        try:
            _, poster = boomerang.make_clip(frames, part, aspect=s.fmt, width=width,
                                            max_width=max_width,
                                            fps=int(s.clip_fps or cfg["fps"]),
                                            scale=scale,
                                            layout=cfg.get("layout", "crop" if s.mode == "boomerang" else "blur"), boomerang=s.mode == "boomerang",
                                            loops=self._clip_loops(s),
                                            rotate=rotate)
            s.poster = compose.save_jpeg(poster, s.folder / "poster.jpg", quality=85, taken=s.created)
            os.replace(part, target)
        finally:
            self._drop_parts(part)
        s.clip = target
        return target

    def _clip_ready(self, s: Session) -> None:
        """Wait for a film that is still being made after its recording (finalize)."""
        job, s.clip_job = s.clip_job, None
        if job is None:
            return
        try:
            job.result(timeout=300)
        except Exception as e:                       # noqa: BLE001 — the kiosk's error screen
            log.error("Durchgang %s (%s): Film nicht erzeugt: %s", s.uid, s.mode, e)
            raise HTTPException(503, "camera.generic") from e

    def _print_qr(self, s: Session) -> Optional[str]:
        """The link for the sheet's QR code, or None when none is printed.

        The template keeps its QR layer either way: the renderer then knows
        where the code would have stood and leaves nothing there — no empty
        window in a punched overlay, no "scan the code" caption flagged
        ``nur_mit_qr``. Removing the layer (as until November 2026) left the
        overlay's window showing the bare sheet.
        """
        return s.share_url if (self.profile["print"].get("qr_on_print") and s.share_url) else None

    def _texts(self, s: Session) -> dict:
        werte = {"event_name": s.event_name or self.store.local.get("booth_name", ""),
                 "date": time.strftime("%d.%m.%Y", time.localtime(s.created)),
                 "gast": s.guest_text or ""}
        # Hat der Gast etwas geschrieben, steht es dort, wo sonst die Unterzeile
        # des Anlasses steht. So funktioniert es mit jeder Vorlage, ohne dass
        # eine davon etwas von Gästetexten wissen müsste; wer eine eigene
        # Vorlage baut, kann {gast} auch woanders hinsetzen.
        if s.guest_text:
            werte["subline"] = s.guest_text
        return werte

    def render(self, s: Session, look: str, max_px: Optional[int] = None,
               drawn: bool = True, keep: Optional[list] = None) -> Image.Image:
        """The run's picture in one look; ``max_px`` = a preview from the working copies.

        ``keep``: a list that receives the shots as prepared and looked (full
        size), for the single pictures of a collage.
        """
        pool = self.render_pool()
        if max_px:
            # A preview: from the run's working copies, decoded once for all looks.
            prepared = self.prepared_shots(s, min(self.WORK_PX, max(520, max_px * 2)))
        else:
            prepared = list(pool.map(lambda p: self.prepare_shot(self._open_shot(p), s), list(s.shots)))
        shots = list(pool.map(lambda im: self.apply_look(look, im), prepared))
        if keep is not None:
            keep.extend(shots)
        if s.mode == "photo" and not s.template:
            out = compose.crop_to_aspect(shots[0], s.fmt)
        else:
            tpl = dict(self._fit_to_paper(s.template or compose.BUILTIN_TEMPLATES[0]))
            bg = s.background if (s.background and (s.background.startswith("#")
                                                    or (self.assets_dir / s.background).exists())) else None
            out = compose.render_template(tpl, shots, assets_dir=self.assets_dir, background=bg,
                                          qr_url=self._print_qr(s), texts=self._texts(s),
                                          style_cfg=self.profile.get("collage_style"),
                                          einpassen=self.profile.get("collage_fit", "auto"))
        out = self.apply_frame(out, s, "photo")
        # The guest's drawing goes on last, over frame and collage sheet: it is
        # drawn on exactly what the result screen showed. Painted on this fresh
        # render every time, so drawing again never lands on an older drawing.
        if drawn and s.drawing:
            out = drawing.render(out, s.drawing)
        if max_px:
            out.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        return out

    def draw_on(self, s: Session, strokes: List[dict]) -> Session:
        """The guest drew on the finished picture: render the run again with it.

        Exactly what a change of look does (``finalize`` again): result, web
        version, thumbnail and print file are made anew from the untouched
        shots, the library row is updated and the upload queued again under
        the same id, so the gallery replaces its copy. A print already made
        stays as it was; no automatic second print. An empty list takes the
        drawing away.
        """
        s.drawing = list(strokes)
        return self.finalize(s, s.look)

    def finalize(self, s: Session, look: str) -> Session:
        look = self.canonical_look(look)      # an old look id from a kiosk page still open
        if look not in self.look_names():
            look = "original"
        with s.lock:
            if s.is_video:
                self._clip_ready(s)
                if not s.clip:
                    raise HTTPException(409, "Clip fehlt")
                s.result = s.clip
                s.thumb = compose.save_jpeg(Image.open(s.poster), s.folder / "thumb.jpg", quality=80, max_px=480,
                                            taken=s.created)
                s.print_file = self._boomerang_print_card(s) if (s.key_frames and s.template) else None
            elif s.mode == "gif":
                if len(s.shots) < s.shots_needed:
                    raise HTTPException(409, "Aufnahmen fehlen")
                cfg = self.profile["gif"]
                pool = self.render_pool()
                # Every file of a GIF run is at most a film's width: made from
                # the working copies, not from the 24 MP originals.
                stills = list(pool.map(lambda im: self.apply_look(look, im),
                                       self.prepared_shots(s, self._gif_work_px())))   # the print: no photo frame
                frames = list(pool.map(lambda fr: self.apply_frame(fr, s, "photo"), stills))
                layers = self._motion_layers(s, cfg) if s.motion_frame else None
                if layers is not None:
                    # The motion frame around every picture, like a boomerang's film.
                    frames = self._gif_in_frame(s, frames, layers)
                card_job = pool.submit(self._gif_print_card, s, stills)
                gif_path, first, _ = boomerang.make_gif(
                    frames, s.folder / "result.gif", aspect=s.fmt,
                    frame_ms=int(cfg.get("frame_ms", 400)), pingpong=bool(cfg.get("pingpong", True)),
                    loops=int(cfg.get("loops", 0)), max_width=int(cfg.get("max_width") or 1080),
                    scale=str(cfg.get("scale") or "native"), mp4=None, mapper=pool.map)
                s.result = gif_path
                s.thumb = compose.save_jpeg(first, s.folder / "thumb.jpg", quality=80, max_px=480, taken=s.created)
                s.poster = compose.save_jpeg(first, s.folder / "poster.jpg", quality=85, taken=s.created)
                s.print_file = card_job.result()
                films = self._gif_films_start(s, frames)
            else:
                if len(s.shots) < s.shots_needed:
                    raise HTTPException(409, "Aufnahmen fehlen")
                looked: List[Image.Image] = []
                laid_out = s.mode == "collage" or (s.mode == "photo" and s.template is not None)
                img = self.render(s, look, keep=looked if laid_out else None)
                pool = self.render_pool()
                # The files of one result are independent of each other: side by side.
                jobs = [pool.submit(compose.save_jpeg, img, s.folder / "result.jpg", quality=93, taken=s.created),
                        pool.submit(compose.save_jpeg, img, s.folder / "result_web.jpg", quality=86, max_px=2048,
                                    taken=s.created),
                        pool.submit(compose.save_jpeg, img, s.folder / "thumb.jpg", quality=80, max_px=480,
                                    taken=s.created),
                        pool.submit(self._still_print_card, s, img)]
                if s.mode == "collage":
                    s.anim = self._collage_gif(s, look)
                    s.singles = self._collage_singles(s, look, looked)
                elif laid_out:
                    # A photo on a layout: the plain photo is kept and uploaded too.
                    s.singles = self._collage_singles(s, look, looked, always=True)
                s.result, s.result_web, s.thumb, s.print_file = [j.result() for j in jobs]
            # Zwei verschiedene Fragen, die vorher eine waren:
            #  * ``frisch``: Das Ergebnis wurde gerade neu gerendert. Gilt auch
            #    nach „Nochmal" — der Löschtimer muss dann neu anlaufen, sonst
            #    verschwindet das Bild nach der alten Uhr.
            #  * ``neu``: Dieser Durchgang ist erstmalig fertig. Nur daran
            #    hängen der Sitzungszähler und der automatische Druck.
            frisch = not s.finalized
            neu = not s.abgerechnet
            s.look, s.finalized, s.abgerechnet = look, True, True
            if s.mode != "gif":
                films = None
        if s.deleted:
            # Deleted while it was being rendered (a look chosen and "Löschen"
            # tapped, the operator's clean-up): no kiosk tile, no queue entries
            # for files that are already gone.
            raise HTTPException(410, "Durchgang verworfen")
        self._library_upsert(s, look)
        self.usb_mark_dirty()
        self._enqueue(s)
        if films is not None:
            films.start()                      # the GIF's films follow the result (see _gif_films_start)
        if neu:
            self.sessions_total += 1
            self.zahlen.durchgang(s.event_slug or self._zahlen_event(), s.mode)
            option = str((s.consent or {}).get("option") or "")
            if option in ("gallery", "print", "notice"):
                self.zahlen.zaehle(s.event_slug or self._zahlen_event(), f"consent.{option}")
            if self.profile["print"].get("auto") and s.print_file:
                try:
                    self.print_uid(s.uid, 1)
                except HTTPException as e:
                    log.info("Automatischer Druck übersprungen: %s", e.detail)
        if frisch and self.run_print_only(s):
            self._schedule_wipe(s.uid)
        plugins.safe_call(self.plugins, "booth_media_ready", session=s,
                          media={"kind": s.mode, "path": str(s.result), "share_url": s.share_url})
        self.webhooks.emit("media_ready", {"uid": s.uid, "mode": s.mode, "look": look,
                                           "event": s.event_slug, "share_url": s.share_url,
                                           "file": s.result.name if s.result else ""})
        return s

    def _collage_singles(self, s: Session, look: str,
                         looked: Optional[List[Image.Image]] = None, always: bool = False) -> List[tuple]:
        """Die Einzelbilder einer Collage, fertig für die Galerie.

        Auf dem Papier bleibt die Collage die Collage. In der Galerie will
        mancher Betreiber aber jedes Bild einzeln haben — zum Herunterladen,
        zum Teilen, weil auf der Collage vier Leute und auf Bild drei nur
        eine steht. Das ist eine Einstellung, kein zweiter Durchgang: Es sind
        dieselben Aufnahmen, dieselbe Bearbeitung, nur einzeln gespeichert.

        ``looked``: the shots as render() made them (full size, prepared, in
        the look) — then they are not decoded a second time. A "Nur drucken"
        run gets none: nothing of it goes to a gallery.
        """
        if not always and str(self.profile.get("collage_upload") or "both") not in ("photos", "both"):
            return []
        if self.run_print_only(s):
            return []

        def eins(i: int, path: Path, fertig: Optional[Image.Image]):
            try:
                if fertig is None:
                    fertig = self.apply_look(look, self.prepare_shot(self._open_shot(path), s))
                fertig = self.apply_frame(fertig, s, "photo")
                fertig = compose.crop_to_aspect(fertig, s.fmt)
                gross = compose.save_jpeg(fertig, s.folder / f"einzel{i}.jpg", quality=93, taken=s.created)
                web = compose.save_jpeg(fertig, s.folder / f"einzel{i}_web.jpg", quality=86, max_px=2048,
                                        taken=s.created)
                return (gross, web)
            except Exception as exc:                  # noqa: BLE001 — die Collage steht schon
                log.warning("Einzelbild %s von %s nicht erzeugt: %s", i, s.uid, exc)
                return None

        fertige = list(looked or []) if looked and len(looked) == len(s.shots) else [None] * len(s.shots)
        jobs = [self.render_pool().submit(eins, i, path, fertig)
                for i, (path, fertig) in enumerate(zip(s.shots, fertige), 1)]
        return [r for r in (j.result() for j in jobs) if r is not None]

    def _collage_gif(self, s: Session, look: str) -> Optional[Path]:
        """Aus den Einzelbildern einer Collage zusätzlich ein GIF.

        Der Gast posiert einmal und bekommt zweierlei: den Streifen auf Papier
        und ein Filmchen fürs Telefon. Nur ab genügend Aufnahmen — aus zwei
        Bildern wird kein Film, sondern ein Zucken.

        Scheitert es, bleibt es bei der Collage. Eine Zugabe darf niemals den
        Durchgang kosten, der schon fertig ist.
        """
        cfg = self.profile.get("gif") or {}
        if not cfg.get("from_collage"):
            return None
        minimum = max(2, int(cfg.get("from_collage_min") or 4))
        if len(s.shots) < minimum:
            return None
        try:
            # At most 1080 wide: from the working copies, not the originals.
            frames = [self.apply_frame(self.apply_look(look, im), s, "photo")
                      for im in self.prepared_shots(s, self._gif_work_px())]
            gif_path, _first, _clip = boomerang.make_gif(
                frames, s.folder / "collage.gif", aspect=s.fmt,
                frame_ms=int(cfg.get("frame_ms", 400)), pingpong=bool(cfg.get("pingpong", True)),
                loops=int(cfg.get("loops", 0)), max_width=int(cfg.get("max_width") or 1080),
                scale=str(cfg.get("scale") or "native"), mp4=None)
            return gif_path
        except Exception as exc:                      # noqa: BLE001 — die Collage steht schon
            log.warning("GIF zur Collage %s nicht erzeugt: %s", s.uid, exc)
            return None

    def _library_upsert(self, s: Session, look: str) -> None:
        self.library.upsert(uid=s.uid, event_slug=s.event_slug, mode=s.mode, result=s.result, thumb=s.thumb,
                            print_file=s.print_file, share_url=s.share_url, look=look,
                            # Bei einer Collage ist „anim" das GIF, bei einem
                            # GIF-Durchgang der Boomerang: in beiden Fällen die
                            # bewegte Zweitfassung, zwischen der der Gast am
                            # Ergebnisbildschirm umschalten kann.
                            anim=s.anim or s.boom, consent=s.consent or None)

    def _still_print_card(self, s: Session, img: Image.Image) -> Path:
        if s.mode == "photo" and not s.template:
            tpl = self._fit_to_paper(compose.single_print_template(
                s.fmt, bool(self.profile["print"].get("qr_on_print") and s.share_url), self.profile.get("print_qr")))
            card = compose.render_template(tpl, [img], qr_url=s.share_url, texts=self._texts(s),
                                           style_cfg=self.profile.get("collage_style"),
                                           einpassen=self.profile.get("collage_fit", "auto"))
            card = self.apply_frame(card, s, "print")
        else:
            # Collage and frame are in the template already. A template for a
            # single 2×6 strip goes out as the 10×15 sheet with the strip
            # twice; every other paper is passed through unchanged.
            # The paper the collage was rendered on (render() fitted it, if asked to).
            papier = (self._fit_to_paper(s.template) or {}).get("paper")
            card = compose.druckbogen(img, papier)
            self._papier_pruefen(papier)
        return compose.save_jpeg(card, s.folder / "print.jpg", quality=95, progressive=False, taken=s.created)

    def _boomerang_print_card(self, s: Session) -> Optional[Path]:
        """The print sheet of a boomerang (``boomerang.print``): its key frames on the chosen layout.

        Rendered like a collage's sheet — the same template fitted to the
        paper, the slots filled by the collage rules (``collage_fit``, a
        slot's own fit), the QR code, the texts, the style, a two-up strip
        doubled — with the frame overlay for print, like the GIF sheet.
        """
        try:
            return self._boomerang_sheet(s)
        except Exception as exc:                      # noqa: BLE001 — the film stands; no sheet, no print
            log.warning("Druckbogen zum Boomerang %s nicht erzeugt: %s", s.uid, exc)
            return None

    def _boomerang_sheet(self, s: Session) -> Path:
        tpl = dict(self._fit_to_paper(s.template))
        card = compose.render_template(tpl, list(s.key_frames), assets_dir=self.assets_dir,
                                       qr_url=self._print_qr(s), texts=self._texts(s),
                                       style_cfg=self.profile.get("collage_style"),
                                       einpassen=self.profile.get("collage_fit", "auto"))
        card = self.apply_frame(card, s, "print")
        papier = tpl.get("paper")
        card = compose.druckbogen(card, papier)
        self._papier_pruefen(papier)
        return compose.save_jpeg(card, s.folder / "print.jpg", quality=95, progressive=False, taken=s.created)

    def _gif_druckvorlage(self) -> Optional[dict]:
        """The operator's own sheet for GIF runs (``gif.print_template``), or None.

        Only a plain file name among the booth's media; a missing or broken
        file means the built-in sheet — a print must never fail for it.
        """
        name = templates.datei_name((self.profile.get("gif") or {}).get("print_template"), (".json",))
        if not name or not (self.assets_dir / name).is_file():
            return None
        try:
            tpl = compose.load_template(self.assets_dir / name)
        except (ValueError, OSError):
            log.warning("GIF-Druckvorlage %s nicht lesbar — eingebaute Vorlage", name)
            return None
        return tpl if isinstance(tpl, dict) and tpl.get("slots") else None

    def _papier_pruefen(self, papier: Optional[str]) -> None:
        """Say once per paper when the printer's profile does not list a template's paper.

        Printing goes ahead — the driver scales or crops — but the operator
        should know before the guests see a cut-off sheet.
        """
        from .printing import models
        mid = (self.profile.get("printer") or {}).get("model")
        if not papier or models.kann_papier(mid, papier):
            return
        gemeldet = self.__dict__.setdefault("_papier_gemeldet", set())
        if papier not in gemeldet:
            gemeldet.add(papier)
            log.warning("Vorlage für Papier %s, aber das Druckprofil (%s) führt es nicht — "
                        "der Treiber wird skalieren oder beschneiden", papier, mid or "SELPHY")

    def _druckpapier(self) -> Optional[str]:
        """The template paper (vorlagen_kern.PAPIERE) of the first printer, or None if unknown.

        From the model and medium set at the box (or by the gallery's hardware
        card): a SELPHY's postcard is "10x15", a DNP with 15x20 media "15x20".
        No model, or a medium without a template paper: None.
        """
        from .printing import models
        cfgs = printer_configs(self.store.local)
        first = cfgs[0] if cfgs else (self.store.local.get("printer") or {})
        mid = str(first.get("model") or "")
        if not mid or mid not in models.NACH_ID:
            return None
        papier = models.medium(mid, first.get("medium")).get("papier")
        return papier if papier in templates.PAPIERE else None

    def _fit_to_paper(self, tpl: Optional[dict]) -> Optional[dict]:
        """Profile ``print.fit_to_paper``: the template fitted to the printer's paper.

        Off, the very same template comes back (the print stays as it was, to
        the pixel). On, a template of the same aspect family as the paper is
        scaled by one factor (``fuer_papier``, no bleed: the sheet is the
        paper's canvas, which is what the booth has always sent). A template
        already on that canvas, of another family, or without a known printer
        model stays as it is; the driver handles it as before.
        """
        if not isinstance(tpl, dict) or not (self.profile.get("print") or {}).get("fit_to_paper"):
            return tpl
        ziel = self._druckpapier()
        if not ziel:
            return tpl
        breite, hoehe = templates.PAPIERE[ziel]["canvas"]
        if sorted(int(x) for x in (tpl.get("canvas") or [0, 0])[:2]) == sorted([breite, hoehe]):
            return tpl
        try:
            neu, _hinweise = templates.fuer_papier(tpl, ziel, anschnitt_mm=0.0)
        except ValueError as e:
            gemeldet = self.__dict__.setdefault("_anpassen_gemeldet", set())
            if (tpl.get("id"), ziel) not in gemeldet:
                gemeldet.add((tpl.get("id"), ziel))
                log.info("Vorlage %s nicht an das Papier %s angepasst: %s", tpl.get("id"), ziel, e)
            return tpl
        return neu

    def _gif_print_card(self, s: Session, stills: List[Image.Image]) -> Path:
        # On paper a GIF is its stills side by side. Until 15.11.2026
        # only the first frame was printed — one of six pictures.
        tpl, auswahl = compose.gif_print_template(s.fmt, len(stills))
        eigene = self._gif_druckvorlage()
        if eigene is not None:
            tpl = eigene
            plaetze = int(eigene.get("shots") or len({sl.get("shot", i) for i, sl in
                                                       enumerate(eigene.get("slots") or [])}) or 1)
            auswahl = compose.gif_auswahl(len(stills), plaetze)
        tpl = dict(self._fit_to_paper(tpl))
        card = compose.render_template(tpl, [stills[i] for i in auswahl], assets_dir=self.assets_dir,
                                       qr_url=self._print_qr(s), texts=self._texts(s),
                                       style_cfg=self.profile.get("collage_style"),
                                       einpassen=self.profile.get("collage_fit", "auto"))
        card = self.apply_frame(card, s, "print")
        return compose.save_jpeg(card, s.folder / "print.jpg", quality=95, progressive=False, taken=s.created)

    def _gif_films_start(self, s: Session, frames: List[Image.Image]) -> Optional[threading.Thread]:
        """The two films of a GIF run — made after the result screen, not before it.

        Measured on the booth PC (15.11.2026, six shots, 15 s films): the GIF
        stood at +23 s, the MP4 at +31 s, the 9:16 film at +52 s, and the
        guest looked at "Daten werden bearbeitet" for all of it. The guest
        sees the GIF; the films are for the gallery and the stick. So they are
        made in the background and queued for upload when they exist. Print,
        QR code and the GIF's upload do not wait for them.

        Made right away, as before, only when the guest may switch between GIF
        and boomerang on the result screen (``gif.boomerang.guest_choice``).
        Not made at all for a "Nur drucken" run: nothing of it travels. A look
        chosen later starts new films; the older ones are dropped.
        Returns the thread to start once the result is on record, or None.
        """
        cfg = self.profile["gif"]
        want_mp4 = bool(cfg.get("also_mp4", True))
        want_boom = bool((cfg.get("boomerang") or {}).get("enabled"))
        sofort = want_boom and bool((cfg.get("boomerang") or {}).get("guest_choice"))
        s.film_gen = getattr(s, "film_gen", 0) + 1
        gen = s.film_gen
        # Films of an earlier look must not be queued with this one.
        for name in ("result.mp4", "boomerang.mp4"):
            try:
                (s.folder / name).unlink()
            except OSError:
                pass
        s.clip, s.boom = None, None
        if self.run_print_only(s) and not sofort:
            return None
        if not (want_mp4 or want_boom):
            return None

        def mp4(target: Path) -> Optional[Path]:
            try:
                return boomerang.make_gif_film(
                    frames, target, aspect=s.fmt, frame_ms=int(cfg.get("frame_ms", 400)),
                    pingpong=bool(cfg.get("pingpong", True)), max_width=int(cfg.get("max_width") or 1080),
                    scale=str(cfg.get("scale") or "native"), seconds=float(cfg.get("video_seconds") or 0))
            except Exception as e:                     # noqa: BLE001 — the GIF alone is enough
                log.warning("MP4 alongside the GIF failed: %s", e)
                return None

        if sofort:
            pool = self.render_pool()
            clip_job = pool.submit(mp4, s.folder / "result.mp4") if want_mp4 else None
            s.boom = self._gif_boomerang(s, frames)
            s.clip = clip_job.result() if clip_job else None
            return None

        def later() -> None:
            t0 = time.monotonic()
            boomerang.in_background()          # the next guest's live view and shutter come first
            # Own names while they are made: a newer look or a deletion must
            # never find half a film under the real name.
            tmp_clip = s.folder / f"result.g{gen}.mp4"
            tmp_boom = s.folder / f"boomerang.g{gen}.mp4"
            clip = mp4(tmp_clip) if want_mp4 and not s.deleted else None
            boom = self._gif_boomerang(s, frames, tmp_boom) if want_boom and not s.deleted else None
            with s.lock:
                current = getattr(s, "film_gen", 0) == gen and not s.deleted
                if current:
                    if clip:
                        os.replace(clip, s.folder / "result.mp4")
                        s.clip = s.folder / "result.mp4"
                    if boom:
                        os.replace(boom, s.folder / "boomerang.mp4")
                        s.boom = s.folder / "boomerang.mp4"
            for leftover in (tmp_clip, tmp_boom):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            if not current:
                return
            log.info("Durchgang %s: Filme zum GIF fertig nach %.1f s", s.uid, time.monotonic() - t0)
            try:
                self._library_upsert(s, s.look)
                self._enqueue(s, films_only=True)
                self.usb_mark_dirty()
            except Exception as e:                     # noqa: BLE001 — the run itself is complete
                log.warning("Filme zu %s nicht eingereiht: %s", s.uid, e)
            if s.deleted:
                self.outbox.cancel_session(s.uid)

        s.films_thread = threading.Thread(target=later, name=f"films-{s.uid}", daemon=True)
        return s.films_thread

    def wait_for_films(self, s: Session, timeout: float = 60.0) -> None:
        """Until the films of a GIF run started by finalize are done (tests, operator tools)."""
        th = getattr(s, "films_thread", None)
        if th is not None and th.is_alive():
            th.join(timeout)

    def _gif_boomerang(self, s: Session, frames: list, target: Optional[Path] = None) -> Optional[Path]:
        """Dasselbe GIF noch einmal als Boomerang — in den Maßen des Betreibers.

        Das MP4 neben dem GIF hat dessen Format (meist 4:5). Fürs Handy wird
        9:16 gebraucht, und dabei darf niemand wegfallen, der am Rand steht.
        Deshalb eine eigene Fassung statt eines Zuschnitts.
        """
        cfg = (self.profile.get("gif") or {}).get("boomerang") or {}
        if not cfg.get("enabled") or not frames:
            return None
        try:
            return boomerang.make_boomerang_from_stills(
                frames, target or (s.folder / "boomerang.mp4"),
                aspect=str(cfg.get("format") or "9:16"),
                layout="crop" if cfg.get("layout") == "crop" else "blur",
                frame_ms=int((self.profile.get("gif") or {}).get("frame_ms", 400)),
                fps=int(cfg.get("fps") or 15), loops=int(cfg.get("loops") or 3),
                max_width=int(cfg.get("max_width") or 1080),
                scale=str(cfg.get("scale") or "native"),
                min_seconds=float((self.profile.get("gif") or {}).get("video_seconds") or 0))
        except Exception as exc:                  # noqa: BLE001 — das GIF steht schon
            log.warning("Boomerang zum GIF %s nicht erzeugt: %s", s.uid, exc)
            return None

    def _enqueue(self, s: Session, films_only: bool = False) -> None:
        """Queue a finished run for every target; ``films_only``: just the films of a GIF run."""
        if self.run_print_only(s):
            # Nichts in die Warteschlange: kein Server, kein Ordner, kein Stick.
            # Auch nicht „nur die Webfassung" — gar nichts.
            return
        targets = self.agent.target_names()
        meta = {"taken_at": s.taken_at, "format": s.fmt, "look": s.look, "mode": s.mode,
                "template": (s.template or {}).get("id"), "frame": self.frame_on_photo()}
        # The event's file-name rule (gallery: app/dateinamen.py) travels with
        # each file, so a stick or an FTP server names it offline
        # (photobox_sync.targets.file_name). Only when the rule is on.
        ev = self.profile.get("event") or {}
        if str(ev.get("file_naming") or "") == "event_datetime":
            meta.update({"file_naming": "event_datetime", "event_name": str(ev.get("name") or ""),
                         # A photo on a layout always has its one single (.f1), even
                         # before it is rendered: the numbers of a run never shift.
                         "n_singles": 1 if (s.mode == "photo" and s.template) else len(s.singles or [])})
        # Network targets of the local "targets" list (the second target card:
        # FTP/FTPS/SFTP) get every file the booth keeps of the run
        # (17.11.2026, "everything must reach the second target"): the collage sheet
        # and its singles whatever the gallery shows, the camera originals
        # whatever ``upload_originals`` says for the gallery, the print sheet
        # when the target asks for it. The target's own choices
        # (``originals``, ``print_sheets``) decide per file at upload time.
        from photobox_sync.targets import NETWORK_TYPES
        netz = {str(c.get("name") or c.get("type") or ""): c for c in (self.store.local.get("targets") or [])
                if isinstance(c, dict) and str(c.get("type") or "").lower() in NETWORK_TYPES}
        for t in targets:
            alles = t in netz
            # A network target also gets the event's name: it fills {event_name}
            # in the second target's folder (zielordner.py). Others keep their meta.
            own = {**meta, "event_name": str(ev.get("name") or "")} if alles and ev.get("name") else meta
            common = dict(target=t, session_uid=s.uid, event_slug=s.event_slug, meta=own)
            if alles and not films_only and netz[t].get("print_sheets") and s.print_file and Path(s.print_file).is_file() \
                    and Path(s.print_file) != Path(s.result or ""):
                self.outbox.add(uid=f"{s.uid}.print", kind="print", variant="print", path=s.print_file,
                                priority=9, **common)
            if s.is_video:
                self.outbox.add(uid=s.uid, kind=s.mode, variant="web", path=s.result, poster=s.poster, priority=1, **common)
                continue
            if s.mode == "gif" and films_only:
                self._enqueue_gif_films(s, common)
                continue
            if s.mode == "gif":
                # the animated GIF is the result; the single stills follow as originals
                self.outbox.add(uid=s.uid, kind="gif", variant="web", path=s.result, poster=s.poster, priority=1, **common)
                self._enqueue_gif_films(s, common)
                if self.profile.get("upload_originals") or alles:
                    for i, shot in enumerate(s.shots, 1):
                        self.outbox.add(uid=f"{s.uid}.{i}", kind="original", variant="original", path=shot,
                                        priority=8, **common)
                continue
            # Was von einer Collage in der Galerie erscheint, entscheidet der
            # Betreiber: das Blatt, die Einzelbilder oder beides.
            collage_modus = str(self.profile.get("collage_upload") or "both")
            blatt_hoch = s.mode != "collage" or collage_modus in ("collage", "both") or alles
            if blatt_hoch:
                self.outbox.add(uid=s.uid, kind=s.mode, variant="web", path=s.result_web, priority=1, **common)
                self.outbox.add(uid=s.uid, kind=s.mode, variant="original", path=s.result, priority=6, **common)
            for i, (gross, web) in enumerate(s.singles or [], 1):
                self.outbox.add(uid=f"{s.uid}.f{i}", kind="photo", variant="web", path=web,
                                priority=2 if not blatt_hoch else 5, **common)
                self.outbox.add(uid=f"{s.uid}.f{i}", kind="photo", variant="original", path=gross,
                                priority=7, **common)
            if s.anim:
                # Eigene Kennung, sonst verdrängt das GIF die Collage: In der
                # Galerie ist ein Medium durch Kennung und Fassung bestimmt.
                self.outbox.add(uid=f"{s.uid}.gif", kind="gif", variant="web", path=s.anim,
                                poster=s.thumb, priority=4, **common)
            originale = bool(self.profile.get("upload_originals")) or alles
            if originale and s.mode == "collage":
                for i, shot in enumerate(s.shots, 1):
                    self.outbox.add(uid=f"{s.uid}.{i}", kind="original", variant="original", path=shot, priority=8, **common)
            elif originale and s.mode == "photo":
                self.outbox.add(uid=f"{s.uid}.1", kind="original", variant="original", path=s.shots[0], priority=8, **common)
        # delete() marks the run before it clears the queue. A deletion that
        # came in while the entries above were added is seen here and its
        # clearing repeated, so no entry outlives the run (seen on the booth
        # PC: the second target kept trying files of deleted runs).
        if s.deleted:
            self.outbox.cancel_session(s.uid)

    def _enqueue_gif_films(self, s: Session, common: dict) -> None:
        # Der Boomerang bekommt eine eigene Kennung — sonst verdrängt er
        # in der Galerie das GIF, das er ergänzen soll.
        if s.boom and s.boom.exists():
            self.outbox.add(uid=f"{s.uid}.boom", kind="boomerang", variant="web",
                            path=s.boom, poster=s.thumb, priority=3, **common)
        # The film of the GIF (gif.also_mp4): the same pictures as a
        # video, which is what phones and messengers actually play.
        # Its own id, or it would push the GIF out of the gallery.
        film = s.folder / "result.mp4"
        if film.is_file():
            self.outbox.add(uid=f"{s.uid}.mp4", kind="video", variant="web",
                            path=film, poster=s.thumb, priority=3, **common)

    def delete(self, uid: str, slip: bool = True) -> None:
        s = self.sessions.get(uid)
        if s:
            s.deleted = True
        item = self.library.delete(uid, slip=slip)
        self.outbox.cancel_session(uid)
        try:
            self.printer.drop_session(uid)       # was noch nicht gedruckt ist, geht auch nicht mehr raus
        except Exception as e:                   # noqa: BLE001 — Löschen darf daran nicht scheitern
            log.warning("Druckaufträge zu %s nicht verworfen: %s", uid, e)
        folder = s.folder if s else (Path(item["result"]).parent if item else None)
        if folder and folder.exists() and self.sessions_dir in folder.parents:
            shutil.rmtree(folder, ignore_errors=True)
        log.info("Durchgang %s gelöscht", uid)

    def deleted_in_gallery(self, uid: str) -> None:
        """The gallery deleted this run (guest, operator or retention).

        Same as deleting it here — files, kiosk tile, queued uploads, queued
        prints — but without a deletion slip, otherwise the deletion would
        travel back and forth. Print counters stay, as with any deletion.
        """
        if not uid or not (self.library.get(uid) or uid in self.sessions
                           or self.outbox.offen_fuer_session(uid)["offen"]):
            return
        self.delete(uid, slip=False)

    # ── Testbilder entfernen ──────────────────────────────
    # After setting up, the booth holds the operator's test pictures; before
    # the guests come it should be neutral again. Only the runs of the event
    # set on the booth and runs without an event are offered — an earlier
    # event's runs may still wait for their upload and are not test pictures.
    #: Up to this many runs without an event a confirmation is enough; above
    #: it, or as soon as the runs belong to an event, the number is typed.
    TESTBILDER_GRENZE = 20
    #: An unfinished run younger than this is a guest at the booth right now.
    GAST_AKTIV_SEKUNDEN = 600

    def _testbilder(self) -> tuple:
        slug = str((self.profile.get("event") or {}).get("slug") or "")
        slugs = sorted({"", slug})
        return slugs, self.library.on_the_booth(slugs)

    def _gast_laeuft(self) -> bool:
        grenze = time.time() - self.GAST_AKTIV_SEKUNDEN
        return any(not s.finalized and not s.deleted and s.created >= grenze
                   for s in list(self.sessions.values()))

    def session_busy(self) -> bool:
        """A guest's run is under way: started and not finished, or a print going through.

        The own window asks before it reloads a silent page or restarts the
        booth (window.OwnWindow._defer): either would cut a run short.
        """
        return self._gast_laeuft() or bool(self.printer.busy)

    def testbilder_stand(self) -> dict:
        slugs, runs = self._testbilder()
        heute = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        im_event = sum(1 for r in runs if r["event_slug"])
        return {
            "anzahl": len(runs),
            "heute": sum(1 for r in runs if r["created"] >= heute),
            "ohne_event": len(runs) - im_event,
            "im_event": im_event,
            "event": str((self.profile.get("event") or {}).get("name") or ""),
            "in_galerie": sum(1 for r in runs if r.get("geliefert")
                              or self.outbox.offen_fuer_session(r["uid"])["erledigt"]),
            "andere_events": self.library.others_on_the_booth(slugs),
            "laeuft": self._gast_laeuft(),
            "tippen": im_event > 0 or len(runs) > self.TESTBILDER_GRENZE,
            "grenze": self.TESTBILDER_GRENZE,
        }

    def testbilder_entfernen(self, anzahl: int, getippt: str = "", zaehler: bool = False,
                             papier: bool = False) -> dict:
        """Remove the test runs from this booth, through the one deletion path.

        Not the gallery: what reached it stays there (no deletion slip). The
        print and evening counters of these runs only with ``zaehler``; the
        paper and ribbon counts only with ``papier`` — that paper was used.
        """
        if self._gast_laeuft():
            raise HTTPException(409, "Gerade läuft ein Durchgang. Bitte warten, bis der Gast fertig ist.")
        stand = self.testbilder_stand()
        if int(anzahl) != stand["anzahl"]:
            raise HTTPException(409, f"Auf der Box sind jetzt {stand['anzahl']} Durchgänge, nicht {anzahl}. "
                                     "Bitte die Karte neu laden.")
        if stand["tippen"] and str(getippt).strip() != str(stand["anzahl"]):
            raise HTTPException(409, f"Bitte zur Bestätigung die Zahl {stand['anzahl']} eintippen.")
        _slugs, runs = self._testbilder()
        uids = [r["uid"] for r in runs]
        offene_drucke = sum(1 for j in self.printer.spool.open_jobs() if j["session_uid"] in set(uids))
        wartende_uploads = sum(self.outbox.offen_fuer_session(u)["offen"] for u in uids)
        for uid in uids:
            self.delete(uid, slip=False)
        if zaehler:
            self.library.forget_prints(uids)
            for slug, day in {(r["event_slug"], Abendzahlen.heute(r["created"])) for r in runs}:
                self.zahlen.vergessen(slug, day)
            self.sessions_total = max(0, self.sessions_total - len(uids))
        if papier:
            self.printer.reset_counter()
            self.printer.paper_changed()
            self.printer.ribbon_changed()
        bericht = {"entfernt": len(uids), "uploads_verworfen": wartende_uploads,
                   "drucke_verworfen": offene_drucke, "in_galerie": stand["in_galerie"],
                   "zaehler": bool(zaehler), "papier": bool(papier)}
        text = (f"Testbilder entfernt: {len(uids)} Durchgänge, {wartende_uploads} wartende Uploads, "
                f"{offene_drucke} wartende Drucke; Zähler {'auf null' if zaehler else 'unverändert'}, "
                f"Papier/Farbband {'auf voll' if papier else 'unverändert'}")
        log.info("%s", text)
        self.logbuf.add("info", text, source="admin")
        return bericht

    def reprint(self, uid: str, copies: int = 1) -> dict:
        """The operator prints an earlier picture once more.

        Not bound to what guests may do (reprint switched off, quota used up):
        whoever holds the admin PIN decides about the paper. The sheets are
        counted all the same, or the paper counter would lie.
        """
        item = self.library.get(uid)
        if not item or not item.get("print_file") or not Path(item["print_file"]).exists():
            raise HTTPException(409, "Zu dieser Aufnahme gibt es kein Druckbild mehr.")
        copies = max(1, min(5, int(copies or 1)))
        try:
            self.printer.submit(PrintJob(self.sheet_for(Path(item["print_file"]), uid), copies, uid))
        except RuntimeError:
            raise HTTPException(503, "printer.none")
        self.library.add_prints(uid, copies)
        log.info("Nachdruck aus der Verwaltung: %s, %d Blatt", uid, copies)
        return {"queued": copies, "pending": getattr(self.printer, "pending", 0)}

    def print_uid(self, uid: str, copies: int, from_gallery: bool = False) -> dict:
        item = self.library.get(uid)
        if not item or not item.get("print_file") or not Path(item["print_file"]).exists():
            raise HTTPException(409, "Nichts zu drucken")
        if from_gallery and not (self.profile.get("print") or {}).get("reprint_from_gallery", True):
            raise HTTPException(403, "print.disabled")
        copies = max(1, int(copies))
        # Two requests at once (a double tap, the result screen and the kiosk
        # gallery) both read "one left" and both printed. The allowance is
        # only true until the prints are booked, so all three steps hold the
        # same lock.
        with self._print_lock:
            allow = self.print_allowance(uid)
            if not allow["enabled"]:
                raise HTTPException(403, "print.disabled")
            deferred = False
            if allow["error_code"] == "printer.error" and (self.profile.get("print") or {}).get("queue_while_empty") \
                    and not self.run_print_only(uid):
                # Paper or ribbon out, and the operator chose to queue: the
                # job waits in the spool and prints when the printer is ready
                # again (held prints retry by themselves). The allowance is
                # booked now, like any accepted print.
                if not allow["can_queue"]:
                    raise HTTPException(503, "print.queue_full")
                deferred = True
            elif allow["error_code"] in ("printer.offline", "printer.error"):
                raise HTTPException(503, allow["error_code"])
            if copies > allow["max_now"]:
                raise HTTPException(409, "print.limit")
            try:
                self.printer.submit(PrintJob(self.sheet_for(Path(item["print_file"]), uid), copies, uid,
                                             deferred=deferred))
            except RuntimeError:
                raise HTTPException(503, "printer.none")
            self.library.add_prints(uid, copies)
        if deferred:
            log.info("Druck vorgemerkt, bis Papier nachgelegt ist: %s, %d Blatt", uid, copies)
        return {"queued": copies, "pending": self.printer.pending, "deferred": deferred,
                **self.print_allowance(uid)}

    def _on_print_failed(self, job: PrintJob) -> None:
        """Ein Druck, der nicht durchging: Kontingent zurück — und im
        datensparsamen Betrieb sofort löschen.

        Dort wartet niemand mit neuem Papier: Das Bild soll nach dem Druck weg
        sein, und wenn es keinen Druck gibt, erst recht. Sonst läge es bis zum
        Ablauf des Sicherheitsnetzes herum.
        """
        if str(job.session_uid or "").startswith(self.TESTDRUCK_PREFIX):
            return                               # a test sheet books nothing to give back
        # Once per job: held, released, held again gives the sheet back once.
        # The marker lets _on_printed book it again if it prints after all.
        if job.session_uid and self.printer.spool.mark_quota_returned(job.spool_id):
            self.library.add_prints(job.session_uid, -job.copies)
        # The whole booth data-minimal: gone at once. A guest's own "Nur drucken"
        # waits instead: the result screen offers "Doch in die Galerie" or
        # "Verwerfen", and the safety-net timer still removes it if nobody answers.
        if job.session_uid and self.ephemeral:
            self._wipe(job.session_uid, reason="Druck fehlgeschlagen")

    def keep_in_gallery(self, uid: str) -> Session:
        """A "Nur drucken" run whose print failed goes to the gallery after all.

        Only on the guest's word (POST /api/session/{uid}/keep). From here it is
        an ordinary run: link, upload, kiosk tile; the wipe timers are off. The
        record says that the choice was changed.
        """
        s = self.sessions.get(uid)
        if s is None or s.deleted or not self.library.get(uid):
            raise HTTPException(410, "Durchgang ist schon gelöscht")
        with s.lock:
            for timers in (self._wipe_timers, self._idle_timers):
                timer = timers.pop(uid, None)
                if timer is not None:
                    timer.cancel()
            langs = self.profile.get("languages") or ["de"]
            s.print_only = False
            s.consent = {**self.consent_record("gallery", str(s.consent.get("lang") or langs[0])),
                         "changed_from": "print"}
            s.share_url = share.qr_target(self.profile, uid, s.mode)
            try:
                (s.folder / _EPHEMERAL_MARKER).unlink()
            except OSError:
                pass
            self.library.set_gallery(uid, s.share_url, s.consent)
        self._enqueue(s)
        log.info("Durchgang %s: nach fehlgeschlagenem Druck doch in die Galerie", uid)
        return s

    def _zahlen_event(self) -> str:
        return str(self.profile["event"].get("slug") or "")

    def _on_print_error(self, job: PrintJob) -> None:
        """One failed attempt at a printer — the sheet may still come out of another."""
        if not str(job.session_uid or "").startswith(self.TESTDRUCK_PREFIX):
            self.zahlen.zaehle(self._zahlen_event(), "print_errors")

    def _on_printed(self, job: PrintJob) -> None:
        if str(job.session_uid or "").startswith(self.TESTDRUCK_PREFIX):
            return                               # a test sheet: no guest, no webhook, no wipe
        # A held print whose allowance was given back came out after all
        # (self-retry, "Papier nachgelegt"): book it again, exactly once.
        if job.session_uid and self.printer.spool.take_quota_back(job.spool_id):
            self.library.add_prints(job.session_uid, job.copies)
        self.zahlen.zaehle(self._zahlen_event(), "prints", max(1, int(job.copies)))
        if job.tried or (self.printer.outcome(job.session_uid) or {}).get("moved_to"):
            self.zahlen.zaehle(self._zahlen_event(), "prints_backup", max(1, int(job.copies)))
        plugins.safe_call(self.plugins, "booth_printed", session_uid=job.session_uid, copies=job.copies)
        self.webhooks.emit("printed", {"uid": job.session_uid, "copies": job.copies})
        if (job.session_uid and self.run_print_only(job.session_uid)
                and self._privacy().get("delete_after", "print") == "print"):
            self._wipe(job.session_uid)

    # ── Wartung ───────────────────────────────────────────
    def test_capture(self) -> Path:
        return self.camera.capture(self.data / "logs" / f"test_{int(time.time())}.jpg")

    #: How long a test print waits for the printer to take the sheet before
    #: the job answers "queued". The job runs in the sync loop; a SELPHY that
    #: needs a minute must not hold the next heartbeat that long.
    TESTDRUCK_WARTEN = 20.0
    #: Run ids of test sheets start with this. They belong to no guest: no
    #: allowance, no wipe, no statistics.
    TESTDRUCK_PREFIX = "test-"

    def test_print(self, payload: Optional[Dict[str, Any]] = None) -> str:
        """Print a test sheet: one saved template, or the calibration page.

        ``payload`` comes from the gallery (job ``test_print``): ``template``
        names a template this booth knows (asset file or built-in id; never
        a path), ``content`` may carry the gallery's saved version of it,
        ``mark`` (default on) puts a small "Testdruck" label in the corner.
        Without ``template`` it is the calibration page, as it always was.

        A test sheet never counts against the event's allowance; it does use
        paper, so the sheet counters count it. The answer says what happened
        at the printer; a printer that is not ready is an error with its
        reason, so the gallery shows the job as failed.
        """
        payload = payload if isinstance(payload, dict) else {}
        self._testdruck_drucker_bereit()
        if str(payload.get("template") or "").strip():
            blatt, was = self._testdruck_vorlage(payload)
        else:
            blatt, was = self._kalibrierungsseite(), "Kalibrierungsseite"
        uid = f"{self.TESTDRUCK_PREFIX}{int(time.time() * 1000)}"
        self.printer.submit(PrintJob(blatt, 1, uid))
        return self._testdruck_ergebnis(uid, was)

    def _testdruck_drucker_bereit(self) -> None:
        from .printing.service import UNAVAILABLE
        stationen = list(self.printer.stations) if self.printer else []
        if not stationen:
            raise RuntimeError("Drucker nicht bereit: kein Drucker eingerichtet")
        if all(st.state() in UNAVAILABLE for st in stationen):
            gruende = [str(st._status.get("detail") or st.last_error or st.state()) for st in stationen]
            raise RuntimeError("Drucker nicht bereit: " + "; ".join(g for g in gruende if g)[:300])

    def _testdruck_ergebnis(self, uid: str, was: str) -> str:
        frist = time.time() + float(self.TESTDRUCK_WARTEN)
        stand: Dict[str, Any] = {}
        while time.time() < frist:
            stand = self.printer.outcome(uid)
            if stand.get("state") in ("printed", "stuck"):
                break
            time.sleep(0.1)
        slot = stand.get("slot") or "?"
        if stand.get("state") == "printed":
            return f"{was} gedruckt (Drucker {slot})"
        if stand.get("state") == "stuck":
            # A test sheet does not wait for new paper: it would come out
            # hours later, in the middle of the guests' prints.
            self.printer.drop_session(uid)
            raise RuntimeError(f"Drucker nicht bereit: {self.printer.last_error or 'Druck fehlgeschlagen'}")
        return f"{was} eingereiht (Drucker {slot}), noch nicht durch — {self.printer.pending} in der Schlange"

    def _testdruck_texte(self) -> Dict[str, str]:
        """What a sample sheet says: the event's name if there is one, the editor's sample otherwise."""
        name = str(self.profile["event"].get("name") or "").strip()
        return {"event_name": name or compose.BEISPIEL_TEXTE["event_name"],
                "date": time.strftime("%d.%m.%Y")}

    def _testdruck_vorlage(self, payload: Dict[str, Any]):
        """The sheet for one template, rendered like a guest's, with the sample photos."""
        from .media import vorlagen_kern, vorlagen_paket

        kennung = str(payload.get("template") or "").strip()
        vorlage: Optional[dict] = None
        datei = vorlagen_kern.datei_name(kennung, (".json",))
        if datei and isinstance(payload.get("content"), dict):
            # The gallery's saved version: newer than a copy on this booth
            # after an edit, and the same check the editor applies.
            try:
                vorlage, _ = vorlagen_kern.annehmen(payload["content"], messen=False)
            except ValueError as e:
                raise RuntimeError(str(e))
        elif datei and (self.assets_dir / datei).is_file():
            try:
                vorlage = compose.load_template(self.assets_dir / datei)
            except Exception as e:                       # noqa: BLE001
                raise RuntimeError(f"Vorlage {datei} nicht lesbar: {e}")
        if vorlage is None and not datei:
            vorlage = next((dict(t) for t in list(self.templates()) + list(compose.BUILTIN_TEMPLATES)
                            if t.get("id") == kennung), None)
        if vorlage is None:
            raise RuntimeError(f"Vorlage nicht auf dieser Box: {kennung[:80]}")
        fehlen = [m for m in vorlagen_paket.medien_von(vorlage) if not (self.assets_dir / m).is_file()]
        bild = compose.render_template(vorlage, compose.beispielbilder(vorlage.get("shots") or 1),
                                       assets_dir=self.assets_dir, qr_url=compose.BEISPIEL_QR,
                                       texts=self._testdruck_texte(),
                                       style_cfg=self.profile.get("collage_style"),
                                       einpassen=self.profile.get("collage_fit", "auto"))
        if payload.get("mark", True) is not False:
            bild = compose.testdruck_marke(bild)
        # The same sheet a guest's print would be: a single strip goes twice.
        bild = compose.druckbogen(bild, vorlage.get("paper"))
        ziel = self.data / "logs" / "testdruck.jpg"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        compose.save_jpeg(bild, ziel, quality=95)
        was = f"Testdruck „{str(vorlage.get('name') or kennung)[:60]}“"
        if fehlen:
            was += " (fehlt auf der Box: " + ", ".join(fehlen[:5]) + ")"
        return self.sheet_for(ziel), was

    def _kalibrierungsseite(self) -> Path:
        """Kalibrierungsseite.

        Bis zum 19.09.2026 war das ein weißes Blatt mit einem Rahmen und
        einem Datum. Es beantwortete „kommt etwas heraus?" und sonst nichts —
        nicht, wie viel der Drucker am Rand wegschneidet, nicht, ob der
        Treiber heimlich skaliert, und schon gar nicht, ob Hauttöne stimmen.
        Genau das sind aber die Fragen am Abend vor dem Event.
        """
        from .printing import kalibrierung
        from .printing.layout import Layout

        layout = Layout.from_profile(self.profile.get("print_layout"))
        cfg = self.profile.get("printer") or {}
        # Die Sprache der Box. Das Blatt wird von einem Menschen am Tisch
        # gelesen — es gehört in dessen Sprache, nicht in die des Codes.
        sprachen = self.profile.get("languages") or ["de"]
        blatt = kalibrierung.blatt(layout, self.data / "logs" / "kalibrierung.jpg",
                                   self._druckinfo(cfg), sprache=sprachen[0])
        # Durch dieselbe Schärfung wie ein echter Abzug: Die Linienfelder
        # sollen zeigen, was die Gäste bekommen, nicht was ohne Schärfung
        # herauskäme.
        return self._nachschaerfen(blatt)

    def _druckinfo(self, cfg: dict) -> dict:
        """Was auf der Kalibrierungsseite stehen muss, damit man sie zuordnen kann."""
        from .printing import models

        name = str(cfg.get("name") or "").strip()
        mid = cfg.get("model") or models.erkenne(name)
        modell = models.modell(mid) if mid else None
        medium = ""
        if mid and cfg.get("medium"):
            medium = str(models.medium(mid, cfg.get("medium")).get("name") or "")
        # Die Stufen heißen im Profil englisch, auf dem Blatt liest sie ein
        # Betreiber — dort gehören sie in seiner Sprache hin.
        stufen = {"off": "aus", "light": "leicht", "medium": "mittel", "strong": "stark"}
        stufe = str((self.profile.get("print") or {}).get("sharpen") or "off")
        return {
            "drucker": name or (modell["name"] if modell else "Standarddrucker"),
            "medium": medium,
            "schaerfe": stufen.get(stufe, stufe),
        }


# ══════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════

def _system_printers() -> list:
    from .printing.backends import CupsPrinter, WindowsPrinter
    try:
        return (WindowsPrinter() if os.name == "nt" else CupsPrinter()).list_printers()
    except Exception:
        return []


#: What answers a request from the network without an admin token. The pages
#: themselves are only HTML; their API calls are gated. /api/admin/* checks
#: its own token, /api/lock its PIN, /api/trigger its token. /assets/ holds
#: the operator's media (logo, sounds), which the admin page previews — no
#: guest photo is ever in there. See business_logic/index.md, register B3.
NETWORK_OPEN = frozenset({"/", "/admin", "/admin/vorlagen", "/api/about", "/api/trigger", "/api/lock",
                          "/favicon.ico"})
NETWORK_OPEN_PREFIXES = ("/static/", "/assets/", "/api/admin/")


#: The kiosk page and the admin pages: never from the cache. After an update
#: the kiosk browser has to load the page that names the new files.
PAGE_CACHE = "no-store"

#: Content-Security-Policy of the kiosk and the booth's admin pages (R2-U1).
#: No inline script and no inline handler: a value that slips past the
#: profile rules and the escaping still cannot run. Styles may be inline
#: (the kiosk sets custom properties such as --i in markup); a style cannot
#: run script. Everything else comes from the booth itself: media and the
#: live view from /api and /assets, fonts from /static, data:/blob: for
#: pictures the page draws itself (drawing, QR codes).
PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' data: blob:; font-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'self'")
PAGE_HEADERS = {"Content-Security-Policy": PAGE_CSP, "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer"}
#: CSS, JS, fonts, operator media: may be kept, but asked about every time
#: (ETag). On this PC a 304 costs a few bytes, and an update can never leave
#: yesterday's app.js running beside today's page — which is what happened
#: with no Cache-Control at all, when Chrome guesses a lifetime from the age
#: of the file. A version parameter per file would save the 304s and is not
#: worth a build step.
STATIC_CACHE = "no-cache"


class _RevalidatedStatic(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = STATIC_CACHE
        return response


def _is_loopback(host: str) -> bool:
    host = str(host or "").strip().lower()
    return host in ("localhost", "::1") or host.startswith("127.") or host.startswith("::ffff:127.")


#: Profile fields that never leave the booth in clear text (group, field).
_PROFILE_SECRETS = (("mail", "password"), ("access", "pin"), ("webhooks", "secret"), ("http_trigger", "token"))


#: Bounds the profile save accepts, path -> (min, max). The same table sits in
#: LIMITS of static/settings.js and in the gallery parser (admin_booth.py);
#: tests/test_local_settings_controls.py keeps this one and the browser's equal.
PROFILE_LIMITS = {
    "countdown.first": (1, 20), "countdown.next": (1, 20), "countdown.review_seconds": (0, 10),
    "countdown.smile_ms": (200, 3000), "review_seconds": (3, 60), "flash.preview_gain": (0, 100),
    "gif.shots": (2, 10), "gif.interval": (0.5, 10), "gif.frame_ms": (80, 3000),
    "gif.video_seconds": (0, 60), "gif.from_collage_min": (2, 8),
    "gif.boomerang.fps": (5, 60), "gif.boomerang.loops": (1, 10),
    "boomerang.seconds": (1, 10), "boomerang.fps": (5, 60), "boomerang.loops": (1, 10),
    "boomerang.min_total_seconds": (5, 60), "boomerang.max_total_seconds": (5, 60),
    "video.seconds": (1, 60), "video.fps": (5, 60),
    "attract.after_seconds": (10, 3600), "attract.seconds_per_item": (2, 120), "attract.dim": (0, 80),
    "attract.hint_size": (100, 250),
    "sounds.volume": (0, 1), "looks_max": (0, 24),
    "aufraeumen.nach_tagen": (1, 365), "aufraeumen.behalten_mindestens": (0, 1000),
    "meldungen.wiederholen_minuten": (0, 1440),
    "print.queue_max": (1, 50), "print.queue_max_hours": (1, 24),
}


def clamp_profile_patch(patch: dict, current: dict) -> None:
    """Pull the numbers of an incoming profile patch into PROFILE_LIMITS, in place.

    Only keys present in the patch are touched. Something that is not a finite
    number (text, bool, null) is replaced by the value the profile has now.
    """
    for path, (low, high) in PROFILE_LIMITS.items():
        keys = path.split(".")
        node, now = patch, current
        for k in keys[:-1]:
            node = node.get(k) if isinstance(node, dict) else None
            now = now.get(k) if isinstance(now, dict) else None
        if not isinstance(node, dict) or keys[-1] not in node:
            continue
        value = node[keys[-1]]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
            node[keys[-1]] = now.get(keys[-1]) if isinstance(now, dict) else None
            continue
        node[keys[-1]] = min(high, max(low, value))


def ziel_fehler_text(roh: str) -> str:
    """What went wrong with an upload target, for the person at the booth.

    The technical wording follows in brackets: whoever helps on the phone
    wants to hear it, but it must not be the first thing to read.
    """
    klein = roh.lower()
    if "wartet auf das passwort" in klein:
        return roh                              # already a sentence (photobox_sync.targets)
    if "anderen schlüssel" in klein:
        return roh
    if "552" in klein or "452" in klein or "quota" in klein or "no space" in klein or "disk full" in klein:
        satz = "Auf dem Server ist kein Platz mehr (Speicher oder Kontingent voll)."
    elif "das konto auf dem server nimmt" in klein:
        return roh                              # already a sentence (FtpsTarget._refused, live-push accounts)
    elif "nicht beschreibbar" in klein or "nicht anlegen" in klein or "553" in klein or "550" in klein \
            or "permission denied" in klein:
        satz = "Der Ordner auf dem Server ist nicht beschreibbar oder lässt sich nicht anlegen. Stimmen Ordner und Rechte?"
    elif "530" in klein or "anmeldung abgelehnt" in klein or "authentication" in klein or "login" in klein:
        satz = "Benutzer oder Passwort stimmen nicht."
    elif "mismatch" in klein or "doesn't match" in klein or "not valid for" in klein:
        satz = ("Das Zertifikat des Servers gehört zu einem anderen Namen. Bitte genau die Adresse eintragen, "
                "die der Anbieter nennt.")
    elif "certificate has expired" in klein:
        satz = "Das Zertifikat des Servers ist abgelaufen. Erneuern muss es der Betreiber des Servers."
    elif "certificate verify failed" in klein or "local issuer" in klein or "self-signed" in klein \
            or "self signed" in klein:
        # Said plainly: the kind (FTPS/SFTP/FTP) was right, the TLS talk got as far
        # as the certificate. Until 18.11.2026 this landed in the generic branch
        # below and sent operators off changing the kind.
        satz = ("Das Zertifikat des Servers ließ sich nicht prüfen: Die Box kennt die Stelle nicht, "
                "die es ausgestellt hat. An der gewählten Art liegt es nicht. Bei einem eigenen NAS "
                "ist das oft ein selbst ausgestelltes Zertifikat, sonst bitte die Box aktualisieren "
                "oder den Anbieter fragen. Die Dateien bleiben in der Warteschlange.")
    elif "unexpected_eof" in klein or "eof occurred in violation of protocol" in klein:
        # Since 18.11.2026 the FTPS target tolerates a server that ends a data
        # connection without TLS close_notify; if this still shows, the server
        # dropped the connection in the middle, not the kind that was chosen.
        satz = ("Der Server hat die verschlüsselte Datenverbindung abgebrochen. Bitte noch einmal prüfen; "
                "bleibt es dabei, die Box aktualisieren oder den Anbieter fragen. "
                "Die Dateien bleiben in der Warteschlange.")
    elif "refused" in klein:
        satz = "Der Server lehnt die Verbindung ab. Stimmen Adresse und Port?"
    elif "timed out" in klein or "timeout" in klein:
        satz = "Der Server antwortet nicht. Stimmt die Adresse, und hat die Box Internet?"
    elif "not known" in klein or "getaddrinfo" in klein or "name resolution" in klein:
        satz = "Diese Adresse gibt es nicht, oder die Box hat kein Internet."
    elif "certificate" in klein or "ssl" in klein or "tls" in klein:
        satz = "Die verschlüsselte Verbindung kommt nicht zustande. Passt die gewählte Art zum Server?"
    else:
        return roh
    return f"{satz} ({roh})"


def create_app(booth: Optional[Booth] = None) -> FastAPI:
    app = FastAPI(title="Photobox Booth", version=__version__, docs_url=None, redoc_url=None)
    b = booth or Booth()
    app.state.booth = b

    @app.middleware("http")
    async def _network_gate(request: Request, call_next):
        """Guest routes answer only on this PC once the booth listens on the network.

        The kiosk always runs here (127.0.0.1). With ``--host 0.0.0.0`` for
        remote admin or a Wi-Fi buzzer, every guest in the venue's Wi-Fi
        reached the live camera, every run's photos, printing and deleting.
        From the network only NETWORK_OPEN answers without more; everything
        else needs an admin token — or ``lan_guest_access`` for a second
        screen the operator set up on purpose. Deny by default, so a new
        guest route is closed until someone decides otherwise.
        """
        if not _is_loopback(b.listen_host) and not _is_loopback(request.client.host if request.client else ""):
            path = request.url.path
            open_ = path in NETWORK_OPEN or path.startswith(NETWORK_OPEN_PREFIXES)
            if not open_ and not b.store.local.get("lan_guest_access") and not _full_admin_token(request):
                return JSONResponse({"detail": "Nur an der Box selbst erreichbar."}, status_code=403)
        return await call_next(request)

    def _full_admin_token(request: Request) -> bool:
        token = request.headers.get("X-Admin-Token", "")
        exp = b.admin_tokens.get(token) if token else None
        return bool(exp and exp >= time.time() and token not in b.pin_change_tokens)

    def _letztes_thumb() -> str:
        """Der Weg zur zuletzt gemachten Aufnahme — oder leer.

        Im datensparsamen Betrieb bleibt er leer: Dort soll nach dem Druck
        nichts mehr zu sehen sein, auch nicht klein in der Ecke.
        """
        if b.ephemeral:
            return ""
        eintraege = b.library.list(b.profile["event"].get("slug", ""), 0, 1)
        return f"/media/{eintraege[0]['uid']}/thumb" if eintraege else ""

    def public_profile() -> dict:
        p = json.loads(json.dumps(b.profile))
        p.get("event", {}).pop("share_key", None)
        # Die PIN steht im Profil, damit sie fernwartbar ist. Hier fliegt sie
        # raus: /api/state liest jeder Gast-Browser mit.
        p.get("access", {}).pop("pin", None)
        # Neither the trigger token nor the webhook address and secret are
        # needed by the kiosk; whoever reads them can fire the booth or fake
        # its notifications.
        p["http_trigger"] = {"enabled": bool((p.get("http_trigger") or {}).get("enabled"))}
        p["webhooks"] = {"enabled": bool((p.get("webhooks") or {}).get("enabled"))}
        # The Wi-Fi password reaches the screen only inside the code of a run
        # (local_share), not with every state poll.
        p.get("wifi_share", {}).pop("password", None)
        mail = p.get("mail") or {}
        p["mail"] = {"enabled": bool(mail.get("enabled")), "attach": mail.get("attach", "web")}
        if b.ephemeral:                      # die Oberfläche soll gar nichts anbieten,
            p["mail"] = {"enabled": False}   # was es in diesem Modus nicht gibt
            p["qr_on_screen"] = False
            p["gallery_on_booth"] = False
            p["allow_delete"] = True
            p["modes"] = b.printable_modes()
        return p

    def require_admin(request: Request, pin_change: bool = False) -> None:
        token = request.headers.get("X-Admin-Token", "")
        exp = b.admin_tokens.get(token)
        if not exp or exp < time.time():
            raise HTTPException(401, "PIN erforderlich")
        if token in b.pin_change_tokens and not pin_change:
            raise HTTPException(403, "Bitte zuerst eine eigene PIN festlegen.")

    def qr_path(url: str) -> str:
        from urllib.parse import quote
        return f"/api/qr.png?data={quote(url, safe='')}" if url else ""

    # The booth is rented out with the program on it: the card has to say where
    # the source of exactly this build is (see source_offer.py).
    ABOUT = {"name": "Photobox Booth", "project_url": "https://photobox-gallery.com",
             "repo_url": source_offer.source_url(), "commit": source_offer.commit(),
             "license": source_offer.LICENSE, "maintainer": "Till Heidrich"}

    @app.get("/static/legal/" + camera_sdk_bundle.LICENCE_FILE)
    def camera_sdk_terms():
        """The end-user terms of the bundled camera SDK; 404 in the free edition."""
        if camera_sdk_bundle.find() is None:
            raise HTTPException(404, "not in this edition")
        return PlainTextResponse(camera_sdk_bundle.licence_text())

    @app.get("/api/about")
    def about():
        """Welche Fassung läuft und wo das Projekt zu Hause ist. Keine Geheimnisse."""
        return {**ABOUT, "version": __version__, "booth_name": b.store.local.get("booth_name", ""),
                "event": b.profile["event"].get("name", ""),
                "gallery": bool(b.store.local["server"].get("url")),
                "standalone": bool(b.store.local.get("standalone")) or not b.store.local["server"].get("url")}

    @app.get("/api/layout-thumb/{art}/{ident}.jpg")
    def layout_thumb(art: str, ident: str):
        """A layout's picture for the set-up screen; 503 while a run is going and it is not ready."""
        if art not in layoutbilder_mod.ARTEN:
            raise HTTPException(404, "unknown kind")
        pfad = b.layoutbilder.datei(art, ident)
        if pfad is None:
            if b.layoutbilder.vorlage(art, ident) is None and not any(
                    a == art and layoutbilder_mod._sauber(i) == ident for a, i, _t in b.layoutbilder.angebot()):
                raise HTTPException(404, "no such layout")
            raise HTTPException(503, "later")
        return FileResponse(pfad, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    def _frames_for_kiosk(mode: str, bilder_m: dict) -> dict:
        offer = b.motion_frame_offer(mode)
        return {"enabled": offer["enabled"], "none": offer["none"], "default": offer["default"],
                "guest_choice": offer["guest_choice"],
                "frames": [{"id": i, "name": f.get("name") or i, "format": f["format"],
                            "canvas": list(motion_frames.FORMATS[f["format"]]),
                            "window": [f["window"]["w"], f["window"]["h"]], "thumb": bilder_m.get(i, "")}
                           for i, f in offer["frames"]]}

    @app.get("/api/state")
    def state(request: Request = None, typing: Optional[str] = None):
        # The kiosk on this PC asks every few seconds; its silence tells the
        # booth that no browser shows it any more (__main__._keep_kiosk).
        if request is not None and request.client and request.client.host in ("127.0.0.1", "::1"):
            b.kiosk_seen = time.monotonic()
            # ``typing``: an e-mail, greeting or PIN dialog is open on the kiosk
            # (sent on every poll and at once when one opens or closes). Remote
            # control takes no picture of the screen meanwhile (fernsteuerung,
            # final review A10). Only from this PC — a phone on the network must
            # not be able to say "closed" — and only when the field is there: the
            # admin page polls the same route without knowing about dialogs.
            if typing in ("0", "1"):
                b.fernsteuerung.kiosk_typing(typing == "1")
        cam = b.camera.info()
        bilder = b.layoutbilder.urls()
        return {
            "version": __version__, "booth_name": b.store.local.get("booth_name"),
            "remote_locked": b.remote_locked,
            # Small mark on the kiosk while the gallery drives this booth (fernsteuerung.py).
            "remote_control": b.fernsteuerung.kiosk_state(),
            "profile": public_profile(), "profile_rev": b.profile_rev * 100000 + b.profile.get("version", 0),
            "templates": [dict(compose.template_info(t), thumb=bilder["t"].get(str(t["id"]), ""))
                          for t in b.templates()],
            # Which of them photo mode offers (one photo each; empty = the plain photo as before).
            "photo_templates": [str(t["id"]) for t in b.photo_templates()],
            # The print card of each photo format, and the GIF sheet: the
            # set-up screen shows them as pictures, not as words.
            "layout_thumbs": {"formats": bilder["f"], "gif": bilder["g"].get("gif", ""),
                              # boomerang.print: "<template>@<format>" -> picture of that sheet
                              "boomerang": bilder["b"]},
            # Frames for boomerang and video ("Rahmen"): per mode what the
            # set-up screen offers, with each frame's picture and window.
            "motion_frames": {m: _frames_for_kiosk(m, bilder["m"]) for m in FRAME_MODES},
            # Lengths the guest may choose for a boomerang (empty: no choice).
            "clip_seconds": {"boomerang": b.clip_seconds_offer("boomerang")},
            "backgrounds": b.backgrounds(), "start_media": b.start_media(),
            "phase_media": b.phase_media(),
            "looks": [{"id": n, "label": b.look_label(n)} for n in b.look_names()],
            "chroma": {"enabled": b.chroma_active(),
                       "guest_choice": bool((b.profile.get("chroma") or {}).get("guest_choice", True)),
                       "backgrounds": b.chroma_backgrounds()},
            "mail": {"enabled": bool((b.profile.get("mail") or {}).get("enabled")), **b.mail.status()},
            "camera": {**cam, "error_code": errors.camera_code(cam.get("error")) if cam["state"] != "ready" else "",
                       # The camera's own film paused after a failure (live view meanwhile).
                       "camera_movie": b.camera_movie_state()},
            "printer": b.printer.status(), "print": b.print_allowance(None),
            "sync": {"online": b.agent.online, "last_sync": b.agent.last_sync, "error": b.agent.last_error,
                     **b.outbox.counts()},
            "gallery_count": b.library.count(b.profile["event"].get("slug", "")),
            # Für den Startbildschirm: Link zur Galerie des Abends und die
            # letzte Aufnahme. Im datensparsamen Betrieb gibt es beides nicht.
            "gallery_url": "" if b.ephemeral else share.event_url(b.profile),
            "last_thumb": _letztes_thumb(),
            "standalone": bool(b.store.local.get("standalone")) or not (b.store.local["server"].get("url")),
            # print_modes: the modes with a print sheet; for the others the
            # kiosk drops "Nur drucken" and shows the note instead (consent_options).
            "consent": {"options": b.consent_options(), "notice_only": b.consent_notice_only(),
                        "print_modes": [m for m in (b.profile.get("modes") or []) if b.mode_prints(m)],
                        "note": {l: b.consent_note(l) for l in (b.profile.get("languages") or ["de"])}},
            "privacy": {"print_only": b.ephemeral,
                        "notice": str((b.profile.get("privacy") or {}).get("notice") or ""),
                        "seconds": int((b.profile.get("privacy") or {}).get("delete_seconds", 120) or 120)},
            "locked": b.locked, "mirror_preview": b.store.local["camera"].get("mirror_preview", True),
            "trigger": b.take_trigger(),
            "kiosk_lang": b.kiosk_lang,
            "webhooks": b.webhooks.status(),
            # Nur die dringendste, und nur wenn sie die eingestellte Schwelle
            # erreicht. Die Oberfläche zeigt sie ausschließlich auf dem
            # Startbildschirm — nie über dem Gesicht eines Gastes.
            "meldung": (b._geraete_abgleichen(), b.meldungen.fuer_die_box())[1],
            # Own window or Chrome, alive, reloads (window.py) - for a check over ssh.
            "window": window_mod.status(),
            # Watcher behind this process? Restarts so far, last exit and why.
            "supervisor": waechter.supervisor_status(),
        }

    @app.post("/api/admin/window/selftest")
    def window_selftest(request: Request):
        """Window facts plus a screenshot into the data folder (window.py)."""
        require_admin(request)
        return window_mod.selftest_report(b.data)

    @app.get("/api/admin/window/screenshot.png")
    def window_screenshot(request: Request):
        """What the screen shows right now, for the owner who cannot see it."""
        require_admin(request)
        try:
            shot = window_mod.screenshot(b.data / window_mod.SCREENSHOT_FILE)
        except Exception as e:                       # noqa: BLE001 - no desktop (service, ssh), no permission
            raise HTTPException(503, f"Bildschirmfoto nicht möglich: {e.__class__.__name__}: {e}")
        return FileResponse(shot["file"], media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/trigger")
    def http_trigger(body: dict = Body(default={}), request: Request = None):
        """Auslösen von außen: Buzzer über WLAN, Leitstand, Fernwartung.

        Die Oberfläche holt den Auftrag beim nächsten Zustandsabruf ab und
        startet den Durchgang — so bleibt der Ablauf an einer Stelle.
        """
        cfg = b.profile.get("http_trigger") or {}
        if not cfg.get("enabled"):
            raise HTTPException(403, "trigger.disabled")
        import hmac as _hmac
        token = str(cfg.get("token") or "")
        given = ""
        if request is not None:
            given = request.headers.get("X-Trigger-Token", "") or request.query_params.get("token", "")
        given = given or str(body.get("token", ""))
        if not token or not _hmac.compare_digest(given, token):
            time.sleep(0.5)
            raise HTTPException(401, "trigger.token")
        if b.locked:
            raise HTTPException(423, "locked")
        mode = str(body.get("mode") or cfg.get("mode") or "photo")
        if mode not in b.profile["modes"]:
            raise HTTPException(400, "trigger.mode")
        b.trigger = {"mode": mode, "at": time.time()}
        log.info("Auslösung über HTTP: %s", mode)
        return {"ok": True, "mode": mode}

    @app.get("/api/live.mjpg")
    async def live():
        async def gen():
            seq = -1
            while True:
                frame, new_seq = await asyncio.to_thread(b.camera.latest_frame, seq, 1.0)
                if frame is None or new_seq == seq:
                    await asyncio.sleep(0.05)
                    continue
                seq = new_seq
                frame = await asyncio.to_thread(b.screen_frame, frame, seq)
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode()
                       + b"\r\n\r\n" + frame + b"\r\n")
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.get("/api/live.jpg")
    def live_single():
        frame, seq = b.camera.latest_frame(-1, 2.0)
        if not frame:
            raise HTTPException(503, "kein Live-Bild")
        return Response(b.screen_frame(frame, seq), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/session")
    def session_start(body: dict = Body(...)):
        consent = body.get("consent")
        s = b.start_session(body.get("mode", "photo"), body.get("format") or b.format_without_choice(body.get("mode", "photo")),
                            body.get("template"), body.get("background"),
                            str(body.get("chroma_background") or ""),
                            consent=consent if isinstance(consent, dict) else None,
                            motion_frame=str(body.get("motion_frame") or "") or None,
                            seconds=body.get("seconds"))
        return s.public()

    @app.post("/api/session/{uid}/text")
    def session_text(uid: str, body: dict = Body(default={})):
        """Den Gruß des Gastes zum Durchgang legen — vor der Aufnahme.

        Abgeschnitten wird hier, nicht erst beim Zeichnen: Was zu lang ist,
        passt auf keinem Abzug, und der Gast soll es am Bildschirm sehen.
        """
        s = b.get_session(uid)
        cfg = b.profile.get("guest_text") or {}
        if not cfg.get("enabled"):
            raise HTTPException(403, "Gästetexte sind aus")
        grenze = max(1, min(120, int(cfg.get("max_len") or 40)))
        s.guest_text = " ".join(str(body.get("text") or "").split())[:grenze]
        return {"ok": True, "text": s.guest_text}

    @app.post("/api/session/{uid}/shot")
    async def session_shot(uid: str, body: dict = Body(default={})):
        ersetzt = body.get("replace")
        try:
            s = b.get_session(uid)
            pfad = await asyncio.to_thread(b.take_shot, s, bool(body.get("retake")),
                                           int(ersetzt) if ersetzt else None)
        except Exception as e:                      # noqa: BLE001 — logged, then the kiosk's error screen
            b.run_failed(uid, "shot", e, replace=ersetzt)
            raise
        # Beim Ersetzen ist das neue Bild nicht das letzte der Reihe — der Link
        # muss auf die Stelle zeigen, die eben noch einmal gemacht wurde.
        nummer = s.shots.index(pfad) + 1 if pfad in s.shots else len(s.shots)
        return {**s.public(), "shot": nummer,
                "shot_url": f"/api/session/{uid}/shot/{nummer}.jpg?t={int(time.time()*1000)}"}

    @app.get("/api/session/{uid}/shot/{n}.jpg")
    def session_shot_img(uid: str, n: int):
        s = b.get_session(uid)
        if not 1 <= n <= len(s.shots):
            raise HTTPException(404)
        with Image.open(s.shots[n - 1]) as im:
            # Shown for a second between two shots: decoded small (a 24 MP
            # file in full took about a second on the booth PC, while the
            # next countdown was already running).
            im.draft("RGB", (1000, 1000))
            im = im.convert("RGB")
            if s.mode == "photo" and not s.template:      # on a layout: like a collage's shot
                im = compose.crop_to_aspect(im, s.fmt)
            im.thumbnail((1000, 1000))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
        return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/session/{uid}/clip")
    async def session_clip(uid: str):
        try:
            s = b.get_session(uid)
            # Answers when the camera stops: the kiosk's recording sign ends
            # with the recording, the film is made while it shows "processing".
            out = await asyncio.to_thread(b.record_clip, s, True)
        except Exception as e:                      # noqa: BLE001 — logged, then the kiosk's error screen
            b.run_failed(uid, "clip", e)
            raise
        return out

    @app.get("/api/session/{uid}/preview.jpg")
    async def session_preview(uid: str, look: str = "original", size: int = 1000, draw: int = 1):
        s = b.get_session(uid)
        if s.is_video:
            if not s.poster:
                raise HTTPException(404)
            return FileResponse(s.poster, headers={"Cache-Control": "no-store"})
        # draw=0: the picture without the guest's drawing, for under the canvas
        # of the drawing screen — the strokes are drawn there live on top.
        data = await asyncio.to_thread(b.preview_jpeg, s, look, max(160, min(size, 1600)), bool(draw))
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/session/{uid}/finish")
    async def session_finish(uid: str, body: dict = Body(default={})):
        try:
            s = b.get_session(uid)
            await asyncio.to_thread(b.finalize, s, body.get("look", "original"))
        except Exception as e:                      # noqa: BLE001 — logged, then the kiosk's error screen
            b.run_failed(uid, "finish", e)
            raise
        return _item_payload(b.library.get(uid))

    @app.post("/api/session/{uid}/draw")
    async def session_draw(uid: str, request: Request):
        """The guest's drawing on the finished picture, as vectors (see drawing.py).

        Only the run the result screen is showing: switched on, a mode with a
        still, finished, and no newer run started since — once the next guest
        has begun, the previous picture is closed. The body is read with a
        hard size limit before it is parsed.
        """
        cfg = b.profile.get("draw") or {}
        if not cfg.get("enabled"):
            raise HTTPException(403, "draw.disabled")
        s = b.get_session(uid)
        if not drawing.offered(b.profile, s.mode):
            raise HTTPException(409, "draw.mode")
        if not s.finalized:
            raise HTTPException(409, "draw.not_ready")
        if next(reversed(b.sessions), None) != uid:
            raise HTTPException(409, "draw.not_current")
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            raise HTTPException(400, "draw.invalid")
        if declared > drawing.MAX_BYTES:
            raise HTTPException(413, "draw.too_large")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > drawing.MAX_BYTES:
                raise HTTPException(413, "draw.too_large")
        try:
            strokes = drawing.parse(bytes(raw), drawing.allowed_colors(cfg))
        except drawing.DrawingError as e:
            raise HTTPException(e.status, e.code)
        await asyncio.to_thread(b.draw_on, s, strokes)
        log.info("Durchgang %s: Zeichnung des Gastes (%d Striche)", uid, len(strokes))
        return {**_item_payload(b.library.get(uid)), "strokes": len(strokes)}

    @app.post("/api/session/{uid}/mail")
    def session_mail(uid: str, body: dict = Body(...)):
        """Ergebnis per E-Mail. Geht in die Warteschlange — offline wird nachgeholt."""
        cfg = b.profile.get("mail") or {}
        if b.run_print_only(uid):
            raise HTTPException(403, "privacy.print_only")
        if not cfg.get("enabled"):
            raise HTTPException(403, "mail.disabled")
        address = str(body.get("address", "")).strip()
        if not valid_address(address):
            raise HTTPException(400, "mail.address")
        item = b.library.get(uid)
        if not item:
            raise HTTPException(404)
        attach = str(cfg.get("attach", "web"))
        files: List[Path] = []
        if attach != "none":
            sess = b.sessions.get(uid)
            wanted = ""
            if attach == "web" and sess and sess.result_web:
                wanted = str(sess.result_web)          # kleinere Fassung, passt eher durch
            if not wanted:
                wanted = str(item.get("result") or "")
            if wanted and Path(wanted).exists():
                files.append(Path(wanted))
        share_url = item.get("share_url") or ""
        # The event's file-name rule names the attachments like a USB stick
        # names the files (photobox_sync.targets); "original" keeps them as is.
        ev = b.profile.get("event") or {}
        sess = b.sessions.get(uid)
        created = sess.created if sess else item.get("created")
        names = attachment_names(files, file_naming=str(ev.get("file_naming") or ""),
                                 event_name=str(ev.get("name") or ""),
                                 when=dt.datetime.fromtimestamp(float(created)) if created else None)
        try:
            mail_id = b.mail.enqueue(address=address, files=files, names=names,
                                     subject=str(cfg.get("subject") or "")
                                     .replace("{event}", b.profile["event"].get("name", "")),
                                     body=str(cfg.get("body") or "").replace("{link}", share_url)
                                     .replace("{event}", b.profile["event"].get("name", "")),
                                     session_uid=uid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        log.info("E-Mail an %s eingereiht (Durchgang %s)", redact.address(address), uid)
        return {"queued": True, "id": mail_id, **b.mail.status()}

    @app.post("/api/session/{uid}/abort")
    def session_abort(uid: str):
        """Einen laufenden Durchgang wegwerfen.

        Das ist nicht dasselbe wie das Löschen einer fertigen Aufnahme: Hier ist
        noch nichts entstanden, nichts verschickt und nichts gedruckt. Deshalb
        hängt der Abbruch auch nicht am Schalter ``allow_delete`` — wer mitten
        in einer Serie merkt, dass es so nichts wird, muss immer herauskommen.
        """
        s = b.get_session(uid)
        # A guest who cancels mid-countdown may do so while a shot or a clip
        # is still being taken. Wait for it under the run's lock; otherwise the
        # camera writes its file into a folder that delete() has just removed
        # (a driver that creates its target folder brings the folder back).
        with s.lock:
            if s.finalized:
                raise HTTPException(409, "Durchgang ist schon fertig")
            b.zahlen.zaehle(s.event_slug or b._zahlen_event(), "aborted.cancel")
            b.delete(uid)
        return {"ok": True}

    @app.post("/api/count/{name}")
    def count(name: str):
        """The kiosk tells what only it sees. One name only: the big QR code was opened."""
        if name != "qr_big":
            raise HTTPException(404)
        b.zahlen.zaehle(b._zahlen_event(), "qr_big")
        return {"ok": True}

    # A buzzer that "sometimes does nothing" (18.11.2026) left no trace: the
    # kiosk now reports each press it handles as the trigger, and the reason
    # when it did nothing. Short tokens only, never typed text, and a flood
    # (a stuck key, a page in a loop) is cut off after 20 lines in 10 s.
    _press_times: List[float] = []
    _token = re.compile(r"[^A-Za-z0-9_:.\-]")

    @app.post("/api/input-log")
    def input_log(body: dict = Body(default={})):
        now = time.monotonic()
        _press_times[:] = [t for t in _press_times if now - t < 10.0]
        if len(_press_times) >= 20:
            return {"ok": False}
        _press_times.append(now)
        f = {k: _token.sub("", str(body.get(k) or ""))[:24] or "-" for k in ("source", "input", "screen", "outcome")}
        log.info("Auslöser: %s %s auf %s → %s", f["source"], f["input"], f["screen"], f["outcome"])
        return {"ok": True}

    @app.delete("/api/session/{uid}")
    def session_delete(uid: str):
        if not b.profile.get("allow_delete", True):
            raise HTTPException(403)
        b.delete(uid)
        return {"ok": True}

    def _item_payload(item: Optional[dict]) -> dict:
        if not item:
            raise HTTPException(404)
        uid = item["uid"]
        # Download without internet: "1. join the Wi-Fi", "2. load your photo"
        # from the booth itself (guest_share.py); None = the usual QR code.
        local = guest_share.for_booth(b).offer(item)
        if local:
            local = {**local, "wifi_qr": qr_path(local["wifi"]), "photo_qr": qr_path(local["url"])}
        extra: Dict[str, Any] = {}
        # Only when switched on and the run has a still: off, the key is not
        # there at all and the kiosk has nothing to show a button for.
        if drawing.offered(b.profile, item["mode"]):
            extra["draw"] = {"colors": drawing.allowed_colors(b.profile.get("draw"))}
        has_print = bool(item.get("print_file")) and Path(item["print_file"]).is_file()
        return {
            **extra,
            "local_share": local,
            "uid": uid, "mode": item["mode"], "share_url": item["share_url"],
            "qr_url": qr_path(item["share_url"] if b.profile.get("qr_on_screen") else ""),
            "result_url": f"/media/{uid}/result", "thumb_url": f"/media/{uid}/thumb",
            "is_video": item["mode"] in ("boomerang", "video"), "prints": item["prints"],
            "print": b.print_allowance(uid) if has_print else {"enabled": False},
            # Whether there is a sheet at all, apart from the guests' switches:
            # the admin's reprint list offers only these (a film has none).
            "has_print": has_print,
            "print_only": b.run_print_only(uid),
        }

    def _gallery_payload(item: Optional[dict]) -> dict:
        """Wie _item_payload, aber für die Galerie an der Box.

        Nachdrucken ist ein eigener Schalter: Der Druck direkt nach der Aufnahme
        und das spätere Nachdrucken durch jemanden, der durch die Galerie
        blättert, sind zwei verschiedene Dinge. Die Kontingente gelten für beides.
        """
        payload = _item_payload(item)
        if not (b.profile.get("print") or {}).get("reprint_from_gallery", True):
            payload["print"] = {"enabled": False, "reprint_off": True}
        # Ein GIF bewegt sich — die Kachel soll das zeigen, sonst hält der Gast
        # es für ein Standbild und tippt nie darauf. Das gilt auch für die
        # Collage, zu der ein GIF gebaut wurde: gedruckt wird der Streifen,
        # angesehen das Filmchen.
        anim = item.get("anim") and Path(item["anim"]).exists()
        payload["anim_url"] = f"/media/{item['uid']}/anim" if anim else ""
        payload["is_animated"] = bool(payload["is_video"] or item["mode"] == "gif" or anim)
        return payload

    # ── Galerie an der Box ────────────────────────────────
    @app.get("/api/gallery")
    def gallery(offset: int = 0, limit: int = 60):
        if b.ephemeral or not b.profile.get("gallery_on_booth", True):
            raise HTTPException(403)          # im Kita-Modus gibt es nichts zu zeigen
        slug = b.profile["event"].get("slug", "")
        items = b.library.list(slug, offset, min(limit, 120))
        return {"total": b.library.count(slug), "items": [_gallery_payload(i) for i in items]}

    @app.get("/api/gallery/{uid}")
    def gallery_item(uid: str):
        return _gallery_payload(b.library.get(uid))

    @app.get("/media/{uid}/{what}")
    def media_file(uid: str, what: str):
        item = b.library.get(uid)
        if not item or what not in ("result", "thumb", "print", "anim"):
            raise HTTPException(404)
        path = item.get({"result": "result", "thumb": "thumb", "print": "print_file",
                         "anim": "anim"}[what])
        if not path or not Path(path).exists():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    @app.get("/api/print/{uid}/status")
    def print_status(uid: str):
        """Where the print of this run stands — the result screen asks every few seconds.

        ``moved_to``: another printer took over. ``stuck``: no printer can
        print right now. ``saved`` tells the guest whether the photo is kept
        anyway (gallery run) or not (their own "Nur drucken").
        """
        outcome = b.printer.outcome(uid) if hasattr(b.printer, "outcome") else {}
        print_only = b.run_print_only(uid)
        return {"state": outcome.get("state", ""), "slot": outcome.get("slot"),
                "moved_to": outcome.get("moved_to"), "print_only": print_only, "saved": not print_only}

    @app.post("/api/session/{uid}/keep")
    def session_keep(uid: str, body: dict = Body(default={})):
        """After a failed print of a "Nur drucken" run: to the gallery after all, or discard."""
        if b.ephemeral or not b.run_print_only(uid):
            raise HTTPException(409, "Nur für Durchgänge mit „Nur drucken“")
        choice = str(body.get("choice") or "")
        if choice == "gallery":
            b.keep_in_gallery(uid)
            return _item_payload(b.library.get(uid))
        if choice == "discard":
            b._wipe(uid, reason="vom Gast verworfen")
            return {"ok": True}
        raise HTTPException(400, "choice")

    @app.post("/api/print/{uid}")
    def print_item(uid: str, body: dict = Body(default={})):
        return b.print_uid(uid, int(body.get("copies", 1)), bool(body.get("from_gallery")))

    @app.get("/api/qr.png")
    def qr_png(data: str, size: int = 600):
        img = compose.qr_image(data, max(120, min(size, 1200)))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    @app.get("/assets/{name}")
    def asset(name: str):
        p = (b.assets_dir / name).resolve()
        if p.parent != b.assets_dir.resolve() or not p.exists():
            raise HTTPException(404)
        # The gallery replaces an asset under the same name when its content
        # changes; an hour of max-age kept the old logo on screen that long.
        return FileResponse(p, headers={"Cache-Control": STATIC_CACHE})

    # ── Verwaltung (lokal, PIN) ───────────────────────────
    def check_pin(pin) -> None:
        """PIN check behind the brake: 429 while it pauses, 401 when wrong."""
        import hmac
        wait = b.pin_brake.wait_seconds()
        if wait:
            raise HTTPException(429, f"Zu viele Fehlversuche – in {wait} s geht es weiter.",
                                headers={"Retry-After": str(wait)})
        if not hmac.compare_digest(str(pin), b.current_pin()):
            b.pin_brake.failed()
            time.sleep(_WRONG_PIN_PAUSE)
            raise HTTPException(401, "Falsche PIN")
        b.pin_brake.succeeded()

    @app.post("/api/admin/login")
    def admin_login(body: dict = Body(...)):
        check_pin(body.get("pin", ""))
        token = secrets.token_urlsafe(24)
        now = time.time()
        for k in [k for k, exp in b.admin_tokens.items() if exp < now]:
            b.admin_tokens.pop(k, None)
            b.pin_change_tokens.discard(k)
        b.admin_tokens[token] = now + 1800
        must_change = b.factory_pin_active()
        if must_change:
            b.pin_change_tokens.add(token)
        return {"token": token, "must_change_pin": must_change}

    @app.post("/api/admin/pin")
    def admin_pin_change(request: Request, body: dict = Body(...)):
        """Choose a new booth PIN. The one call a factory-PIN login may make."""
        from .config import weak_pin_reason
        require_admin(request, pin_change=True)
        new_pin = str(body.get("new_pin", "")).strip()
        reason = weak_pin_reason(new_pin)
        if reason:
            raise HTTPException(400, reason)
        b.set_admin_pin(new_pin)
        b.pin_change_tokens.discard(request.headers.get("X-Admin-Token", ""))
        return {"ok": True, "governed_by_gallery": b.current_pin() != new_pin}

    @app.post("/api/pin/change")
    def pin_change_at_the_booth(body: dict = Body(...)):
        """"PIN ändern" in the operator menu: the current PIN, then the new one.

        No admin token, like /api/lock: the menu behind the corner asks for the
        PIN on its own pad. The current PIN goes through the same brake as every
        login, so this is no way round it. The factory PIN cannot be kept: it
        is refused as a new PIN (weak_pin_reason).
        """
        from .config import weak_pin_reason
        check_pin(body.get("current_pin", ""))
        new_pin = str(body.get("new_pin", "")).strip()
        reason = weak_pin_reason(new_pin)
        if reason:
            raise HTTPException(400, reason)
        b.set_admin_pin(new_pin)
        # Sessions opened with the old PIN end: whoever knew it is out.
        b.admin_tokens.clear()
        b.pin_change_tokens.clear()
        return {"ok": True, "in_force": b.current_pin() == new_pin}

    @app.post("/api/lock")
    def toggle_lock(body: dict = Body(...)):
        """Box vor Ort sperren oder entsperren — mit PIN, sonst könnte jeder Gast das.

        Absichtlich kein Admin-Token: der Betreiber soll zwischen zwei Gruppen
        schnell sperren können, ohne sich erst in die Verwaltung einzuloggen.
        The PIN brake (shared with the admin login) stops trying them all.
        """
        if not b.may_lock_on_booth():
            raise HTTPException(403, "An dieser Box ist das Sperren abgeschaltet")
        check_pin(body.get("pin", ""))
        want = bool(body.get("locked"))
        b.set_locked(want)
        log.info("Box %s (an der Box)", "gesperrt" if want else "entsperrt")
        return {"locked": want}

    @app.get("/api/admin/config")
    def admin_config(request: Request):
        require_admin(request)
        local = json.loads(json.dumps(b.store.local))
        if local["server"].get("api_key"):
            local["server"]["api_key"] = "••••" + local["server"]["api_key"][-4:]
        for t in local.get("targets", []):
            if t.get("password"):
                t["password"] = "••••"
        # The PIN never goes into the page: the field is masked and empty,
        # empty means "unchanged" (admin_config_save).
        local["admin_pin_set"] = bool(local.pop("admin_pin", ""))
        usb_local = (local.get("storage") or {}).get("usb") or {}
        if usb_local.get("trusted"):         # the ids stay on the box; the form needs the labels only
            usb_local["trusted"] = [{"label": t.get("label", ""), "since": t.get("since", "")}
                                    for t in usb_local["trusted"] if isinstance(t, dict)]
        profile = json.loads(json.dumps(b.profile))
        # The form shows looks under their current ids, so a house look saved
        # under an old name is selected, not lost on the next save.
        lut_cfg = profile.get("luts") or {}
        if lut_cfg.get("baked"):
            lut_cfg["baked"] = luts.canonical_key(str(lut_cfg["baked"]))
        if lut_cfg.get("show"):
            lut_cfg["show"] = [luts.canonical_key(str(x)) for x in lut_cfg["show"]]
        profile["looks"] = [b.canonical_look(x) for x in profile.get("looks") or []]
        if (profile.get("mail") or {}).get("password"):
            profile["mail"]["password"] = "••••"
        if (profile.get("access") or {}).get("pin"):
            profile["access"]["pin"] = "••••"
        import platform
        import sys
        about_admin = {**ABOUT, "version": __version__, "python": sys.version.split()[0],
                       "platform": platform.platform(terse=True), "data_dir": str(b.data),
                       # The edition with camera SDK: Canon's libraries are a
                       # separate, proprietary component, not under the AGPL.
                       "camera_sdk": camera_sdk_bundle.component()}
        from .camera import models as camera_models
        from .printing import models as printer_models
        return {"local": local, "printers": _system_printers(), "printer_models": printer_models.auswahl(),
                "camera_models": camera_models.auswahl(os.name == "nt"),
                "profile": profile, "about": about_admin, "pairing": b.pairing_status(),
                "luts": b.lut_catalog(), "mail": b.mail.status(), "logs": b.logbuf.counts(),
                "log_tail": _tail(b.data / "logs" / "booth.log", 60), "data_dir": str(b.data)}

    @app.post("/api/admin/printers/detect")
    def admin_printer_detect(request: Request):
        """Noch einmal nachsehen, was für ein Drucker dranhängt.

        Beim Start passiert das von allein. Im Betrieb wird ein Drucker
        getauscht oder eine andere Kassette eingelegt — dafür dieser Knopf.
        """
        require_admin(request)
        return {"ok": True, **b.drucker_suchen(), "local": {"printer": b.store.local.get("printer"),
                                                            "printers": b.store.local.get("printers")}}

    @app.post("/api/admin/config")
    def admin_config_save(request: Request, body: dict = Body(...)):
        require_admin(request)
        patch = dict(body)
        # An empty or masked PIN field means "unchanged": a PIN is never erased by a field left alone.
        if "admin_pin" in patch and (not str(patch["admin_pin"] or "").strip()
                                     or not str(patch["admin_pin"]).strip().isdigit()):
            patch.pop("admin_pin")
        if "admin_pin" in patch and str(patch["admin_pin"] or "") != str(b.store.local.get("admin_pin") or ""):
            from .config import weak_pin_reason
            reason = weak_pin_reason(patch["admin_pin"])
            if reason:
                raise HTTPException(400, reason)
        srv = patch.get("server")
        if isinstance(srv, dict) and str(srv.get("api_key", "")).startswith("••••"):
            srv.pop("api_key")
        old_targets = {t.get("name") or t.get("host"): t for t in b.store.local.get("targets", [])}
        # Rule B2 (17.11.2026): the dots mean "keep the stored password" only
        # for the same type, host, port and user. A changed server loses the
        # old password and its pinned SFTP key and waits for a new password;
        # a password typed here is bound to the server it was typed for.
        from photobox_sync.targets import NETWORK_TYPES
        from .second_target import bind_password
        for t in patch.get("targets") or []:
            if not isinstance(t, dict) or str(t.get("type") or "").lower() not in NETWORK_TYPES:
                continue
            old = old_targets.get(t.get("name") or t.get("host"))
            typed = t.get("password")
            from photobox_sync.targets import normal_type
            t["type"] = normal_type(t.get("type"))     # ssh/scp are SFTP: one name for key pinning and reports
            from .second_target import clean_path
            try:
                if "path" in t:
                    t["path"] = clean_path(t.get("path") or "")
            except ValueError as e:
                raise HTTPException(400, str(e))
            for k in ("password_for", "awaiting_password"):
                t.pop(k, None)                 # never taken from the form
                if old and k in old:
                    t[k] = old[k]
            new_pw = typed if isinstance(typed, str) and typed and typed != "••••" else None
            # Nothing stored before and the field left empty: the operator at
            # the booth means "this server needs no password" — nothing to protect.
            bind_password(t, old, new_pw, b.host_keys, strict_new=(typed == "••••"))
        # S-7: the list of confirmed sticks is never taken from a form (it is
        # made by "Stick freigeben" only), and the switch and the originals
        # choice change only from this PC — not from a remote admin session.
        if isinstance(patch.get("storage"), dict):
            old_usb = dict((b.store.local.get("storage") or {}).get("usb") or {})
            new_usb = dict(patch["storage"].get("usb") or old_usb)
            new_usb["trusted"] = old_usb.get("trusted") or []
            here = _is_loopback(b.listen_host) or _is_loopback(request.client.host if request.client else "")
            if not here:
                for k in ("enabled", "originals", "path"):
                    if k in old_usb:
                        new_usb[k] = old_usb[k]
                    else:
                        new_usb.pop(k, None)
            patch["storage"] = {**patch["storage"], "usb": new_usb}
        b.store.save_local(patch)
        return {"ok": True, "restart_required": True}

    @app.post("/api/admin/pair")
    def admin_pair(request: Request, body: dict = Body(...)):
        """Pair with the gallery by the code its admin shows ("Kopplungscode erzeugen").

        The code is a key in disguise: it is neither logged nor sent back.
        """
        require_admin(request)
        from photobox_sync.pairing import PairingError
        try:
            status = b.pair(str(body.get("code") or "")[:2000])
        except PairingError as e:
            log.info("Kopplung abgelehnt (%s)", e.reason)
            code = {"too_many": 429, "unreachable": 502}.get(e.reason, 400)
            raise HTTPException(code, str(e))
        return {"ok": True, "restart_required": False, "pairing": status}

    @app.post("/api/admin/pair/phone")
    def admin_pair_phone_start(request: Request, body: dict = Body(default={})):
        """Start pairing from the phone: the answer carries the QR code, never the verifier."""
        require_admin(request)
        from photobox_sync.pairing import PairingError
        try:
            return b.phone_pair_start(str((body or {}).get("url") or "")[:300])
        except PairingError as e:
            code = {"too_many": 429, "unreachable": 502}.get(e.reason, 400)
            raise HTTPException(code, str(e))

    @app.get("/api/admin/pair/phone")
    def admin_pair_phone_poll(request: Request):
        require_admin(request)
        return b.phone_pair_poll()

    @app.post("/api/admin/pair/phone/cancel")
    def admin_pair_phone_cancel(request: Request):
        require_admin(request)
        b.phone_pair_cancel()
        return {"ok": True}

    @app.post("/api/admin/profile")
    def admin_profile_save(request: Request, body: dict = Body(...)):
        """Profil an der Box pflegen — für den Betrieb ohne Galerie.

        Mit Galerie gewinnt beim nächsten Sync wieder der Server; die Oberfläche
        sagt das auch. So lässt sich eine Box trotzdem vor Ort umstellen.
        """
        require_admin(request)
        patch = json.loads(json.dumps(body))
        clamp_profile_patch(patch, b.profile)
        # The full editor sends back what /api/admin/profile/full gave it:
        # a mask means "unchanged", never the new value.
        for gruppe, feld in _PROFILE_SECRETS:
            teil = patch.get(gruppe)
            if isinstance(teil, dict) and str(teil.get(feld, "")).startswith("••••"):
                teil[feld] = (b.profile.get(gruppe) or {}).get(feld, "")
        new_access_pin = str((patch.get("access") or {}).get("pin") or "") if isinstance(patch.get("access"), dict) else ""
        if new_access_pin and new_access_pin != str((b.profile.get("access") or {}).get("pin") or ""):
            from .config import weak_pin_reason
            reason = weak_pin_reason(new_access_pin)
            if reason:
                raise HTTPException(400, reason)
        mail = patch.get("mail")
        if isinstance(mail, dict) and str(mail.get("password", "")).startswith("••••"):
            mail["password"] = (b.profile.get("mail") or {}).get("password", "")
        merged = {**json.loads(json.dumps(b.profile)), **patch}
        merged["version"] = int(b.profile.get("version", 0))
        b._apply_profile(merged)
        return {"ok": True, "server_wins": bool(b.store.local["server"].get("url"))}

    @app.get("/api/admin/profile/full")
    def admin_profile_full(request: Request):
        """The whole profile with its defaults — what the editor at the booth is built from."""
        require_admin(request)
        from .config import DEFAULT_PROFILE
        profil = json.loads(json.dumps(b.profile))
        for gruppe, feld in _PROFILE_SECRETS:
            if (profil.get(gruppe) or {}).get(feld):
                profil[gruppe][feld] = "••••"
        return {"profile": profil, "defaults": DEFAULT_PROFILE,
                "hold": bool(b.store.local.get("profile_hold")),
                "server": bool(b.store.local["server"].get("url"))}

    @app.post("/api/admin/profile/hold")
    def admin_profile_hold(request: Request, body: dict = Body(...)):
        """Pin the profile to what was set here, or hand it back to the gallery."""
        require_admin(request)
        b.store.save_local({"profile_hold": bool(body.get("hold"))})
        return {"ok": True, "hold": bool(b.store.local.get("profile_hold"))}

    # ── Sicherung auf einen USB-Stick ─────────────────────
    @app.get("/api/admin/usb")
    def admin_usb(request: Request):
        """What is plugged in, what would be saved, and how far a backup is."""
        require_admin(request)
        b._usb_seen = (0.0, [])
        found = usb_backup.events(b.sessions_dir, b.library.withheld)
        slug = b.usb_event_slug()
        b._usb_read_job()
        target = b._usb_target()
        drives = [dict(d, trusted=b.usb_trusted(d["path"])) for d in b.usb_drives()]
        return {"drives": drives, "target": target,
                "current": {"slug": slug, "runs": next((e["runs"] for e in found if e["slug"] == slug), 0)},
                "events": found, "all_runs": sum(e["runs"] for e in found),
                "auto": {"enabled": bool(b._usb_cfg().get("enabled")), "last_ok": b.usb_last_ok,
                         "error": b.usb_error},
                "job": b.usb_job.status() if b.usb_job else None}

    @app.post("/api/admin/reprint/{uid}")
    def admin_reprint(uid: str, request: Request, body: dict = Body(default={})):
        require_admin(request)
        try:
            copies = int((body or {}).get("copies") or 1)
        except (TypeError, ValueError):
            copies = 1
        return b.reprint(uid, copies)

    @app.post("/api/admin/targets/check")
    def admin_target_check(request: Request, body: dict = Body(...)):
        """Does this upload target answer — asked before the first guest stands there.

        The form sends the dots for a password it was never shown; the stored
        one is put back in here. Nothing of the password goes into the answer.
        """
        require_admin(request)
        from photobox_sync.targets import build_extra_targets, endpoint_key, needs_password
        cfg = dict(body.get("target") or {})
        for k in ("password_for", "awaiting_password"):
            cfg.pop(k, None)
        if cfg.get("password") == "••••":
            # The stored password only for the server it was typed for (rule
            # B2): a form with another host, port, type or user is checked
            # without it, so a test never hands it to a different machine.
            alt = {t.get("name") or t.get("host"): t for t in b.store.local.get("targets", [])}
            stored = alt.get(cfg.get("name") or cfg.get("host"), {})
            same = endpoint_key(stored) == endpoint_key(cfg) and not needs_password(stored)
            cfg["password"] = stored.get("password", "") if same else ""
            if not same:
                return {"ok": False, "message": "Server, Port, Art oder Benutzer sind neu: Bitte das Passwort "
                                                "für diesen Server eintragen, dann prüfen."}
        geheim = str(cfg.get("password") or "")
        gebaut = build_extra_targets([dict(cfg, enabled=True)], b.host_keys)  # testing is allowed before switching on
        if not gebaut:
            return {"ok": False, "message": "Diese Art von Ziel kennt die Box nicht."}
        try:
            antwort = gebaut[0].check()
        except Exception as e:                   # noqa: BLE001 — whatever went wrong is the answer
            text = str(e) or type(e).__name__
            if geheim:
                text = text.replace(geheim, "…")
            ok, message = False, ziel_fehler_text(text)[:300]
        else:
            ok, message = True, str(antwort or "Verbindung steht.")[:300]
        # The stored target checked from here: the gallery sees the same "last check".
        from .second_target import find as _find_second
        _, stored = _find_second(b.store.local)
        if stored and cfg.get("name") in (stored.get("name"), None) and cfg.get("host") == stored.get("host"):
            b.store.save_local({"second_target_check": {"ok": ok, "message": redact.text(message),
                                                        "at": time.time()}})
        return {"ok": ok, "message": message}

    def require_at_the_booth(request: Request) -> None:
        """The stick decisions are made standing at the box (S-7).

        With the booth on loopback every request is from this PC. Listening on
        the network for remote admin, an admin token from another device is
        not enough for these two: whoever decides where guest pictures are
        copied must be able to see the stick.
        """
        if not _is_loopback(b.listen_host) and not _is_loopback(request.client.host if request.client else ""):
            raise HTTPException(403, "Nur an der Box selbst möglich.")

    @app.post("/api/admin/usb/trust")
    def admin_usb_trust(request: Request, body: dict = Body(...)):
        """Confirm the plugged-in stick for backups — once, with the PIN, at the box."""
        require_admin(request)
        require_at_the_booth(request)
        try:
            return {"ok": True, **b.usb_trust(str(body.get("drive") or ""))}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/admin/usb/auto")
    def admin_usb_auto(request: Request, body: dict = Body(...)):
        """The switch works at once, without "save and restart"."""
        require_admin(request)
        require_at_the_booth(request)
        storage = dict(b.store.local.get("storage") or {})
        storage["usb"] = {**(storage.get("usb") or {}), "enabled": bool(body.get("enabled"))}
        b.store.save_local({"storage": storage})
        b.usb_mark_dirty()
        return {"ok": True, "enabled": storage["usb"]["enabled"]}

    @app.post("/api/admin/usb/backup")
    def admin_usb_backup(request: Request, body: dict = Body(...)):
        require_admin(request)
        scope = "event" if body.get("scope") == "event" else "all"
        try:
            return {"ok": True, "job": b.usb_start(scope, str(body.get("drive") or ""))}
        except (ValueError, LookupError) as exc:
            raise HTTPException(409, str(exc))

    # ── Event an der Box ──────────────────────────────────
    # ── Vorlagen an der Box ───────────────────────────────
    # Derselbe Editor wie im Web-Admin, nur ohne Netz: Er schreibt die Vorlage
    # in den Medienordner der Box, damit sie sofort wirkt. Steht eine Galerie
    # dahinter, wandert sie beim nächsten Sync hoch und von dort auf die
    # anderen Boxen — und wenn nicht, funktioniert sie trotzdem, hier.

    @app.get("/api/admin/templates")
    def admin_templates(request: Request):
        require_admin(request)
        aus = [{"file": datei, "template": tpl} for datei, tpl in b.own_template_files()]
        # The picture of each template the guests are offered (layoutbilder);
        # one that is not offered keeps its drawn outline.
        bilder = b.layoutbilder.urls()["t"]
        for e in aus:
            e["thumb"] = bilder.get(str((e["template"] or {}).get("id") or ""), "")
        uebersicht = b.layout_overview()
        if b._threads:
            # Every tile's picture onto disk while the page is being looked at.
            b.layoutbilder.vorwaermen([("t", x["id"], x["template"]) for x in uebersicht["layouts"]])
        return {"eigene": aus, "mitgeliefert": [compose.template_info(t) for t in compose.BUILTIN_TEMPLATES],
                "builtin": compose.BUILTIN_TEMPLATES, "aktiv": b.profile.get("collage_templates") or [],
                "server": bool(b.store.local["server"].get("url")), **uebersicht}

    @app.post("/api/admin/templates/visibility")
    def admin_templates_visibility(request: Request, body: dict = Body(...)):
        """Exactly these layouts for the guests (collage_exact + the two lists)."""
        require_admin(request)
        dateien = body.get("templates") if isinstance(body.get("templates"), list) else []
        ids = body.get("builtins") if isinstance(body.get("builtins"), list) else []
        try:
            return {"ok": True, **b.set_layout_visibility(dateien, ids)}
        except ValueError as exc:
            art, _, text = str(exc).partition(":")
            raise HTTPException(409 if art == "locked" else 400, text)

    @app.get("/api/admin/frames")
    def admin_frames(request: Request):
        """The frames for boomerang and video as tiles with their switches (templates page)."""
        require_admin(request)
        return b.frame_overview()

    @app.post("/api/admin/frames/visibility")
    def admin_frames_visibility(request: Request, body: dict = Body(...)):
        """Exactly these frames for one mode; ``none`` offers "Ohne Rahmen" as well."""
        require_admin(request)
        ids = body.get("ids") if isinstance(body.get("ids"), list) else []
        try:
            return {"ok": True, **b.set_frame_visibility(str(body.get("mode") or ""), ids,
                                                         body.get("none") is not False)}
        except ValueError as exc:
            art, _, text = str(exc).partition(":")
            raise HTTPException(409 if art == "locked" else 400, text)

    @app.get("/api/admin/frames/picture")
    def admin_frames_picture(request: Request, id: str = "", v: str = ""):  # noqa: A002 — the query name
        """A frame with the sample picture in its window, shown or hidden — for its tile."""
        require_admin(request)
        frame = next((f for f in b.motion_frame_builtins() if f["id"] == id), None) \
            or next((f for d, f in b.own_motion_frames() if d == id), None)
        if frame is None:
            raise HTTPException(404, "no such frame")
        pfad = b.layoutbilder.datei_fuer("m", id, frame)
        if pfad is None:
            raise HTTPException(503, "later", headers={"Retry-After": "5"})
        fest = bool(v) and v == b.layoutbilder.schluessel("m", id, frame)
        return FileResponse(pfad, media_type="image/jpeg", headers={
            "Cache-Control": "private, max-age=31536000, immutable" if fest else "private, no-cache"})

    @app.get("/api/admin/templates/picture")
    def admin_templates_picture(request: Request, kind: str = "", id: str = "", v: str = ""):  # noqa: A002 — the query name
        """The printed sheet of any layout the box knows, shown or hidden - for its tile.

        The kiosk's /api/layout-thumb serves only what guests are offered; a
        hidden layout needs its picture here too, behind the PIN. Rendered
        with the same code and cache (layoutbilder); never while a run is going
        (503 with Retry-After: the page asks again later). Asked for with the
        cache key the tile list named (``v``), the answer may be kept for good:
        a changed template has another key and so another address.
        """
        require_admin(request)
        tpl = None
        if kind == "builtin":
            tpl = next((t for t in compose.BUILTIN_TEMPLATES if str(t["id"]) == id), None)
        elif kind == "own":
            tpl = next((t for d, t in b.own_template_files() if d == id), None)
        if tpl is None:
            raise HTTPException(404, "no such layout")
        ident = str(tpl.get("id") or id)
        pfad = b.layoutbilder.datei_fuer("t", ident, tpl)
        if pfad is None:
            raise HTTPException(503, "later", headers={"Retry-After": "5"})
        fest = bool(v) and v == b.layoutbilder.schluessel("t", ident, tpl)
        return FileResponse(pfad, media_type="image/jpeg", headers={
            "Cache-Control": "private, max-age=31536000, immutable" if fest else "private, no-cache"})

    @app.post("/api/admin/templates")
    def admin_templates_save(request: Request, body: dict = Body(...)):
        require_admin(request)
        try:
            vorlage, hinweise = templates.annehmen(dict(body))
        except templates.VorlageAbgelehnt as e:
            raise HTTPException(400, str(e))
        datei = b.assets_dir / templates.dateiname(vorlage)
        b.assets_dir.mkdir(parents=True, exist_ok=True)
        datei.write_bytes(templates.inhalt(vorlage))
        # Die neue Vorlage soll auch benutzt werden; sonst liegt sie da und
        # niemand versteht, warum sich nichts ändert.
        aktiv = list(b.profile.get("collage_templates") or [])
        if datei.name not in aktiv:
            aktiv.append(datei.name)
            b._apply_profile({**json.loads(json.dumps(b.profile)), "collage_templates": aktiv})
        b.push_template(datei.name)
        return {"ok": True, "template": vorlage, "hinweise": hinweise,
                "asset": {"file": datei.name}}

    @app.get("/api/admin/templates/export")
    def admin_templates_export(request: Request, datei: str = "", von: str = ""):
        """Vorlagen als Paket herunterladen — an der Box genauso wie im Web.

        Damit wandert eine Vorlage von einer Box auf die nächste, auch wenn
        beide nie dieselbe Galerie gesehen haben: Stick rein, Datei drauf,
        an der anderen Box einlesen.
        """
        require_admin(request)
        from .media import vorlagen_paket

        vorlagen = []
        if datei:
            pfad = b.assets_dir / Path(datei).name
            if pfad.is_file():
                try:
                    vorlagen.append(compose.load_template(pfad))
                except ValueError as e:
                    raise HTTPException(400, str(e))
        elif von:
            quelle = next((t for t in compose.BUILTIN_TEMPLATES if t["id"] == von), None)
            if quelle:
                vorlagen.append(quelle)
        else:
            for pfad in sorted(b.assets_dir.glob("*.json")):
                try:
                    vorlagen.append(compose.load_template(pfad))
                except Exception:                     # noqa: BLE001
                    continue
        if not vorlagen:
            raise HTTPException(404, "Keine Vorlage zum Ausgeben")

        def lies(name: str):
            pfad = b.assets_dir / name
            return pfad.read_bytes() if pfad.is_file() else None

        # Alles, was in den Medien der Box als Hintergrund taugt, reist mit:
        # Eine Vorlage ohne ihre Hintergründe sieht drüben anders aus.
        hintergruende = sorted(p_.name for p_ in b.assets_dir.iterdir()
                               if p_.is_file() and p_.suffix.lower() in vorlagen_paket.MEDIEN_ENDUNGEN)
        daten = vorlagen_paket.packen(vorlagen, lies, hintergruende=hintergruende,
                                      programm=f"Abzug Box {__version__}")
        name = vorlagen_paket.paketname(vorlagen)
        return Response(content=daten, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/admin/einrichtung")
    def admin_einrichtung(request: Request):
        """Die Testseite: Was fehlt dieser Box noch?"""
        require_admin(request)
        from . import einrichtung
        return einrichtung.stand(b.store.local, b.profile)

    @app.post("/api/admin/einrichtung/edsdk")
    async def admin_einrichtung_edsdk(request: Request, datei: UploadFile = File(...)):
        """Das Canon-SDK als ZIP hochladen — auspacken, prüfen, eintragen.

        Der bisherige Weg verlangte einen entpackten Ordner und einen Aufruf
        auf der Kommandozeile. Wer vor einem Kiosk-Rechner ohne Tastatur
        steht, hat beides nicht.
        """
        require_admin(request)
        from . import edsdk_setup
        roh = await datei.read()
        if len(roh) > 200 * 1024 * 1024:
            raise HTTPException(413, "Das Archiv ist zu groß")
        ok, zeilen = edsdk_setup.aus_zip(roh)
        pfad = edsdk_setup.configure(b.store) if ok else ""
        if ok:
            log.info("EDSDK aus Archiv eingerichtet: %s", pfad)
        return {"ok": ok, "zeilen": zeilen, "pfad": pfad,
                "hinweis": "" if ok else "Das war kein vollständiges Canon-SDK. "
                                          "Gebraucht wird das ZIP, das Canon nach der "
                                          "Anforderung zum Herunterladen anbietet."}

    @app.get("/api/admin/kamera/linux")
    def admin_kamera_linux(request: Request):
        """Was der Kamera am Kabel unter Linux gerade im Weg steht."""
        require_admin(request)
        from . import gphoto_setup
        b = gphoto_setup.pruefen()
        return {"bereit": b.bereit, "linux": b.linux, "kameras": b.kameras,
                "blockierer": b.blockierer, "hinweise": b.hinweise,
                "python_gphoto2": b.python_gphoto2, "gphoto2_programm": b.gphoto2_programm,
                "text": b.zusammenfassung(),
                "einstellungen": gphoto_setup.KAMERA_EINSTELLUNGEN}

    @app.post("/api/admin/kamera/linux/freigeben")
    def admin_kamera_freigeben(request: Request):
        """Die Dienste beenden, die die Kamera festhalten — auf Anweisung."""
        require_admin(request)
        from . import gphoto_setup
        return gphoto_setup.freigeben()

    # ── Queues: prints and failed uploads, for the operator ──
    @app.get("/api/admin/queues")
    def admin_queues(request: Request):
        """What waits: prints not out yet, uploads that gave up — with time, state and reason."""
        require_admin(request)
        from photobox_sync import reasons
        # The reason in plain words first (reasons.py); the raw error stays for
        # whoever needs the detail.
        failed = [{**f, "reason": reasons.explain(f.get("error") or ""), "error": redact.text(f.get("error") or "")}
                  for f in b.outbox.failed_list(50)]
        return {"prints": b.print_queue_view(50), "failed_uploads": failed, "counts": b.outbox.counts(),
                "failed_reasons": reasons.summary(f.get("error") or "" for f in b.outbox.failed_list(500)),
                # Sent / waiting / failed per target: did the second target get what the gallery got?
                "targets": b.outbox.counts_by_target(),
                "second_target": b.second_target.report().get("configured", False)}

    @app.get("/api/admin/targets/host-key")
    def admin_target_host_key(request: Request):
        """The second target's SFTP server key: the pinned fingerprint and a different one it showed."""
        require_admin(request)
        rep = b.second_target.report()
        return {k: rep[k] for k in ("host_key", "host_key_seen") if k in rep}

    @app.post("/api/admin/targets/host-key")
    def admin_target_host_key_accept(request: Request, body: dict = Body(...)):
        """Accept the new key the server showed — with the PIN, the exact fingerprint compared."""
        require_admin(request)
        try:
            return {"ok": True, **b.second_target.accept_host_key(str((body or {}).get("fingerprint") or ""))}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/admin/targets/backfill")
    def admin_target_backfill(request: Request):
        """"Bisherige Aufnahmen dieses Events nachsenden": one tap, this event only, never by itself."""
        require_admin(request)
        try:
            n = b.second_target.backfill()
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": True, "added": n,
                "result": (f"{n} Dateien dieses Events warten jetzt auf das zweite Ziel." if n else
                           "Nichts nachzusenden: Das zweite Ziel hat schon alles von diesem Event.")}

    @app.post("/api/admin/queues/prints/discard")
    def admin_queue_discard_prints(request: Request, body: dict = Body(...)):
        """One print (``ids``) or all of them (``confirm: "all"``, the page's second step)."""
        require_admin(request)
        try:
            return {"ok": True, "result": b.discard_prints(body)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/admin/queues/uploads/retry")
    def admin_queue_retry_uploads(request: Request, body: dict = Body(default={})):
        """Failed uploads back into the queue: these ``ids``, or all without."""
        require_admin(request)
        from photobox_sync.agent import job_ids
        try:
            n = b.outbox.retry_ids(job_ids(body))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "result": f"{n} Upload(s) wieder in der Warteschlange"}

    @app.post("/api/admin/queues/uploads/discard")
    def admin_queue_discard_uploads(request: Request, body: dict = Body(...)):
        """Give up on failed uploads: ``ids``, or all with ``confirm: "all"``. The files stay on the booth."""
        require_admin(request)
        try:
            return {"ok": True, "result": b.agent._job_discard_failed(body)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/admin/assets")
    def admin_assets(request: Request):
        """Was in der Medienablage liegt — und wo es gebraucht wird."""
        require_admin(request)
        from .medienablage import liste
        from .config import BUILTIN_PREFIX, START_BUILTINS
        return {"assets": liste(b.assets_dir, b.profile, b.templates()),
                "max_bytes": medienablage.MAX_BYTES,
                "erlaubt": list(medienablage.ERLAUBT),
                # Backgrounds that ship with the booth: chosen like a file, never uploaded.
                "start_builtins": [{"name": f"{BUILTIN_PREFIX}{k}.svg", "label": v, "url": f"/static/start/{k}.svg"}
                                   for k, v in START_BUILTINS.items()]}

    @app.post("/api/admin/assets")
    async def admin_assets_upload(request: Request, datei: UploadFile = File(...)):
        """Ein Bild, ein Video oder einen Ton einlagern.

        Der Name wird entschärft, nicht abgelehnt: Wer vom Telefon hochlädt,
        hat Leerzeichen und Umlaute im Namen, und das ist der Normalfall.
        Überschrieben wird nie — aus ``ton.mp3`` wird ``ton-2.mp3``.
        """
        require_admin(request)
        roh = await datei.read()
        if not roh:
            raise HTTPException(400, "Die Datei ist leer")
        if len(roh) > medienablage.MAX_BYTES:
            raise HTTPException(413, f"Zu groß — erlaubt sind "
                                     f"{medienablage.MAX_BYTES // (1024 * 1024)} MB")
        name = medienablage.sauberer_name(datei.filename or "")
        if not medienablage.art(name):
            raise HTTPException(400, "Das ist kein Bild, Video oder Ton, den die Box abspielen kann")
        b.assets_dir.mkdir(parents=True, exist_ok=True)
        name = medienablage.freier_name(b.assets_dir, name)
        (b.assets_dir / name).write_bytes(roh)
        log.info("Medium eingelagert: %s (%.1f MB)", name, len(roh) / 1e6)
        return {"ok": True, "name": name, "art": medienablage.art(name), "bytes": len(roh)}

    @app.delete("/api/admin/assets/{name}")
    def admin_assets_delete(request: Request, name: str):
        """Löschen — außer es ist in Gebrauch.

        Die Absage sagt, wo es gebraucht wird. Eine Fehlermeldung, die nur
        „geht nicht" sagt, kostet jemanden am Abend zehn Minuten.
        """
        require_admin(request)
        sicher = Path(medienablage.sauberer_name(name)).name
        pfad = (b.assets_dir / sicher).resolve()
        if pfad.parent != b.assets_dir.resolve() or not pfad.is_file():
            raise HTTPException(404, "Gibt es hier nicht")
        benutzt = (medienablage.verwendung(b.profile, sicher)
                   + [f"Vorlage „{v}“" for v in medienablage.in_vorlagen(b.templates(), sicher)])
        if benutzt:
            raise HTTPException(409, "Wird noch verwendet: " + ", ".join(benutzt)
                                     + ". Erst dort abwählen, dann löschen.")
        pfad.unlink()
        return {"ok": True, "name": sicher}

    @app.post("/api/admin/templates/import")
    async def admin_templates_import(request: Request, paket: UploadFile = File(...)):
        """Ein Paket einlesen: Bilder zu den Medien, Vorlagen in die Liste."""
        require_admin(request)
        from .media import vorlagen_paket

        try:
            gelesen = vorlagen_paket.lesen(await paket.read())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        vorlagen, medien, hinweise = gelesen.vorlagen, gelesen.medien, list(gelesen.hinweise)
        b.assets_dir.mkdir(parents=True, exist_ok=True)
        for name, roh in medien.items():
            ziel = b.assets_dir / name
            if ziel.exists() and ziel.read_bytes() != roh:
                hinweise.append(f"{name}: Ein Bild mit diesem Namen war schon da — behalten.")
                continue
            if not ziel.exists():
                ziel.write_bytes(roh)
        aktiv = list(b.profile.get("collage_templates") or [])
        namen = []
        for v in vorlagen:
            ziel = b.assets_dir / templates.dateiname(v)
            ziel.write_bytes(templates.inhalt(v))
            namen.append(ziel.name)
            if ziel.name not in aktiv:
                aktiv.append(ziel.name)
        b._apply_profile({**json.loads(json.dumps(b.profile)), "collage_templates": aktiv})
        # Was hier ankommt, gehört auch in die Galerie — sonst steht die Vorlage
        # nur auf dieser einen Box und ist beim nächsten Aufbau wieder weg.
        for name in namen:
            b.push_template(name)
        return {"ok": True, "vorlagen": [v["name"] for v in vorlagen],
                "medien": sorted(medien), "hintergruende": gelesen.hintergruende,
                "hinweise": hinweise}

    @app.post("/api/admin/templates/delete")
    def admin_templates_delete(request: Request, body: dict = Body(...)):
        require_admin(request)
        name = str(body.get("file") or "")
        ziel = b.assets_dir / name
        if ziel.suffix == ".json" and ziel.is_file() and ziel.parent == b.assets_dir:
            kachel = next((x for x in b.layout_overview()["layouts"] if x.get("file") == name), None)
            if kachel and not kachel["deletable"]:
                # The gallery sends it again with the next sync: deleting here would not last.
                raise HTTPException(409, "Kommt aus der Galerie und käme beim nächsten Abgleich zurück. "
                                         "In der Galerie löschen; hier nur ausblenden.")
            aktiv = [x for x in (b.profile.get("collage_templates") or []) if x != name]
            if b.profile.get("collage_exact") and name in (b.profile.get("collage_templates") or []) \
                    and not [x for x in aktiv if (b.assets_dir / x).is_file()] \
                    and not (b.profile.get("collage_builtins") or []):
                raise HTTPException(409, "Das ist das letzte sichtbare Layout. Erst ein anderes sichtbar schalten.")
            ziel.unlink()
            b._apply_profile({**json.loads(json.dumps(b.profile)), "collage_templates": aktiv})
            return {"ok": True}
        raise HTTPException(404, "Vorlage nicht gefunden")

    @app.get("/admin/vorlagen")
    def admin_templates_page():
        return FileResponse(WEB / "vorlagen.html", headers={"Cache-Control": PAGE_CACHE, **PAGE_HEADERS})

    @app.get("/api/admin/events")
    def admin_events(request: Request):
        require_admin(request)
        return b.events_liste()

    @app.post("/api/admin/events")
    def admin_event_new(request: Request, body: dict = Body(...)):
        require_admin(request)
        return b.event_anlegen(str(body.get("name", "")), str(body.get("galerie", "privat")))

    @app.post("/api/admin/events/select")
    def admin_event_select(request: Request, body: dict = Body(...)):
        require_admin(request)
        return b.event_waehlen(body.get("event_id") or body.get("slug") or "")

    @app.get("/api/admin/profile/export")
    def admin_profile_export(request: Request):
        """Profil als Datei sichern — Vorlage für die nächste Box oder das nächste Event."""
        require_admin(request)
        data = json.loads(json.dumps(b.profile))
        data.get("event", {}).pop("share_key", None)      # Schlüssel gehört zum Event, nicht zur Vorlage
        if data.get("mail"):
            data["mail"].pop("password", None)
        name = (b.store.local.get("booth_name") or "fotobox").lower().replace(" ", "-")
        return Response(json.dumps(data, indent=2, ensure_ascii=False), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="profil-{name}.json"'})

    @app.post("/api/admin/profile/import")
    def admin_profile_import(request: Request, body: dict = Body(...)):
        """Gesicherte Vorlage einspielen. Event und Version der Box bleiben, wie sie sind."""
        require_admin(request)
        incoming = body.get("profile") if isinstance(body.get("profile"), dict) else body
        if not isinstance(incoming, dict):
            raise HTTPException(400, "profile.invalid")
        merged = {**json.loads(json.dumps(b.profile)), **incoming}
        merged["event"] = b.profile.get("event", {})
        merged["version"] = int(b.profile.get("version", 0))
        if not (incoming.get("mail") or {}).get("password"):
            merged.setdefault("mail", {})["password"] = (b.profile.get("mail") or {}).get("password", "")
        b._apply_profile(merged)
        return {"ok": True, "server_wins": bool(b.store.local["server"].get("url"))}

    @app.get("/api/admin/doctor")
    def admin_doctor(request: Request):
        """Selbstdiagnose für die Verwaltung — dieselben Prüfungen wie ``--doctor``."""
        require_admin(request)
        from .doctor import Doctor, summary
        checks = Doctor(b).run()
        return {"checks": [c.as_dict() for c in checks], **summary(checks)}

    @app.get("/api/admin/aufraeumen")
    def admin_aufraeumen(request: Request):
        require_admin(request)
        return b.aufraeumer.stand()

    @app.post("/api/admin/aufraeumen/jetzt")
    def admin_aufraeumen_jetzt(request: Request):
        """Von Hand anstoßen — dieselben Regeln, nur ohne auf die Uhr zu warten."""
        require_admin(request)
        import shutil as _sh
        frei = _sh.disk_usage(b.data).free / 1e9
        return b.aufraeumer.lauf(frei_gb=frei)

    @app.get("/api/admin/testbilder")
    def admin_testbilder(request: Request):
        require_admin(request)
        return b.testbilder_stand()

    @app.post("/api/admin/testbilder/entfernen")
    def admin_testbilder_entfernen(request: Request, body: dict = Body(...)):
        """The test runs off the booth; the gallery keeps what it already has."""
        require_admin(request)
        try:
            anzahl = int(body.get("anzahl"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Anzahl fehlt")
        return b.testbilder_entfernen(anzahl, getippt=str(body.get("getippt") or ""),
                                      zaehler=bool(body.get("zaehler")), papier=bool(body.get("papier")))

    @app.get("/api/admin/meldungen")
    def admin_meldungen(request: Request, limit: int = 50):
        require_admin(request)
        b._geraete_abgleichen()
        return {"offen": b.meldungen.offen(), "verlauf": b.meldungen.verlauf(limit),
                "schlimmste": b.meldungen.schlimmste()}

    @app.post("/api/admin/meldungen/probe")
    def admin_meldungen_probe(request: Request):
        """Eine Meldung je Stufe verschicken — den Weg einmal sehen.

        Ohne das merkt der Betreiber einen falschen SMTP-Server an dem Abend,
        an dem die Meldung wirklich gebraucht wird.
        """
        require_admin(request)
        return b.meldungen.probe()

    @app.post("/api/admin/meldungen/{key}/entwarnung")
    def admin_meldungen_entwarnung(request: Request, key: str):
        require_admin(request)
        b.meldungen.entwarnung(key)
        return {"ok": True, "offen": b.meldungen.offen()}

    @app.get("/api/admin/dauerbetrieb")
    def admin_dauerbetrieb(request: Request):
        """Was Windows dem Dauerbetrieb in den Weg stellt — und was wir davon verstellt haben."""
        require_admin(request)
        return {"windows": b.dauerbetrieb.windows, "punkte": b.dauerbetrieb.status()}

    @app.post("/api/admin/dauerbetrieb/{key}/{was}")
    def admin_dauerbetrieb_setzen(request: Request, key: str, was: str):
        """Eine einzelne Einstellung anwenden oder zurücknehmen.

        Einzeln, weil der Betreiber sonst nicht weiß, was auf seinem Rechner
        passiert ist. „Alles" gibt es als eigene Schaltfläche, nicht als
        Nebenwirkung.
        """
        require_admin(request)
        if was == "anwenden":
            ergebnis = ([b.dauerbetrieb.anwenden(key)] if key != "alle"
                        else b.dauerbetrieb.empfohlen_anwenden())
        elif was == "zuruecksetzen":
            ergebnis = ([b.dauerbetrieb.zuruecksetzen(key)] if key != "alle"
                        else b.dauerbetrieb.alles_zuruecksetzen())
        else:
            raise HTTPException(status_code=400, detail="anwenden oder zuruecksetzen")
        return {"ergebnis": ergebnis, "punkte": b.dauerbetrieb.status()}

    @app.get("/api/admin/logs")
    def admin_logs(request: Request, limit: int = 100):
        require_admin(request)
        return {"counts": b.logbuf.counts(), "items": b.logbuf.peek(max(1, min(limit, 500))),
                "tail": _tail(b.data / "logs" / "booth.log", 200)}

    @app.post("/api/admin/action/{name}")
    def admin_action(request: Request, name: str):
        require_admin(request)
        actions = {
            "test_capture": lambda: str(b.test_capture()),
            "test_print": b.test_print,
            "test_meldung": lambda: b.meldungen.probe()["hinweis"],
            "sync_now": lambda: (b.outbox.reset_backoff(), b.agent.sync_once())[1],
            "retry_failed": b.outbox.retry_failed,
            "retry_mail": b.mail.retry_failed,
            "test_webhook": lambda: ("gesendet" if b.webhooks.deliver(
                {"event": "error", "at": time.time(), "data": {"test": True}}) else "nicht zugestellt"),
            "reset_print_counter": b.printer.reset_counter,
            # „Papier oder Farbband gewechselt" — alles, was wartet, geht erneut
            # in den Drucker. Ohne diesen Knopf müsste der Betreiber die Box neu
            # starten, und genau dabei gingen früher die Drucke verloren.
            "release_prints": lambda: f"{b.printer.release_held()} wartende Drucke erneut",
            "paper_changed": lambda: (f"{b.paper_changed()} Drucker auf voll, "
                                      f"{b.printer.release_held()} wartende Drucke erneut"),
            # SELPHY: the ribbon runs out at another count than the tray.
            # What waited for it may go again, as after a paper change.
            "ribbon_changed": lambda: (f"Farbband neu bei {b.printer.ribbon_changed()} Drucker(n), "
                                       f"{b.printer.release_held()} wartende Drucke erneut"),
            # Prints that wait for paper (queued or held), thrown away on the
            # operator's word; each guest gets the sheet back.
            "discard_waiting_prints": lambda: f"{b.printer.discard_waiting()} wartende Drucke verworfen",
            "restart": b.restart_on_purpose,
            "quit": b.quit_on_purpose,
        }
        if name not in actions:
            raise HTTPException(404)
        try:
            return {"ok": True, "result": str(actions[name]())}
        except Exception as e:
            # Eine fehlgeschlagene Wartungsaktion gehört ins Protokoll und damit
            # auch in die Galerie — sonst sieht sie nur, wer davorsteht.
            log.warning("Aktion %s fehlgeschlagen: %s", name, e)
            return JSONResponse({"ok": False, "result": str(e)}, status_code=500)

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": PAGE_CACHE, **PAGE_HEADERS})

    @app.get("/admin/hilfe")
    def help_page():
        """What every function does, German and English. Ships with the program."""
        return FileResponse(WEB / "hilfe.html", headers={"Cache-Control": PAGE_CACHE, **PAGE_HEADERS})

    @app.get("/admin")
    def admin_page():
        return FileResponse(WEB / "admin.html", headers={"Cache-Control": PAGE_CACHE, **PAGE_HEADERS})

    app.mount("/static", _RevalidatedStatic(directory=WEB / "static"), name="static")
    plugins.safe_call(b.plugins, "booth_startup", app=app, booth=b)
    return app


def _tail(path: Path, n: int) -> List[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]
    except OSError:
        return []
