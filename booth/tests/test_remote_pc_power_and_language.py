"""Remote control: restart or shut down the booth PC, and set the kiosk language.

Paket A (a). The same guards as every remote action (local switch
``remote_control.allowed``, fixed list, event must match) plus: never while a
run is open or a print is on its way, and a deliberate shutdown says goodbye
to the gallery first, with the reason, so its silence alarm stays quiet.

The operating system is reached through one function only,
``Fernsteuerung.pc_power``. The suite's conftest replaces it with one that
fails the test; tests here replace it with a recorder. Nothing in this file
can restart or shut down the machine it runs on.
"""
import json
import tempfile
import threading
from pathlib import Path

import pytest

from boothcore import fernsteuerung, lifecycle


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-pc-power-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    monkeypatch.setattr(lifecycle, "_announced", [])
    monkeypatch.setattr(lifecycle, "_code", [])
    monkeypatch.setattr(lifecycle, "_leaving", threading.Lock())
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    import time
    for _ in range(100):
        if b.camera.state == "ready":
            break
        time.sleep(0.05)
    b.store.save_local({"admin_pin": "468213", "remote_control": {"allowed": True, "indicator": True}})
    b.quits = []
    monkeypatch.setattr(b, "quit_on_purpose", lambda delay=1.0: b.quits.append(delay) or "Beenden")
    b.power = []
    b.fernsteuerung.pc_power = lambda restart: b.power.append(restart) or (True, "ok")
    yield b
    b.camera.stop()
    b.mail.stop()


def _job(b, action, **args):
    return json.loads(b.agent.job_handlers["remote_control"]({"action": action, "args": args}))


def test_the_three_actions_exist_on_both_sides():
    assert {"restart_pc", "shutdown_pc", "set_language"} <= fernsteuerung.ACTIONS


def test_the_suite_cannot_reach_the_real_power_switch():
    with pytest.raises(AssertionError):
        fernsteuerung.Fernsteuerung.pc_power(True)


def test_off_at_the_booth_nothing_happens(booth):
    booth.store.save_local({"remote_control": {"allowed": False, "indicator": True}})
    with pytest.raises(Exception, match="nicht erlaubt"):
        _job(booth, "restart_pc")
    assert booth.power == [] and booth.quits == []


def test_restart_says_goodbye_as_a_restart_and_leaves_in_order(booth):
    r = _job(booth, "restart_pc")
    assert booth.power == [True]
    assert booth.quits, "the booth program ends itself, so the goodbye goes out and the watcher stays down"
    assert lifecycle.why(0) == ("windows_shutdown", True)        # the gallery hears: comes back
    assert r["pc"] == "restart"


def test_shutdown_says_goodbye_as_not_coming_back(booth):
    _job(booth, "shutdown_pc")
    assert booth.power == [False]
    assert lifecycle.why(0) == ("windows_shutdown", False)


def test_never_while_a_run_is_open(booth):
    booth.start_session("photo", "4:5", None, None)
    with pytest.raises(Exception, match="Durchgang"):
        _job(booth, "shutdown_pc")
    assert booth.power == [] and booth.quits == [] and lifecycle._announced == []


def test_never_while_a_print_is_on_its_way(booth, monkeypatch):
    monkeypatch.setattr(type(booth.printer), "pending", property(lambda self: 1))
    with pytest.raises(Exception, match="gedruckt"):
        _job(booth, "restart_pc")
    assert booth.power == []


def test_when_the_system_refuses_the_booth_keeps_running(booth):
    booth.fernsteuerung.pc_power = lambda restart: (False, "Nur unter Windows")
    with pytest.raises(Exception, match="Nur unter Windows"):
        _job(booth, "restart_pc")
    assert booth.quits == [] and lifecycle._announced == []


def test_outside_windows_the_real_function_does_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(fernsteuerung.os, "name", "posix")
    monkeypatch.setattr(fernsteuerung.subprocess, "run", lambda *a, **k: calls.append(a))
    ok, text = fernsteuerung.power_off(True)
    assert ok is False and "Windows" in text and calls == []


def test_on_windows_the_real_function_asks_for_a_delayed_restart_or_shutdown(monkeypatch):
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(fernsteuerung.os, "name", "nt")
    monkeypatch.setattr(fernsteuerung.subprocess, "run", lambda args, **k: calls.append(list(args)) or Done())
    assert fernsteuerung.power_off(True)[0] is True
    assert fernsteuerung.power_off(False)[0] is True
    assert calls[0][:2] == ["shutdown", "/r"] and calls[1][:2] == ["shutdown", "/s"]
    assert "/t" in calls[0] and int(calls[0][calls[0].index("/t") + 1]) >= 10   # time to say goodbye


def test_the_kiosk_language_is_set_and_reset(booth):
    booth.store.profile["languages"] = ["de", "en"]
    assert _job(booth, "set_language", lang="en")["lang"] == "en"
    assert booth.kiosk_lang == "en"
    with pytest.raises(Exception, match="Sprache"):
        _job(booth, "set_language", lang="fr")
    assert booth.kiosk_lang == "en"
    _job(booth, "set_language", lang="")
    assert booth.kiosk_lang == ""


def test_the_language_does_not_change_under_a_running_guest(booth):
    booth.store.profile["languages"] = ["de", "en"]
    booth.start_session("photo", "4:5", None, None)
    with pytest.raises(Exception, match="Durchgang"):
        _job(booth, "set_language", lang="en")
    assert booth.kiosk_lang == ""


def test_the_kiosk_reads_the_language_from_its_state(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["languages"] = ["de", "en"]
    booth.kiosk_lang = "en"
    c = TestClient(create_app(booth), client=("127.0.0.1", 50000))
    assert c.get("/api/state").json()["kiosk_lang"] == "en"
    js = (Path(__file__).resolve().parents[1] / "boothcore/web/static/app.js").read_text(encoding="utf-8")
    assert "kiosk_lang" in js
