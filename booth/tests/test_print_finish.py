"""Was zwischen Ergebnis und Abzug passiert.

Zwei kleine Dinge, die auf dem Papier den Unterschied machen:

* **Nachschärfen.** Thermosublimation weicht Kanten auf; was am Bildschirm
  scharf ist, wirkt auf dem Abzug weich. dslrBooth nennt das „Druckschärfen"
  und hat es an. Zu viel davon sieht nach Handykamera aus.
* **Zifferntasten.** Am Tresen braucht der Betreiber drei Kopien und soll
  nicht dreimal auf Plus tippen.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-print-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()


def _bild(pfad: Path) -> Path:
    Image.new("RGB", (1200, 1800), (120, 90, 70)).save(pfad, "JPEG", quality=95)
    return pfad


def test_sharpening_is_on_by_default_and_makes_a_new_file(booth, tmp_path):
    quelle = _bild(tmp_path / "print.jpg")
    blatt = booth.sheet_for(quelle)
    assert blatt != quelle and blatt.exists()
    assert "scharf" in blatt.name


def test_switching_it_off_prints_the_file_as_it_is(booth, tmp_path):
    booth.store.profile["print"] = {**booth.profile["print"], "sharpen": "off"}
    quelle = _bild(tmp_path / "print.jpg")
    assert booth.sheet_for(quelle) == quelle


def test_every_level_produces_something_printable(booth, tmp_path):
    for stufe in ("light", "medium", "strong"):
        booth.store.profile["print"] = {**booth.profile["print"], "sharpen": stufe}
        quelle = _bild(tmp_path / f"print-{stufe}.jpg")
        blatt = booth.sheet_for(quelle)
        with Image.open(blatt) as im:
            assert im.size == (1200, 1800), stufe


def test_a_broken_file_is_printed_unsharpened_rather_than_not_at_all(booth, tmp_path):
    kaputt = tmp_path / "kaputt.jpg"
    kaputt.write_bytes(b"kein bild")
    assert booth.sheet_for(kaputt) == kaputt


def test_the_setting_is_part_of_the_profile(booth):
    assert booth.profile["print"]["sharpen"] == "light"
    assert booth.profile["print"]["number_keys"] is True


def test_the_smile_picture_is_off_until_somebody_sets_one(booth):
    assert booth.profile["countdown"]["smile_media"] == ""
    assert 200 <= booth.profile["countdown"]["smile_ms"] <= 3000
