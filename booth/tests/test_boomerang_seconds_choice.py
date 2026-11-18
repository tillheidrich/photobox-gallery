"""The guest chooses how long a boomerang records (owner, 18.11.2026).

``boomerang.seconds_choices`` lists the lengths offered ("2 s", "5 s",
"10 s"), ``boomerang.seconds_guest_choice`` switches the choice on. With two
or more valid lengths the set-up screen shows them as big buttons; the
chosen one travels in the session start and is checked against the offer.

The finished film lasts between ``min_total_seconds`` (15) and
``max_total_seconds`` (30): one loop is the recording forward and back, the
number of loops is the smallest that reaches the minimum and never passes
the maximum (2 s -> 4 loops = 16 s, 5 s -> 2 = 20 s, 10 s -> 1 = 20 s).
Without the guest's choice ``loops`` works as before.
"""
import io
import tempfile
import time
from concurrent.futures import Future
from pathlib import Path

import pytest
from PIL import Image

from boothcore import config

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "booth" / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
I18N = (WEB / "static" / "i18n.js").read_text(encoding="utf-8")
SETTINGS = (WEB / "static" / "settings.js").read_text(encoding="utf-8")


def _jpegs(n, size=(480, 320)):
    out = []
    for i in range(n):
        buf = io.BytesIO()
        Image.new("RGB", size, (i * 9 % 255, 90, 160)).save(buf, "JPEG", quality=80)
        out.append(buf.getvalue())
    return out


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-boom-seconds-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "boomerang", "video"]
    b.store.profile["boomerang"].update({"seconds": 2.0, "fps": 20, "loops": 3, "layout": "crop",
                                         "seconds_choices": [2, 5, 10], "seconds_guest_choice": True})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


# ── the profile ───────────────────────────────────────────

def test_the_new_keys_exist_with_todays_behaviour_as_default():
    boom = config.DEFAULT_PROFILE["boomerang"]
    assert boom["seconds_choices"] == [] and boom["seconds_guest_choice"] is False
    assert boom["min_total_seconds"] == 15 and boom["max_total_seconds"] == 30


def test_the_gallery_knows_the_same_boomerang_keys():
    import sys
    sys.path.insert(0, str(ROOT))
    from app import ecosystem
    assert ecosystem.DEFAULT_PROFILE["boomerang"] == config.DEFAULT_PROFILE["boomerang"]


def test_the_booth_reads_a_bad_choice_list_leniently():
    from boothcore import profile_rules
    clean, problems = profile_rules.check(
        {"boomerang": {"seconds_choices": ["5", 99, "x", 0.5, 2, True]}}, config.DEFAULT_PROFILE, strict=False)
    assert clean["boomerang"]["seconds_choices"] == [5, 2]
    assert problems


def test_at_most_four_lengths_are_kept():
    from boothcore import profile_rules
    clean, problems = profile_rules.check(
        {"boomerang": {"seconds_choices": [1, 2, 3, 4, 5]}}, config.DEFAULT_PROFILE, strict=False)
    assert clean["boomerang"]["seconds_choices"] == [1, 2, 3, 4] and problems


# ── the loops rule ────────────────────────────────────────

@pytest.mark.parametrize("seconds, loops", [(2, 4), (5, 2), (10, 1), (15, 1), (1, 8), (3, 3), (7, 2), (8, 1)])
def test_the_film_lasts_at_least_fifteen_and_at_most_thirty_seconds(seconds, loops):
    from boothcore.media.boomerang import loops_for_total
    assert loops_for_total(seconds, 15, 30) == loops
    assert 15 <= 2 * seconds * loops <= 30


def test_a_maximum_below_the_minimum_never_gives_zero_loops():
    from boothcore.media.boomerang import loops_for_total
    assert loops_for_total(2, 30, 10) >= 1


# ── the offer ─────────────────────────────────────────────

def test_the_offer_lists_valid_lengths_and_the_preselection(booth):
    assert booth.clip_seconds_offer("boomerang") == {"choices": [2.0, 5.0, 10.0], "default": 2.0}
    booth.store.profile["boomerang"]["seconds"] = 4.0                  # not offered: the first
    assert booth.clip_seconds_offer("boomerang")["default"] == 2.0
    booth.store.profile["boomerang"]["seconds"] = 5.0
    assert booth.clip_seconds_offer("boomerang")["default"] == 5.0


def test_no_choice_when_switched_off_or_fewer_than_two_lengths(booth):
    booth.store.profile["boomerang"]["seconds_guest_choice"] = False
    assert booth.clip_seconds_offer("boomerang")["choices"] == []
    booth.store.profile["boomerang"].update({"seconds_guest_choice": True, "seconds_choices": [5, 5, 40]})
    assert booth.clip_seconds_offer("boomerang")["choices"] == []
    assert booth.clip_seconds_offer("video")["choices"] == []


def test_a_length_whose_single_loop_is_longer_than_the_maximum_is_not_offered(booth):
    booth.store.profile["boomerang"].update({"seconds_choices": [2, 5, 15], "max_total_seconds": 20})
    assert booth.clip_seconds_offer("boomerang")["choices"] == [2.0, 5.0]


def test_the_live_view_offers_up_to_fifteen_the_camera_movie_up_to_ten_seconds(booth):
    booth.store.profile["boomerang"]["seconds_choices"] = [5, 10, 15]
    assert booth.clip_seconds_offer("boomerang")["choices"] == [5.0, 10.0, 15.0]
    booth.store.profile["boomerang"]["source"] = "camera"
    assert booth.CAMERA_CHOICE_MAX_SECONDS == 10
    assert booth.clip_seconds_offer("boomerang")["choices"] == [5.0, 10.0]


# ── the run ───────────────────────────────────────────────

def test_the_chosen_length_is_kept_on_the_run(booth):
    assert booth.start_session("boomerang", "", None, None, seconds=10).clip_choice_seconds == 10.0
    booth.sessions.clear()
    assert booth.start_session("boomerang", "", None, None, seconds=7).clip_choice_seconds == 2.0   # not offered
    booth.sessions.clear()
    assert booth.start_session("boomerang", "", None, None, seconds="viel").clip_choice_seconds == 2.0
    booth.sessions.clear()
    assert booth.start_session("boomerang", "", None, None).clip_choice_seconds == 2.0


def test_without_the_choice_nothing_is_chosen(booth):
    booth.store.profile["boomerang"]["seconds_guest_choice"] = False
    assert booth.start_session("boomerang", "", None, None, seconds=10).clip_choice_seconds is None


@pytest.mark.parametrize("value", [10, "10", 99, "abc", None, [1], {"x": 1}, -3])
def test_the_session_start_never_fails_on_the_length(booth, value):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    r = c.post("/api/session", json={"mode": "boomerang", "seconds": value})
    assert r.status_code == 200, r.text
    expected = 10.0 if value in (10, "10") else 2.0
    assert r.json()["clip_seconds"] == expected


def test_the_kiosk_state_carries_the_offer(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    st = TestClient(create_app(booth)).get("/api/state").json()
    assert st["clip_seconds"]["boomerang"] == {"choices": [2.0, 5.0, 10.0], "default": 2.0}


def _capture_make_clip(monkeypatch):
    from boothcore.media import boomerang
    seen = {}
    real = boomerang.make_clip

    def spy(*a, **k):
        seen["loops"] = k.get("loops")
        return real(*a, **k)

    monkeypatch.setattr(boomerang, "make_clip", spy)
    return seen


@pytest.mark.parametrize("chosen, loops", [(2, 4), (5, 2), (10, 1)])
def test_the_live_recording_takes_the_chosen_length_and_the_loops_rule(booth, monkeypatch, chosen, loops):
    asked = []

    def record_timed(seconds, fps):
        asked.append(seconds)
        return _jpegs(6), seconds

    monkeypatch.setattr(booth.camera, "record_timed", record_timed)
    seen = _capture_make_clip(monkeypatch)
    s = booth.start_session("boomerang", "", None, None, seconds=chosen)
    out = booth.record_clip(s)
    assert asked == [float(chosen)]
    assert seen["loops"] == loops
    assert out["clip_seconds"] == float(chosen)


def test_without_the_choice_loops_and_seconds_are_the_profiles(booth, monkeypatch):
    booth.store.profile["boomerang"].update({"seconds_guest_choice": False, "seconds": 3.0, "loops": 3})
    asked = []
    monkeypatch.setattr(booth.camera, "record_timed", lambda seconds, fps: (asked.append(seconds) or _jpegs(6), seconds))
    seen = _capture_make_clip(monkeypatch)
    s = booth.start_session("boomerang", "", None, None, seconds=10)
    booth.record_clip(s)
    assert asked == [3.0] and seen["loops"] == 3


def test_the_camera_movie_takes_the_chosen_length_and_the_loops_rule(booth, monkeypatch):
    from boothcore.media import camera_movie
    booth.store.profile["boomerang"]["source"] = "camera"
    raw = booth.data / "cache" / "kamerafilm" / "raw.mp4"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"movie")
    asked = {}

    def record_movie(seconds, fps, folder):
        asked["seconds"] = seconds
        movie = Future()
        movie.set_result(str(raw))
        return _jpegs(6), seconds, movie

    def make_film(path, target, **k):
        asked.update(film_seconds=k["seconds"], loops=k["loops"])
        Path(target).write_bytes(b"film")
        return Path(target), Image.new("RGB", (90, 160)), {"fps": 20}

    monkeypatch.setattr(booth, "_movie_paused", lambda: False)
    monkeypatch.setattr(booth.camera, "record_movie", record_movie)
    monkeypatch.setattr(camera_movie, "make_film", make_film)
    s = booth.start_session("boomerang", "", None, None, seconds=5)
    booth.record_clip(s)
    assert asked["seconds"] == pytest.approx(5 + booth.CAMERA_MOVIE_LEAD)
    assert asked["film_seconds"] == 5.0 and asked["loops"] == 2


# ── the kiosk ─────────────────────────────────────────────

def test_the_set_up_screen_has_a_row_for_the_length():
    group = HTML.split('id="groupSeconds"')[1].split('<div class="group"')[0]
    assert 'id="secondsChips"' in group and 'data-t="setup.seconds"' in group
    assert "#secondsChips" in JS.split("const CHOICE_ROWS = ")[1].split("\n")[0]


def test_the_length_choice_opens_the_set_up_screen_and_travels_with_the_start():
    assert "showSeconds" in JS.split("const needsChoice = ")[1].split(";")[0]
    start = JS.split('api("/api/session", { method: "POST"')[1].split("\n")[0]
    assert "seconds: flow.seconds" in start
    assert "if (b.dataset.seconds)" in JS


def test_the_recording_ring_counts_the_chosen_length():
    assert "recordingIndicator(clipSeconds(flow.mode, flow.seconds))" in JS
    assert "clipSecondsText(mode, flow.seconds)" in JS


def test_the_texts_exist_in_both_languages():
    for key in ('"setup.seconds"', '"setup.seconds_hint"', '"clip.seconds_short"', '"clip.seconds_range"'):
        assert I18N.count(key) == 2, key


def test_the_booth_admin_reads_the_lengths_as_numbers():
    assert '"boomerang.seconds_choices"' in SETTINGS
    assert 'case "numlist"' in SETTINGS
    assert '"boomerang.min_total_seconds": [5, 60, 1]' in SETTINGS
