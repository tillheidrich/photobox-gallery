"""A box that goes down on purpose tells the gallery first — in at most 2 s.

The gallery's silence alarm (off by default, app/silence_alarm.py) must not
warn about a box the guests switched off. Quit, restart, update and the
Windows console events say why; a crash or a power cut says nothing.
"""
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

from boothcore import lifecycle


@pytest.fixture(autouse=True)
def fresh_lifecycle(monkeypatch):
    monkeypatch.setattr(lifecycle, "_announced", [])
    monkeypatch.setattr(lifecycle, "_code", [])
    monkeypatch.setattr(lifecycle, "_hooks", [])
    monkeypatch.setattr(lifecycle, "_leaving", threading.Lock())


def test_the_exit_code_says_why_when_nobody_announced_anything():
    assert lifecycle.why(0) == ("quit", False)
    assert lifecycle.why(3) == ("restart", True)
    assert lifecycle.why(1) == ("error", True)
    lifecycle.announce("update", True)
    lifecycle.announce("quit", False)           # the first word counts
    assert lifecycle.why(0) == ("update", True)


def test_the_goodbye_runs_before_the_camera_is_handed_back(monkeypatch):
    order = []
    monkeypatch.setattr(lifecycle.os, "_exit", lambda code: order.append(("exit", code)))
    lifecycle.before_exit(lambda: order.append("camera"))
    lifecycle.before_exit(lambda: order.append(("goodbye", lifecycle.why(lifecycle.exit_code()))), first=True)
    lifecycle.leave(3)
    assert order == [("goodbye", ("restart", True)), "camera", ("exit", 3)]


def _agent(tmp_path):
    from photobox_sync.agent import Agent
    from photobox_sync.outbox import Outbox
    cfg = {"server": {"url": "https://galerie.example.org", "api_key": "geraet-123"}}
    return Agent(outbox=Outbox(tmp_path / "q.db"), local_config=lambda: cfg)


def test_the_goodbye_posts_reason_and_expectation_with_the_device_key(tmp_path, monkeypatch):
    from photobox_sync import agent as agent_mod
    seen = []

    def answer(request):
        seen.append((request.url.path, request.headers.get("X-API-Key"), request.read()))
        return httpx.Response(200, json={"ok": True})

    real = agent_mod.GalleryTarget

    def target(*a, **kw):
        g = real(*a, **kw)
        g.client = httpx.Client(transport=httpx.MockTransport(answer), headers={"X-API-Key": "geraet-123"})
        return g

    monkeypatch.setattr(agent_mod, "GalleryTarget", target)
    assert _agent(tmp_path).goodbye("quit", False) is True
    path, key, body = seen[0]
    assert path == "/api/v2/booth/going-down" and key == "geraet-123"
    import json
    assert json.loads(body) == {"reason": "quit", "expect_back": False}


def test_a_slow_network_never_holds_the_shutdown_longer_than_two_seconds(tmp_path, monkeypatch):
    from photobox_sync import agent as agent_mod

    class Slow:
        def __init__(self, *a, **kw):
            pass

        def going_down(self, reason, expect_back, timeout=1.5):
            time.sleep(6)
            return True

    monkeypatch.setattr(agent_mod, "GalleryTarget", Slow)
    t0 = time.monotonic()
    assert _agent(tmp_path).goodbye("restart", True, limit=2.0) is False
    assert time.monotonic() - t0 < 2.5


def test_without_a_gallery_there_is_nothing_to_say(tmp_path):
    from photobox_sync.agent import Agent
    from photobox_sync.outbox import Outbox
    a = Agent(outbox=Outbox(tmp_path / "q.db"), local_config=lambda: {"server": {}})
    assert a.goodbye("quit", False) is False


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-bye-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def test_the_booth_says_update_and_expects_to_be_back(booth, monkeypatch):
    said = []
    monkeypatch.setattr(booth.agent, "goodbye", lambda r, e, limit=2.0: said.append((r, e, limit)) or True)
    lifecycle.announce("update", True)
    booth.say_goodbye()
    assert said == [("update", True, 2.0)]


def test_a_windows_shutdown_is_announced_and_leaves_in_order(booth, monkeypatch):
    codes = []
    monkeypatch.setattr(booth, "shutdown", lambda code=0: codes.append(code))
    booth._on_console_event("windows_shutdown")
    assert codes == [0]
    assert lifecycle.why(0) == ("windows_shutdown", False)


def test_the_console_events_are_close_signout_and_shutdown():
    assert lifecycle.CONSOLE_EVENTS == {2: "windows_close", 5: "windows_logoff", 6: "windows_shutdown"}


def test_an_update_announces_that_the_box_comes_back():
    src = (Path(lifecycle.__file__).parent / "updater.py").read_text(encoding="utf-8")
    i = src.index("def einspielen")
    assert 'lifecycle.announce("update", True)' in src[i:i + 3000]
