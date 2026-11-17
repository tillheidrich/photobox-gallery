"""Remote control from the gallery: the job ``remote_control`` (MCP right ``control``).

The gallery may only *ask*. This booth decides:

* Nothing happens unless "Fernsteuerung durch die Galerie erlauben" is on at
  this booth (local setting ``remote_control.allowed``, PIN, **off** by
  default, never on ``REMOTE_LOCAL_KEYS``): a rented booth at a customer's
  party is not driveable from outside unless its operator decided so.
* Only the actions in :data:`ACTIONS` exist — the same keys as
  ``ACTIONS`` in app/remote_control.py (each suite compares the two files).
  No command line, no path, no address comes in.
* A job carrying another event than this booth's is refused.
* Everything runs through the methods the kiosk uses: a locked booth starts
  nothing, the consent step applies, print allowances count; on top, one run
  at a time (the kiosk has one screen, a remote run must not cut into a
  guest's run).

The answer is JSON text; a picture (live image, screenshot) comes as a small
JPEG in ``image_base64`` (at most 640 px), which the gallery turns into MCP
image content.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Tuple

from . import __version__, redact

log = logging.getLogger(__name__)

ACTIONS = {
    "status", "trigger", "start_run", "shot", "clip", "finish", "run", "abort", "set_text", "print", "reprint",
    "lock", "unlock", "restart_app", "paper_changed", "ribbon_changed", "reset_print_counter", "release_prints",
    "discard_prints", "test_print", "test_capture", "doctor", "logs", "live_image", "screenshot", "list_runs",
    "delete_run", "quit", "restart_pc", "shutdown_pc", "set_language",
}
#: The kiosk mark "Fernsteuerung aktiv" shows this long after the last job.
ACTIVE_SECONDS = 120
#: A run younger than this that is neither finished nor deleted counts as running.
RUN_BUSY_SECONDS = 600
#: After the result screen: time a guest may still be typing an e-mail address.
#: Only the fallback for a kiosk that has not said whether a dialog is open.
TYPING_SECONDS = 60
#: The kiosk polls /api/state every 4 s and carries ``typing`` along; a report
#: older than this means the kiosk has gone quiet and the fallback applies.
KIOSK_REPORT_FRESH_SECONDS = 15
#: After a typing dialog closed: no picture yet, the screen may still fade it
#: out and the guest may open it again at once.
TYPING_GRACE_SECONDS = 8
PICTURE_MAX = 640
#: Seconds Windows waits before it restarts or shuts down: the booth program
#: ends first (goodbye to the gallery, camera handed back) and has 3 s for it.
POWER_DELAY_SECONDS = 15


def open_runs(booth) -> List[str]:
    """Runs a guest has started and not finished (younger than RUN_BUSY_SECONDS)."""
    now = time.time()
    return [uid for uid, s in list(booth.sessions.items())
            if not s.finalized and not s.deleted and now - s.created < RUN_BUSY_SECONDS]


def nothing_running(booth) -> None:
    """Raise unless no guest run is open and no print is on its way.

    One rule for live control and for the ordinary jobs ``restart_app``,
    ``lock`` and ``test_capture`` (second review R2-U2): those came through
    ``send_booth_job`` and ran in the middle of a guest's run.
    """
    busy = open_runs(booth)
    if busy:
        raise RuntimeError(f"Es läuft ein Durchgang ({busy[0]}); erst fertigstellen oder abbrechen.")
    if int(getattr(booth.printer, "pending", 0) or 0) > 0:
        raise RuntimeError("Es wird gerade gedruckt; erst wenn der Drucker fertig ist.")


def power_off(restart: bool) -> Tuple[bool, str]:
    """Ask Windows to restart (``restart``) or shut down this PC after a delay.

    The only place in the booth that reaches the power switch. Outside
    Windows it does nothing and says so; the test suite replaces it
    (booth/tests/conftest.py), so no test can switch off the machine it runs on.
    """
    if os.name != "nt":
        return False, "Neustart und Herunterfahren gehen nur unter Windows."
    args = ["shutdown", "/r" if restart else "/s", "/t", str(POWER_DELAY_SECONDS),
            "/c", "Photobox: " + ("Neustart" if restart else "Herunterfahren") + " aus der Galerie"]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=20)
    except Exception as e:                                   # noqa: BLE001 - the reason goes back
        return False, f"shutdown nicht ausführbar: {e}"
    if out.returncode != 0:
        return False, ((out.stdout or "") + (out.stderr or "")).strip() or f"shutdown: Fehler {out.returncode}"
    return True, f"Windows {'startet neu' if restart else 'fährt herunter'} in {POWER_DELAY_SECONDS} s"


class NotAllowed(RuntimeError):
    pass


def settings(local: Dict[str, Any]) -> Dict[str, Any]:
    rc = local.get("remote_control") or {}
    return {"allowed": bool(rc.get("allowed")), "indicator": rc.get("indicator", True) is not False}


def _detail(e: BaseException) -> str:
    """An HTTPException of the kiosk routes carries its reason in ``detail``."""
    return str(getattr(e, "detail", "") or e)


def _jpeg(raw: bytes) -> str:
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as im:
        im = im.convert("RGB")
        im.thumbnail((PICTURE_MAX, PICTURE_MAX))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=75)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class Fernsteuerung:
    #: The power switch (see power_off). Replaced in tests, never called there.
    pc_power = staticmethod(power_off)

    def __init__(self, booth):
        self.b = booth
        self.last_job = 0.0
        #: What the kiosk on this PC last said about a dialog in which someone
        #: types (e-mail address, greeting, PIN): open or not, and when
        #: (time.monotonic). ``typing_reported`` stays None until it has said anything.
        self.typing_open = False
        self.typing_reported = None
        self.typing_closed = 0.0

    # ── what the kiosk says and asks ────────────────────────────────
    def kiosk_typing(self, is_open: bool) -> None:
        """The kiosk on this PC reports that a typing dialog opened or closed."""
        now = time.monotonic()
        if self.typing_open and not is_open:
            self.typing_closed = now
        self.typing_open = bool(is_open)
        self.typing_reported = now

    def active(self) -> bool:
        return time.time() - self.last_job < ACTIVE_SECONDS

    def kiosk_state(self) -> Dict[str, Any]:
        s = settings(self.b.store.local)
        return {"active": bool(s["allowed"] and self.active()), "indicator": s["indicator"]}

    # ── the job ────────────────────────────────────────────────────
    def handle(self, payload: Dict[str, Any]) -> str:
        if not settings(self.b.store.local)["allowed"]:
            raise NotAllowed("Fernsteuerung ist an dieser Box nicht erlaubt (Box-Verwaltung → "
                             "„Fernsteuerung durch die Galerie erlauben“).")
        action = str((payload or {}).get("action") or "")
        if action not in ACTIONS:
            raise ValueError(f"Unbekannte Aktion: {action[:40]}")
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        wanted = str(payload.get("event") or "")
        current = str(self.b.profile["event"].get("slug") or "")
        if wanted and current and wanted != current:
            raise ValueError(f"Die Box ist auf einem anderen Event ({current}) als der Auftrag ({wanted}).")
        self.last_job = time.time()
        log.info("Fernsteuerung: %s (von %s)", action, str(payload.get("by") or "Galerie")[:80])
        try:
            result = getattr(self, "_" + action)(args)
        except NotAllowed:
            raise
        except Exception as e:                                # noqa: BLE001 — the reason goes back to the gallery
            raise RuntimeError(_detail(e)) from e
        return json.dumps(result, ensure_ascii=False, default=str)

    # ── helpers ────────────────────────────────────────────────────
    def _open_runs(self) -> List[str]:
        return open_runs(self.b)

    def _one_at_a_time(self) -> None:
        if self.b.locked:
            raise RuntimeError("Die Box ist gesperrt.")
        busy = self._open_runs()
        if busy:
            raise RuntimeError(f"Es läuft schon ein Durchgang ({busy[0]}); erst fertigstellen oder abbrechen.")

    def _run_item(self, uid: str) -> Dict[str, Any]:
        item = self.b.library.get(uid) or {}
        return {"uid": uid, "mode": item.get("mode"), "share_url": item.get("share_url"),
                "prints": item.get("prints"), "print": self.b.print_allowance(uid) if item.get("print_file") else None}

    def _consent(self, args: Dict[str, Any]):
        option = str(args.get("consent") or "")
        return {"option": option, "lang": str(args.get("lang") or "")} if option else None

    # ── actions ────────────────────────────────────────────────────
    def _status(self, args):
        b = self.b
        return {"camera": b.camera.info(), "printer": b.printer.status(), "locked": b.locked,
                "event": b.profile["event"].get("slug", ""), "open_runs": self._open_runs(),
                "print": b.print_allowance(None), "modes": b.profile.get("modes"),
                "consent_options": b.consent_options(), "version": __version__}

    def _trigger(self, args):
        b = self.b
        if b.locked:
            raise RuntimeError("Die Box ist gesperrt.")
        mode = str(args.get("mode") or "photo")
        if mode not in b.profile["modes"]:
            raise ValueError("Modus nicht aktiv")
        b.trigger = {"mode": mode, "at": time.time()}         # the kiosk picks it up like the buzzer's
        return {"triggered": mode, "hint": "Der Kiosk startet den Durchgang mit Countdown auf dem Bildschirm."}

    def _start_run(self, args):
        self._one_at_a_time()
        b = self.b
        mode = str(args.get("mode") or "photo")
        s = b.start_session(mode, args.get("format") or b.format_without_choice(mode),
                            args.get("template"), args.get("background"), str(args.get("chroma_background") or ""),
                            consent=self._consent(args))
        return s.public()

    def _shot(self, args):
        b = self.b
        s = b.get_session(str(args.get("uid") or ""))
        replace = args.get("replace")
        try:
            path = b.take_shot(s, bool(args.get("retake")), int(replace) if replace else None)
        except Exception as e:
            b.run_failed(s.uid, "shot", e, replace=replace)
            raise
        return {**s.public(), "shot": s.shots.index(path) + 1 if path in s.shots else len(s.shots)}

    def _clip(self, args):
        b = self.b
        s = b.get_session(str(args.get("uid") or ""))
        b.record_clip(s)
        return s.public()

    def _finish(self, args):
        b = self.b
        s = b.get_session(str(args.get("uid") or ""))
        b.finalize(s, str(args.get("look") or "original"))
        return self._run_item(s.uid)

    def _run(self, args):
        """A whole run without the screen: start, every shot or the clip, finish, optionally print."""
        s_public = self._start_run(args)
        b = self.b
        s = b.get_session(s_public["uid"])
        if s.is_video:
            b.record_clip(s)
        else:
            while len(s.shots) < max(1, s.shots_needed):
                b.take_shot(s)
        b.finalize(s, str(args.get("look") or "original"))
        out = self._run_item(s.uid)
        copies = int(args.get("copies") or 0)
        if copies > 0:
            out["printed"] = b.print_uid(s.uid, copies)
        return out

    def _abort(self, args):
        b = self.b
        s = b.get_session(str(args.get("uid") or ""))
        with s.lock:
            if s.finalized:
                raise RuntimeError("Durchgang ist schon fertig")
            b.zahlen.zaehle(s.event_slug or b._zahlen_event(), "aborted.cancel")
            b.delete(s.uid)
        return {"aborted": s.uid}

    def _set_text(self, args):
        b = self.b
        cfg = b.profile.get("guest_text") or {}
        if not cfg.get("enabled"):
            raise RuntimeError("Gästetexte sind aus")
        s = b.get_session(str(args.get("uid") or ""))
        limit = max(1, min(120, int(cfg.get("max_len") or 40)))
        s.guest_text = " ".join(str(args.get("text") or "").split())[:limit]
        return {"uid": s.uid, "text": s.guest_text}

    def _print(self, args):
        return self.b.print_uid(str(args.get("uid") or ""), max(1, int(args.get("copies") or 1)))

    def _reprint(self, args):
        return self.b.reprint(str(args.get("uid") or ""), max(1, int(args.get("copies") or 1)))

    def _job(self, name: str, args):
        """The same handler the ordinary job of that name runs."""
        return self.b.agent.job_handlers[name](args or {})

    def _lock(self, args):
        return self._job("lock", args)

    def _unlock(self, args):
        return self._job("unlock", args)

    def _restart_app(self, args):
        self._nothing_running()
        return self._job("restart_app", args)

    def _paper_changed(self, args):
        return self._job("paper_changed", args)

    def _ribbon_changed(self, args):
        return self._job("ribbon_changed", args)

    def _reset_print_counter(self, args):
        return self._job("reset_print_counter", args)

    def _release_prints(self, args):
        return self._job("release_prints", args)

    def _test_print(self, args):
        return self._job("test_print", args)

    def _test_capture(self, args):
        self._nothing_running()
        return self._job("test_capture", args)

    def _doctor(self, args):
        return self._job("doctor", args)

    def _discard_prints(self, args):
        # With "ids": those prints only. Without: all of them, as before — the
        # gallery's tool asks for confirmation first (two steps), so that call
        # is the confirmed one.
        if (args or {}).get("ids") is not None:
            return self.b.discard_prints({"ids": args.get("ids")})
        return self.b.discard_prints({"confirm": "all"})

    def _logs(self, args):
        # The buffer masks on the way in; this is the net for anything older
        # (final review A4): no guest address leaves through this answer.
        lines = self.b.logbuf.peek(max(1, min(int(args.get("limit") or 50), 200)))
        return [{k: (redact.text(v) if isinstance(v, str) else v) for k, v in line.items()} for line in lines]

    def _live_image(self, args):
        self._nothing_running()
        self._no_guest_at_the_result()
        frame, _seq = self.b.camera.latest_frame(-1, 2.0)
        if not frame:
            raise RuntimeError("kein Livebild (Kamera nicht bereit?)")
        return {"image_base64": _jpeg(frame), "max_px": PICTURE_MAX}

    def _screenshot(self, args):
        self._nothing_running()
        self._no_guest_at_the_result()
        from . import window as window_mod
        shot = window_mod.screenshot(self.b.data / window_mod.SCREENSHOT_FILE)
        with open(shot["file"], "rb") as f:
            raw = f.read()
        return {"image_base64": _jpeg(raw), "width": shot.get("width"), "height": shot.get("height")}

    def _list_runs(self, args):
        b = self.b
        slug = b.profile["event"].get("slug", "")
        items = b.library.list(slug, 0, max(1, min(int(args.get("limit") or 20), 60)))
        return {"total": b.library.count(slug),
                "runs": [{"uid": i["uid"], "mode": i.get("mode"), "prints": i.get("prints"),
                          "created": i.get("created") or i.get("created_at")} for i in items]}

    def _delete_run(self, args):
        b = self.b
        self._nothing_running()
        if not b.profile.get("allow_delete", True):
            raise RuntimeError("Löschen ist im Profil dieser Box aus")
        uid = str(args.get("uid") or "")
        if not uid or not (b.library.get(uid) or b.sessions.get(uid)):
            raise RuntimeError("Durchgang unbekannt")
        b.delete(uid)
        return {"deleted": uid}

    def _quit(self, args):
        self._nothing_running()
        return self.b.quit_on_purpose(3.0)

    def _no_guest_at_the_result(self) -> None:
        """No picture of the screen or the room while someone types into the kiosk.

        The dialogs (e-mail address, greeting, PIN) live in the kiosk page. The
        kiosk on this PC says when one opens or closes (``kiosk_typing``, via
        ``/api/state?typing=``); while that report is fresh it decides, with
        a short grace after closing. A kiosk that has gone quiet with a dialog
        open still counts as typing for the result-screen window. Without a
        fresh report the booth guesses as before (final review A10): a run
        finished less than the result time plus a minute ago counts as "a guest
        may be typing".
        """
        window = float(self.b.profile.get("result_seconds") or 45) + TYPING_SECONDS
        mono = time.monotonic()
        said = self.typing_reported
        if said is not None and (mono - said < KIOSK_REPORT_FRESH_SECONDS or (self.typing_open and mono - said < window)):
            if self.typing_open:
                raise RuntimeError("Am Bildschirm der Box tippt gerade jemand (E-Mail-Adresse, Gruß oder PIN); "
                                   "kein Bild, solange das Fenster offen ist.")
            left = TYPING_GRACE_SECONDS - (mono - self.typing_closed)
            if self.typing_closed and left > 0:
                raise RuntimeError("Am Bildschirm der Box wurde eben noch getippt (E-Mail-Adresse, Gruß oder PIN); "
                                   f"in {int(left) + 1} s noch einmal.")
            return
        now = time.time()
        for uid, s in list(self.b.sessions.items()):
            if s.deleted or not s.finalized:
                continue
            done = s.created
            for p in (s.result, s.result_web, s.thumb):
                try:
                    done = max(done, p.stat().st_mtime) if p else done
                except OSError:
                    pass
            if now - done < window:
                raise RuntimeError("Am Ergebnisbildschirm steht gerade ein Gast und tippt vielleicht seine "
                                   f"E-Mail-Adresse (Durchgang {uid}); in {int(window - (now - done)) + 1} s noch einmal.")

    def _nothing_running(self) -> None:
        """Power, language, quitting, screen pictures: never under a guest, never with a print on its way.

        Since the final review (A10) also ``quit``, ``restart_app``,
        ``delete_run``, ``test_capture``, ``screenshot`` and ``live_image``.
        """
        nothing_running(self.b)

    def _pc(self, restart: bool) -> Dict[str, Any]:
        self._nothing_running()
        ok, text = self.pc_power(restart)
        if not ok:
            raise RuntimeError(text)
        # Windows is about to end everything. The booth leaves first and on
        # purpose: the goodbye carries "windows_shutdown" (and whether it comes
        # back), so the gallery's silence alarm stays quiet, and the watcher
        # does not start the program again in the seconds before the end.
        from . import lifecycle
        lifecycle.announce("windows_shutdown", restart)
        self.b.quit_on_purpose(3.0)
        return {"pc": "restart" if restart else "shutdown", "hint": text}

    def _restart_pc(self, args):
        return self._pc(True)

    def _shutdown_pc(self, args):
        return self._pc(False)

    def _set_language(self, args):
        """The language the kiosk starts each run in, until the next restart ("" = the profile's first)."""
        self._nothing_running()
        lang = str(args.get("lang") or "").strip().lower()
        languages = list(self.b.profile.get("languages") or ["de"])
        if lang and lang not in languages:
            raise ValueError(f"Sprache {lang[:8]} ist im Profil nicht eingeschaltet (möglich: {', '.join(languages)})")
        self.b.kiosk_lang = lang
        return {"lang": lang, "default": languages[0], "hint": "Gilt ab sofort am Startbildschirm, bis zum "
                "nächsten Neustart der Box; leer = wieder die erste Sprache des Profils."}


__all__: List[str] = ["ACTIONS", "Fernsteuerung", "NotAllowed", "settings"]
