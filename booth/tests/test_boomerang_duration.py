"""How long a boomerang records, and that the guest can see it.

Reported from the booth (15.11.2026): "Wie lange geht ein Boomerang? Das muss
einstellbar sein bzw. muss dem User auch angezeigt werden." Measured on this
base before the change, with the mock camera:

* The camera records live frames for ``boomerang.seconds`` of wall time
  (2 s by default) at *up to* ``boomerang.fps`` (20). A live view that
  delivers fewer frames (an EOS over USB: roughly 10-15 a second) gives
  fewer frames, and the film still played them at the nominal 20 fps — the
  forward part of a 2 s recording lasted 1.2 s at 12 real frames a second,
  the motion ran too fast and the loop was shorter than the setting.
* The kiosk showed "Bewegt euch!" from the end of the countdown until the
  HTTP answer — and that answer came only after the film was encoded
  (three ping-pong passes through x264). The guest saw the recording sign
  for seconds after the camera had stopped: no visible end, and no number.

Now the film plays at the rate the frames were taken, the recording answers
as soon as the camera stops (the film is made while the kiosk shows
"processing", finish waits for it), and the kiosk shows the duration before
the start and a ring with the remaining seconds while it records.
"""
import io
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore import config

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
I18N = (WEB / "static" / "i18n.js").read_text(encoding="utf-8")


class FakeClock:
    """A monotonic clock that only moves when the camera works or the loop sleeps."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += max(0.0, s)


def _grab_at(clock, per_frame):
    def grab():
        clock.now += per_frame
        return b"\xff\xd8frame"
    return grab


def test_a_fast_live_view_is_taken_for_exactly_the_set_seconds():
    from boothcore.camera.service import collect_live_frames
    clock = FakeClock()
    frames, elapsed = collect_live_frames(_grab_at(clock, 0.01), 2.0, 20, clock=clock.monotonic,
                                          sleep=clock.sleep)
    assert len(frames) == 40
    assert elapsed == pytest.approx(2.0, abs=0.051)


def test_a_slow_live_view_still_records_the_set_seconds_with_fewer_frames():
    from boothcore.camera.service import collect_live_frames
    clock = FakeClock()
    frames, elapsed = collect_live_frames(_grab_at(clock, 1 / 12), 2.0, 20, clock=clock.monotonic,
                                          sleep=clock.sleep)
    assert len(frames) == 24
    assert elapsed == pytest.approx(2.0, abs=1 / 12 + 0.001)


def test_the_film_plays_at_the_rate_the_frames_were_taken():
    from boothcore.media.boomerang import played_fps
    assert played_fps(24, 2.0, 20) == 12          # the slow camera: real time, not 1.2 s
    assert played_fps(40, 2.0, 20) == 20          # a camera as fast as asked: unchanged
    assert played_fps(60, 2.0, 20) == 20          # never faster than the setting
    assert played_fps(5, 0.0, 20) == 20           # no time measured: the setting
    assert played_fps(4, 2.0, 20) == 5            # at least five a second


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-boom-duration-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "boomerang", "video"]
    b.store.profile["boomerang"].update({"seconds": 2.0, "fps": 20, "loops": 1, "layout": "crop"})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _jpegs(n, size=(480, 320)):
    out = []
    for i in range(n):
        buf = io.BytesIO()
        Image.new("RGB", size, (i * 9 % 255, 90, 160)).save(buf, "JPEG", quality=80)
        out.append(buf.getvalue())
    return out


def _mp4_seconds(path: Path) -> float:
    from boothcore.media.boomerang import ffmpeg_exe
    r = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    assert m, r.stderr[-300:]
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def test_a_two_second_boomerang_from_a_slow_camera_plays_forward_for_two_seconds(booth, monkeypatch):
    # 24 frames in 2 s (12 a second): forward 24 + back 22 = 46 frames at 12 fps.
    monkeypatch.setattr(booth.camera, "record_timed", lambda seconds, fps: (_jpegs(24), 2.0))
    s = booth.start_session("boomerang", "", None, None)
    booth.record_clip(s)
    assert s.clip_fps == 12
    assert _mp4_seconds(s.clip) == pytest.approx(46 / 12, abs=0.15)


def test_the_recording_is_asked_for_the_profiles_seconds(booth, monkeypatch):
    asked = []

    def record_timed(seconds, fps):
        asked.append((seconds, fps))
        return _jpegs(8), seconds

    monkeypatch.setattr(booth.camera, "record_timed", record_timed)
    booth.store.profile["boomerang"]["seconds"] = 3.5
    s = booth.start_session("boomerang", "", None, None)
    booth.record_clip(s)
    assert asked == [(3.5, 20.0)]


def test_the_mock_camera_records_for_the_set_seconds(booth):
    booth.store.profile["boomerang"].update({"seconds": 1.0, "fps": 10})
    t0 = time.monotonic()
    frames, elapsed = booth.camera.record_timed(1.0, 10)
    took = time.monotonic() - t0
    assert 0.95 <= elapsed <= 1.25 and took < 3
    assert 5 <= len(frames) <= 11


def test_the_recording_answers_before_the_film_is_encoded(booth, monkeypatch):
    from boothcore.media import boomerang
    monkeypatch.setattr(booth.camera, "record_timed", lambda seconds, fps: (_jpegs(10), 1.0))
    gate = threading.Event()
    real = boomerang.make_clip

    def slow_make_clip(*a, **k):
        assert gate.wait(10)
        return real(*a, **k)

    monkeypatch.setattr(boomerang, "make_clip", slow_make_clip)
    s = booth.start_session("boomerang", "", None, None)
    t0 = time.monotonic()
    out = booth.record_clip(s, encode_later=True)
    assert time.monotonic() - t0 < 2
    assert out["recorded_seconds"] == pytest.approx(1.0)
    assert out["frames"] == 10
    assert s.clip is None                     # not yet: the film is still being made
    gate.set()
    booth.finalize(s, "original")            # finish waits for the film
    assert s.result and s.result.exists() and s.result.suffix == ".mp4"


def test_a_film_that_fails_after_the_recording_fails_the_finish(booth, monkeypatch):
    from fastapi import HTTPException
    from boothcore.media import boomerang
    monkeypatch.setattr(booth.camera, "record_timed", lambda seconds, fps: (_jpegs(10), 1.0))

    def broken(*a, **k):
        raise RuntimeError("ffmpeg fehlgeschlagen")

    monkeypatch.setattr(boomerang, "make_clip", broken)
    s = booth.start_session("boomerang", "", None, None)
    booth.record_clip(s, encode_later=True)
    with pytest.raises(HTTPException) as e:
        booth.finalize(s, "original")
    assert e.value.status_code == 503


def test_the_http_recording_answers_with_what_was_recorded(booth, monkeypatch):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    monkeypatch.setattr(booth.camera, "record_timed", lambda seconds, fps: (_jpegs(12), 2.0))
    c = TestClient(create_app(booth))
    s = booth.start_session("boomerang", "", None, None)
    r = c.post(f"/api/session/{s.uid}/clip")
    assert r.status_code == 200, r.text
    assert r.json()["recorded_seconds"] == pytest.approx(2.0)
    r = c.post(f"/api/session/{s.uid}/finish", json={"look": "original"})
    assert r.status_code == 200, r.text
    assert booth.library.get(s.uid)["result"].endswith(".mp4")


# ── the setting ───────────────────────────────────────────

def test_the_duration_is_a_profile_setting_with_sensible_bounds():
    from boothcore.app import PROFILE_LIMITS
    assert config.DEFAULT_PROFILE["boomerang"]["seconds"] == 2.0       # today's nominal value
    assert PROFILE_LIMITS["boomerang.seconds"] == (1, 10)
    js = (WEB / "static" / "settings.js").read_text(encoding="utf-8")
    assert '"boomerang.seconds": [1, 10, 0.5]' in js


# ── what the guest sees ───────────────────────────────────

def test_the_guest_reads_the_duration_before_the_start():
    for lang in ("de", "en"):
        assert '"clip.seconds"' in I18N
    assert "Sekunden" in I18N and "seconds" in I18N
    # On the mode tile and in the set-up title ("Boomerang · 2 Sekunden").
    assert "clipSecondsText(" in JS
    tile = JS.split("modes.innerHTML = ")[1].split("\n")[0]
    assert "modeHint(" in tile
    assert re.search(r'setupTitle"\)\.textContent = [^;]*clipSecondsText', JS)


def test_the_recording_shows_a_ring_with_the_remaining_seconds():
    rec = HTML.split('id="rec"')[1].split("</div>")[0]
    assert 'class="rec-ring"' in rec and 'class="rec-left"' in rec
    assert "recordingIndicator(" in JS
    # ends with the answer of the recording, not with the film
    body = JS.split("async function runCapture")[1].split("} else if (retakeFlow)")[0]
    assert "recordingIndicator(" in body and "/clip" in body
    assert "prefers-reduced-motion" in JS.split("function recordingIndicator")[1].split("\n  }\n")[0]


def test_the_ring_is_quiet_for_reduced_motion():
    assert re.search(r"prefers-reduced-motion:\s*reduce\)\s*\{[^}]*\.rec-arc", CSS)
