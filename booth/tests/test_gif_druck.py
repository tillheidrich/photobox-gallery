"""A printed GIF run is a sheet with all its pictures.

Found on the real booth on 15.11.2026: a GIF run takes six pictures, and the
print showed one of them, the first, on the single-picture card. On paper the
pictures of a GIF are what the guest posed for, so they all go on the sheet,
in one of the built-in collage layouts that fits their number and the
orientation of the pictures. Photo and collage runs print as before.
"""
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import compose

FARBEN = [(220, 30, 30), (30, 200, 40), (40, 60, 220), (230, 210, 30), (200, 40, 200), (30, 200, 210)]


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-gifdruck-"))
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "collage", "gif"]
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    zaehler = {"n": 0}

    def capture(target):
        farbe = FARBEN[zaehler["n"] % len(FARBEN)]
        zaehler["n"] += 1
        Image.new("RGB", (1500, 1000), farbe).save(target, "JPEG", quality=95)
        return Path(target)
    monkeypatch.setattr(b.camera, "capture", capture)
    yield b
    b.camera.stop()
    b.mail.stop()


def _lauf(booth, mode, fmt="4:5", vorlage=None, shots=None):
    if shots:
        booth.store.profile["gif"]["shots"] = shots
    s = booth.start_session(mode, fmt, vorlage, None)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _naehe(a, b, toleranz=40):
    return all(abs(x - y) <= toleranz for x, y in zip(a, b))


def _farben_in_den_feldern(pfad, vorlage_id):
    tpl = next(t for t in compose.BUILTIN_TEMPLATES if t["id"] == vorlage_id)
    with Image.open(pfad) as im:
        im = im.convert("RGB")
        assert list(im.size) == tpl["canvas"]
        return [im.getpixel((sl["x"] + sl["w"] // 2, sl["y"] + sl["h"] // 2)) for sl in tpl["slots"]]


def test_a_gif_run_of_six_prints_all_six(booth):
    s = _lauf(booth, "gif", shots=6)
    gesehen = _farben_in_den_feldern(s.print_file, "sechs")
    for farbe in FARBEN:
        assert any(_naehe(farbe, g) for g in gesehen), f"{farbe} fehlt auf dem Blatt"


def test_a_gif_run_of_four_prints_the_four_picture_grid(booth):
    s = _lauf(booth, "gif", shots=4)
    gesehen = _farben_in_den_feldern(s.print_file, "grid4")
    assert all(_naehe(f, g) for f, g in zip(FARBEN[:4], gesehen))


def test_landscape_pictures_go_on_the_landscape_sheet(booth):
    s = _lauf(booth, "gif", fmt="3:2", shots=6)
    gesehen = _farben_in_den_feldern(s.print_file, "sechs-quer")
    assert all(_naehe(f, g) for f, g in zip(FARBEN, gesehen))


@pytest.mark.parametrize("anzahl", range(2, 11))
def test_every_number_of_pictures_has_a_sheet(anzahl):
    for fmt, quer in (("4:5", False), ("3:2", True)):
        tpl, auswahl = compose.gif_print_template(fmt, anzahl)
        assert tpl["shots"] == len(auswahl) == min(anzahl, 6)
        assert (tpl["canvas"][0] > tpl["canvas"][1]) is quer
        assert tpl.get("paper", "10x15").startswith("10x15")
        assert auswahl == sorted(set(auswahl)) and auswahl[0] == 0 and auswahl[-1] == anzahl - 1


def test_a_photo_still_prints_on_the_single_picture_card(booth):
    s = _lauf(booth, "photo")
    with Image.open(s.print_file) as im:
        assert list(im.size) == compose.single_print_template("4:5", False)["canvas"]


def test_a_collage_still_prints_its_own_template(booth):
    s = _lauf(booth, "collage", vorlage="grid4")
    gesehen = _farben_in_den_feldern(s.print_file, "grid4")
    assert all(_naehe(f, g) for f, g in zip(FARBEN[:4], gesehen))
