"""The camera-movie clip source under failure (review of 17.11.2026).

With ``source: camera`` and a missing or full card every boomerang paid the
whole failure path (bounded waits of several seconds) before the live view
took over — long after the guest's "move now" ring had ended — and nothing
remembered the failure. Two /clip on one run wrote the same file names from
two threads. A downloaded movie that ffmpeg could not read, with too few live
frames, ended the run with "camera.generic". A movie future that resolved
after its caller had given up left its raw file in the cache.
"""
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import pytest

from boothcore.media import boomerang, camera_movie
from test_camera_movie_source import booth, make_movie, movies  # noqa: F401 — fixtures


def _calls(booth, monkeypatch):
    calls = []
    real = booth.camera.record_movie

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(booth.camera, "record_movie", spy)
    return calls


# ── 2: circuit breaker ───────────────────────────────────

def test_a_start_failure_pauses_the_camera_movie_and_uses_the_live_view_directly(booth, monkeypatch, caplog):
    calls = _calls(booth, monkeypatch)
    booth.camera.backend.movie_fail = "start"           # e.g. no card in the camera
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s)
    assert len(calls) == 1 and s.clip.exists()
    s2 = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s2)
    assert len(calls) == 1, "paused: the second run must not try the camera again"
    assert s2.clip.exists()
    assert sum("Film der Kamera pausiert bis" in r.getMessage() for r in caplog.records) == 1
    info = booth.camera_movie_state()
    assert info["paused"] is True and info["until"] and "Filmstart" in info["reason"]


def test_after_the_pause_the_camera_is_tried_again(booth, monkeypatch):
    calls = _calls(booth, monkeypatch)
    booth.camera.backend.movie_fail = "start"
    booth.record_clip(booth.start_session("boomerang", "9:16", None, None))
    booth.camera.backend.movie_fail = ""
    booth._movie_pause["until"] = time.monotonic() - 1  # the pause is over
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s)
    assert len(calls) == 2
    assert booth.camera_movie_state()["paused"] is False


def test_a_reconnect_of_the_camera_ends_the_pause(booth, monkeypatch):
    calls = _calls(booth, monkeypatch)
    booth.camera.backend.movie_fail = "start"
    booth.record_clip(booth.start_session("boomerang", "9:16", None, None))
    booth.camera.backend.movie_fail = ""
    booth.camera.connections += 1                       # what _connect does on success
    booth.record_clip(booth.start_session("boomerang", "9:16", None, None))
    assert len(calls) == 2


def test_failures_after_the_recording_pause_only_after_two_in_a_row(booth, monkeypatch):
    calls = _calls(booth, monkeypatch)
    booth.camera.backend.movie_fail = "download"
    for _ in range(2):
        s = booth.start_session("boomerang", "9:16", None, None)
        booth.record_clip(s)
        assert s.clip.exists()
    assert len(calls) == 2
    booth.record_clip(booth.start_session("boomerang", "9:16", None, None))
    assert len(calls) == 2, "two failed movies in a row pause the camera"


def test_the_pause_is_in_the_kiosk_state(booth, monkeypatch):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.camera.backend.movie_fail = "start"
    booth.record_clip(booth.start_session("boomerang", "9:16", None, None))
    state = TestClient(create_app(booth)).get("/api/state").json()
    assert state["camera"]["camera_movie"]["paused"] is True


# ── 3: two /clip on one run ──────────────────────────────

def test_a_second_clip_on_the_same_run_waits_for_the_first_and_leaves_one_valid_file(booth, monkeypatch):
    from test_camera_movie_source import mp4_info
    real = camera_movie.make_film

    def slow(*a, **k):
        time.sleep(3.0)                              # still encoding when the second take ends
        return real(*a, **k)
    monkeypatch.setattr(camera_movie, "make_film", slow)
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s, encode_later=True)
    first = s.clip_job
    booth.record_clip(s, encode_later=True)
    assert first.done(), "the second take must wait for the first film"
    booth.finalize(s, "original")
    assert mp4_info(s.result)[0] == (1080, 1920)
    left = [p.name for p in s.folder.iterdir() if ".tmp" in p.name or ".pass." in p.name]
    assert left == []


# ── 4: no dead end ───────────────────────────────────────

def test_a_broken_movie_without_live_frames_still_gives_a_clip(booth, monkeypatch, caplog):
    real = booth.camera.record_movie

    def no_live_frames(*a, **k):
        _frames, elapsed, movie = real(*a, **k)
        return [], elapsed, movie
    monkeypatch.setattr(booth.camera, "record_movie", no_live_frames)

    def broken(*a, **k):
        raise RuntimeError("ffmpeg: broken movie")
    monkeypatch.setattr(camera_movie, "make_film", broken)
    monkeypatch.setattr(camera_movie, "extract_frames", broken)
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s)
    booth.finalize(s, "original")
    assert s.result and s.result.exists()
    assert booth.live_calls, "recorded once more from the live view"


# ── 6: no raw file left behind ───────────────────────────

def test_a_movie_that_arrives_after_its_caller_gave_up_is_deleted(booth, tmp_path):
    raw = booth._movie_folder() / "MVI_9999.MP4"
    fut: Future = Future()
    booth._discard_when_done(fut)
    raw.write_bytes(b"x" * 10)
    fut.set_result(raw)
    assert not raw.exists()


def test_the_raw_movie_folder_is_swept_at_start(monkeypatch, tmp_path):
    d = tmp_path / "data"
    folder = d / "cache" / "kamerafilm"
    (folder / "frames-abc").mkdir(parents=True)
    (folder / "frames-abc" / "0001.jpg").write_bytes(b"x")
    (folder / "MVI_0001.MP4").write_bytes(b"x")
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    try:
        assert list(folder.iterdir()) == []
    finally:
        b.mail.stop()


# ── 7: length and rate ───────────────────────────────────

def test_the_clip_length_is_capped_by_the_frame_limit(booth, monkeypatch):
    monkeypatch.setattr(boomerang, "MAX_FRAMES", 10)
    booth.store.profile["boomerang"].update({"seconds": 5.0, "fps": 20})
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s)
    assert s.clip_seconds <= 10 / 20 + 1e-6


def test_a_poster_failure_keeps_the_live_rate(booth, monkeypatch):
    from boothcore.media import compose
    real = compose.save_jpeg
    calls = {"n": 0}

    def fail_first(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real(*a, **k)
    monkeypatch.setattr(compose, "save_jpeg", fail_first)
    booth.camera.backend.movie_file = make_movie(Path(booth.data), (1920, 1080), 30, name="m30.mp4")
    booth.store.profile["boomerang"]["fps"] = 60      # the file's 30/s, the live view's far fewer
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s)
    assert s.clip.exists()
    assert s.clip_fps != 30, "the movie's rate must not stay on a live-view clip"
