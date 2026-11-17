"""The booth's own window: the kiosk page in a native program window.

Until November 2026 the booth started its web server and then Chrome with
``--kiosk``. That works, but it is a browser: it can ask to restore pages
after a power cut, show its first-run screens, update itself in the middle of
an evening, and it is a second program the operator has to keep installed.
dslrBooth and the like are one program with one window; so is this, since the
owner decided on 15.11.2026 ("Bau alles auf das eigene Programm um").

``window.mode`` in local.json (or ``--window`` on the command line) picks:

* ``own``    - a native window hosted by this process (pywebview; on Windows
               the Edge WebView2 runtime that ships with Windows 11). Default.
* ``chrome`` - the old way: Chrome/Edge in kiosk mode. Also the automatic
               fallback when the own window cannot start.
* ``none``   - server only (tests, a remote screen, ``--no-kiosk``).

Everything that can be decided without a screen lives in small functions and
classes here (mode choice, fallback, single instance, heartbeat watchdog,
server guard), so the test suite checks them with the web view mocked.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger("booth.window")

MODES = ("own", "chrome", "none")
DEFAULT_MODE = "own"
TITLE = "Photobox"
#: WebView2 builds before this cannot host pywebview's EdgeChromium backend.
MIN_WEBVIEW2 = (86, 0, 622, 0)
#: Same keys pywebview reads (runtime, then beta/dev/canary channels).
WEBVIEW2_KEYS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",
)
#: Chromium switches for the web view. The same set the Chrome kiosk got,
#: in one string: ``--disable-features`` given twice keeps only the last one,
#: so ElasticOverscroll (which pywebview sets itself) is repeated here.
#: The background switches keep the heartbeat's timer running when the
#: window is covered (a remote-desktop tool, a Windows dialog over the
#: booth): Chromium otherwise throttles a hidden page's setInterval to once
#: a minute, and the watchdog would take that for a dead page.
#: ``--touch-events=enabled``: Chromium looks for a touch screen once, at
#: start. The booth starts at logon, and a USB touch controller that Windows
#: has not enumerated yet makes it decide "no touch screen" for the whole
#: evening (16.11.2026: a finger did not scroll the admin pages, the wheel did).
BROWSER_ARGS = ("--autoplay-policy=no-user-gesture-required --touch-events=enabled "
                "--disable-features=ElasticOverscroll,TranslateUI,IntensiveWakeUpThrottling,"
                "CalculateNativeWinOcclusion "
                "--disable-background-timer-throttling --disable-renderer-backgrounding "
                "--disable-backgrounding-occluded-windows "
                "--disable-pinch --overscroll-history-navigation=0 "
                "--noerrdialogs --disable-session-crashed-bubble --hide-crash-restore-bubble")
#: Seconds without a heartbeat from the page before the window reloads it.
HEARTBEAT_TIMEOUT = 15.0
#: Grace after a (re)load: a slow first paint is not a crash.
LOAD_GRACE = 25.0
#: A page that shows a native dialog (confirm/prompt/alert in the admin and
#: the template editor) runs no script until it is answered. The guard says
#: so before it opens one; the watchdog then waits this long.
DIALOG_HOLD = 600.0
#: A silent page during a run (countdown, capture, processing, printing) is
#: reloaded only once the run is over - or after this long, because a page
#: silent for minutes in the middle of a run is dead and holds nothing back.
RELOAD_DEFER_MAX = 180.0
#: Heartbeats for this long since the first one: the own window works on
#: this PC, earlier failed starts are forgotten.
STABLE_AFTER = 120.0
#: Starts in a row that the own window did not survive (no page, a dead web
#: view, a crash of the whole process) before the booth starts with Chrome.
FALLBACK_AFTER = 3
#: Failures older than this are forgotten: the next event day tries again.
FAILURE_MEMORY = 12 * 3600.0
FAILURE_FILE = "fenster-fehlversuche.json"
SELFTEST_FILE = "fenster-selbsttest.json"
SCREENSHOT_FILE = "fenster-bildschirm.png"


# ── What the window reports (/api/state, self-test) ─────────────────────
_status_lock = threading.Lock()
_status: Dict[str, Any] = {
    "mode": "none", "requested": "", "backend": "", "backend_version": "",
    "window_created": False, "alive": False, "last_heartbeat": 0.0, "reloads": 0,
    "fallback_reason": "", "screen": {}, "page": {}, "started_at": 0.0,
    "reload_deferred": False, "failures_in_a_row": 0,
}


def status() -> Dict[str, Any]:
    """A copy of the window's state, with the heartbeat's age worked out."""
    with _status_lock:
        out = json.loads(json.dumps(_status))
    beat = out.get("last_heartbeat") or 0.0
    out["heartbeat_age"] = round(time.time() - beat, 1) if beat else None
    if out["mode"] == "own":
        out["alive"] = bool(out["window_created"] and beat and time.time() - beat < HEARTBEAT_TIMEOUT)
    return out


def _set(**values: Any) -> None:
    with _status_lock:
        _status.update(values)


def _reset_status() -> None:
    """For the tests: every test starts from a window that never existed."""
    _set(mode="none", requested="", backend="", backend_version="", window_created=False,
         alive=False, last_heartbeat=0.0, reloads=0, fallback_reason="", screen={}, page={},
         started_at=0.0, reload_deferred=False, failures_in_a_row=0)
    global _attempt_open
    _attempt_open = False


# ── Failed starts of the own window, across processes ──────────────────
class FailureMemory:
    """How many starts in a row the own window did not survive.

    Counted up when the window is about to start, down to zero once it has
    beaten for ``STABLE_AFTER`` seconds. A start that ends any other way - no
    page after 60 s, a dead web view, the whole process killed by a native
    crash inside WebView2 - leaves its count standing, so the next process
    knows without being told. Kept in the data folder because the watcher
    starts a fresh process each time.
    """

    def __init__(self, folder: Path):
        self.path = Path(folder) / FAILURE_FILE

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: Dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.exception("Fehlversuche des Fensters nicht gespeichert")

    def count(self, now: Optional[float] = None) -> Tuple[int, str]:
        """(failed starts in a row, the last reason). Old ones do not count."""
        data = self._read()
        now = time.time() if now is None else now
        if now - float(data.get("last") or 0.0) > FAILURE_MEMORY:
            return 0, ""
        return int(data.get("in_a_row") or 0), str(data.get("reason") or "")

    def attempt(self) -> int:
        global _attempt_open
        n, reason = self.count()
        self._write({"in_a_row": n + 1, "reason": reason, "last": time.time()})
        _set(failures_in_a_row=n + 1)
        _attempt_open = True
        return n + 1

    def settle(self) -> None:
        """This process ends on purpose (admin "Neustart"/"Beenden", --quit): its start was no failure."""
        global _attempt_open
        if not _attempt_open:
            return
        _attempt_open = False
        data = self._read()
        n = int(data.get("in_a_row") or 0)
        if n > 0:
            data["in_a_row"] = n - 1
            self._write(data)
            _set(failures_in_a_row=n - 1)

    def note(self, reason: str) -> None:
        data = self._read()
        data["reason"] = reason[:200]
        self._write(data)

    def forget(self) -> None:
        global _attempt_open
        _attempt_open = False
        if self._read().get("in_a_row"):
            self._write({"in_a_row": 0, "reason": "", "last": time.time()})
        _set(failures_in_a_row=0)


#: This process counted an attempt of the own window that is still open
#: (not yet stable, not yet settled).
_attempt_open = False


def ended_on_purpose(data: Path) -> None:
    """Called before a deliberate restart or quit: does not count against the own window."""
    FailureMemory(Path(data) / "window").settle()


# ── Which mode ─────────────────────────────────────────────────────────
def settings(local: Dict[str, Any]) -> Dict[str, Any]:
    """The ``window`` section of local.json with every key present."""
    w = dict(local.get("window") or {})
    w.setdefault("mode", DEFAULT_MODE)
    w.setdefault("screen", -1)
    w.setdefault("on_top", False)
    w.setdefault("debug", False)
    w.setdefault("hide_console", True)
    return w


def requested_mode(local: Dict[str, Any], cli: Optional[str] = None, no_kiosk: bool = False) -> str:
    """What the operator asked for. The command line wins over local.json.

    ``--no-kiosk`` and the older switch ``"kiosk": false`` (settable from the
    gallery) both still mean "no screen at all"; they predate this module and
    scripts use them.
    """
    if no_kiosk:
        return "none"
    if cli:
        return cli if cli in MODES else DEFAULT_MODE
    if not local.get("kiosk", True):
        return "none"
    mode = settings(local)["mode"]
    return mode if mode in MODES else DEFAULT_MODE


def _version_tuple(text: str) -> Tuple[int, ...]:
    try:
        return tuple(int(p) for p in str(text).split("."))
    except ValueError:
        return ()


def webview2_version(read: Optional[Callable[[str, str], str]] = None) -> str:
    """The installed Edge WebView2 runtime, e.g. ``"131.0.2903.70"``, or ``""``.

    ``read(hive, path)`` returns the ``pv`` value of a registry key or ``""``;
    the tests hand in a dict lookup, Windows uses winreg.
    """
    if read is None:
        if os.name != "nt":
            return ""
        read = _read_registry_pv
    for key in WEBVIEW2_KEYS:
        for hive, path in (("HKEY_LOCAL_MACHINE", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{key}"),
                           ("HKEY_LOCAL_MACHINE", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}"),
                           ("HKEY_CURRENT_USER", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}")):
            version = read(hive, path) or ""
            # "0.0.0.0" is what a broken uninstall leaves behind.
            if _version_tuple(version) >= MIN_WEBVIEW2:
                return version
    return ""


def _read_registry_pv(hive: str, path: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(getattr(winreg, hive), path) as key:
            return str(winreg.QueryValueEx(key, "pv")[0])
    except OSError:
        return ""


@dataclass
class Engine:
    ok: bool
    backend: str = ""
    version: str = ""
    reason: str = ""


def detect_engine(platform: str = sys.platform,
                  importer: Callable[[str], Any] = __import__,
                  wv2: Callable[[], str] = webview2_version) -> Engine:
    """Can this machine host the own window? Never raises."""
    try:
        webview = importer("webview")
    except Exception as e:                       # noqa: BLE001 - ImportError, or a broken pythonnet
        return Engine(False, reason=f"pywebview fehlt ({e.__class__.__name__})")
    try:
        from importlib.metadata import version as _dist_version
        lib = _dist_version("pywebview")
    except Exception:                            # noqa: BLE001 - a frozen build may lack the metadata
        lib = str(getattr(webview, "__version__", "") or "?")
    if platform == "win32":
        version = wv2()
        if not version:
            return Engine(False, "edgechromium", reason="WebView2-Laufzeit von Microsoft fehlt")
        return Engine(True, "edgechromium", version)
    if platform == "darwin":
        return Engine(True, "cocoa", f"WebKit (pywebview {lib})".strip())
    # Linux needs GTK or Qt bindings next to pywebview; nobody set that up yet.
    return Engine(False, reason="eigenes Fenster unter Linux nicht eingerichtet")


def camera_needs_main_thread(platform: str, backend: str) -> bool:
    """macOS + Canon SDK: the camera loop must own the main thread (see __main__).

    A Cocoa window needs that thread too, so the two cannot share a Mac.
    "auto" may end up at the SDK, so it counts as the SDK.
    """
    return platform == "darwin" and (backend or "auto") in ("auto", "edsdk")


@dataclass
class Plan:
    mode: str
    requested: str
    engine: Engine = field(default_factory=lambda: Engine(False))
    fallback_reason: str = ""


def plan(local: Dict[str, Any], cli: Optional[str] = None, no_kiosk: bool = False,
         camera_backend: str = "", platform: str = sys.platform,
         engine: Optional[Callable[[], Engine]] = None,
         failures: Tuple[int, str] = (0, "")) -> Plan:
    """Decide the mode once, at start. Falls back to Chrome with one log line.

    ``failures``: ``FailureMemory.count()`` - the own window's failed starts
    in a row. From ``FALLBACK_AFTER`` on the booth starts with Chrome instead
    of trying (and failing, and restarting) again.
    """
    wanted = requested_mode(local, cli, no_kiosk)
    result = Plan(wanted, wanted)
    in_a_row, last_reason = failures
    _set(failures_in_a_row=in_a_row)
    if wanted == "own":
        if in_a_row >= FALLBACK_AFTER:
            result.fallback_reason = (f"eigenes Fenster {in_a_row}-mal hintereinander gescheitert"
                                      + (f" (zuletzt: {last_reason})" if last_reason else
                                         " (der Box-Prozess endete jedes Mal, bevor das Fenster 2 min lief)")
                                      + "; neuer Versuch am nächsten Tag oder nach dem Umstellen-Skript")
        elif camera_needs_main_thread(platform, camera_backend):
            result.fallback_reason = "Kamera braucht unter macOS den Haupt-Thread (Canon-SDK)"
        else:
            result.engine = (engine or (lambda: detect_engine(platform)))()
            if not result.engine.ok:
                result.fallback_reason = result.engine.reason
        if result.fallback_reason:
            result.mode = "chrome"
            log.warning("Eigenes Fenster nicht möglich (%s) - die Box öffnet Chrome im Kiosk-Modus.",
                        result.fallback_reason)
    _set(mode=result.mode, requested=wanted, backend=result.engine.backend,
         backend_version=result.engine.version, fallback_reason=result.fallback_reason,
         started_at=time.time())
    return result


# ── One booth per machine ──────────────────────────────────────────────
class SingleInstance:
    """A lock file plus a loopback line to the running booth.

    The lock is held by the operating system (``msvcrt.locking`` /
    ``fcntl.flock``), so a booth that crashed or was killed never leaves a
    stale lock behind. The running booth listens on 127.0.0.1 at a port it
    writes next to the lock; a second start sends ``show`` and leaves, so a
    double tap on the desktop icon brings the window to the front instead of
    starting a second booth that would fight over the camera.
    """

    def __init__(self, folder: Path, create: bool = True):
        self.folder = Path(folder)
        if create:                               # only talking to a running booth needs no folder
            self.folder.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.folder / "instance.lock"
        self.port_path = self.folder / "instance.port"
        self._fh = None
        self._server: Optional[socket.socket] = None
        self.handlers: Dict[str, Callable[[], str]] = {}

    def acquire(self) -> bool:
        fh = open(self.lock_path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._fh is not None:
            try:
                self._fh.close()             # closing drops the OS lock
            except OSError:
                pass
            self._fh = None

    def serve(self) -> int:
        """Listen for commands from a second start. Returns the port."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        self._server = srv
        port = srv.getsockname()[1]
        self.port_path.write_text(str(port), encoding="ascii")
        threading.Thread(target=self._loop, args=(srv,), name="window-instance", daemon=True).start()
        return port

    def _loop(self, srv: socket.socket) -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(3)
                    command = conn.recv(64).decode("ascii", "replace").strip()
                    if _leaving():
                        answer = "leaving"
                    else:
                        handler = self.handlers.get(command)
                        answer = handler() if handler else "unknown"
                    conn.sendall((answer or "ok").encode("ascii", "replace"))
                except Exception:                # noqa: BLE001 - a bad caller must not end the listener
                    log.exception("Fensterbefehl")

    def send(self, command: str, timeout: float = 3.0) -> str:
        """Talk to the running booth. ``""`` when nobody answers."""
        try:
            port = int(self.port_path.read_text(encoding="ascii").strip())
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as conn:
                conn.settimeout(timeout)
                conn.sendall(command.encode("ascii"))
                return conn.recv(256).decode("ascii", "replace").strip()
        except (OSError, ValueError):
            return ""


def _leaving() -> bool:
    """True while the booth is on its way out (lifecycle.leave has started).

    A restart from the admin page starts the successor before the old process
    has ended; the successor must wait for it, not hand over to it.
    """
    from . import lifecycle
    return lifecycle._leaving.locked()           # noqa: SLF001 - read-only look at the exit latch


#: How a booth that did not get the instance lock ends (waechter's table):
#: "shown" - another booth runs and shows its window, nothing to do (4, the
#: watcher ends too); "silent" - a booth holds the lock and does not answer,
#: a failure (1, the watcher tries again after its pause).
EXIT_FOR_CLAIM = {"shown": 4, "silent": 1}


def claim_outcome(instance: SingleInstance, wait: float = 30.0, sleep: Callable[[float], None] = time.sleep,
                  clock: Callable[[], float] = time.monotonic) -> str:
    """"mine": this process is the booth. "shown": another one runs and was
    asked to show itself. "silent": another one holds the lock and does not answer.

    While the other one answers "leaving" (a restart) or does not answer at
    all (it is just starting or just dying), keep trying for ``wait`` seconds.
    """
    deadline = clock() + wait
    while True:
        if instance.acquire():
            return "mine"
        answer = instance.send("show")
        if answer and answer != "leaving":
            log.info("Die Box läuft schon - ihr Fenster wurde nach vorn geholt.")
            return "shown"
        if clock() >= deadline:
            log.error("Eine andere Box hält die Sperre %s und antwortet nicht.",
                      getattr(instance, "lock_path", "?"))
            return "silent"
        sleep(0.5)


def claim(instance: SingleInstance, wait: float = 30.0, sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> bool:
    """True: this process is the booth (``claim_outcome`` == "mine")."""
    return claim_outcome(instance, wait, sleep, clock) == "mine"


# ── Heartbeat and reload ───────────────────────────────────────────────
class Watchdog:
    """Reload the page when its heartbeat stops.

    The page calls ``heartbeat`` through the web view's bridge every two
    seconds (``GUARD_JS``). A crashed renderer, a white page or the engine's
    own error page sends nothing; after ``timeout`` seconds the window loads
    the kiosk again. Each (re)load gets ``grace`` seconds before it counts.
    """

    def __init__(self, timeout: float = HEARTBEAT_TIMEOUT, grace: float = LOAD_GRACE,
                 clock: Callable[[], float] = time.monotonic):
        self.timeout = timeout
        self.grace = grace
        self.clock = clock
        self.armed = False
        self.reloads = 0
        #: Reloads since the last heartbeat. Several in a row: reloading does
        #: not help (the WebView2 browser process itself is gone).
        self.silent_reloads = 0
        self._last = 0.0
        self._hold_until = 0.0

    def arm(self) -> None:
        """The kiosk URL was just (re)loaded."""
        self.armed = True
        self._last = self.clock() + self.grace - self.timeout

    def beat(self) -> None:
        self._last = max(self._last, self.clock())
        self.silent_reloads = 0

    def hold(self, seconds: float) -> None:
        """The page is about to block itself (a native dialog): no silence counts for ``seconds``."""
        self._hold_until = self.clock() + seconds

    def release(self) -> None:
        self._hold_until = 0.0

    def overdue(self) -> bool:
        """The page has been silent too long (and is not waiting for a dialog)."""
        now = self.clock()
        return self.armed and now >= self._hold_until and now - self._last >= self.timeout

    def due(self) -> bool:
        """True once per silence: the caller reloads, the watchdog re-arms itself."""
        if not self.overdue():
            return False
        self.reloads += 1
        self.silent_reloads += 1
        self.arm()
        return True


def run_server(serve: Callable[[], Any], on_exit: Callable[[int], None]) -> threading.Thread:
    """The web server on a thread of its own; its end is the program's end.

    The window owns the main thread, so a server that dies (port taken, an
    exception inside uvicorn) would otherwise leave a window showing nothing.
    Exit code 1, so the task scheduler or a supervisor sees a failure and
    starts the booth again. A deliberate exit (admin "Beenden", a signal) has
    ended the process before this ``finally`` matters.
    """
    def _web() -> None:
        try:
            serve()
        except BaseException:                    # noqa: BLE001 - logged, then the exit below
            log.exception("Webserver beendet")
        finally:
            log.error("Der Webserver der Box läuft nicht mehr - Programmende mit Code 1.")
            on_exit(1)

    thread = threading.Thread(target=_web, name="web", daemon=True)
    thread.start()
    return thread


def wait_for_server(url: str, timeout: float = 90.0, sleep: Callable[[float], None] = time.sleep,
                    opener: Callable[..., Any] = urllib.request.urlopen,
                    clock: Callable[[], float] = time.monotonic) -> bool:
    """True once the kiosk page answers. Until then the splash stays."""
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            with opener(url, timeout=2) as resp:
                if 200 <= getattr(resp, "status", 200) < 500:
                    return True
        except Exception:                        # noqa: BLE001 - not up yet
            pass
        sleep(0.3)
    return False


#: Injected after every page load. Blocks what a browser offers and a kiosk
#: must not (zoom, print, save, find, back/forward, drag, pinch) and beats.
#: The heartbeat also carries what only the page can see: its size, the
#: pixel ratio and whether sound may start without a touch.
GUARD_JS = r"""
(function () {
  if (window.__photoboxGuard) return;
  window.__photoboxGuard = true;
  var no = function (e) { e.preventDefault(); };
  document.addEventListener('wheel', function (e) { if (e.ctrlKey) e.preventDefault(); }, {passive: false});
  ['gesturestart', 'gesturechange', 'gestureend'].forEach(function (t) {
    document.addEventListener(t, no, {passive: false});
  });
  // Pictures and links are not dragged out of the kiosk; the template
  // editor's own drag and drop (layer order) stays untouched.
  document.addEventListener('dragstart', function (e) {
    var t = e.target && e.target.tagName;
    if (t === 'IMG' || t === 'A' || t === 'VIDEO') e.preventDefault();
  });
  // A file dropped on the window would replace the kiosk with that file.
  ['dragover', 'drop'].forEach(function (t) {
    document.addEventListener(t, function (e) {
      var files = e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') >= 0;
      var input = e.target && e.target.type === 'file';
      if (files && !input) e.preventDefault();
    });
  });
  document.addEventListener('touchmove', function (e) { if (e.touches && e.touches.length > 1) e.preventDefault(); }, {passive: false});
  document.addEventListener('keydown', function (e) {
    var k = (e.key || '').toLowerCase();
    if ((e.ctrlKey || e.metaKey) && ['+', '-', '=', '0', 'p', 's', 'o', 'f', 'g', 'j', 'h', 'u', 'n', 't', 'w', 'r'].indexOf(k) >= 0) e.preventDefault();
    if (e.altKey && (k === 'arrowleft' || k === 'arrowright' || k === 'home')) e.preventDefault();
    if (k === 'browserback' || k === 'browserforward' || k === 'f3' || k === 'f7') e.preventDefault();
  }, true);
  var sound = 'unknown';
  try {
    var a = new Audio('data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=');
    var p = a.play();
    if (p && p.then) p.then(function () { sound = 'allowed'; }, function (e) { sound = (e && e.name) || 'blocked'; });
  } catch (e) { sound = 'error'; }
  function send(info) {
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.heartbeat) {
        window.pywebview.api.heartbeat(info);
      }
    } catch (e) {}
  }
  // Which kind of pointer the last press was: "touch" for a finger the web
  // view sees as one, "mouse" when the touch screen arrives as a mouse (then
  // a finger cannot pan natively). The self-test shows it over ssh.
  var pointer = '';
  document.addEventListener('pointerdown', function (e) { pointer = e.pointerType || ''; }, {capture: true, passive: true});
  function beat() {
    send({w: window.innerWidth, h: window.innerHeight,
          dpr: window.devicePixelRatio, autoplay_sound: sound, path: location.pathname,
          pointer: pointer, touch_points: navigator.maxTouchPoints || 0});
  }
  // A native dialog (the admin asks with confirm()/prompt(), the template
  // editor with confirm()) stops every script until it is answered - the
  // heartbeat too. Say so first, so the window does not reload the page
  // under the operator's finger, and beat again once it is answered.
  function holding(original) {
    return function () {
      send({dialog: true});
      try { return original.apply(window, arguments); } finally { beat(); }
    };
  }
  window.alert = holding(window.alert);
  window.confirm = holding(window.confirm);
  window.prompt = holding(window.prompt);
  setInterval(beat, 2000);
  window.addEventListener('pywebviewready', beat);
  document.addEventListener('visibilitychange', beat);
  beat();
})();
"""

#: What the window shows until the web server answers: dark, not white.
SPLASH_HTML = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<style>html,body{margin:0;height:100%;background:#0c0c0e;color:#f4f4f5;cursor:none;
font:600 clamp(24px,4vh,48px)/1.2 system-ui,'Segoe UI',sans-serif;user-select:none}
body{display:grid;place-items:center}p{margin:0;opacity:.85}
</style></head><body><p>Photobox startet …</p></body></html>"""

SPLASH_BACKGROUND = "#0C0C0E"


def screenshot(target: Path, grab: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """The screen as the guest sees it, as a PNG in the data folder.

    For the owner, who checks the booth over ssh and cannot see it. Taken by
    the booth process, because only a process in the logged-in session can
    see the desktop (an ssh session cannot). Pillow is there anyway.
    """
    if grab is None:
        from PIL import ImageGrab
        grab = ImageGrab.grab
    image = grab()
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "PNG")
    return {"file": str(target), "width": image.width, "height": image.height,
            "taken_at": time.time()}


# ── The window itself ──────────────────────────────────────────────────
#: Seconds after the first load without a single heartbeat before the
#: window gives up and Chrome takes over (WebView2 failed to initialise).
START_FALLBACK = 60.0
#: Reloads in a row without a heartbeat before the booth restarts itself:
#: when the WebView2 browser process has died, the control stays dead and
#: no reload brings it back; a fresh process does.
STUCK_AFTER = 3


def _pick_screen(screens: list, index: int) -> Any:
    """The configured screen, else the primary one (the one at 0,0)."""
    if not screens:
        return None
    if 0 <= index < len(screens):
        return screens[index]
    for s in screens:
        if getattr(s, "x", None) == 0 and getattr(s, "y", None) == 0:
            return s
    return screens[0]


def _hide_own_console() -> None:
    """Windows: hide the console the .exe opens, unless somebody started it from one.

    The .exe keeps its console so --install and --doctor can print. Started
    by the task scheduler, the console belongs to the booth alone (the
    one-file bootloader and the booth = two processes); started from cmd or
    PowerShell there is a third, and then the window stays where it is.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        hwnd = k.GetConsoleWindow()
        if not hwnd:
            return
        ids = (ctypes.c_uint * 8)()
        if k.GetConsoleProcessList(ids, 8) <= 2:
            ctypes.windll.user32.ShowWindow(hwnd, 0)       # SW_HIDE
    except Exception:                            # noqa: BLE001
        pass


def _watched() -> bool:
    from .waechter import watched
    return watched()


def _hide_console_window() -> None:
    """Windows: hide the console this process shares with its watcher."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)       # SW_HIDE
    except Exception:                            # noqa: BLE001
        pass


def hide_console_for(mode: str, environ=None, hide: Optional[Callable[[], None]] = None) -> bool:
    """Under the watcher: hide the console when the watcher decided so (waechter.main).

    The watcher counts the processes on the console but must not load
    user32 itself (it would no longer hear Windows sign out or shut down),
    so the booth does the hiding - in every mode with a screen.
    """
    from .waechter import CHILD_ENV, HIDE_CONSOLE_ENV
    env = os.environ if environ is None else environ
    if env.get(CHILD_ENV) != "1" or env.get(HIDE_CONSOLE_ENV) != "1" or mode == "none":
        return False
    (hide or _hide_console_window)()
    return True


#: GetSystemMetrics index: non-zero while the session is ending (sign-out, shutdown).
SM_SHUTTINGDOWN = 0x2000


def _session_ending() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN))
    except Exception:                            # noqa: BLE001
        return False


class OwnWindow:
    """One frameless full-screen window that shows the kiosk page."""

    def __init__(self, url: str, data: Path, opts: Dict[str, Any], webview: Any = None,
                 open_chrome: Optional[Callable[[str], None]] = None,
                 watchdog: Optional[Watchdog] = None, clock: Callable[[], float] = time.monotonic,
                 on_stuck: Optional[Callable[[], Any]] = None, busy: Optional[Callable[[], bool]] = None,
                 on_session_end: Optional[Callable[[str], Any]] = None,
                 shutting_down: Callable[[], bool] = _session_ending):
        #: Windows signs out or shuts down and asks the window to close: let it,
        #: and let the booth leave in order (goodbye, camera). The window has
        #: user32 loaded and so hears no console event of its own.
        self.on_session_end = on_session_end
        self.shutting_down = shutting_down
        self._session_ended = False
        self.url = url
        self.data = Path(data)
        self.opts = opts
        self.webview = webview
        self.open_chrome = open_chrome
        self.watchdog = watchdog or Watchdog(clock=clock)
        self.clock = clock
        self.on_stuck = on_stuck
        #: True while a guest's run is under way (Booth.session_busy): no
        #: reload, no restart until it is over.
        self.busy = busy
        self.failures = FailureMemory(self.data / "window")
        self._stuck_reported = False
        self.window = None
        self.allow_close = bool(opts.get("debug"))
        self.result = "closed"
        self.ever_beat = False
        self._first_beat: Optional[float] = None
        self._stable = False
        self._deferred_since: Optional[float] = None
        self._busy_failed = False
        self._first_load = 0.0
        self._stop = threading.Event()

    # Called from the page (pywebview's bridge), every two seconds.
    def heartbeat(self, info: Any = None) -> str:
        if isinstance(info, dict) and info.get("dialog"):
            # The page opens a native dialog and stops beating until it is answered.
            self.watchdog.beat()
            self.watchdog.hold(DIALOG_HOLD)
            return "ok"
        self.watchdog.release()
        self.watchdog.beat()
        self.ever_beat = True
        now = self.clock()
        if self._first_beat is None:
            self._first_beat = now
        if not self._stable and now - self._first_beat >= STABLE_AFTER:
            self._stable = True
            self.failures.forget()
        page = {}
        if isinstance(info, dict):
            for key in ("w", "h", "dpr", "autoplay_sound", "path", "pointer", "touch_points"):
                if isinstance(info.get(key), (int, float, str)):
                    page[key] = info[key] if not isinstance(info[key], str) else info[key][:80]
        _set(last_heartbeat=time.time(), page=page, alive=True)
        return "ok"

    def on_closing(self) -> bool:
        """Alt+F4, the taskbar's "close", a shutdown request: refused in kiosk mode.

        Ending the booth is the admin page's "Beenden" (behind the PIN) or,
        in an emergency, the Task Manager. A guest with a keyboard must not
        be able to close the booth with two keys.
        """
        if self.allow_close:
            return True
        try:
            ending = bool(self.shutting_down())
        except Exception:                        # noqa: BLE001
            ending = False
        if ending:
            # Windows signs out or shuts down. Refusing would hold the session
            # end up ("app is preventing shutdown"); the booth leaves in order
            # instead, saying goodbye once (the watcher may pass the same news on).
            if not self._session_ended:
                self._session_ended = True
                self.result = "session_end"
                log.info("Windows beendet die Sitzung - Fenster schließt, die Box beendet sich geordnet.")
                if self.on_session_end is not None:
                    threading.Thread(target=self.on_session_end, args=("windows_shutdown",),
                                     name="session-end", daemon=True).start()
            return True
        # Alt+F4 and the like: refused, no goodbye, nothing ends.
        log.info("Fenster schließen abgelehnt (Kiosk). Beenden: Verwaltung -> Beenden.")
        return False

    def show(self) -> str:
        w = self.window
        if w is None:
            return "starting"
        try:
            w.restore()
            w.show()
            # Windows refuses SetForegroundWindow to a process in the background;
            # a moment of "always on top" brings the window up regardless.
            w.on_top = True
            w.on_top = bool(self.opts.get("on_top"))
        except Exception:                        # noqa: BLE001
            log.exception("Fenster nach vorn holen")
        return "ok"

    def reload(self) -> None:
        log.warning("Keine Lebenszeichen der Oberfläche seit %.0f s - lade neu.", self.watchdog.timeout)
        _set(reloads=self.watchdog.reloads)
        w = self.window
        if w is not None:
            # On its own thread: load_url waits for the UI thread, which may be the thing that hangs.
            threading.Thread(target=self._load, name="window-reload", daemon=True).start()

    def _load(self) -> None:
        try:
            self.window.load_url(self.url)
        except Exception:                        # noqa: BLE001
            log.exception("Neu laden")

    def on_loaded(self) -> None:
        try:
            self.window.evaluate_js(GUARD_JS)
        except Exception:                        # noqa: BLE001 - the watchdog notices a page without guard
            log.exception("Schutzskript")

    def _busy_now(self) -> bool:
        if self.busy is None:
            return False
        try:
            return bool(self.busy())
        except Exception:                        # noqa: BLE001 - a broken check never blocks the watchdog
            if not self._busy_failed:
                self._busy_failed = True
                log.exception("Prüfung 'Durchgang läuft' fehlgeschlagen - Fenster lädt ohne Rücksicht neu")
            return False

    def _defer(self) -> bool:
        """True: the page is silent, but a run is under way - wait with reload or restart.

        A reload ends what the page shows (countdown, result, print notice)
        and a restart ends the process that is composing or printing the
        run. Neither may happen in the middle of a guest's run; both may once
        it is over. A page silent for longer than ``RELOAD_DEFER_MAX`` during
        a run is dead anyway and is reloaded.
        """
        if not self.watchdog.overdue():
            if self._deferred_since is not None:
                self._deferred_since = None
                _set(reload_deferred=False)
            return False
        if not self._busy_now():
            return False
        now = self.clock()
        if self._deferred_since is None:
            self._deferred_since = now
            _set(reload_deferred=True)
            log.info("Oberfläche ohne Lebenszeichen, aber ein Durchgang läuft - Neuladen wartet, bis er fertig ist.")
        if now - self._deferred_since < RELOAD_DEFER_MAX:
            return True
        log.warning("Oberfläche seit %.0f s ohne Lebenszeichen mitten im Durchgang - lade trotzdem neu.",
                    now - self._deferred_since)
        return False

    def tick(self) -> Optional[str]:
        """One look at the page. "fallback": never came alive. "stuck": dead after being alive."""
        if self._defer():
            return None
        if self.watchdog.due():
            if self._deferred_since is not None:
                self._deferred_since = None
                _set(reload_deferred=False)
            if self.ever_beat and self.watchdog.silent_reloads > STUCK_AFTER:
                if not self._stuck_reported:
                    self._stuck_reported = True
                    log.error("Oberfläche nach %d Neuladungen weiter ohne Lebenszeichen - die Box startet neu.",
                              STUCK_AFTER)
                    self.failures.note("Web-Ansicht blieb nach %d Neuladungen tot" % STUCK_AFTER)
                    if self.on_stuck is not None:
                        self.on_stuck()
                return "stuck"
            self.reload()
        if (not self.ever_beat and self._first_load
                and self.clock() - self._first_load > START_FALLBACK):
            return "fallback"
        return None

    def _supervise(self) -> None:
        """Runs beside the GUI loop: splash until the server answers, then watch."""
        if not wait_for_server(self.url):
            log.error("Der Webserver hat nicht geantwortet - das Fenster zeigt weiter den Startbildschirm.")
            return
        self._load()
        self._first_load = self.clock()
        self.watchdog.arm()
        while not self._stop.wait(2.0):
            if self.tick() == "fallback":
                self.give_up("das Fenster zeigt nach %d s keine Oberfläche (WebView2?)" % START_FALLBACK)
                return

    def give_up(self, reason: str) -> None:
        log.error("Eigenes Fenster aufgegeben: %s - die Box öffnet Chrome im Kiosk-Modus.", reason)
        self.failures.note(reason)
        _set(fallback_reason=reason, mode="chrome", window_created=False)
        self.result = "fallback"
        self.allow_close = True
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:                        # noqa: BLE001
            log.exception("Fenster schließen")

    def start(self) -> str:
        """Open the window and run the GUI loop on this (the main) thread.

        Returns "closed" when the window was closed, "fallback" when Chrome
        has to take over. Raises when the engine cannot even start.
        """
        webview = self.webview
        if webview is None:
            import webview                       # noqa: PLC0415 - optional dependency, only in this mode
        self.webview = webview
        existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (existing + " " + BROWSER_ARGS).strip()
        try:
            webview.settings["ALLOW_DOWNLOADS"] = False
            webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
            webview.settings["SHOW_DEFAULT_MENUS"] = bool(self.opts.get("debug"))
        except Exception:                        # noqa: BLE001 - an older pywebview without these keys
            pass
        try:
            screens = list(getattr(webview, "screens", None) or [])
        except Exception:                        # noqa: BLE001
            screens = []
        screen = _pick_screen(screens, int(self.opts.get("screen", -1)))
        if screen is not None:
            _set(screen={"width": getattr(screen, "width", 0), "height": getattr(screen, "height", 0),
                         "scale": getattr(screen, "scale", 1.0), "count": len(screens)})
        self.window = webview.create_window(
            TITLE, html=SPLASH_HTML, fullscreen=True, frameless=True, easy_drag=False,
            on_top=bool(self.opts.get("on_top")), background_color=SPLASH_BACKGROUND,
            text_select=False, zoomable=False, confirm_close=False, screen=screen, focus=True)
        self.window.expose(self.heartbeat)
        self.window.events.closing += self.on_closing
        self.window.events.loaded += self.on_loaded
        _set(window_created=True)
        if self.opts.get("hide_console", True) and not self.opts.get("debug") and not _watched():
            # Under the watcher the watcher decides (it counts the processes on
            # its console) and __main__ hides it (hide_console_for); here four
            # processes share the console and the count would say "a prompt".
            _hide_own_console()
        profile = self.data / "window" / "profile"
        profile.mkdir(parents=True, exist_ok=True)
        gui = "edgechromium" if sys.platform == "win32" else None
        webview.start(self._supervise, gui=gui, debug=bool(self.opts.get("debug")),
                      private_mode=False, storage_path=str(profile))
        self._stop.set()
        _set(window_created=False, alive=False)
        return self.result


# ── Self-test (readable over ssh) ──────────────────────────────────────
def selftest_report(data: Path, take_screenshot: bool = True,
                    grab: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """Facts of the running window, written to ``fenster-selbsttest.json``.

    Runs inside the booth process (asked through the instance line), because
    only that process knows whether its window exists and its page beats.
    """
    data = Path(data)
    report = {"written_at": time.strftime("%Y-%m-%d %H:%M:%S"), "running": True, **status()}
    if take_screenshot:
        try:
            report["screenshot"] = screenshot(data / SCREENSHOT_FILE, grab=grab)
        except Exception as e:                   # noqa: BLE001 - the facts are worth having without the picture
            report["screenshot"] = {"error": f"{e.__class__.__name__}: {e}"}
    report["ok"] = bool(report["mode"] != "own" or report["alive"])
    (data / SELFTEST_FILE).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def selftest_cli(data: Path, local: Dict[str, Any], wait: float = 20.0,
                 sleep: Callable[[float], None] = time.sleep) -> int:
    """``--window-selftest``: ask the running booth, or report why there is none.

    Prints the JSON. Exit 0 when the box runs and, in mode "own", its window
    is alive; 1 otherwise.
    """
    data = Path(data)
    target = data / SELFTEST_FILE
    before = target.stat().st_mtime if target.exists() else 0.0
    answer = SingleInstance(data / "window").send("selftest", timeout=15.0)
    report: Dict[str, Any]
    if answer == "ok":
        for _ in range(int(wait / 0.25)):
            if target.exists() and target.stat().st_mtime > before:
                break
            sleep(0.25)
        try:
            report = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {"running": True, "ok": False, "error": "Box hat geantwortet, aber keinen Bericht geschrieben"}
    else:
        engine = detect_engine()
        report = {"written_at": time.strftime("%Y-%m-%d %H:%M:%S"), "running": False, "ok": False,
                  "requested": requested_mode(local), "backend": engine.backend,
                  "backend_version": engine.version, "engine_ok": engine.ok,
                  "engine_problem": engine.reason,
                  "hint": "Keine laufende Box gefunden. Starten: schtasks /Run /TN \"Photobox Booth\""}
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


# ── Putting it together (called from __main__) ─────────────────────────
def claim_instance_outcome(data: Path, mode: str) -> Tuple[Optional[SingleInstance], str]:
    """The instance lock for a booth with a screen, and how claiming it went (``claim_outcome``)."""
    instance = SingleInstance(Path(data) / "window")
    outcome = claim_outcome(instance)
    if outcome != "mine":
        return None, outcome
    instance.serve()
    instance.handlers["selftest"] = lambda: (selftest_report(Path(data)), "ok")[1]
    instance.handlers["show"] = lambda: "ok"          # replaced by the window once it exists
    return instance, outcome


def claim_instance(data: Path, mode: str) -> Optional[SingleInstance]:
    """The instance lock for a booth with a screen. ``None``: another booth runs."""
    return claim_instance_outcome(data, mode)[0]


def run_own(url: str, data: Path, opts: Dict[str, Any], instance: Optional[SingleInstance],
            open_chrome: Callable[[str], None], webview: Any = None,
            on_stuck: Optional[Callable[[], Any]] = None,
            busy: Optional[Callable[[], bool]] = None,
            on_session_end: Optional[Callable[[str], Any]] = None) -> str:
    """Show the own window on the main thread until it is closed.

    "closed": the window was closed (only possible with ``debug``) - the
    caller ends the booth. "session_end": Windows signs out or shuts down -
    the caller leaves with that reason. "fallback": Chrome was opened
    instead - the caller keeps the server running. Each call counts as an attempt in the
    ``FailureMemory`` until the window has run for ``STABLE_AFTER`` seconds.
    """
    win = OwnWindow(url, data, opts, webview=webview, open_chrome=open_chrome, on_stuck=on_stuck, busy=busy,
                    on_session_end=on_session_end)
    win.failures.attempt()
    if instance is not None:
        instance.handlers["show"] = win.show
    try:
        result = win.start()
    except Exception as e:                       # noqa: BLE001 - any failure to start means: Chrome
        log.exception("Eigenes Fenster startet nicht")
        win.failures.note(f"{e.__class__.__name__}: {e}")
        _set(fallback_reason=f"{e.__class__.__name__}: {e}"[:200], mode="chrome", window_created=False)
        log.error("Eigenes Fenster nicht möglich (%s) - die Box öffnet Chrome im Kiosk-Modus.", e)
        result = "fallback"
    if result == "fallback":
        threading.Thread(target=open_chrome, args=(url,), name="kiosk-chrome", daemon=True).start()
    return result
