"""A print sheet from a boomerang, on the normal print layouts (``boomerang.print``).

The owner's wish from the booth (15.11.2026): "Geil wäre, wenn man optional
auch noch die Layouts für Print verwenden könnte, das gilt auch für ein 9:16
Boomerang!" Off by default. On, the boomerang run gets a print sheet on one
of the booth's collage layouts: evenly spaced key frames of the recording
fill the layout's photo slots (a one-photo layout takes the middle frame),
each frame as the film shows it (the format's crop, or the whole frame with
layout "blur"), fitted into its slot by the collage rules
(``collage_fit``, a slot's own ``fit``). With more than one layout the
set-up screen offers the same cards as the collage, their pictures made
with the boomerang's framing in the chosen format. Print quota, held
prints and the QR code on the sheet work as for a collage because the
sheet is the run's ``print_file`` like a collage's. Off, nothing changes:
no template, no key frames, no sheet, the same film to the byte.
"""
import hashlib
import io
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore import config
from boothcore.media import boomerang, compose

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
COLOURS = [(250, 30, 30), (30, 220, 30), (30, 30, 250), (240, 240, 20), (20, 230, 230), (230, 20, 230),
           (250, 140, 0), (120, 0, 200), (0, 120, 60), (200, 200, 200), (90, 60, 30), (255, 255, 255)]


def test_off_by_default_on_both_sides():
    assert config.DEFAULT_PROFILE["boomerang"]["print"] is False
    import sys
    sys.path.insert(0, str(ROOT.parent))
    from app import ecosystem
    assert ecosystem.DEFAULT_PROFILE["boomerang"] == config.DEFAULT_PROFILE["boomerang"]


def test_key_frames_are_spread_evenly_and_the_same_every_time():
    assert boomerang.key_frame_indices(37, 1) == [18]           # the middle frame (= the poster)
    assert boomerang.key_frame_indices(37, 4) == [4, 13, 23, 32]
    assert boomerang.key_frame_indices(40, 3) == [6, 20, 33]
    assert boomerang.key_frame_indices(3, 4) == [0, 1, 1, 2]     # fewer frames than slots: repeated in order
    assert boomerang.key_frame_indices(37, 4) == boomerang.key_frame_indices(37, 4)


def test_a_print_frame_is_what_the_film_shows():
    src = Image.new("RGB", (1200, 800))
    assert boomerang.print_frame(src, "9:16", "crop").size == (450, 800)
    assert boomerang.print_frame(src, "9:16", "blur").size == (1200, 800)     # the whole frame
    assert boomerang.print_frame(src, "3:2", "blur").size == (1200, 800)
    assert boomerang.print_frame(src, "1:1", "crop").size == (800, 800)


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-boom-print-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "collage", "boomerang"]
    b.store.profile["formats"] = ["4:5", "9:16", "3:2"]
    b.store.profile["boomerang"].update({"seconds": 1.0, "fps": 8, "loops": 1, "layout": "crop"})
    b.store.profile["print"].update({"enabled": True, "limit_session": 2, "copies_max": 2})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _frames(n=12):
    """3:2 frames, each in one plain colour, so a slot shows which frame it got."""
    out = []
    for i in range(n):
        buf = io.BytesIO()
        Image.new("RGB", (1200, 800), COLOURS[i % len(COLOURS)]).save(buf, "JPEG", quality=95)
        out.append(buf.getvalue())
    return out


def _template(booth, landscape: bool, shots: int):
    for t in booth.templates():
        info = compose.template_info(t)
        if (info["orientation"] == "landscape") == landscape and info["shots"] == shots \
                and "x2" not in str(info.get("paper")):
            return t
    pytest.skip(f"no built-in {'landscape' if landscape else 'portrait'} layout with {shots} photos")


def _run(booth, monkeypatch, fmt, tpl_id, share_url=""):
    monkeypatch.setattr(booth.camera, "record_timed", lambda seconds, fps: (_frames(), 1.0))
    s = booth.start_session("boomerang", fmt, tpl_id, None)
    if share_url:
        s.share_url = share_url                      # a booth with a gallery has one from the start
    booth.record_clip(s)
    booth.finalize(s, "original")
    return s


def _collage_sheet_size(booth, tpl_id):
    s = booth.start_session("collage", "4:5", tpl_id, None)
    while len(s.shots) < s.shots_needed:
        booth.take_shot(s)
    booth.finalize(s, "original")
    with Image.open(s.print_file) as im:
        return im.size


def _near(c, want, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(c, want))


@pytest.mark.parametrize("landscape", [True, False])
def test_a_9_16_boomerang_prints_on_a_landscape_and_a_portrait_layout(booth, monkeypatch, landscape):
    booth.store.profile["boomerang"]["print"] = True
    tpl = _template(booth, landscape, 4) if any(compose.template_info(t)["shots"] == 4 for t in booth.templates()) \
        else _template(booth, landscape, 3)
    s = _run(booth, monkeypatch, "9:16", tpl["id"])
    assert s.fmt == "9:16" and s.result.suffix == ".mp4"
    assert s.print_file and s.print_file.exists()
    with Image.open(s.print_file) as sheet:
        size = sheet.size
        sheet = sheet.convert("RGB")
        info = compose.template_info(tpl)
        # every slot holds the key frame chosen for it, in the frame's colour
        want = boomerang.key_frame_indices(12, info["shots"])
        for slot, box in zip(info["slots"], info["slot_boxes"]):
            x, y, w, h = box
            got = sheet.getpixel((x + w // 2, y + h // 2))
            assert _near(got, COLOURS[want[slot["shot"] % len(want)]]), (slot, got)
    assert size == _collage_sheet_size(booth, tpl["id"])          # the paper size of that layout
    assert size == tuple(compose.template_info(tpl)["canvas"])


def test_a_one_photo_layout_takes_the_middle_frame(booth, monkeypatch):
    booth.store.profile["boomerang"]["print"] = True
    tpl = _template(booth, False, 1)
    s = _run(booth, monkeypatch, "9:16", tpl["id"])
    info = compose.template_info(tpl)
    x, y, w, h = info["slot_boxes"][0]
    with Image.open(s.print_file) as sheet:
        assert _near(sheet.convert("RGB").getpixel((x + w // 2, y + h // 2)), COLOURS[6])   # frame 6 of 12


def test_the_sheet_prints_like_a_collage_with_quota_and_qr(booth, monkeypatch):
    booth.store.profile["boomerang"]["print"] = True
    booth.store.profile["print"]["qr_on_print"] = True
    seen = {}
    real = compose.render_template

    def spy(tpl, shots, **kw):
        seen["qr"] = kw.get("qr_url")
        seen["n"] = len(shots)
        return real(tpl, shots, **kw)

    monkeypatch.setattr(compose, "render_template", spy)
    tpl = booth.templates()[0]
    s = _run(booth, monkeypatch, "9:16", tpl["id"], share_url="https://galerie.example/b/abc")
    assert seen["qr"] == "https://galerie.example/b/abc"
    assert booth.print_allowance(s.uid)["enabled"] is True
    assert booth.print_uid(s.uid, 2)["queued"] == 2
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        booth.print_uid(s.uid, 1)                    # limit per run, as for a collage


def test_the_kiosk_item_offers_printing(booth, monkeypatch):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["boomerang"]["print"] = True
    monkeypatch.setattr(booth.camera, "record_timed", lambda seconds, fps: (_frames(), 1.0))
    c = TestClient(create_app(booth))
    uid = c.post("/api/session", json={"mode": "boomerang", "format": "9:16",
                                       "template": booth.templates()[0]["id"]}).json()["uid"]
    assert c.post(f"/api/session/{uid}/clip").status_code == 200
    item = c.post(f"/api/session/{uid}/finish", json={"look": "original"}).json()
    assert item["is_video"] is True and item["print"]["enabled"] is True


def test_off_nothing_changes_not_even_the_film(booth, monkeypatch):
    frames = _frames()
    monkeypatch.setattr(booth.camera, "record_timed", lambda seconds, fps: (frames, 1.0))
    s = booth.start_session("boomerang", "9:16", booth.templates()[0]["id"], None)
    assert s.template is None
    booth.record_clip(s)
    booth.finalize(s, "original")
    assert s.print_file is None and s.key_frames == []
    # the very film the code before this switch made from the same frames
    ref = s.folder / "ref.mp4"
    cfg = booth.profile["boomerang"]
    _, poster = boomerang.make_clip(frames, ref, aspect="9:16", width=cfg["width"], max_width=cfg["max_width"],
                                    fps=s.clip_fps, scale=cfg["scale"], layout=cfg["layout"], boomerang=True,
                                    loops=cfg["loops"], rotate=lambda im: im)
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    assert digest(s.result) == digest(ref)
    with Image.open(s.poster) as a:
        assert a.size == poster.size


def test_on_the_layout_pictures_show_the_boomerangs_framing(booth):
    booth.store.profile["boomerang"]["print"] = True
    urls = booth.layoutbilder.urls()
    tid = str(booth.templates()[0]["id"])
    assert f"{tid}@9:16" in urls["b"] and f"{tid}@3:2" in urls["b"]
    path = booth.layoutbilder.datei("b", f"{tid}@9:16", warten=True)
    assert path and path.exists()
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    url = urls["b"][f"{tid}@3:2"]
    r = TestClient(create_app(booth)).get(url)          # the kiosk can load it (kind "b" is served)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    booth.store.profile["boomerang"]["print"] = False
    assert booth.layoutbilder.urls()["b"] == {}


def test_the_set_up_screen_offers_the_layouts_for_the_boomerang_with_the_switch():
    assert 'mode === "boomerang" && !!(p.boomerang || {}).print' in JS
    assert "boomerangLayoutThumb(" in JS
    # a format change re-draws the layout cards in that format
    click = JS.split("if (b.dataset.format)")[1].split("\n")[0]
    assert "renderTemplateCards(" in click
