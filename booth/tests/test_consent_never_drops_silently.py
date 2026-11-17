"""A consent step that is switched on never disappears without a trace.

Without a printer only "In die Galerie" is left, and one option is no choice,
so the screen is skipped. Until 0.4 the note ("gelöscht nach 30 Tagen")
went with it: the guest was told nothing. Now the note is shown on the
set-up screen, and the run records "notice" instead of a choice.
"""
import logging
import re
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-notice-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["consent"].update({"enabled": True, "days": 21})
    b.store.profile["print"]["enabled"] = False          # no printer: one option left
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def test_without_a_choice_the_state_asks_for_the_note(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth)).get("/api/state").json()["consent"]
    assert c["options"] == [] and c["notice_only"] is True
    assert "21 Tagen" in c["note"]["de"]


def test_the_run_records_that_the_note_was_shown(booth, caplog):
    caplog.set_level(logging.INFO, logger="booth")
    s = booth.start_session("photo", "4:5", None, None, consent={"option": "notice", "lang": "en"})
    assert s.consent["option"] == "notice" and s.consent["lang"] == "en"
    assert len(s.consent["text_hash"]) == 16
    assert any("notice shown" in r.getMessage() for r in caplog.records)


def test_an_old_kiosk_without_the_field_still_leaves_the_record(booth):
    s = booth.start_session("photo", "4:5", None, None)
    assert s.consent["option"] == "notice"


def test_switched_off_there_is_no_note_and_no_record(booth):
    booth.store.profile["consent"]["enabled"] = False
    from boothcore.app import create_app
    assert TestClient(create_app(booth)).get("/api/state").json()["consent"]["notice_only"] is False
    assert booth.start_session("photo", "4:5", None, None).consent == {}


def test_the_kiosk_shows_the_note_on_the_set_up_screen():
    js = (WEB / "static" / "app.js").read_text("utf-8")
    html = (WEB / "index.html").read_text("utf-8")
    assert 'id="consentNotice"' in html
    setup = re.search(r"function openSetup\(mode, chosen\) \{(.*?)\n  \}", js, re.S).group(1)
    assert "notice_only" in setup and "#consentNotice" in setup
    # The screen is not skipped while the note is to be read.
    skip = next(l for l in setup.splitlines() if "return runCapture()" in l)
    assert "notice" in skip


def test_the_operator_is_told_the_right_corner():
    from boothcore.config import DEFAULT_PROFILE, admin_corner_hint
    assert admin_corner_hint(DEFAULT_PROFILE) == "3 Sekunden oben rechts"
    p = {"layout": {"corner": "bottom-left"}, "access": {"hotspot_seconds": 5}}
    assert admin_corner_hint(p) == "5 Sekunden unten links"
