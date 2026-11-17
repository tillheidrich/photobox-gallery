"""A signal ends the booth even while its main thread is stuck in the camera SDK.

14.11.2026, EOS M50 stuck "busy": the booth's main thread sat inside
EdsCloseSession and SIGTERM did nothing at all — Python only runs a signal
handler between two of its own instructions, and there were none. The signal
now also wakes a watcher thread through a socket, which needs no cooperation
from the main thread.

And: a web server that cannot start (port taken) ends the program instead of
leaving a booth without a screen behind — seen the same day on a Mac, where
the camera loop owns the main thread.
"""
import os
import signal
import sys
import threading
from pathlib import Path

import pytest

from boothcore import lifecycle

MAIN = (Path(lifecycle.__file__).parent / "__main__.py").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="os.kill with SIGUSR1 is POSIX")
def test_a_signal_reaches_the_watcher_without_the_handler_running():
    called = threading.Event()
    old_handler = signal.getsignal(signal.SIGUSR1)
    old_fd = None
    try:
        old_fd = lifecycle.watch_signals(lambda: called.set(), names=("SIGUSR1",))
        os.kill(os.getpid(), signal.SIGUSR1)
        assert called.wait(3), "the watcher never heard of the signal"
    finally:
        signal.signal(signal.SIGUSR1, old_handler)
        if old_fd is not None:
            signal.set_wakeup_fd(old_fd)


def test_the_program_uses_the_watcher():
    assert "lifecycle.watch_signals(" in MAIN


def test_a_web_server_that_ends_takes_the_program_with_it():
    i = MAIN.index("def _web()")
    block = MAIN[i:i + 500]
    assert "finally:" in block and "booth.shutdown(" in block
