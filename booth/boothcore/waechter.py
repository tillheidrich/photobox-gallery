"""Bring the booth back when its process dies — nobody is there to double-click.

The scheduled task starts the booth once at logon. Its "restart on failure"
setting only covers a task that fails to *start*: a booth that crashes later
(a native crash inside the camera SDK, out of memory) ends the task with an
exit code, and Windows does nothing more. The kiosk then shows Chrome's
"site can't be reached" for the rest of the wedding.

So the .exe started by the task is a small watcher. It runs the real booth
as a child process and starts it again when it ends with anything but 0:

* exit 0      — somebody quit on purpose, or an update handed over to the
                new .exe. The watcher ends too.
* exit 3      — the booth asked for a restart ("Neustart" in the admin, the
                remote job ``restart_app``). Started again at once.
* anything else — a crash. Started again after a pause that grows from a few
                seconds to a minute when it keeps crashing, never giving up.

Before every start it looks whether something already answers on the booth's
port (a second copy started by hand): then it waits instead of starting one
that would die on the taken port and open another kiosk window.

``PHOTOBOX_NO_WATCH=1`` switches the watcher off (the child runs directly).
Pure logic with injected functions, so the tests need no process at all.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Callable, List, Optional

log = logging.getLogger("booth.waechter")

#: Set in the child's environment; the booth then leaves restarts to the watcher.
CHILD_ENV = "PHOTOBOX_WATCHED"
#: What the watcher saw before this start, for /api/state (supervisor_status).
_ENV_STARTS = "PHOTOBOX_WATCH_STARTS"
_ENV_LAST_CODE = "PHOTOBOX_WATCH_LAST_CODE"
_ENV_LAST_REASON = "PHOTOBOX_WATCH_LAST_REASON"
_ENV_SINCE = "PHOTOBOX_WATCH_SINCE"
_ENV_PID = "PHOTOBOX_WATCH_PID"
#: The watcher decided the console goes away; the booth hides it. The watcher
#: itself must not touch user32 (see ``main``).
HIDE_CONSOLE_ENV = "PHOTOBOX_WATCH_HIDE_CONSOLE"

#: Exit codes of the booth process. One table for the watcher, the window
#: and the source starters (start-booth.bat / .command):
#:   0  quit on purpose (admin "Beenden", --quit, update hand-over): stay down
#:   3  restart now (admin "Neustart", remote restart_app, a dead web view)
#:   4  another booth already runs and was brought to the front: nothing to do
#:   anything else: a failure (crash, web server died) - restart after a pause
QUIT_CODE = 0
RESTART_CODE = 3
ALREADY_RUNNING = 4
#: Arguments that are one-off commands, not the booth itself: never watched.
ONE_OFF = ("--install", "--uninstall", "--doctor", "--install-edsdk", "--window-selftest", "--quit",
           "--dauerbetrieb", "--autostart", "--help", "-h")


def watched() -> bool:
    """True inside a booth that runs under the watcher."""
    return os.environ.get(CHILD_ENV) == "1"


def should_watch(argv: List[str], frozen: bool, environ=os.environ) -> bool:
    if environ.get(CHILD_ENV) == "1" or environ.get("PHOTOBOX_NO_WATCH") == "1":
        return False
    if any(a.split("=")[0] in ONE_OFF for a in argv):
        return False
    return frozen


def pause_after(crashes_in_a_row: int) -> float:
    """3 s, 6 s, 12 s … at most 60 s: a camera that kills the process at every
    start must not keep the PC busy, a single crash must cost only seconds."""
    return float(min(60, 3 * 2 ** max(0, crashes_in_a_row - 1)))


def port_taken(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except OSError:
        return False


def reason_for(code: int) -> str:
    """The word /api/state shows for an exit code (see the table above)."""
    return {QUIT_CODE: "quit", RESTART_CODE: "restart", ALREADY_RUNNING: "already_running"}.get(code, "crash")


def watch(start: Callable[[], int], *, port_busy: Callable[[], bool], sleep: Callable[[float], None] = time.sleep,
          max_starts: Optional[int] = None, record: Optional[dict] = None,
          stop: Optional[threading.Event] = None) -> int:
    """Run ``start`` (returns the child's exit code) until it ends with 0.

    ``record`` is filled before every start (starts, restarts, last exit code
    and its reason); ``run_child`` hands it to the booth, which shows it in
    /api/state. ``max_starts`` exists for tests; at an event there is no limit.
    """
    record = record if record is not None else {}
    record.update(starts=0, restarts=0, last_exit_code=None, last_reason="", since=time.time())
    starts = crashes = 0
    while max_starts is None or starts < max_starts:
        if port_busy():
            # Somebody else serves the booth (started by hand, or the old
            # process is still handing back the camera). Look again later.
            sleep(10.0)
            if port_busy():
                continue
        starts += 1
        record.update(starts=starts, restarts=starts - 1)
        begun = time.monotonic()
        code = start()
        record.update(last_exit_code=code, last_reason=reason_for(code))
        if stop is not None and stop.is_set():
            # Windows signs out or shuts down: the booth was told and said
            # goodbye itself. Whatever its code, nothing starts again.
            log.info("Sitzung endet (Code %s) — Wächter endet ohne Neustart", code)
            return 0
        if code == QUIT_CODE:
            log.info("Fotobox beendet (Code 0) — Wächter endet auch")
            return 0
        if code == ALREADY_RUNNING:
            # A second start (desktop icon, task run by hand): the running
            # booth was asked to show its window. Nothing to watch here.
            log.info("Eine Fotobox läuft schon und wurde nach vorn geholt — dieser Wächter endet")
            return 0
        if code == RESTART_CODE:
            crashes = 0
            log.info("Fotobox startet auf Wunsch neu")
            continue
        # A run of an hour was no crash loop: count from the start again.
        crashes = 1 if time.monotonic() - begun > 600 else crashes + 1
        warte = pause_after(crashes)
        log.error("Fotobox ist abgestürzt (Code %s) — Neustart in %.0f s", code, warte)
        sleep(warte)
    return 1


def child_env(record: dict, base=None) -> dict:
    """The environment of a watched booth: the marker plus what the watcher saw."""
    env = dict(os.environ if base is None else base)
    env.update({CHILD_ENV: "1", "PYINSTALLER_RESET_ENVIRONMENT": "1",
                _ENV_STARTS: str(int(record.get("starts") or 1)),
                _ENV_LAST_CODE: "" if record.get("last_exit_code") is None else str(record["last_exit_code"]),
                _ENV_LAST_REASON: str(record.get("last_reason") or ""),
                _ENV_SINCE: str(record.get("since") or ""),
                _ENV_PID: str(os.getpid())})
    if record.get("hide_console"):
        env[HIDE_CONSOLE_ENV] = "1"
    return env


def supervisor_status(environ=None) -> dict:
    """For /api/state: is a watcher behind this booth, and what did it see?"""
    env = os.environ if environ is None else environ
    if env.get(CHILD_ENV) != "1":
        return {"watched": False, "restarts": 0, "last_exit_code": None, "last_reason": "",
                "watcher_pid": None, "watching_since": None}

    def _int(key):
        try:
            return int(env.get(key) or "")
        except ValueError:
            return None
    starts = _int(_ENV_STARTS) or 1
    try:
        since = float(env.get(_ENV_SINCE) or "") or None
    except ValueError:
        since = None
    return {"watched": True, "restarts": starts - 1, "last_exit_code": _int(_ENV_LAST_CODE),
            "last_reason": env.get(_ENV_LAST_REASON, ""), "watcher_pid": _int(_ENV_PID),
            "watching_since": since}


def child_command(argv: List[str], frozen: Optional[bool] = None) -> List[str]:
    """The booth itself: this same .exe, or ``python -m boothcore`` from the source.

    From the source the watcher runs as ``python -m boothcore.waechter`` -
    the same supervision as the .exe, used on the Mac to check it end to end.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    head = [sys.executable] if frozen else [sys.executable, "-m", "boothcore"]
    return head + list(argv)


def run_child(argv: List[str], record: Optional[dict] = None) -> int:
    """Start this same program as the watched booth and wait for it.

    The child shares the watcher's console (hidden by ``main``); the booth's
    window, single-instance lock and camera all belong to the child.
    """
    record = record if record is not None else {}
    env = child_env(record)
    try:
        proc = subprocess.Popen(child_command(argv), env=env, close_fds=True)
    except OSError as e:
        log.error("Fotobox ließ sich nicht starten: %s", e)
        return 1
    record["_proc"] = proc                         # for forward_session_end: is the box gone yet?
    try:
        return proc.wait()
    finally:
        record.pop("_proc", None)


#: Seconds the watcher waits for the booth after passing on a session end:
#: the goodbye is capped at 2 s, the camera gets the rest. Windows gives a
#: console handler about 5 s for sign-out and shutdown.
SESSION_END_WAIT = 4.0


def forward_session_end(reason: str, send: Callable[[str], str], child_gone: Callable[[], bool],
                        stop: threading.Event, wait: float = SESSION_END_WAIT,
                        sleep: Callable[[float], None] = time.sleep) -> bool:
    """Windows signs out or shuts down: tell the booth, wait for it, restart nothing.

    The watcher is a console process without windows, so it is the one that
    hears CTRL_LOGOFF/CTRL_SHUTDOWN (a process with a window - the booth
    with its own window - does not). It says no goodbye itself: the booth
    does, once, with the reason (``end:<reason>`` on its instance line), and
    hands its camera back on the way out. True when the booth was gone in time.
    """
    stop.set()
    answer = send(f"end:{reason}")
    log.info("Sitzungsende (%s) an die Fotobox weitergegeben: %s", reason, answer or "keine Antwort")
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if child_gone():
            return True
        sleep(0.1)
    return child_gone()


#: How often a watched booth looks whether its watcher still lives.
PARENT_CHECK_SECONDS = 2.0


def parent_alive(pid: int) -> Callable[[], bool]:
    """A check for the watcher's process. Never ``os.kill(pid, 0)``: on Windows that kills it."""
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return lambda: False
        except Exception:                   # noqa: BLE001 — access denied and the like: assume alive
            return lambda: True
        return proc.is_running              # compares the start time too: a reused pid is not it
    if os.name == "nt":
        return lambda: True                 # cannot tell safely without psutil: assume alive
    else:
        def _posix() -> bool:
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
        return _posix


def watch_parent(alive: Callable[[], bool], on_gone: Callable[[str], None],
                 sleep: Callable[[float], None] = time.sleep, rounds: Optional[int] = None) -> None:
    """In the booth: when the watcher disappears, leave in order instead of living on alone.

    "Task beenden" on the watcher, the task scheduler stopping the task, a
    kill of the watcher: the booth would run on unwatched, and nobody would
    bring it back after its next crash. It says goodbye ("signal", not
    coming back) and hands the camera back. Once.
    """
    n = 0
    while rounds is None or n < rounds:
        n += 1
        if not alive():
            log.warning("Wächter ist weg — die Fotobox beendet sich geordnet")
            on_gone("signal")
            return
        sleep(PARENT_CHECK_SECONDS)


#: The kiosk page asks for /api/state every four seconds. Silence this long
#: means no kiosk is showing the booth: Chrome crashed, was closed with
#: Alt+F4, or still shows "site can't be reached" from before a restart.
KIOSK_SILENT_SECONDS = 90.0
#: Never open the kiosk more often than this — each start is a new window.
KIOSK_RELAUNCH_GAP = 300.0


def kiosk_needs_relaunch(last_seen: Optional[float], last_launch: float, now: float) -> bool:
    """Should the booth open its kiosk page again? Times are ``time.monotonic()``.

    ``last_seen`` None = the page never asked yet; then the launch itself is
    the start of the silence.
    """
    since_launch = now - last_launch
    if since_launch < KIOSK_RELAUNCH_GAP:
        return False
    silent_since = last_seen if last_seen is not None and last_seen > last_launch else last_launch
    return now - silent_since >= KIOSK_SILENT_SECONDS


def kiosk_should_reopen(window_mode: str, last_seen: Optional[float], last_launch: float, now: float) -> bool:
    """``kiosk_needs_relaunch``, but only while Chrome is what shows the booth.

    The page in the own window polls /api/state like the Chrome kiosk does,
    and its silence would look the same - yet the own window has its own
    liveness signal (the heartbeat, window.py) and its own cure (reload,
    restart). Opening Chrome then would put a browser over a healthy window.
    """
    if window_mode != "chrome":
        return False
    return kiosk_needs_relaunch(last_seen, last_launch, now)


def _local_settings() -> dict:
    try:
        import json

        from .config import data_dir
        return json.loads((data_dir() / "local.json").read_text(encoding="utf-8")) or {}
    except Exception:                                # noqa: BLE001 — no settings yet: defaults
        return {}


def _cli_window(argv: List[str]) -> Optional[str]:
    for i, a in enumerate(argv):
        if a == "--window" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--window="):
            return a.split("=", 1)[1]
    return None


def console_should_hide(argv: List[str], local: dict) -> bool:
    """Does the console the .exe opened go away? The watcher decides, it owns it.

    The .exe is a console program so that --install and --doctor can print.
    Started by the task, that console shows nothing anybody needs (the logs
    are in logs/) and sits on the taskbar next to the booth's window. Kept:
    with --window-debug, with no screen at all (then the console is the only
    sign that the booth runs), and when ``window.hide_console`` is false.
    """
    from . import window as fenster
    opts = fenster.settings(local)
    if "--window-debug" in argv or not opts.get("hide_console", True) or opts.get("debug"):
        return False
    mode = fenster.requested_mode(local, cli=_cli_window(argv), no_kiosk="--no-kiosk" in argv)
    return mode != "none"


def startup(port_busy: Callable[[], bool], ask_running: Callable[[], str]) -> Optional[int]:
    """Before the first start: is a booth already running? Then show it and leave.

    A double tap on the desktop icon, or ``schtasks /Run`` while the booth
    runs, starts a second watcher. Before 15.11.2026 it waited for the port
    forever, unseen. Now it asks the running booth over its instance line to
    bring its window forward and ends. Nobody answering (a foreign program
    on the port, or the booth just restarting) leaves the decision to the
    wait loop in ``watch``. Returns the exit code, or None to go on.
    """
    if not port_busy():
        return None
    answer = ask_running()
    if answer == "ok":
        log.info("Die Fotobox läuft schon — ihr Fenster wurde nach vorn geholt, kein zweiter Start")
        return 0
    return None


def port_from(argv: List[str]) -> int:
    """The port the booth will listen on: ``--port`` if given, else its settings, else 8765."""
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            return int(argv[i + 1])
        if a.startswith("--port="):
            return int(a.split("=", 1)[1])
    try:
        import json

        from .config import data_dir
        return int(json.loads((data_dir() / "local.json").read_text(encoding="utf-8")).get("port") or 8765)
    except Exception:                                # noqa: BLE001 — no settings yet: the default
        return 8765


def main(argv: List[str]) -> int:
    """Entry for the frozen .exe: watch the booth, write what happened to logs/waechter.log."""
    try:
        from logging.handlers import RotatingFileHandler

        from .config import data_dir
        (data_dir() / "logs").mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(data_dir() / "logs" / "waechter.log", maxBytes=200_000, backupCount=2,
                                 encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
        log.setLevel(logging.INFO)
    except Exception:                                # noqa: BLE001 — a log never stops the watcher
        pass
    from . import window as fenster
    from .config import data_dir
    port = port_from(argv)
    line = fenster.SingleInstance(data_dir() / "window", create=False)
    early = startup(port_busy=lambda: port_taken(port), ask_running=lambda: line.send("show"))
    if early is not None:
        return early
    record: dict = {}
    # The console goes away - but the booth hides it, not the watcher. Hiding
    # it takes user32.dll, and a console process that loaded user32 (or gdi32)
    # no longer hears Windows sign out or shut down; the watcher must hear
    # that, it is the one process without a window. The decision is made
    # here, where the console is counted: two processes on it (the one-file
    # bootloader and this watcher) means nobody opened it from a prompt.
    record["hide_console"] = console_should_hide(argv, _local_settings()) and _console_is_ours()
    stop = threading.Event()

    def _session_end(reason: str) -> None:
        proc = record.get("_proc")
        forward_session_end(reason, send=lambda c: line.send(c, timeout=1.5),
                            child_gone=lambda: proc is None or proc.poll() is not None, stop=stop)

    from . import lifecycle
    lifecycle.watch_console_events(_session_end)
    if os.name != "nt":
        # launchd/systemd/kill end the watcher with SIGTERM: pass it on the same way.
        import signal

        def _term(*_):
            _session_end("signal")
            raise SystemExit(0)
        try:
            signal.signal(signal.SIGTERM, _term)
        except (ValueError, OSError):
            pass
    log.info("Wächter gestartet (Port %s)", port)
    return watch(lambda: run_child(argv, record), port_busy=lambda: port_taken(port), record=record, stop=stop)


def _console_is_ours() -> bool:
    """Windows: only the bootloader and this watcher on the console (kernel32 only)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        ids = (ctypes.c_uint * 8)()
        return ctypes.windll.kernel32.GetConsoleProcessList(ids, 8) <= 2
    except Exception:                                # noqa: BLE001
        return False


if __name__ == "__main__":                           # python -m boothcore.waechter [booth arguments]
    raise SystemExit(main(sys.argv[1:]))
