"""Processing on a slow booth PC: decode once, make the small things small.

Measured on the booth PC (i5-6200U, 2 cores, 6000×4000 camera files,
15.11.2026): a GIF run of six shots stood 55 s on "Daten werden bearbeitet",
and every one of the 17 look previews the result screen asks for decoded the
24 MP originals again (six of them for a GIF). Now:

* previews come from one working copy per shot, decoded once at a reduced
  JPEG scale and kept; a preview asked twice is made once;
* a GIF run never decodes an original in full — every file of it is at most
  a film's width — and the working copies are started while the guests pose;
* the GIF's two films are made after the result screen and queued for upload
  when they exist; print, QR code and the GIF itself do not wait for them.

What is delivered keeps its sizes; the GIF file is byte for byte what the
pictures give with the old code (the palette of each picture is made once).
"""
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import boomerang


@pytest.fixture()
def booth(monkeypatch):
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=Path(tempfile.mkdtemp(prefix="booth-speed-")), start_threads=False)
    b.store.profile["modes"] = ["photo", "collage", "gif"]
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _decodes(booth, monkeypatch):
    calls = []
    real = booth._open_shot

    def counting(path, box=None):
        calls.append((Path(path).name, box))
        return real(path, box)
    monkeypatch.setattr(booth, "_open_shot", counting)
    return calls


def _run(booth, mode):
    s = booth.start_session(mode, "4:5", None, None)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    return s


def _settle(booth):
    """Let the warm-up started by take_shot finish."""
    booth.render_pool().submit(lambda: None).result()
    time.sleep(0.05)
    booth.render_pool().submit(lambda: None).result()


def test_the_look_previews_do_not_decode_the_original_again(booth, monkeypatch):
    s = _run(booth, "photo")
    booth.finalize(s, "original")
    calls = _decodes(booth, monkeypatch)
    looks = booth.look_names()
    assert len(looks) > 3
    for look in looks:
        assert booth.preview_jpeg(s, look, 260)[:2] == b"\xff\xd8"
    assert not [c for c in calls if c[1] is None], "no preview may decode the full original"
    assert len(calls) <= len(s.shots), f"one working copy per shot for all looks, got {calls}"
    calls.clear()
    for look in looks:
        booth.preview_jpeg(s, look, 260)
    assert calls == [], "asked again, a preview is not made again"


def test_a_preview_asked_twice_over_http_is_made_once(booth, monkeypatch):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    s = _run(booth, "photo")
    booth.finalize(s, "original")
    renders = []
    real = booth.render
    monkeypatch.setattr(booth, "render", lambda *a, **k: renders.append(a) or real(*a, **k))
    for _ in range(2):
        r = client.get(f"/api/session/{s.uid}/preview.jpg?look=original&size=260")
        assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert len(renders) == 1


def test_a_new_drawing_or_a_reshoot_makes_the_preview_anew(booth, monkeypatch):
    s = _run(booth, "photo")
    booth.finalize(s, "original")
    first = booth.preview_jpeg(s, "original", 260)
    s.drawing = [{"color": "#e5484d", "width": 0.02, "points": [[0.1, 0.1], [0.9, 0.9]]}]
    assert booth.preview_jpeg(s, "original", 260) != first
    s.drawing = []
    time.sleep(0.01)
    booth.take_shot(s, retake=True)
    calls = _decodes(booth, monkeypatch)
    booth.preview_jpeg(s, "original", 260)
    assert calls, "a reshot picture is decoded anew"


def test_a_gif_run_never_decodes_an_original_in_full(booth, monkeypatch):
    calls = _decodes(booth, monkeypatch)
    s = _run(booth, "gif")
    _settle(booth)
    assert len([c for c in calls if c[1] == booth._gif_work_px()]) == len(s.shots), \
        "each shot's working copy is started while the guests pose for the next one"
    calls.clear()
    booth.finalize(s, "original")
    booth.wait_for_films(s)
    assert not [c for c in calls if c[1] is None]
    assert calls == [], "the working copies made during the shots are used"
    with Image.open(s.result) as gif:
        assert gif.size == (1080, 1350)
    with Image.open(s.print_file) as card:
        assert card.size[0] >= 1200 and card.size[1] >= 1200


def test_the_gif_is_on_record_before_its_films_and_they_follow(booth, monkeypatch):
    booth.store.save_local({"storage": {"folder": str(Path(tempfile.mkdtemp()) / "zweit")}})
    started = []
    real = boomerang.make_boomerang_from_stills

    def slow(*a, **k):
        started.append(time.monotonic())
        time.sleep(0.5)
        return real(*a, **k)
    monkeypatch.setattr(boomerang, "make_boomerang_from_stills", slow)
    s = _run(booth, "gif")
    booth.finalize(s, "original")
    # the result screen can show: GIF, thumbnail, poster, print file, upload of the GIF
    assert s.result.is_file() and s.thumb.is_file() and s.poster.is_file() and s.print_file.is_file()
    uids = {e["uid"] for e in booth.outbox.due(60)}
    assert s.uid in uids and f"{s.uid}.boom" not in uids
    booth.wait_for_films(s)
    assert (s.folder / "result.mp4").is_file() and (s.folder / "boomerang.mp4").is_file()
    uids = {e["uid"] for e in booth.outbox.due(60)}
    assert f"{s.uid}.mp4" in uids and f"{s.uid}.boom" in uids
    assert booth.library.get(s.uid)["anim"]
    assert not list(s.folder.glob("*.g*.mp4")), "no half film left under its working name"


def test_a_look_chosen_while_the_films_run_drops_the_older_films(booth, monkeypatch):
    real = boomerang.make_boomerang_from_stills

    def slow(*a, **k):
        time.sleep(0.4)
        return real(*a, **k)
    monkeypatch.setattr(boomerang, "make_boomerang_from_stills", slow)
    s = _run(booth, "gif")
    booth.finalize(s, "original")
    first = s.films_thread
    other = next(l for l in booth.look_names() if l != "original")
    booth.finalize(s, other)
    first.join(30)
    booth.wait_for_films(s)
    assert s.look == other and s.film_gen == 2
    assert s.boom and s.boom.is_file()
    assert not list(s.folder.glob("*.g*.mp4"))


def test_with_the_boomerang_as_a_guest_choice_the_films_are_there_at_once(booth):
    booth.store.profile["gif"]["boomerang"]["guest_choice"] = True
    s = _run(booth, "gif")
    booth.finalize(s, "original")
    assert s.boom and s.boom.is_file() and s.clip and s.clip.is_file()


def test_a_print_only_gif_makes_no_films(booth, monkeypatch):
    s = _run(booth, "gif")
    monkeypatch.setattr(booth, "run_print_only", lambda *_a, **_k: True)
    booth.finalize(s, "original")
    booth.wait_for_films(s)
    assert s.films_thread is None
    assert not (s.folder / "result.mp4").exists() and not (s.folder / "boomerang.mp4").exists()


def test_spreading_the_gif_work_gives_the_same_file(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    imgs = [Image.effect_noise((900, 600), 40 + 10 * i).convert("RGB") for i in range(4)]
    a = boomerang.make_gif(imgs, tmp_path / "a.gif", frame_ms=200)[0].read_bytes()
    with ThreadPoolExecutor(2) as pool:
        b = boomerang.make_gif(imgs, tmp_path / "b.gif", frame_ms=200, mapper=pool.map)[0].read_bytes()
    # and the same as Pillow quantizing every frame itself, as before
    size, laid, seq = boomerang._gif_frames(imgs, "4:5", 1080, "native", True)
    seq[0].save(tmp_path / "c.gif", "GIF", save_all=True, append_images=seq[1:], duration=200, loop=0,
                optimize=True, disposal=2)
    assert a == b == (tmp_path / "c.gif").read_bytes()


# ── the 15 s films: one pass encoded, the others copied (booth PC, 15.11.2026) ──
# The 9:16 film of a six-shot GIF pushed 240 frames at 1080×1920 through x264
# (+21 s on two cores) although every pass shows the same pictures.
def _duration(path: Path) -> float:
    import re
    import subprocess
    err = subprocess.run([boomerang.ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def _stills(n=6):
    return [Image.effect_noise((960, 1200), 30 + 8 * i).convert("RGB") for i in range(n)]


def _count_encoded(monkeypatch):
    counted = []
    real = boomerang.encode_mp4

    def counting(frames, size, target, fps, **kw):
        frames = list(frames)
        counted.append(len(frames))
        return real(iter(frames), size, target, fps, **kw)
    monkeypatch.setattr(boomerang, "encode_mp4", counting)
    return counted


def test_the_gif_film_encodes_one_pass_and_keeps_its_length(tmp_path, monkeypatch):
    counted = _count_encoded(monkeypatch)
    out = boomerang.make_gif_film(_stills(), tmp_path / "result.mp4", frame_ms=200, seconds=15)
    fps, passes = boomerang.gif_film_passes(10, 200, 15)
    assert passes > 1
    assert counted == [10], "one pass of the ping-pong (6 + 4 pictures), not every pass"
    assert abs(_duration(out) - passes * 10 / fps) < 0.3
    assert not list(tmp_path.glob("*.pass.*")), "no working file left"


def test_the_boomerang_film_encodes_one_pass_and_keeps_its_length(tmp_path, monkeypatch):
    counted = _count_encoded(monkeypatch)
    out = boomerang.make_boomerang_from_stills(_stills(), tmp_path / "boomerang.mp4", aspect="9:16",
                                               frame_ms=200, fps=15, loops=3, max_width=540, min_seconds=15)
    one_pass = 10 * 3                     # 10 pictures, each held 3 frames at 15 fps
    assert counted == [one_pass]
    assert abs(_duration(out) - 16.0) < 0.3, "8 whole passes of 2 s, at least the 15 s asked"


def test_a_join_that_fails_still_gives_the_whole_film(tmp_path, monkeypatch):
    import subprocess
    counted = _count_encoded(monkeypatch)
    real_run = subprocess.run

    def broken(cmd, *a, **k):
        if "concat" in cmd:
            raise OSError("concat unavailable (fake)")
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(boomerang.subprocess, "run", broken)
    out = boomerang.make_gif_film(_stills(), tmp_path / "result.mp4", frame_ms=200, seconds=15)
    fps, passes = boomerang.gif_film_passes(10, 200, 15)
    assert counted == [10, 10 * passes]
    assert abs(_duration(out) - passes * 10 / fps) < 0.3
    assert not list(tmp_path.glob("*.pass.*"))
