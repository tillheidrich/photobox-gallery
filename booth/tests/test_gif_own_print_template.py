"""A GIF run printed on the operator's own sheet (``gif.print_template``).

Empty = the built-in sheet of that number of pictures, as before. A named
template that is on the booth takes the pictures in its own number of slots
(more pictures are thinned evenly from the first to the last); a template
that is missing falls back to the built-in sheet, so a print never fails
for it.
"""
import json
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import compose

FARBEN = [(220, 30, 30), (30, 200, 40), (40, 60, 220), (230, 210, 30), (200, 40, 200), (30, 200, 210)]


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-gif-eigen-"))
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "collage", "gif"]
    b.store.profile["gif"]["shots"] = 6
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    zaehler = {"n": 0}

    def capture(target):
        Image.new("RGB", (1500, 1000), FARBEN[zaehler["n"] % 6]).save(target, "JPEG", quality=95)
        zaehler["n"] += 1
        return Path(target)
    monkeypatch.setattr(b.camera, "capture", capture)
    yield b
    b.camera.stop()
    b.mail.stop()


def _eigene_sechs(booth):
    v = {"id": "sechs-eigen", "name": "Sechs eigen", "paper": "10x15-quer", "canvas": [1800, 1200],
         "background": "#123456",
         "shapes": [{"id": "grund", "kind": "rect", "x": 0, "y": 0, "w": 1800, "h": 1200, "fill": "#f0e0d0",
                     "stroke": "", "stroke_width": 0, "opacity": 100, "radius": 0}],
         "order": ["grund", "overlay"],
         "slots": [{"x": 96 + c * 544, "y": 96 + r * 412, "w": 520, "h": 388, "shot": r * 3 + c, "radius": 0}
                   for r in range(2) for c in range(3)], "texts": []}
    booth.assets_dir.mkdir(parents=True, exist_ok=True)
    (booth.assets_dir / "sechs-eigen.json").write_text(json.dumps(v), encoding="utf-8")
    return v


def _gif(booth):
    s = booth.start_session("gif", "3:2", None, None)
    for _ in range(s.shots_needed):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def test_the_own_six_photo_sheet_is_used_for_a_gif_run(booth):
    v = _eigene_sechs(booth)
    booth.store.profile["gif"]["print_template"] = "sechs-eigen.json"
    s = _gif(booth)
    with Image.open(s.print_file) as bogen:
        bogen = bogen.convert("RGB")
        assert bogen.size == (1800, 1200)
        assert all(abs(a - b) < 12 for a, b in zip(bogen.getpixel((40, 40)), (240, 224, 208)))
        # Six slots, six different pictures, in order.
        for slot, farbe in zip(v["slots"], FARBEN):
            mitte = bogen.getpixel((slot["x"] + slot["w"] // 2, slot["y"] + 30))
            assert all(abs(a - b) < 40 for a, b in zip(mitte, farbe)), (slot, mitte)


def test_without_a_choice_or_with_a_missing_file_the_built_in_sheet_prints(booth):
    eingebaut, _ = compose.gif_print_template("3:2", 6)
    for wahl in ("", "fehlt.json", "../ausserhalb.json"):
        booth.store.profile["gif"]["print_template"] = wahl
        s = _gif(booth)
        with Image.open(s.print_file) as bogen:
            assert list(bogen.size) == eingebaut["canvas"], wahl


def test_more_pictures_than_slots_are_thinned_evenly():
    assert compose.gif_auswahl(6, 4) == [0, 2, 3, 5]
    assert compose.gif_auswahl(6, 6) == [0, 1, 2, 3, 4, 5]
    assert compose.gif_auswahl(3, 6) == [0, 1, 2, 0, 1, 2]
