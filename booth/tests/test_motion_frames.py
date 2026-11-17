"""Frames for boomerang and video ("Rahmen", 17.11.2026).

The film plays in the window of a frame: the frame's canvas is the film's
size, the background around the window, overlay and texts above it. Off by
default, and off means the run is exactly as before. A frame that cannot be
drawn never costs the guest the film: it is made without the frame.
"""
import io
import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from boothcore.media import boomerang, motion_frames

ROOT = Path(__file__).resolve().parents[2]
SRC = (1200, 800)


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-rahmen-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "boomerang", "video"]
    b.store.profile["boomerang"].update({"seconds": 1.0, "fps": 8, "loops": 1})
    b.store.profile["video"].update({"seconds": 1.0, "fps": 8})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    monkeypatch.setattr(b.camera, "record_timed", lambda seconds, fps: (_frames(), 1.0))
    yield b
    b.camera.stop()
    b.mail.stop()


def _frames(n=6):
    out = []
    for i in range(n):
        im = Image.new("RGB", SRC, (200, 40 + i * 20, 40))
        ImageDraw.Draw(im).rectangle([500, 300, 700, 500], fill="white")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
        out.append(buf.getvalue())
    return out


def _mp4(path: Path):
    r = subprocess.run([boomerang.ffmpeg_exe(), "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})", r.stderr)
    frames = re.findall(r"frame=\s*(\d+)", r.stderr)
    return (int(m.group(1)), int(m.group(2))), int(frames[-1]) if frames else 0


def _frames_on(b, mode="boomerang", **extra):
    b.store.profile[mode].update({"frames": True, **extra})


def own_frame(b, **over):
    raw = {"kind": "motion_frame", "id": "eigener", "name": "Eigener", "format": "9:16", "background": "#204060",
           "window": {"x": 100, "y": 100, "w": 880, "h": 1400, "radius": 30},
           "texts": [{"value": "{event_name}", "x": 540, "y": 1600, "size": 60, "align": "center",
                      "color": "#ffffff"}]}
    raw.update(over)
    (b.assets_dir / "rahmen-eigener.json").write_text(json.dumps(raw), encoding="utf-8")
    return raw


# ── off = as before ───────────────────────────────────────

def test_frames_are_off_by_default_and_the_run_is_as_before(booth):
    assert booth.profile["boomerang"]["frames"] is False and booth.profile["video"]["frames"] is False
    assert booth.motion_frame_offer("boomerang")["enabled"] is False
    s = booth.start_session("boomerang", "9:16", None, None, motion_frame="titel-unten")
    assert s.motion_frame is None and "motion_frame" not in s.public()
    booth.record_clip(s)
    (w, h), _ = _mp4(s.clip)
    assert (w, h) != (1080, 1920)            # the unframed native film of a 1200x800 source


def test_a_frame_file_is_not_offered_as_a_print_layout(booth):
    own_frame(booth)
    assert all(name != "rahmen-eigener.json" for name, _t in booth.own_template_files())
    booth.store.profile["collage_templates"] = ["rahmen-eigener.json"]
    assert all(t.get("kind") != "motion_frame" for t in booth.templates())


# ── on ────────────────────────────────────────────────────

def test_with_frames_on_every_built_in_is_offered_and_without_frame_too(booth):
    _frames_on(booth)
    offer = booth.motion_frame_offer("boomerang")
    ids = [i for i, _f in offer["frames"]]
    assert {"titel-unten", "rahmen-hell", "rahmen-dunkel", "titel-links"} <= set(ids)
    assert offer["none"] is True and offer["default"] == ids[0]


@pytest.mark.parametrize("mode", ["boomerang", "video"])
def test_a_framed_film_has_the_frame_size_and_every_frame(booth, mode):
    _frames_on(booth, mode)
    s = booth.start_session(mode, "4:5", None, None, motion_frame="titel-unten")
    assert s.motion_frame_id == "titel-unten" and s.fmt == "9:16"
    booth.record_clip(s)
    booth.finalize(s, "original")
    size, count = _mp4(s.result)
    assert size == (1080, 1920)
    plain = 6 + 4 if mode == "boomerang" else 6           # forward and back, one pass
    assert count == plain
    with Image.open(s.poster) as poster:
        assert poster.size == (1080, 1920)
        # the window shows the recording (reddish), the background stays dark
        win = motion_frames.window_box(s.motion_frame, (1080, 1920))
        r, g, bl = poster.getpixel((win[0] + 40, win[1] + win[3] // 2))[:3]
        assert r > 150 and bl < 100
        assert max(poster.getpixel((10, 1900))[:3]) < 80


def test_a_landscape_frame_makes_a_landscape_film(booth):
    _frames_on(booth, "video")
    s = booth.start_session("video", "9:16", None, None, motion_frame="titel-links")
    booth.record_clip(s)
    assert _mp4(s.clip)[0] == (1920, 1080)


def test_without_frame_is_the_film_as_before(booth):
    _frames_on(booth)
    s = booth.start_session("boomerang", "9:16", None, None, motion_frame="none")
    assert s.motion_frame is None
    booth.record_clip(s)
    assert _mp4(s.clip)[0] != (1080, 1920)


def test_without_guest_choice_the_default_is_taken(booth):
    _frames_on(booth, frame_guest_choice=False, frame_default="rahmen-dunkel")
    s = booth.start_session("boomerang", "9:16", None, None, motion_frame="titel-unten")
    assert s.motion_frame_id == "rahmen-dunkel"
    _frames_on(booth, frame_default="none")
    s = booth.start_session("boomerang", "9:16", None, None)
    assert s.motion_frame is None


def test_only_the_named_frames_and_an_unknown_pick_falls_back_to_the_default(booth):
    own_frame(booth)
    _frames_on(booth, frame_templates=["rahmen-eigener.json"], frame_builtins=["rahmen-hell"], frame_none=False)
    offer = booth.motion_frame_offer("boomerang")
    assert [i for i, _f in offer["frames"]] == ["rahmen-eigener.json", "rahmen-hell"]
    assert offer["none"] is False
    s = booth.start_session("boomerang", "9:16", None, None, motion_frame="titel-links")
    assert s.motion_frame_id == "rahmen-eigener.json"
    s = booth.start_session("boomerang", "9:16", None, None, motion_frame="none")
    assert s.motion_frame_id == "rahmen-eigener.json"          # "Ohne Rahmen" is not offered here


def test_a_frame_that_cannot_be_drawn_costs_the_frame_not_the_film(booth, monkeypatch, caplog):
    _frames_on(booth)

    def kaputt(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(motion_frames, "cached_layers", kaputt)
    s = booth.start_session("boomerang", "9:16", None, None, motion_frame="titel-unten")
    with caplog.at_level(logging.WARNING):
        booth.record_clip(s)
    assert s.clip and s.clip.is_file() and s.motion_frame is None
    assert any("ohne Rahmen" in r.getMessage() for r in caplog.records)


def test_an_encoder_failure_with_a_frame_falls_back_to_the_film_without(booth, monkeypatch, caplog):
    _frames_on(booth)
    real = boomerang.make_clip

    def nur_ohne(frames, target, **kw):
        if kw.get("frame_layers"):
            raise RuntimeError("ffmpeg fehlgeschlagen")
        return real(frames, target, **kw)
    monkeypatch.setattr(boomerang, "make_clip", nur_ohne)
    s = booth.start_session("boomerang", "9:16", None, None, motion_frame="rahmen-hell")
    with caplog.at_level(logging.WARNING):
        booth.record_clip(s)
    assert s.clip.is_file() and s.motion_frame_id == ""
    assert any("ohne Rahmen" in r.getMessage() for r in caplog.records)


def test_the_still_layers_are_rendered_once_per_content(booth):
    _frames_on(booth)
    s = booth.start_session("boomerang", "9:16", None, None, motion_frame="titel-unten")
    booth.record_clip(s)
    cache = booth.data / "cache" / "rahmen"
    first = sorted(p.name for p in cache.glob("*.png"))
    assert len(first) == 2
    stamp = [(cache / n).stat().st_mtime_ns for n in first]
    s2 = booth.start_session("boomerang", "9:16", None, None, motion_frame="titel-unten")
    booth.record_clip(s2)
    assert sorted(p.name for p in cache.glob("*.png")) == first
    assert [(cache / n).stat().st_mtime_ns for n in first] == stamp


def test_the_kiosk_state_lists_frames_with_pictures(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    _frames_on(booth, "video")
    client = TestClient(create_app(booth))
    st = client.get("/api/state").json()
    assert st["motion_frames"]["boomerang"]["enabled"] is False
    video = st["motion_frames"]["video"]
    assert video["enabled"] and video["frames"][0]["thumb"].startswith("/api/layout-thumb/m/")
    pic = client.get(video["frames"][0]["thumb"])
    assert pic.status_code == 200 and pic.headers["content-type"] == "image/jpeg"


# ── shared files ──────────────────────────────────────────

def test_the_frame_module_and_built_ins_are_the_same_in_gallery_and_booth():
    for a, b in ((ROOT / "app" / "motion_frames.py", ROOT / "booth" / "boothcore" / "media" / "motion_frames.py"),
                 (ROOT / "app" / "static" / "data" / "motion_frames.json",
                  ROOT / "booth" / "boothcore" / "media" / "motion_frames.json")):
        assert a.read_bytes() == b.read_bytes(), "tools/sync_editor.py / tools/build_motion_frames.py laufen lassen"


def test_the_built_ins_are_what_the_script_writes():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bmf", ROOT / "tools" / "build_motion_frames.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (ROOT / "booth" / "boothcore" / "media" / "motion_frames.json").read_bytes() == mod.built()
    frames = motion_frames.builtins(ROOT / "booth" / "boothcore" / "media" / "motion_frames.json")
    assert [f["id"] for f in frames] == ["titel-unten", "rahmen-hell", "rahmen-dunkel", "titel-links"]


# ── the check ─────────────────────────────────────────────

def test_the_check_says_in_german_what_is_wrong():
    with pytest.raises(motion_frames.FrameError) as e:
        motion_frames.check({"kind": "motion_frame", "format": "3:2"})
    assert "Format" in str(e.value) and "Videofenster" in str(e.value)
    with pytest.raises(motion_frames.FrameError) as e:
        motion_frames.check({"kind": "motion_frame", "format": "9:16", "background": "../x.png",
                             "window": {"x": 0, "y": 0, "w": 50, "h": 50}})
    assert "zu klein" in str(e.value) and "Hintergrund" in str(e.value)
    frame, hints = motion_frames.check({"kind": "motion_frame", "format": "9:16",
                                        "window": {"x": 900, "y": 0, "w": 500, "h": 500}})
    assert frame["window"]["x"] == 580 and any("hineingeschoben" in h for h in hints)


def test_the_check_with_media_names_a_missing_background(tmp_path):
    with pytest.raises(motion_frames.FrameError) as e:
        motion_frames.check({"kind": "motion_frame", "format": "16:9", "background": "fehlt.png",
                             "window": {"x": 0, "y": 0, "w": 800, "h": 600}}, media=tmp_path)
    assert "fehlt in den Medien" in str(e.value)


# ── templates page: frames as tiles with switches ─────────

def test_the_templates_page_lists_frames_and_a_switch_turns_them_on(booth):
    own_frame(booth)
    over = booth.frame_overview()
    tiles = over["modes"]["boomerang"]["frames"]
    assert {t["id"] for t in tiles} >= {"rahmen-eigener.json", "titel-unten"}
    assert not any(t["visible"] for t in tiles) and over["modes"]["boomerang"]["enabled"] is False
    after = booth.set_frame_visibility("boomerang", ["rahmen-eigener.json", "titel-links"], True)
    assert booth.profile["boomerang"]["frames"] is True
    assert booth.profile["boomerang"]["frame_templates"] == ["rahmen-eigener.json"]
    assert booth.profile["boomerang"]["frame_builtins"] == ["titel-links"]
    shown = {t["id"] for t in after["modes"]["boomerang"]["frames"] if t["visible"]}
    assert shown == {"rahmen-eigener.json", "titel-links"}
    with pytest.raises(ValueError, match="empty"):
        booth.set_frame_visibility("boomerang", [], False)
    booth.set_frame_visibility("boomerang", [], True)              # only "Ohne Rahmen": frames off
    assert booth.profile["boomerang"]["frames"] is False


def test_frames_set_by_the_event_lock_the_switches(booth):
    booth.store.local["server"] = {**(booth.store.local.get("server") or {}), "url": "https://galerie.example"}
    booth.store.profile["event"] = {**booth.profile["event"], "name": "Sommerfest 2027", "frames_fixed": True}
    vis = booth.frame_visibility()
    assert vis["source"] == "event" and vis["locked"]
    with pytest.raises(ValueError, match="locked"):
        booth.set_frame_visibility("video", ["titel-links"], True)


def test_the_frame_picture_route_serves_hidden_frames_behind_the_pin(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    assert client.get("/api/admin/frames").status_code in (401, 403)
    tile = booth.frame_overview()["modes"]["video"]["frames"][0]
    assert tile["picture"].startswith("/api/admin/frames/picture?")
