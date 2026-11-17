"""Brightness, contrast and saturation per printer.

Sublimation printers often print too dark; the big booth programs have a
slider per printer for it. Here: three numbers per printer entry (-50 … +50,
0 = off), applied to the copy that goes to the printer — never to the saved
picture or the gallery's — and read live, so a remote change at night is in
the next print without a restart.
"""
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from boothcore.config import REMOTE_LOCAL_KEYS, clean_remote_local, nest_remote_local
from boothcore.printing import korrektur
from boothcore.printing.service import FolderPrinter, PrintJob, PrintService


def _bild(pfad: Path, grau: int = 110) -> Path:
    img = Image.new("RGB", (300, 200), (grau, grau - 20, grau + 20))
    for x in range(0, 300, 10):
        for y in range(200):
            img.putpixel((x, y), (200, 180, 160))
    img.save(pfad, "JPEG", quality=92)
    return pfad


def _drucke(tmp_path, cfg, quelle):
    out = tmp_path / "out"
    svc = PrintService([FolderPrinter(out)], tmp_path / "counter.txt", options=[cfg],
                       korrektur_fuer=lambda i: cfg)
    svc.submit(PrintJob(quelle, 1, "s1"))
    import time
    for _ in range(100):
        files = list(out.glob("*.jpg"))
        if files and svc.pending == 0:
            return files[0]
        time.sleep(0.05)
    raise AssertionError("nothing printed")


def _hell(p: Path) -> float:
    with Image.open(p) as im:
        return sum(ImageStat.Stat(im.convert("L")).mean)


def test_zero_sends_the_file_byte_for_byte(tmp_path):
    quelle = _bild(tmp_path / "a.jpg")
    out = _drucke(tmp_path, {"brightness": 0, "contrast": 0, "saturation": 0}, quelle)
    assert out.read_bytes() == quelle.read_bytes()


def test_no_setting_at_all_is_the_same_as_zero(tmp_path):
    quelle = _bild(tmp_path / "a.jpg")
    assert _drucke(tmp_path, {}, quelle).read_bytes() == quelle.read_bytes()


def test_plus_twenty_brightness_prints_brighter_and_leaves_the_original(tmp_path):
    quelle = _bild(tmp_path / "a.jpg")
    vorher = quelle.read_bytes()
    out = _drucke(tmp_path, {"brightness": 20}, quelle)
    assert _hell(out) > _hell(quelle) + 5
    assert quelle.read_bytes() == vorher, "the saved picture must stay untouched"
    assert not list((tmp_path / "druck-korrektur").glob("*")), "the corrected copy is removed after printing"


def test_contrast_and_saturation_change_the_print(tmp_path):
    quelle = _bild(tmp_path / "a.jpg")
    out = _drucke(tmp_path, {"contrast": 30, "saturation": -40}, quelle)
    assert out.read_bytes() != quelle.read_bytes()


@pytest.mark.parametrize("roh,erwartet", [
    ({"brightness": 80}, 50), ({"brightness": -999}, -50), ({"brightness": "12"}, 12),
    ({"brightness": "viel"}, 0), ({"brightness": None}, 0), ({"brightness": True}, 0),
])
def test_values_stay_within_their_limits(roh, erwartet):
    assert korrektur.werte(roh)["brightness"] == erwartet


def test_the_remote_list_allows_the_three_numbers_within_tight_limits():
    for slot in (1, 2):
        for name in ("brightness", "contrast", "saturation"):
            assert REMOTE_LOCAL_KEYS[f"printers.{slot}.{name}"] == ("int", -50, 50)
    ok, refused = clean_remote_local({"printers.1.brightness": 15, "printers.1.contrast": 51})
    assert ok == {"printers.1.brightness": 15} and refused


def test_a_remote_change_lands_on_the_right_printer_entry():
    local = {"printer": {"backend": "auto"}, "printers": [{"label": "links"}, {"label": "rechts"}]}
    patch = nest_remote_local(local, {"printers.2.brightness": 10})
    assert patch["printers"][1] == {"label": "rechts", "brightness": 10}
    assert patch["printers"][0] == {"label": "links"}
    # Without a printer list, slot 1 is the single printer section.
    patch = nest_remote_local({"printer": {"backend": "auto"}}, {"printers.1.contrast": -5})
    assert patch == {"printer": {"backend": "auto", "contrast": -5}}


def test_the_booth_applies_a_remote_change_to_the_next_print_without_restart(tmp_path):
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    antwort = b.remote_local_config({"printers.1.brightness": 25})
    assert "printers.1.brightness" in antwort
    assert korrektur.werte(b._druckkorrektur(0))["brightness"] == 25


def test_calibration_page_goes_through_the_correction(tmp_path):
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.TESTDRUCK_WARTEN = 10
    b.test_print({})
    ohne = sorted((b.data / "print-out").glob("*.jpg"))[-1]
    b.store.save_local({"printer": {**b.store.local["printer"], "brightness": 30}})
    b.test_print({})
    mit = sorted((b.data / "print-out").glob("*.jpg"))[-1]
    assert _hell(mit) > _hell(ohne)
