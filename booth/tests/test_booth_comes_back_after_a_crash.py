"""A booth that crashes at 22:00 is back by 22:00:05 — nobody is there to double-click.

The scheduled task's "restart on failure" only covers a task that cannot
start; a booth process that dies later stayed dead. The .exe is now a
watcher (boothcore/waechter.py) that runs the booth as a child.
"""
from __future__ import annotations

import pytest

from boothcore import waechter


def _starter(codes):
    calls = []

    def start():
        calls.append(1)
        return codes[len(calls) - 1]
    return start, calls


def test_a_crash_is_followed_by_a_new_start():
    start, calls = _starter([0xC0000005, 0xC0000005, 0])
    pauses = []
    assert waechter.watch(start, port_busy=lambda: False, sleep=pauses.append) == 0
    assert len(calls) == 3
    assert pauses == [3.0, 6.0]


def test_quitting_on_purpose_ends_the_watcher_too():
    start, calls = _starter([0])
    assert waechter.watch(start, port_busy=lambda: False, sleep=lambda s: None) == 0
    assert len(calls) == 1


def test_the_admin_restart_comes_back_at_once():
    start, calls = _starter([waechter.RESTART_CODE, 0])
    pauses = []
    waechter.watch(start, port_busy=lambda: False, sleep=pauses.append)
    assert len(calls) == 2 and pauses == []


def test_a_crash_loop_slows_down_but_never_gives_up():
    start, calls = _starter([1] * 12)
    pauses = []
    assert waechter.watch(start, port_busy=lambda: False, sleep=pauses.append, max_starts=12) == 1
    assert max(pauses) == 60.0 and len(calls) == 12


def test_no_second_booth_while_another_one_serves_the_port():
    busy = iter([True, True, False])
    start, calls = _starter([0])
    waechter.watch(start, port_busy=lambda: next(busy), sleep=lambda s: None)
    assert len(calls) == 1


@pytest.mark.parametrize("argv,frozen,env,expected", [
    ([], True, {}, True),
    (["--port", "8774"], True, {}, True),
    ([], False, {}, False),                                  # python -m boothcore: start-booth.bat loops
    (["--install"], True, {}, False),
    (["--doctor"], True, {}, False),
    ([], True, {waechter.CHILD_ENV: "1"}, False),            # the child itself
    ([], True, {"PHOTOBOX_NO_WATCH": "1"}, False),
])
def test_only_the_booth_itself_is_watched(argv, frozen, env, expected):
    assert waechter.should_watch(argv, frozen, env) is expected


def test_the_port_comes_from_the_arguments_first():
    assert waechter.port_from(["--port", "8791"]) == 8791
    assert waechter.port_from(["--port=8792"]) == 8792


# ── The kiosk browser: closed, crashed, or stuck on an error page ──

def test_a_kiosk_that_keeps_asking_is_left_alone():
    assert not waechter.kiosk_needs_relaunch(last_seen=1000.0, last_launch=0.0, now=1010.0)


def test_a_silent_kiosk_is_opened_again():
    assert waechter.kiosk_needs_relaunch(last_seen=900.0, last_launch=0.0, now=1000.0)


def test_a_kiosk_that_never_asked_is_opened_again_after_the_gap():
    assert not waechter.kiosk_needs_relaunch(last_seen=None, last_launch=0.0, now=100.0)
    assert waechter.kiosk_needs_relaunch(last_seen=None, last_launch=0.0, now=waechter.KIOSK_RELAUNCH_GAP)


def test_no_window_storm_when_the_new_window_stays_silent_too():
    # Opened again at 1000 and still nothing: the next try only after the gap.
    assert not waechter.kiosk_needs_relaunch(last_seen=900.0, last_launch=1000.0, now=1200.0)


def test_the_kiosk_poll_is_noted_by_the_booth(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from fastapi.testclient import TestClient

    from boothcore.app import Booth, create_app

    booth = Booth(start_threads=False)
    try:
        client = TestClient(create_app(booth), client=("127.0.0.1", 50000))
        assert booth.kiosk_seen is None
        assert client.get("/api/state").status_code == 200
        assert booth.kiosk_seen is not None
    finally:
        booth.mail.stop()


def test_the_self_check_does_not_take_the_camera_from_a_running_booth(capsys):
    """Einrichten.bat → 3 while the booth runs: no second camera session."""
    import socket
    import types

    from boothcore import __main__ as start

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        code = start._doctor(types.SimpleNamespace(port=port, host="127.0.0.1"))
    assert code == 2
    assert "läuft gerade" in capsys.readouterr().out
