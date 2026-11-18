"""The boomerang's format: chosen like the photo's.

Reported from the booth (15.11.2026): "Man kann beim Boomerang kein Format
auswählen, das muss identisch wie bei den anderen sein!" Until then only
``boomerang.guest_choice`` (off by default) opened the choice. Now the
boomerang follows the photo's switch ``guest_choice.format`` (on by
default) with the same cards; ``boomerang.guest_choice`` still opens it for
the boomerang alone when the general switch is off. The booth takes the
guest's format only from ``formats`` — anything else falls back to
``boomerang.format``, which is also the one pre-selected when it is offered.
"""
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-boom-format-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "boomerang", "video"]
    b.store.profile["formats"] = ["4:5", "9:16", "3:2"]
    b.store.profile["boomerang"]["format"] = "9:16"
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def test_both_switches_off_the_operators_format_wins(booth):
    booth.store.profile["guest_choice"]["format"] = False
    booth.store.profile["boomerang"]["guest_choice"] = False
    s = booth.start_session("boomerang", "4:5", None, None)
    assert s.fmt == "9:16"


def test_the_photos_format_choice_applies_to_the_boomerang_too(booth):
    assert booth.store.profile["guest_choice"]["format"] is True          # the default
    booth.store.profile["boomerang"]["guest_choice"] = False
    assert booth.start_session("boomerang", "4:5", None, None).fmt == "4:5"
    assert booth.start_session("boomerang", "3:2", None, None).fmt == "3:2"


def test_the_boomerang_switch_alone_still_opens_it(booth):
    booth.store.profile["guest_choice"]["format"] = False
    booth.store.profile["boomerang"]["guest_choice"] = True
    assert booth.start_session("boomerang", "4:5", None, None).fmt == "4:5"


def test_on_the_guest_picks_among_the_formats(booth):
    booth.store.profile["boomerang"]["guest_choice"] = True
    assert booth.start_session("boomerang", "4:5", None, None).fmt == "4:5"


def test_on_a_format_not_offered_falls_back(booth):
    booth.store.profile["boomerang"]["guest_choice"] = True
    assert booth.start_session("boomerang", "16:9", None, None).fmt == "9:16"


def test_the_video_mode_is_not_affected(booth):
    booth.store.profile["boomerang"]["guest_choice"] = True
    booth.store.profile["video"]["format"] = "9:16"
    assert booth.start_session("video", "4:5", None, None).fmt == "9:16"


def test_the_set_up_screen_offers_formats_for_the_boomerang_like_for_the_photo():
    js = (ROOT / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    # the GIF joined on 18.11.2026 (test_gif_setup_like_boomerang.py)
    assert 'const FORMAT_MODES = ["photo", "boomerang", "gif"];' in js
    assert "(mayFormat && FORMAT_MODES.includes(mode))" in js
    assert 'mode === "boomerang" && !!(p.boomerang || {}).guest_choice' in js
    # pre-selected: the operator's boomerang format when it is offered
    assert "clipDefaultFormat(" in js


def test_a_run_that_names_no_format_keeps_the_boomerangs_own(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["default_format"] = "4:5"
    r = TestClient(create_app(booth)).post("/api/session", json={"mode": "boomerang"})
    assert r.status_code == 200, r.text
    assert r.json()["format"] == "9:16"
    booth.sessions.clear()                     # the remote control runs one run at a time
    assert booth.fernsteuerung._start_run({"mode": "boomerang"})["format"] == "9:16"
