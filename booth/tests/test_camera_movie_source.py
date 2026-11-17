"""Boomerang and video from a movie the camera records itself ("source": "camera").

The live view of an EOS over USB is about 960x640, so a 9:16 boomerang was
360x640. With the switch on, the camera films onto its card, the booth
downloads the file and builds the clip from it: 1080x1920 for 9:16, whatever
the camera records (Full HD or 4K; owner's decision 17.11.2026: a Full HD
portrait crop is scaled up to the full size). Off by default. Every failure
on that path still gives the guest a clip, from the live view, within the
same run.
"""
import io
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import boomerang, camera_movie


def _ffmpeg():
    return boomerang.ffmpeg_exe()


def make_movie(folder: Path, size=(1920, 1080), fps=25, seconds=1.2, name="src.mp4") -> Path:
    """A small camera-like movie: H.264 with an AAC sound track (the booth must drop it)."""
    out = folder / name
    w, h = size
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(out)], check=True, capture_output=True, timeout=120)
    return out


def mp4_info(path: Path):
    r = subprocess.run([_ffmpeg(), "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})", r.stderr)
    frames = re.findall(r"frame=\s*(\d+)", r.stderr)
    return (int(m.group(1)), int(m.group(2))), (int(frames[-1]) if frames else 0), ("Audio:" in r.stderr)


@pytest.fixture(scope="module")
def movies():
    d = Path(tempfile.mkdtemp(prefix="booth-movie-src-"))
    return {"fhd25": make_movie(d, (1920, 1080), 25), "fhd30": make_movie(d, (1920, 1080), 30, name="b.mp4"),
            "uhd": make_movie(d, (3840, 2160), 25, seconds=0.6, name="c.mp4")}


# ── sizes: fixed targets, scaled up from Full HD ─────────

@pytest.mark.parametrize("fmt,size", [("9:16", (1080, 1920)), ("4:5", (1080, 1350)), ("1:1", (1080, 1080)),
                                      ("2:3", (1080, 1620)), ("3:2", (1920, 1280)), ("16:9", (1920, 1080))])
def test_every_format_has_its_fixed_film_size(fmt, size):
    assert camera_movie.target_size(fmt) == size


@pytest.mark.parametrize("src", [(1920, 1080), (3840, 2160)])
@pytest.mark.parametrize("fmt", ["9:16", "4:5", "1:1", "3:2", "16:9"])
def test_the_centre_crop_is_scaled_to_the_full_size_from_full_hd_and_4k(src, fmt):
    box = camera_movie.target_size(fmt)
    cw, ch, ow, oh = camera_movie.geometry(src, box, fill=True)
    assert (ow, oh) == box                            # up from 608x1080, down from 1215x2160
    assert cw <= src[0] and ch <= src[1]
    assert abs(cw / ch - box[0] / box[1]) < 0.01      # same shape: nothing stretched
    assert ch == src[1] or cw == src[0]               # the largest centre crop


def test_a_full_hd_portrait_crop_is_608_wide_and_scaled_up_with_lanczos():
    vf = camera_movie.video_filter((1920, 1080), (1080, 1920), True, 0, 20)
    assert vf == "fps=20,crop=608:1080,scale=1080:1920:flags=lanczos"


def test_blur_keeps_the_whole_frame_fitted_into_the_film():
    assert camera_movie.geometry((1920, 1080), (1080, 1920), fill=False) == (1920, 1080, 1080, 608)
    assert camera_movie.geometry((3840, 2160), (1920, 1080), fill=False) == (3840, 2160, 1920, 1080)


def test_a_turned_camera_turns_before_the_crop():
    vf = camera_movie.video_filter((1920, 1080), (1080, 1920), True, 90, 25)
    assert vf == "fps=25,transpose=1,scale=1080:1920:flags=lanczos"    # upright 1080x1920 needs no crop


def test_the_film_never_has_more_frames_a_second_than_the_file():
    assert camera_movie.film_fps(20, 25.0) == 20
    assert camera_movie.film_fps(60, 29.97) == 30
    assert camera_movie.film_fps(25, 30.0) == 25


@pytest.mark.parametrize("key,fps_file", [("fhd25", 25), ("fhd30", 30)])
def test_the_rate_is_read_from_the_file(movies, key, fps_file):
    w, h, fps = camera_movie.probe(_ffmpeg(), movies[key])
    assert (w, h) == (1920, 1080) and round(fps) == fps_file


def test_frames_from_a_4k_movie_come_in_the_film_size(movies, tmp_path):
    frames, fps = camera_movie.extract_frames(movies["uhd"], tmp_path / "f", box=(1080, 1920), fill=True,
                                              fps=20, seconds=0.5)
    assert fps == 20 and 8 <= len(frames) <= 11
    assert Image.open(io.BytesIO(frames[0])).size == (1080, 1920)
    assert not (tmp_path / "f").exists()                # the work folder goes


# ── the booth ─────────────────────────────────────────────

@pytest.fixture()
def booth(monkeypatch, movies):
    d = Path(tempfile.mkdtemp(prefix="booth-movie-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "boomerang", "video"]
    b.store.profile["boomerang"].update({"seconds": 1.0, "fps": 20, "loops": 1, "source": "camera"})
    b.store.profile["video"].update({"seconds": 1.0, "fps": 25, "source": "camera"})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    b.camera.backend.movie_file = movies["fhd25"]
    live_calls = []
    real = b.camera.record_timed

    def record_timed(seconds, fps):
        live_calls.append(seconds)
        return real(seconds, fps)

    monkeypatch.setattr(b.camera, "record_timed", record_timed)
    b.live_calls = live_calls
    yield b
    b.camera.stop()
    b.mail.stop()


def _raw_movies(b):
    folder = b.data / "cache" / "kamerafilm"
    return sorted(p.name for p in folder.rglob("*")) if folder.exists() else []


def test_the_default_is_the_live_view_on_both_sides():
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["boomerang"]["source"] == "live"
    assert DEFAULT_PROFILE["video"]["source"] == "live"


def test_with_the_default_the_booth_films_from_the_live_view(booth):
    booth.store.profile["boomerang"]["source"] = "live"
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s)
    assert booth.live_calls and booth.camera.backend.movies_recorded == 0
    assert mp4_info(s.clip)[0] != (1080, 1920)


@pytest.mark.parametrize("fmt,layout,size", [("9:16", "crop", (1080, 1920)), ("4:5", "crop", (1080, 1350)),
                                             ("16:9", "crop", (1920, 1080)), ("9:16", "blur", (1080, 1920))])
def test_a_boomerang_from_the_camera_movie_has_the_full_size(booth, fmt, layout, size):
    booth.store.profile["formats"] = ["4:5", "9:16", "16:9"]
    booth.store.profile["boomerang"]["layout"] = layout
    s = booth.start_session("boomerang", fmt, None, None)
    booth.record_clip(s)
    booth.finalize(s, "original")
    (w, h), count, audio = mp4_info(s.result)
    assert (w, h) == size and not audio
    assert count >= 30                             # 20/s of a 1 s movie, there and back
    assert not booth.live_calls                    # no second recording
    with Image.open(s.poster) as poster:
        assert poster.size == size
    assert _raw_movies(booth) == []                # the raw movie is gone, never in the run
    assert not list(s.folder.glob("*.MP4")) and not list(s.folder.glob("MVI_*"))


def test_a_video_reads_the_rate_from_a_30p_file(booth, movies):
    booth.camera.backend.movie_file = movies["fhd30"]
    booth.store.profile["video"]["fps"] = 60
    s = booth.start_session("video", "9:16", None, None)
    booth.record_clip(s)
    assert s.clip_fps == 30
    assert mp4_info(s.clip)[0] == (1080, 1920)


def test_encode_later_builds_the_movie_clip_after_the_answer(booth):
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s, encode_later=True)
    booth.finalize(s, "original")
    assert mp4_info(s.result)[0] == (1080, 1920)


@pytest.mark.parametrize("fail", ["start", "file", "download"])
def test_every_camera_failure_still_gives_the_guest_a_live_view_clip(booth, fail, caplog):
    backend = booth.camera.backend
    backend.movie_fail = fail
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s)
    booth.finalize(s, "original")
    assert s.result and s.result.exists()
    assert mp4_info(s.result)[0] != (1080, 1920)          # the live view's size
    assert backend.movie_restored == 1                    # the camera was put back
    # Failed before the recording: recorded from the live view now. After it:
    # the live frames taken during the movie are used, nobody records twice.
    assert bool(booth.live_calls) == (fail == "start")
    assert "Live-Bild" in caplog.text
    assert booth.camera.state == "ready"


@pytest.mark.parametrize("step", ["make_film", "extract_frames"])
def test_an_ffmpeg_failure_on_the_movie_gives_the_live_view_clip(booth, monkeypatch, caplog, step):
    def broken(*a, **k):
        raise RuntimeError("ffmpeg: broken movie")
    monkeypatch.setattr(camera_movie, step, broken)
    if step == "extract_frames":
        booth.store.profile["boomerang"].update({"frames": True})   # the frame path
    s = booth.start_session("boomerang", "9:16", None, None,
                            **({"motion_frame": "titel-unten"} if step == "extract_frames" else {}))
    booth.record_clip(s)
    booth.finalize(s, "original")
    assert s.result.exists()
    if step == "make_film":
        assert mp4_info(s.result)[0] != (1080, 1920)
    assert "broken movie" in caplog.text and not booth.live_calls
    assert _raw_movies(booth) == []                       # deleted on the failure path too


def test_a_camera_that_cannot_film_falls_back_without_a_try(booth):
    booth.camera.backend.movie_file = None                # like gphoto2, webcam, folder
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s)
    assert booth.live_calls and s.clip.exists()


def test_a_motion_frame_renders_the_movie_at_the_frame_size(booth, monkeypatch):
    booth.store.profile["boomerang"].update({"frames": True})
    seen = {}
    real = camera_movie.extract_frames

    def spy(path, work, **kw):
        seen.update(kw)
        return real(path, work, **kw)

    monkeypatch.setattr(camera_movie, "extract_frames", spy)
    s = booth.start_session("boomerang", "9:16", None, None, motion_frame="titel-unten")
    booth.record_clip(s)
    booth.finalize(s, "original")
    from boothcore.media import motion_frames
    win = motion_frames.window_box(s.motion_frame, (1080, 1920))
    assert seen["box"] == (win[2], win[3]) and seen["fill"] is True
    assert mp4_info(s.result)[0] == (1080, 1920)
    assert not booth.live_calls


# ── device test 17.11.2026: start delay, speed ───────────

def test_the_camera_records_a_little_longer_and_the_clip_keeps_the_end(booth, monkeypatch):
    asked = []
    real = booth.camera.backend.record_movie

    def spy(seconds, folder, **kw):
        asked.append(seconds)
        return real(seconds, folder, **kw)

    monkeypatch.setattr(booth.camera.backend, "record_movie", spy)
    s = booth.start_session("boomerang", "9:16", None, None)
    booth.record_clip(s)
    assert asked and abs(asked[0] - (1.0 + booth.CAMERA_MOVIE_LEAD)) < 1e-6
    assert s.clip_seconds == 1.0
    assert camera_movie.tail_start(2.7, 2.0) == 0.7      # the last 2 s of a 2.7 s file
    assert camera_movie.tail_start(1.3, 2.0) == 0.0      # shorter than asked: all of it


def test_without_frame_or_sheet_the_film_is_made_in_one_ffmpeg_pass(booth, monkeypatch, caplog):
    def no_frames(*a, **k):
        raise AssertionError("frame path used")
    monkeypatch.setattr(camera_movie, "extract_frames", no_frames)
    for layout in ("crop", "blur"):
        booth.store.profile["boomerang"]["layout"] = layout
        s = booth.start_session("boomerang", "9:16", None, None)
        booth.record_clip(s)
        booth.finalize(s, "original")
        (w, h), count, audio = mp4_info(s.result)
        assert (w, h) == (1080, 1920) and not audio and count >= 30
        with Image.open(s.poster) as poster:
            assert poster.size == (1080, 1920)
    assert "in einem Durchgang" in caplog.text and "Warten auf den Film" in caplog.text


def test_a_boomerang_with_loops_is_one_encoded_pass_joined(movies, tmp_path):
    target = tmp_path / "b.mp4"
    _, poster, times = camera_movie.make_film(movies["fhd25"], target, box=(1080, 1920), fill=True, fps=20,
                                              seconds=1.0, boomerang=True, loops=3)
    (w, h), count, audio = mp4_info(target)
    assert (w, h) == (1080, 1920) and not audio
    assert 3 * 36 <= count <= 3 * 40 and "join" in times
    assert not list(tmp_path.glob("*.pass.*"))
