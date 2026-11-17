"""Remote control never acts under a guest (final review A10, A4).

* ``quit``, ``restart_app``, ``delete_run``, ``test_capture``, ``screenshot``
  and ``live_image`` refuse while a guest's run is open — the same rule as
  restarting the PC (``_nothing_running``).
* ``screenshot`` and ``live_image`` also refuse while someone types into the
  kiosk (e-mail address, greeting, PIN). The kiosk reports its dialogs; without
  a fresh report the booth treats the result screen time after a finished run
  (``result_seconds`` plus a minute for typing) as such.
* The ``logs`` answer carries no full guest address (A4).
"""
import json
import time

import pytest


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.camera.start()
    for _ in range(100):
        if b.camera.state == "ready" and b.camera.latest_frame(-1, 0.1)[0]:
            break
        time.sleep(0.1)
    b.store.save_local({"admin_pin": "468213", "remote_control": {"allowed": True, "indicator": True}})
    yield b
    b.camera.stop()
    b.mail.stop()


def _job(b, action, **args):
    return json.loads(b.agent.job_handlers["remote_control"]({"action": action, "args": args}))


def _guest_run(b):
    """A run the guest started at the kiosk and has not finished."""
    started = _job(b, "start_run", mode="photo")
    return started["uid"]


@pytest.mark.parametrize("action", ["quit", "restart_app", "test_capture", "screenshot", "live_image"])
def test_nothing_that_disturbs_the_screen_runs_under_a_guest(booth, monkeypatch, action):
    from boothcore import window as window_mod
    called = []
    monkeypatch.setattr(booth, "quit_on_purpose", lambda delay=1.0: called.append("quit") or "Beenden")
    monkeypatch.setattr(window_mod, "screenshot", lambda *a, **k: called.append("shot") or {})
    _guest_run(booth)
    with pytest.raises(Exception, match="Durchgang"):
        _job(booth, action)
    assert called == []


def test_a_run_is_not_deleted_while_another_guest_is_at_the_booth(booth):
    first = _job(booth, "run", mode="photo")["uid"]
    booth.sessions[first].created -= 3600                 # an older, finished run
    _guest_run(booth)
    with pytest.raises(Exception, match="Durchgang"):
        _job(booth, "delete_run", uid=first)
    assert booth.library.get(first)


@pytest.mark.parametrize("action", ["screenshot", "live_image"])
def test_no_picture_of_the_screen_while_a_guest_may_type_an_address(booth, monkeypatch, action):
    from boothcore import window as window_mod
    monkeypatch.setattr(window_mod, "screenshot", lambda *a, **k: pytest.fail("screenshot taken"))
    _job(booth, "run", mode="photo")                    # just finished: the result screen is up
    with pytest.raises(Exception, match="E-Mail"):
        _job(booth, action)


def test_the_live_image_works_again_when_the_result_screen_is_gone(booth):
    uid = _job(booth, "run", mode="photo")["uid"]
    s = booth.sessions[uid]
    old = time.time() - 3600
    s.created = old
    import os
    for p in (s.result, s.result_web, s.thumb):
        if p and p.exists():
            os.utime(p, (old, old))
    assert _job(booth, "live_image")["image_base64"]


# ── the kiosk says when a typing dialog is open (A10, done properly) ──────
# The kiosk reports on its /api/state poll (``?typing=1|0``) and the moment an
# e-mail, greeting or PIN dialog opens or closes. While that report is fresh the
# booth goes by it; the result-screen time above is only the fallback.

def _kiosk_says(booth, typing, client="127.0.0.1"):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    TestClient(create_app(booth), client=(client, 50000)).get("/api/state", params={"typing": typing})


def _old_run(booth):
    import os
    uid = _job(booth, "run", mode="photo")["uid"]
    s = booth.sessions[uid]
    old = time.time() - 3600
    s.created = old
    for p in (s.result, s.result_web, s.thumb):
        if p and p.exists():
            os.utime(p, (old, old))
    return uid


@pytest.mark.parametrize("action", ["screenshot", "live_image"])
def test_no_picture_while_the_kiosk_reports_an_open_typing_dialog(booth, monkeypatch, action):
    from boothcore import window as window_mod
    monkeypatch.setattr(window_mod, "screenshot", lambda *a, **k: pytest.fail("screenshot taken"))
    _old_run(booth)                                     # the time-based rule alone would allow it
    _kiosk_says(booth, "1")
    with pytest.raises(Exception, match="E-Mail"):
        _job(booth, action)


def test_a_short_grace_after_the_dialog_closed(booth, monkeypatch):
    from boothcore import fernsteuerung
    _old_run(booth)
    _kiosk_says(booth, "1")
    _kiosk_says(booth, "0")
    with pytest.raises(Exception, match="E-Mail"):
        _job(booth, "live_image")
    # Once the grace is over the picture comes again.
    fs = booth.fernsteuerung
    fs.typing_closed -= fernsteuerung.TYPING_GRACE_SECONDS + 1
    assert _job(booth, "live_image")["image_base64"]


def test_a_fresh_kiosk_report_overrides_the_result_screen_guess(booth):
    _job(booth, "run", mode="photo")                    # time rule: "a guest may be typing"
    _kiosk_says(booth, "0")
    booth.fernsteuerung.typing_closed -= 3600           # no dialog has been open for a long time
    assert _job(booth, "live_image")["image_base64"]


def test_a_stale_kiosk_report_falls_back_to_the_time_rule(booth):
    from boothcore import fernsteuerung
    _kiosk_says(booth, "0")
    booth.fernsteuerung.typing_reported -= fernsteuerung.KIOSK_REPORT_FRESH_SECONDS + 1
    booth.fernsteuerung.typing_closed -= 3600
    _job(booth, "run", mode="photo")
    with pytest.raises(Exception, match="E-Mail"):
        _job(booth, "live_image")


def test_a_stale_report_of_an_open_dialog_still_refuses(booth):
    """A kiosk that froze with the keyboard up still shows what was typed."""
    from boothcore import fernsteuerung
    _old_run(booth)
    _kiosk_says(booth, "1")
    booth.fernsteuerung.typing_reported -= fernsteuerung.KIOSK_REPORT_FRESH_SECONDS + 1
    with pytest.raises(Exception, match="E-Mail"):
        _job(booth, "screenshot")


def test_only_the_kiosk_on_this_pc_may_report(booth):
    _old_run(booth)
    _kiosk_says(booth, "1", client="192.168.1.50")
    assert booth.fernsteuerung.typing_reported is None
    assert _job(booth, "live_image")["image_base64"]


def test_a_state_poll_without_the_field_changes_nothing(booth):
    """The admin page on the same PC also polls /api/state; it knows nothing about dialogs."""
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    _kiosk_says(booth, "1")
    TestClient(create_app(booth), client=("127.0.0.1", 50000)).get("/api/state")
    assert booth.fernsteuerung.typing_open is True


def test_the_logs_answer_carries_no_guest_address(booth):
    import logging
    logging.getLogger("boothcore.delivery.mail").warning("mail to %s failed", "anna.meier@example.org")
    booth.logbuf.add("warning", "550 <anna.meier@example.org> unknown", source="test")
    text = json.dumps(_job(booth, "logs", limit=50), ensure_ascii=False)
    assert "anna.meier@example.org" not in text
    assert "a***@example.org" in text
