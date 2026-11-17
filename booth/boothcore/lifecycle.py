"""The one way out of the program.

A booth process that ends with the camera session still open leaves the
camera unusable for the next start: on a real EOS M50 the SDK then blocked or
answered 0xC0 (bus error) until somebody switched the camera off and on —
nobody is there to do that when a booth restarts itself at midnight. So a
restart, an update, "quit" in the menu and a signal all leave through here,
and what has to be handed back is handed back first.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, List

log = logging.getLogger("booth.lifecycle")

_hooks: List[Callable[[], None]] = []
_leaving = threading.Lock()

#: Seconds the hooks get before the program ends anyway. A camera that does
#: not answer must not keep a restart from happening.
PATIENCE = 8.0


#: Why the program ends, if somebody said so before calling ``leave``:
#: (reason, expect_back). Read by the goodbye to the gallery (app.py).
_announced: List[tuple] = []
_code: List[int] = []


def before_exit(fn: Callable[[], None], first: bool = False) -> None:
    """Run ``fn`` before the program ends (camera first, then the rest).

    ``first`` puts it before everything registered so far — for the short
    goodbye to the gallery, which must not wait behind a hanging camera.
    """
    if first:
        _hooks.insert(0, fn)
    else:
        _hooks.append(fn)


def announce(reason: str, expect_back: bool) -> None:
    """Say why the program is about to end. The first word counts."""
    if not _announced:
        _announced.append((reason, bool(expect_back)))


def why(code: int = 0) -> tuple:
    """(reason, expect_back) for the goodbye. Without an announcement the exit
    code decides: 3 is a restart, 1 an error (the watcher brings both back),
    0 an ordinary quit (the watcher ends too)."""
    if _announced:
        return _announced[0]
    if code == 3:
        return ("restart", True)
    if code != 0:
        return ("error", True)
    return ("quit", False)


def exit_code() -> int:
    return _code[0] if _code else 0


def leave(code: int = 0) -> None:
    """Hand everything back, then end the process with ``code``."""
    if not _leaving.acquire(blocking=False):
        return                                    # somebody is already on the way out
    _code.append(code)

    def aufraeumen() -> None:
        for fn in list(_hooks):
            try:
                fn()
            except Exception:                     # noqa: BLE001 — leaving must not fail
                log.exception("Aufräumen vor dem Beenden")

    worker = threading.Thread(target=aufraeumen, name="leave", daemon=True)
    worker.start()
    worker.join(PATIENCE)
    os._exit(code)


def watch_signals(on_signal: Callable[[], None], names=("SIGTERM", "SIGINT", "SIGBREAK")) -> int:
    """Call ``on_signal`` on a thread of its own when one of the signals arrives.

    A Python signal handler only runs between two instructions of the main
    thread. A main thread that sits inside the camera SDK never gets there —
    the program then ignores SIGTERM for as long as the camera hangs. The
    wake-up socket is written by the C-level handler itself, so the watcher
    hears of the signal at once. Returns the previous wake-up descriptor.
    """
    import signal
    import socket

    reader, writer = socket.socketpair()
    writer.setblocking(False)
    for name in names:
        if hasattr(signal, name):
            try:
                # A handler has to be installed, or nothing is written to the socket.
                signal.signal(getattr(signal, name), lambda *_: None)
            except (ValueError, OSError):
                pass
    previous = signal.set_wakeup_fd(writer.fileno(), warn_on_full_buffer=False)

    def watch() -> None:
        while True:
            try:
                if not reader.recv(1):
                    return
            except OSError:
                return
            try:
                on_signal()
            except Exception:                     # noqa: BLE001
                log.exception("Beenden auf Signal")

    thread = threading.Thread(target=watch, name="signals", daemon=True)
    thread._booth_sockets = (reader, writer)      # keep both ends alive
    thread.start()
    return previous


#: Windows console control events we act on, and the reason each one gives.
CONSOLE_EVENTS = {2: "windows_close", 5: "windows_logoff", 6: "windows_shutdown"}
_console_handler: List[object] = []                # keep the ctypes callback alive


def watch_console_events(on_event: Callable[[str], None]) -> bool:
    """Windows only: hear the console window closing, sign-out and shutdown.

    These three never become Python signals; without a handler Windows ends
    the process with no word to anyone. The handler runs on a thread the
    system makes and must finish within about five seconds, so ``on_event``
    has to be quick (the goodbye is capped at two). Ctrl+C and Ctrl+Break
    are left to the signal handling. Limitation, from the Windows docs: a
    process that has loaded user32.dll/gdi32.dll gets no sign-out/shutdown
    event here (only the close event) — then the box goes down silently
    and the gallery warns after its window, as after a power cut.
    """
    if os.name != "nt" or _console_handler:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        PHANDLER = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def handler(kind: int) -> bool:
            reason = CONSOLE_EVENTS.get(int(kind))
            if not reason:
                return False                       # Ctrl+C / Ctrl+Break: Python's turn
            try:
                on_event(reason)
            except Exception:                      # noqa: BLE001
                log.exception("Beenden auf Konsolenereignis")
            return True

        callback = PHANDLER(handler)
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(callback, True):
            return False
        _console_handler.append(callback)
        return True
    except Exception:                              # noqa: BLE001 — never a reason not to start
        log.exception("Konsolenereignisse nicht eingerichtet")
        return False


def respawn_kwargs() -> dict:
    """How a frozen booth starts its own successor.

    Two things went wrong with a plain detached start. Without a console
    ``sys.stdout`` is ``None`` and the web server dies while setting up its
    log; and the child inherits the one-file bundle's environment, takes
    itself for a helper of the old process and reads from a temp folder the
    old process deletes on its way out.

    Nor does it inherit the watcher's marks (``PHOTOBOX_WATCHED`` and the
    ``PHOTOBOX_WATCH_*`` facts, waechter.py): a successor or an updated .exe
    started from inside a watched booth is a start of its own and must
    become a watcher again, not a booth that only believes it has one.
    """
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith("PHOTOBOX_WATCH")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return {"close_fds": True,
            "env": env,
            "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0)}


def pause(text: str = "\nZum Schließen Eingabetaste drücken … ") -> None:
    """Keep a set-up window open for reading — unless nobody can press a key."""
    if os.environ.get("PHOTOBOX_NO_PAUSE"):
        return
    try:
        input(text)
    except (EOFError, OSError):
        pass


def calm_console() -> None:
    """Windows only: a touch inside the console window must not freeze the booth.

    QuickEdit turns a click into a text selection, and while a selection is
    open every write to the console blocks — with it every thread that logs.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        mode = ctypes.c_uint()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)
    except Exception:                           # noqa: BLE001
        pass
