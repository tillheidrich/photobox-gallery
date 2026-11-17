"""Kamera-Dienst: ein Thread besitzt die Kamera, alle anderen fragen an.

Aufgaben:
* hält das neueste Live-Bild bereit (für den MJPEG-Stream der Oberfläche)
* führt Aufnahmen seriell aus
* sammelt Frames für den Boomerang
* verbindet sich nach Fehlern selbst neu (Kabel gezogen, Kamera aus)

Einfrieren ist nie erlaubt: jeder Auftrag hat ein Timeout, und die Oberfläche
bekommt einen klaren Zustand ("verbinde…") statt eines hängenden Bildes.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import queue
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Dict, Callable, List, Optional

from PIL import Image

from .base import CameraBackend, CameraError, CaptureRefused

log = logging.getLogger("booth.camera")

_AF_NG = 0x8D01


def _refusal_code(e: CaptureRefused) -> int:
    """The camera's refusal code: from the exception, else from "(0x00008D01)" in its text."""
    code = int(getattr(e, "code", 0) or 0)
    if code:
        return code
    import re
    m = re.search(r"0x0000(8D[0-9A-F]{2})", str(e), re.IGNORECASE)
    return int(m.group(1), 16) if m else 0


def _discard_movie(fut: Future) -> None:
    try:
        if not fut.cancelled() and fut.exception() is None:
            Path(fut.result()).unlink(missing_ok=True)
    except Exception:                                # noqa: BLE001 — the booth sweeps the folder at start
        pass


EXTRA_BACKENDS: Dict[str, Callable[[dict], CameraBackend]] = {}

_LABEL = {"iso": "ISO", "shutter": "Verschlusszeit", "aperture": "Blende"}


def _describe(values: dict) -> str:
    """{"iso": "200", "shutter": "1/125", "aperture": "5.6"} -> "ISO 200, 1/125, f/5.6"."""
    out = []
    if values.get("iso"):
        out.append(f"ISO {values['iso']}")
    if values.get("shutter"):
        out.append(str(values["shutter"]))
    if values.get("aperture"):
        out.append(f"f/{values['aperture']}")
    return ", ".join(out)


def _digicam_da(url: str) -> bool:
    """Läuft oder liegt digiCamControl auf diesem Rechner?

    Erst der Webserver (läuft es schon?), dann die übliche Installation — das
    Programm startet die Box notfalls selbst.
    """
    import shutil

    try:
        import httpx

        antwort = httpx.get((url or "http://127.0.0.1:5513") + "/?CMD=Status",
                            timeout=httpx.Timeout(1.0, connect=0.7))
        if antwort.status_code < 500:
            return True
    except Exception:                                 # noqa: BLE001 — nicht da ist kein Fehler
        pass
    if shutil.which("CameraControlCmd.exe"):
        return True
    from pathlib import Path as _P
    return any(_P(p).is_file() for p in (
        r"C:\Program Files (x86)\digiCamControl\CameraControl.exe",
        r"C:\Program Files\digiCamControl\CameraControl.exe"))


def _edsdk_da() -> bool:
    """Liegt eine EDSDK.dll neben dem Programm? Dann ist eine Canon gemeint."""
    from pathlib import Path as _P
    hier = _P(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else _P.cwd()
    return any((ordner / "EDSDK.dll").is_file() or (ordner / "EDSDK" / "EDSDK.dll").is_file()
               for ordner in (hier, _P.cwd()))


def create_backend(cfg: dict) -> CameraBackend:
    kind = (cfg.get("backend") or "auto").lower()
    if kind == "auto":
        if os.environ.get("BOOTH_CAMERA"):
            kind = os.environ["BOOTH_CAMERA"]
        elif cfg.get("ccapi_url"):
            kind = "ccapi"                    # eingetragene Adresse heißt: so ist die Kamera angebunden
        elif cfg.get("edsdk_dll") and (os.name == "nt" or sys.platform == "darwin"):
            kind = "edsdk"                    # Canon liefert das SDK für Windows und macOS
        elif os.name != "nt":
            try:
                import gphoto2  # noqa: F401
                kind = "gphoto2"
            except ImportError:
                kind = "mock"
        elif _digicam_da(cfg.get("digicam_url") or ""):
            # Windows: Steht digiCamControl bereit, ist das der Weg für Nikon,
            # Sony und Fujifilm — der Betreiber muss nichts einstellen.
            kind = "digicamcontrol"
        elif _edsdk_da():
            kind = "edsdk"
        else:
            kind = "mock"
    if kind == "ccapi":
        from .ccapi import CcapiCamera
        return CcapiCamera(cfg.get("ccapi_url", ""), cfg.get("ccapi_liveview", "medium"),
                           cfg.get("ccapi_display", "on"))
    if kind == "edsdk":
        from .edsdk import EdsdkCamera
        return EdsdkCamera(cfg.get("edsdk_dll", ""), release=str(cfg.get("edsdk_release") or "hold"))
    if kind == "gphoto2":
        from .gphoto2cam import Gphoto2Camera
        return Gphoto2Camera()
    if kind == "webcam":
        from .webcam import WebcamCamera
        return WebcamCamera(int(cfg.get("webcam_index", 0)))
    if kind == "digicamcontrol":
        from .digicam import DigiCamControlCamera
        return DigiCamControlCamera(cfg.get("digicam_url", ""), cfg.get("folder", ""))
    if kind == "folder":
        from .digicam import FolderCamera
        return FolderCamera(cfg.get("folder", ""), cfg.get("preview_webcam"), cfg.get("trigger_url", ""))
    if kind in EXTRA_BACKENDS:                    # aus Plugins (booth_register_cameras)
        return EXTRA_BACKENDS[kind](cfg)
    from .mock import MockCamera
    return MockCamera()


def rotate_image(img: Image.Image, degrees: int) -> Image.Image:
    degrees = int(degrees) % 360
    if degrees == 90:
        return img.transpose(Image.Transpose.ROTATE_270)   # im Uhrzeigersinn
    if degrees == 180:
        return img.transpose(Image.Transpose.ROTATE_180)
    if degrees == 270:
        return img.transpose(Image.Transpose.ROTATE_90)
    return img


def collect_live_frames(grab: Callable[[], Optional[bytes]], seconds: float, fps: float, *,
                        publish: Optional[Callable[[bytes], None]] = None,
                        clock: Callable[[], float] = time.monotonic,
                        sleep: Callable[[float], None] = time.sleep) -> tuple:
    """Live frames for ``seconds`` of wall time, at most ``fps`` a second: ``(frames, elapsed)``.

    ``elapsed`` is the time the recording really took — at least ``seconds``,
    a little more when the last frame was still being fetched. With a live
    view slower than ``fps`` there are simply fewer frames in that time.
    """
    frames: List[bytes] = []
    start = clock()
    end = start + float(seconds)
    interval = 1.0 / max(1.0, float(fps))
    k = 0
    # Frames are due on a fixed grid (start + k * interval), so small delays
    # do not add up into an extra frame or a longer recording.
    while clock() < end - 1e-9:
        jpeg = grab()
        if jpeg:
            frames.append(jpeg)
            if publish:
                publish(jpeg)
        k += 1
        due = start + k * interval
        if due >= end - 1e-9:
            break
        sleep(max(0.0, due - clock()))
    # A fast camera is done early by up to one interval: the window it
    # covers is the full setting, and so is the time the guest is told.
    if clock() < end:
        sleep(end - clock())
    return frames, clock() - start


def create_video_backend(cfg: dict) -> Optional[CameraBackend]:
    """Optionale zweite Quelle für Live-Bild und Video (Standard: keine — die Kamera per USB reicht).

    Fotos kommen weiter in voller Auflösung über das Haupt-Backend (EDSDK).
    """
    src = (cfg.get("video_source") or "").strip().lower()
    if src.startswith("webcam:"):
        from .webcam import WebcamCamera
        return WebcamCamera(int(src.split(":", 1)[1] or 0))
    return None


class CameraService:
    # Set by the program start on macOS: every service built afterwards leaves
    # its loop to the caller (see start()).
    RUN_IN_CALLER = False

    def __init__(self, backend_factory: Callable[[], CameraBackend], rotate: int = 0,
                 video_factory: Optional[Callable[[], Optional[CameraBackend]]] = None):
        self._factory = backend_factory
        self._video_factory = video_factory
        self.video: Optional[CameraBackend] = None
        self.rotate = rotate
        self.backend: Optional[CameraBackend] = None
        self._beat = time.monotonic()     # last sign of life of the camera loop
        #: Successful connections so far (a fresh session ends the booth's pause of the camera movie).
        self.connections = 0
        self.state = "starting"          # starting | ready | reconnecting | error
        self.error: Optional[str] = None
        self._jobs: "queue.Queue[tuple]" = queue.Queue()
        self._frame: Optional[bytes] = None
        self._frame_seq = 0
        self._frame_cond = threading.Condition()
        self._recording: Optional[List[bytes]] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self.run_in_caller = self.RUN_IN_CALLER
        self._ended = threading.Event()
        self.loop_thread: Optional[threading.Thread] = None
        self.preview_fps = 20.0
        # refused: shots the camera turned down (no focus, card full) without a
        # broken link. focus_fallbacks: "no focus" answered with a press without
        # autofocus (the backend counts per connection, this across them).
        self.stats = {"captures": 0, "errors": 0, "reconnects": 0, "refused": 0, "focus_fallbacks": 0}
        self.desired_settings: dict = {}
        self.settings_result: dict = {}
        # Live view with an exposure of its own (flash in use, see exposure.py).
        # ``preview_settings`` empty = nothing is ever switched: the camera's
        # and the profile's values serve live view and shot alike.
        self.preview_settings: dict = {}
        self._preview_on = False             # the preview values are on the camera now
        self._shot_values: dict = {}         # what the shutter needs back (profile, else read from the camera)
        self._auto_iso: Optional[str] = None
        self._auto_shutter: Optional[str] = None
        self._auto_at = 0.0
        self._preview_now: dict = {}          # the live-view values on the camera now
        self._refused: set = set()            # (axis, value) the camera turned down: not tried again
        self._reported: dict = {}             # what the camera said it holds after a step, if it differed
        self._last_mean: Optional[float] = None
        #: The automatic ladder is at its end and the frame is still too dark:
        #: the booth then brightens the served frames in software (app.py).
        self.preview_at_end = False
        self._auto_failed = False
        #: Where the shot's values read from the camera are kept while the live
        #: view holds its own (set by the booth: data folder, next to local.json).
        #: A booth that dies with the live-view values on the camera would
        #: otherwise read them back as the shot's at the next start.
        self.shot_values_file: Optional[Path] = None
        self.preview_log: List[str] = []     # last few switches, for the self-check and tests
        #: Camera battery: percent, "ac" on mains power, None while unknown.
        #: Read on the camera thread at connect and every BATTERY_EVERY_S,
        #: between two live-view frames — never while a shot is running.
        self.battery = None
        self._battery_due = 0.0

    # ── Zustand ─────────────────────────────────────────────
    # A call into a camera SDK can block for good (EdsOpenSession on a camera
    # left mid-session did, on the real M50). The loop cannot be interrupted,
    # but it must not look alive: no sign of life for STALL_SECONDS reads as a
    # camera that is not there.
    STALL_SECONDS = 60.0
    #: Refused shots in a row (not "no focus") after which the camera is opened afresh.
    REFUSALS_BEFORE_RECONNECT = 2
    _refused_in_row = 0
    STALL_TEXT = "Die Kamera antwortet nicht. Bitte Kamera aus- und wieder einschalten."
    BATTERY_EVERY_S = 300.0

    def _read_battery(self) -> None:
        """Camera thread only. A failed read leaves "unknown", never a reconnect."""
        now = time.monotonic()
        if now < self._battery_due or not self.backend:
            return
        self._battery_due = now + self.BATTERY_EVERY_S
        try:
            value = self.backend.battery_level()
        except Exception as e:                       # noqa: BLE001 — a gauge is no reason to reconnect
            log.debug("Akkustand nicht lesbar: %s", e)
            value = None
        self.battery = value if value == "ac" or isinstance(value, int) else None

    def _stalled(self) -> bool:
        thread = self.loop_thread
        return bool(thread and thread.is_alive() and time.monotonic() - self._beat > self.STALL_SECONDS)

    @property
    def state(self) -> str:
        return "reconnecting" if self._stalled() else self._state

    @state.setter
    def state(self, value: str) -> None:
        self._state = value
        self._beat = time.monotonic()

    @property
    def error(self) -> Optional[str]:
        return self.STALL_TEXT if self._stalled() else self._error

    @error.setter
    def error(self, value: Optional[str]) -> None:
        self._error = value

    # ── öffentliche API (thread-sicher) ─────────────────────
    def start(self) -> None:
        # On macOS the Canon SDK only works on the thread that owns the run
        # loop — the main thread. There the caller runs the loop itself with
        # run_forever() and no thread is started here.
        if self.run_in_caller:
            return
        self._thread.start()

    def run_forever(self) -> None:
        """Run the camera loop on the calling thread until stop()."""
        self._run()

    def stop(self) -> None:
        """End the loop and wait until it has handed the camera back."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        elif self.loop_thread and self.loop_thread is not threading.current_thread():
            self._ended.wait(timeout=5)           # the loop runs in the caller (macOS)

    def latest_frame(self, after_seq: int = -1, timeout: float = 1.0) -> tuple[Optional[bytes], int]:
        with self._frame_cond:
            if self._frame_seq <= after_seq:
                self._frame_cond.wait(timeout)
            return self._frame, self._frame_seq

    def capture(self, target: Path, timeout: float = 0) -> Path:
        # länger warten als das langsamste Backend (Ordner/digiCamControl), sonst läuft die Aufnahme ins Leere
        timeout = timeout or float(getattr(self.backend, "capture_timeout", 25.0)) + 15.0
        fut: Future = Future()
        self._jobs.put(("capture", target, fut))
        return fut.result(timeout=timeout)

    def record_frames(self, seconds: float, fps: float) -> List[Image.Image]:
        """Live-Bilder für ``seconds`` sammeln (Boomerang) — dekodiert. Für lange Clips ``record_jpegs``."""
        frames = []
        for b in self.record_jpegs(seconds, fps):
            try:
                frames.append(rotate_image(Image.open(io.BytesIO(b)).convert("RGB"), self.rotate))
            except Exception:
                continue
        return frames

    def record_jpegs(self, seconds: float, fps: float) -> List[bytes]:
        """Live-Bilder als JPEG-Bytes (klein im Speicher); Drehung übernimmt der Encoder."""
        return self.record_timed(seconds, fps)[0]

    def record_timed(self, seconds: float, fps: float) -> tuple:
        """``(jpegs, elapsed)``: the live frames and the wall time they were taken in.

        The rate a live view delivers is the camera's, not the setting's (an
        EOS over USB gives 10-15 a second); the film needs the real one.
        """
        fut: Future = Future()
        self._jobs.put(("record", (seconds, fps), fut))
        frames, elapsed = fut.result(timeout=seconds + 20)
        return [b for b in frames if b[:2] == b"\xff\xd8"], elapsed

    def can_record_movie(self) -> bool:
        """Can the connected camera film a movie file of its own (``record_movie``)?"""
        return self.state == "ready" and bool(getattr(self.backend, "can_record_movie", False))

    def record_movie(self, seconds: float, fps: float, folder: Path) -> tuple:
        """Film on the camera: ``(live_jpegs, elapsed, movie)`` once the camera has stopped.

        ``movie`` is a Future of the downloaded file's path; it is resolved
        later on the camera thread (file, download, card cleanup, back to
        photo state). The live view keeps running all the while and its
        frames of the recording are collected too: should the movie fail
        after the stop, the clip is made from them and the guest records
        nothing twice. Raises ``CameraError`` when the recording did not even
        start (the caller then records from the live view).
        """
        if not self.can_record_movie():
            raise CameraError("Kamera kann keinen Film auf der Karte aufnehmen")
        started: Future = Future()
        movie: Future = Future()
        self._jobs.put(("movie", (float(seconds), float(fps), Path(folder), movie), started))
        try:
            frames, elapsed = started.result(timeout=float(seconds) + 30)
        except BaseException:
            # Nobody will claim this movie: its raw file goes whenever it arrives.
            movie.add_done_callback(_discard_movie)
            raise
        return [b for b in frames if b[:2] == b"\xff\xd8"], elapsed, movie

    def _record_movie(self, seconds: float, fps: float, folder: Path, started: Future, movie: Future) -> None:
        """Camera thread: the "movie" job. Never raises; both futures always end."""
        frames: List[bytes] = []
        interval = 1.0 / max(1.0, fps)
        last = [0.0]

        def tick(recording: Optional[bool]) -> None:
            self._beat = time.monotonic()          # a long job is no stalled camera
            if recording is None:
                return                               # a sign of life only (download, card, restore)
            now = time.monotonic()
            # While the camera switches, starts or hands over the file, a few
            # live frames a second keep the screen moving; every live frame
            # fetched is time the poll for the camera's answer waits.
            if now - last[0] < (interval if recording else 0.25):
                return
            last[0] = now
            try:
                jpeg = self._preview_source().preview_jpeg(max_width=1920)
            except Exception:                        # noqa: BLE001 — a lost live frame is no lost film
                jpeg = None
            if jpeg:
                self._publish(jpeg)
                if recording:
                    frames.append(jpeg)

        def stopped(elapsed: float) -> None:
            if not started.done():
                started.set_result((list(frames), elapsed))

        try:
            movie.set_result(self.backend.record_movie(seconds, folder, tick=tick, stopped=stopped))
        except Exception as e:                       # noqa: BLE001 — the booth films from the live view
            log.warning("Film auf der Kamera fehlgeschlagen: %s", e)
            if not started.done():
                started.set_exception(e)
            movie.set_exception(e)
        finally:
            if not started.done():                   # a backend that never said "stopped"
                started.set_exception(CameraError("Film ohne Stopp-Meldung"))
            problem = getattr(self.backend, "restore_problem", "")
            if problem:
                # Not back in photo mode: open the camera afresh (SaveTo, live
                # view), as after any other camera error.
                self.stats["errors"] += 1
                self.state, self.error = "reconnecting", f"Nach dem Film: {problem}"

    def apply_settings(self, settings: dict, timeout: float = 15.0) -> dict:
        """ISO/Blende/Zeit/WB merken und anwenden (auch nach jedem Neuverbinden)."""
        self.desired_settings = {k: v for k, v in (settings or {}).items() if v not in (None, "")}
        if self.state != "ready":
            return {"applied": {}, "failed": {}, "pending": True}
        fut: Future = Future()
        self._jobs.put(("settings", self.desired_settings, fut))
        return fut.result(timeout=timeout)

    def set_preview_settings(self, settings: Optional[dict]) -> None:
        """Live-view values of their own (``{"iso": "auto"|"1600", "aperture": …, "shutter": …}``).

        Empty or None: no switching at all; values set earlier are taken back
        once. Applied on the camera thread, between live frames, never during
        a capture. Does not wait.
        """
        wanted = {k: str(v) for k, v in (settings or {}).items()
                  if k in self.PREVIEW_KEYS and v not in (None, "")}
        if wanted == self.preview_settings:
            return
        self.preview_settings = wanted
        self._auto_iso = None
        self._auto_shutter = None
        self._refused = set()
        self.preview_at_end = False
        self._jobs.put(("preview", None, Future()))

    #: Camera values the live view may have of its own, and the two limits of
    #: the automatic ladder (``iso_max``, ``shutter_min``) that are never written.
    CAMERA_KEYS = ("iso", "aperture", "shutter")
    PREVIEW_KEYS = CAMERA_KEYS + ("iso_max", "shutter_min")
    #: How often the automatic preview looks at the live frame. A change
    #: needs a few frames to show; looking faster would overshoot.
    AUTO_EVERY = 0.6

    def _note(self, text: str) -> None:
        self.preview_log = (self.preview_log + [text])[-20:]

    def _auto(self) -> bool:
        return str(self.preview_settings.get("iso", "")).lower() == "auto"

    def _auto_shutter_on(self) -> bool:
        """The shutter is the ladder's second axis only with "auto" and no fixed preview shutter."""
        return self._auto() and not self.preview_settings.get("shutter")

    def _preview_values(self) -> dict:
        values = {k: v for k, v in self.preview_settings.items() if k in self.CAMERA_KEYS}
        if self._auto():
            start = self._auto_iso or self._shot_values.get("iso") or "400"
            values["iso"] = start if start.lower() != "auto" else "400"
            self._auto_iso = values["iso"]
        if self._auto_shutter_on() and (self._auto_shutter or self._shot_values.get("shutter")):
            values["shutter"] = self._auto_shutter or self._shot_values["shutter"]
            self._auto_shutter = values["shutter"]
        return values

    # ── the shot's values across a crash ──────────────────
    def _load_shot_file(self) -> dict:
        path = self.shot_values_file
        if not path:
            return {}
        try:
            import json
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception as e:                       # noqa: BLE001 — a broken note is no note
            log.warning("Gemerkte Aufnahmewerte unlesbar (%s): %s", path, e)
            return {}
        values = {k: str(v) for k, v in (data or {}).items() if k in self.CAMERA_KEYS and v not in (None, "")}
        if values:
            log.info("Aufnahmewerte aus dem letzten Lauf übernommen (die Kamera hält evtl. noch "
                     "Live-Bild-Werte): %s", values)
        return values

    def _save_shot_file(self, values: dict) -> None:
        path = self.shot_values_file
        if not path:
            return
        try:
            if not values:
                Path(path).unlink(missing_ok=True)
                return
            import json
            tmp = Path(path).with_suffix(".tmp")
            tmp.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as e:                       # noqa: BLE001 — a note we cannot keep costs no shot
            log.warning("Aufnahmewerte nicht gemerkt (%s): %s", path, e)

    def _preview_enter(self, announce: bool = True) -> None:
        """Camera thread: remember what the shot needs, then set the live-view values."""
        if not self.preview_settings or not self.backend:
            return
        keys = [k for k in self.preview_settings if k in self.CAMERA_KEYS]
        if self._auto_shutter_on() and "shutter" not in keys:
            keys.append("shutter")                  # the ladder may move it: it must go back
        if not self._shot_values:
            self._shot_values = self._load_shot_file()
        # Known shot values are kept (after a capture, a reconnect, a profile
        # change): a camera that still holds the live-view values must not
        # have them read back as the shot's. The profile's values win; only a
        # key never switched before is read from the camera.
        shot = {k: v for k, v in self._shot_values.items() if k in keys}
        shot.update({k: self.desired_settings[k] for k in keys if self.desired_settings.get(k)})
        missing = [k for k in keys if k not in shot]
        if missing:
            try:
                shot.update(self.backend.read_settings(missing))
            except Exception as e:                   # noqa: BLE001
                log.warning("Belichtung der Kamera nicht lesbar: %s", e)
        unknown = [k for k in keys if k not in shot]
        if unknown:
            # What we cannot put back we do not touch: the shot keeps its exposure.
            log.warning("Live-Bild-Belichtung ohne %s: Aufnahmewert unbekannt", ", ".join(unknown))
        self._shot_values = shot
        # Only what was read from the camera needs keeping across a crash; the
        # profile's values come back with the profile.
        self._save_shot_file({k: v for k, v in shot.items() if not self.desired_settings.get(k)})
        values = {k: v for k, v in self._preview_values().items() if k in shot}
        if not values:
            log.warning("Live-Bild-Belichtung nicht gesetzt: kein Aufnahmewert bekannt, der sich zurückstellen ließe")
            return
        was_on = self._preview_on
        res = self.backend.apply_settings(values)
        self._preview_on = True
        self._preview_now = {k: v for k, v in values.items() if k not in (res.get("failed") or {})}
        self._note(f"preview {values}")
        if announce and not was_on:
            log.info("Live-Bild-Belichtung an: %s (Aufnahme: %s)",
                     _describe(self._preview_now), _describe(self._shot_values))
        if res.get("failed"):
            log.warning("Live-Bild-Belichtung nicht ganz gesetzt: %s", res["failed"])

    def _preview_leave(self) -> None:
        """Camera thread: the shot's values back on the camera."""
        if not self._preview_on or not self.backend:
            return
        res = self.backend.apply_settings(dict(self._shot_values))
        self._note(f"shot {self._shot_values}")
        if res.get("failed"):
            # Still (partly) on live-view values: tried again before the next shot.
            raise CameraError(f"Aufnahmewerte nicht gesetzt: {res['failed']}")
        self._preview_on = False
        self._preview_now = {}

    def _preview_off(self) -> None:
        """Camera thread: the flash is no longer in use — the shot's values back, once, and forgotten."""
        was_on = self._preview_on
        if was_on:
            try:
                self._preview_leave()
            except Exception as e:                   # noqa: BLE001
                log.warning("Aufnahmebelichtung nicht zurückgesetzt: %s", e)
        if not self._preview_on:
            self._save_shot_file({})                 # cleanly back: nothing left to remember
            if was_on:
                log.info("Live-Bild-Belichtung aus, Aufnahmewerte zurück: %s", _describe(self._shot_values))
        self._shot_values = {}
        self._auto_iso = None
        self._auto_shutter = None
        self._refused = set()
        self._reported = {}
        self.preview_at_end = False

    def _restore_leftover(self) -> None:
        """Camera thread, no live-view exposure wanted: a previous run that died with
        the live-view values on the camera left the shot's values in the file —
        they go back on (the profile's own values were set just before)."""
        values = {k: v for k, v in self._load_shot_file().items() if not self.desired_settings.get(k)}
        if not values or not self.backend:
            self._save_shot_file({})
            return
        res = self.backend.apply_settings(values)
        if res.get("failed"):
            log.warning("Aufnahmewerte aus dem letzten Lauf nicht gesetzt: %s", res["failed"])
            return
        log.info("Aufnahmewerte aus dem letzten Lauf wieder eingestellt: %s", _describe(values))
        self._save_shot_file({})

    def _preview_auto(self, jpeg: bytes) -> None:
        """Camera thread: one look at the live frame; one rung of the ladder if it is out of the band.

        At most once per AUTO_EVERY. Each step is one INFO line; a value the
        camera refuses is remembered and not tried again (that axis stops
        climbing there); a value the camera says it did not keep is reported.
        """
        if not self._preview_on:
            return
        now = time.monotonic()
        if now - self._auto_at < self.AUTO_EVERY:
            return
        self._auto_at = now
        from . import exposure
        mean = exposure.jpeg_mean(jpeg)
        if mean is not None:
            self._last_mean = round(mean, 1)
        if not self._auto():
            return
        step = exposure.next_auto_step(
            self._auto_iso or "400", self._auto_shutter, self._shot_values.get("shutter"), mean,
            iso_max=self.preview_settings.get("iso_max") or exposure.ISO_MAX_DEFAULT,
            shutter_min=self.preview_settings.get("shutter_min") or exposure.SHUTTER_MIN_DEFAULT,
            auto_shutter=self._auto_shutter_on() and "shutter" in self._shot_values,
            refused=self._refused)
        if step is None:
            if mean is None:
                return
            at_end = mean < exposure.TARGET_BAND[0]
            if at_end and not self.preview_at_end:
                log.info("Live-Bild: Ende der Belichtung (%s), Helligkeit %.0f/255 — die Box hellt "
                         "das Live-Bild jetzt zusätzlich in Software auf", _describe(self._preview_now), mean)
                self._note("end of ladder")
            self.preview_at_end = at_end
            return
        axis, value = step
        res = self.backend.apply_settings({axis: value})
        if axis not in (res.get("applied") or {}):
            self._refused.add((axis, value))
            self._note(f"refused {axis} {value}")
            log.warning("Live-Bild: Kamera nimmt %s %s nicht an (%s) — auf dieser Achse geht es nicht weiter",
                        _LABEL[axis], value, (res.get("failed") or {}).get(axis, "?"))
            return
        if axis == "iso":
            self._auto_iso = value
        else:
            self._auto_shutter = value
        self._preview_now[axis] = value
        self.preview_at_end = False
        self._note(f"auto {axis} {value}")
        log.info("Live-Bild: %s %s", _LABEL[axis], value)
        # Did the camera keep it? EDSDK answers OK to a write it may then
        # ignore; the read-back is the only evidence there is.
        try:
            said = self.backend.read_settings([axis]).get(axis)
        except Exception:                            # noqa: BLE001 — no answer is no evidence
            said = None
        if said and said != value:
            if self._reported.get(axis) != said:
                log.warning("Live-Bild: %s %s gesetzt, die Kamera meldet %s", _LABEL[axis], value, said)
            self._reported[axis] = said
        elif said:
            self._reported.pop(axis, None)

    def preview_status(self) -> dict:
        """The live view's own exposure as /api/state and the self-check show it."""
        return {"on": self._preview_on, "wanted": dict(self.preview_settings),
                "live": dict(self._preview_now) if self._preview_on else {},
                "shot": dict(self._shot_values), "mean": self._last_mean,
                "at_end": self.preview_at_end,
                "refused": sorted(f"{a} {v}" for a, v in self._refused),
                "camera_reports": dict(self._reported), "log": self.preview_log[-6:]}

    def preview_line(self) -> str:
        """One line for the self-check; "" when the live view has no exposure of its own."""
        if not self._preview_on:
            return ""
        parts = [f"Live-Bild: {_describe(self._preview_now) or '–'}",
                 f"Aufnahme: {_describe(self._shot_values) or '–'}"]
        if self._last_mean is not None:
            parts.append(f"Helligkeit {self._last_mean:.0f}/255")
        if self.preview_at_end:
            parts.append("Ende der Leiter, Software-Aufhellung an")
        if self._refused:
            parts.append("abgelehnt: " + ", ".join(sorted(f"{a} {v}" for a, v in self._refused)))
        if self._reported:
            parts.append("Kamera meldet: " + _describe(self._reported))
        return " · ".join(parts)

    def _capture(self, target: Path) -> Path:
        """Camera thread: the shot, with the shot's exposure if the live view had its own.

        A failure to put the shot's values back never costs the shot: it is
        taken anyway and the failure logged. Afterwards the live-view values
        go on again.
        """
        switched = self._preview_on
        if switched:
            try:
                self._preview_leave()
            except Exception as e:                   # noqa: BLE001 — the shot comes first
                log.error("Aufnahmebelichtung nicht gesetzt, ausgelöst wird trotzdem: %s", e)
        before = getattr(self.backend, "focus_fallbacks", 0)
        try:
            return self.backend.capture(target)
        finally:
            self.stats["focus_fallbacks"] += max(0, getattr(self.backend, "focus_fallbacks", 0) - before)
            if switched and self.preview_settings:
                try:
                    self._preview_enter(announce=False)
                except Exception as e:               # noqa: BLE001 — a dark live view is no lost shot
                    log.warning("Live-Bild-Belichtung nach der Aufnahme nicht gesetzt: %s", e)

    def frage(self, name: str, timeout: float = 5.0):
        """Eine Auskunft beim Backend einholen — auf dem Kamera-Thread.

        Das EDSDK verlangt alle Aufrufe auf genau einem Thread. Wer
        ``camera.backend.irgendwas()`` von außen ruft, verletzt das; die
        Folge reicht von DEVICE_BUSY über ein abreißendes Live-Bild bis zum
        Absturz im SDK. Über diesen Weg landet die Frage in der Schlange.
        """
        if self.state != "ready" or not self.backend:
            return None
        fut: Future = Future()
        self._jobs.put(("frage", name, fut))
        try:
            return fut.result(timeout=timeout)
        except Exception:                            # noqa: BLE001
            return None

    def info(self) -> dict:
        d = {"state": self.state, "error": self.error, **self.stats, "settings": self.settings_result}
        if self.backend:
            d.update(self.backend.info())
        d["video_source"] = self.video.name if self.video else ""
        d["preview"] = self.preview_status()
        # /api/state → camera.battery: percent, "ac" or null (unknown).
        d["battery"] = self.battery
        return d

    # ── Kamera-Thread ───────────────────────────────────────
    def _connect(self) -> bool:
        try:
            if self.backend:
                try:
                    self.backend.close()
                except Exception:
                    pass
            self.backend = self._factory()
            self.backend.open()
            if self._video_factory:
                try:
                    if self.video:
                        self.video.close()
                    self.video = self._video_factory()
                    if self.video:
                        self.video.open()
                except Exception as e:
                    log.warning("Video-Quelle nicht verfügbar, nutze Kamera-Live-Bild: %s", e)
                    self.video = None
            if self.desired_settings:
                try:
                    self.settings_result = self.backend.apply_settings(self.desired_settings)
                except Exception as e:
                    log.warning("Kamera-Einstellungen nicht gesetzt: %s", e)
            # A new connection: whatever the camera holds, the live view gets
            # its own values again (the shot's values known before are kept).
            # Values it refused before get another chance on a fresh session.
            self._preview_on = False
            self._preview_now = {}
            self._refused = set()
            if self.preview_settings:
                try:
                    self._preview_enter()
                except Exception as e:               # noqa: BLE001 — a dark live view still shoots
                    log.warning("Live-Bild-Belichtung nicht gesetzt: %s", e)
            else:
                self._restore_leftover()
            self._battery_due = 0.0              # read the battery right after connecting
            self.connections += 1
            self.state, self.error = "ready", None
            log.info("Kamera verbunden: %s", self.backend.name)
            return True
        except Exception as e:  # CameraError oder SDK-Fehler
            self.battery = None
            self.state, self.error = "reconnecting", str(e)
            log.warning("Kamera nicht bereit: %s", e)
            return False

    def _publish(self, jpeg: bytes) -> None:
        if self.rotate:
            try:
                img = rotate_image(Image.open(io.BytesIO(jpeg)), self.rotate)
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=75)
                jpeg = buf.getvalue()
            except Exception:
                pass
        with self._frame_cond:
            self._frame = jpeg
            self._frame_seq += 1
            self._frame_cond.notify_all()

    def _run(self) -> None:
        self.loop_thread = threading.current_thread()
        if os.name == "nt":
            # Canon asks for an apartment-threaded COM on every thread that
            # talks to the camera other than the main one. Harmless if it
            # already is (the call then only reports so).
            try:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 2)
            except Exception:                   # noqa: BLE001
                log.debug("CoInitializeEx nicht möglich", exc_info=True)
        backoff = 1.0
        last_keepalive = time.monotonic()
        while not self._stop.is_set():
            self._beat = time.monotonic()
            if self._state != "ready":
                if not self._connect():
                    self.stats["reconnects"] += 1
                    self._fail_pending("Kamera nicht verbunden")
                    time.sleep(backoff)
                    backoff = min(backoff * 1.6, 8.0)
                    continue
                backoff = 1.0
            try:
                self._handle_jobs()
                # Between jobs, on this thread: a shot is a job, so the
                # battery is never read while one runs.
                self._read_battery()
                t0 = time.monotonic()
                jpeg = self._preview_source().preview_jpeg()
                if jpeg:
                    self._publish(jpeg)
                    if self._recording is not None:
                        self._recording.append(jpeg)
                    if self._preview_on and not self.video:
                        try:
                            self._preview_auto(jpeg)
                        except Exception as e:       # noqa: BLE001 — brightness is no reason to reconnect
                            # Once loud, then quiet: it would come every 0.6 s.
                            (log.debug if self._auto_failed else log.warning)(
                                "Automatische Live-Bild-Belichtung: %s", e)
                            self._auto_failed = True
                if time.monotonic() - last_keepalive > 60:
                    self.backend.keepalive()
                    last_keepalive = time.monotonic()
                dt = time.monotonic() - t0
                time.sleep(max(0.0, 1.0 / self.preview_fps - dt))
            except Exception as e:
                self.stats["errors"] += 1
                log.warning("Kamerafehler, verbinde neu: %s", e)
                self.state, self.error = "reconnecting", str(e)
        # Beim Beenden wurde die Kamera bisher einfach stehen gelassen: kein
        # EdsCloseSession, kein EdsTerminateSDK. Sie blieb mit Live-Bild auf
        # den Rechner geschaltet zurück, das Display schwarz, und der nächste
        # Start fand sie belegt. Das Schließen gehört auf DIESEN Thread — das
        # SDK verlangt alle Aufrufe auf einem.
        if self._preview_on and self.backend:
            # The camera is left with the shot's values, so the next start
            # reads the dial right and the note about them can go.
            try:
                self._preview_leave()
                self._save_shot_file({})
            except Exception as e:                   # noqa: BLE001 — the note stays for the next start
                log.warning("Aufnahmewerte beim Beenden nicht zurückgesetzt: %s", e)
        for teil in (self.video, self.backend):
            if not teil:
                continue
            try:
                teil.close()
            except Exception as e:                   # noqa: BLE001
                log.warning("Kamera schließen: %s", e)
        self.state = "off"
        self._ended.set()

    def _preview_source(self) -> CameraBackend:
        return self.video or self.backend

    def _fail_pending(self, msg: str) -> None:
        while True:
            try:
                _, _, fut = self._jobs.get_nowait()
            except queue.Empty:
                return
            fut.set_exception(CameraError(msg))

    def _handle_jobs(self) -> None:
        while True:
            try:
                kind, arg, fut = self._jobs.get_nowait()
            except queue.Empty:
                return
            if kind == "capture":
                try:
                    path = self._capture(arg)
                except CaptureRefused as e:
                    # The camera answered and only refused this shot ("no
                    # focus", card full): the guest's screen gets the reason,
                    # the link stays as it is. Reconnecting here showed the
                    # operator "Die Kamera antwortet nicht" for a dim scene.
                    self.stats["refused"] += 1
                    fut.set_exception(e)
                    if _refusal_code(e) == _AF_NG:
                        self._refused_in_row = 0
                        continue
                    # Safety net: anything but "no focus" twice in a row is a
                    # camera stuck in a state (card, mode) that the photo
                    # setup of a fresh connection clears; without it every
                    # following shot was refused until a power cycle.
                    self._refused_in_row += 1
                    if self._refused_in_row >= self.REFUSALS_BEFORE_RECONNECT:
                        self._refused_in_row = 0
                        self.stats["errors"] += 1
                        log.warning("Kamera verweigert %d Aufnahmen in Folge (%s), verbinde neu",
                                    self.REFUSALS_BEFORE_RECONNECT, e)
                        self.state, self.error = "reconnecting", str(e)
                        return
                    continue
                except Exception as e:
                    self._refused_in_row = 0
                    self.stats["errors"] += 1
                    fut.set_exception(e)
                    raise
                self._refused_in_row = 0
                try:
                    if self.rotate:
                        with Image.open(path) as img:
                            exif = img.info.get("exif")
                            out = rotate_image(img, self.rotate)
                            out.save(path, "JPEG", quality=94, **({"exif": exif} if exif else {}))
                    self.stats["captures"] += 1
                    fut.set_result(path)
                except Exception as e:
                    self.stats["errors"] += 1
                    fut.set_exception(e)
                    raise
            elif kind == "frage":
                # Auskünfte, die ins SDK greifen, müssen auf diesen Thread.
                # `shooting_mode()` wurde vorher direkt aus der Selbstdiagnose
                # gerufen — aus dem HTTP-Thread, aus dem Auftragsweg und aus
                # `--doctor` vom Hauptthread, während hier das Live-Bild lief.
                try:
                    fut.set_result(getattr(self.backend, arg)())
                except Exception as e:               # noqa: BLE001
                    fut.set_exception(e)
            elif kind == "settings":
                try:
                    self.settings_result = self.backend.apply_settings(arg)
                    if self._preview_on:
                        # The new shot values replace the ones to put back; the
                        # live view keeps its own (set again where just overwritten).
                        self._preview_enter()
                    fut.set_result(self.settings_result)
                except Exception as e:
                    fut.set_exception(e)
            elif kind == "preview":
                try:
                    if self.preview_settings:
                        self._preview_enter()
                    else:
                        self._preview_off()
                    fut.set_result(True)
                except Exception as e:               # noqa: BLE001 — logged; the live view goes on
                    log.warning("Live-Bild-Belichtung: %s", e)
                    fut.set_result(False)
            elif kind == "movie":
                seconds, fps, folder, movie = arg
                self._record_movie(seconds, fps, folder, fut, movie)
                if self._state != "ready":
                    return                          # reconnect first; the loop sees the state
            elif kind == "record":
                seconds, fps = arg
                try:
                    fut.set_result(collect_live_frames(lambda: self._preview_source().preview_jpeg(max_width=1920),
                                                       seconds, fps, publish=self._publish))
                except Exception as e:
                    self.stats["errors"] += 1
                    fut.set_exception(e)
                    raise
