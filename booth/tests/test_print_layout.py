"""Papier und Format: was wirklich aus dem Drucker kommt.

Der Streifendruck ist die Stelle, an der Fotoboxen sich unterscheiden. Drucker
können keine 5x15-Streifen; es entsteht ein 10x15-Blatt mit zwei Streifen, das
der Drucker in der Mitte schneidet.
"""
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from boothcore.printing.layout import Layout, compose_sheet, describe, sheet_size_px


@pytest.fixture()
def shot(tmp_path):
    def make(width=1200, height=1800, color=(200, 60, 60)):
        p = tmp_path / f"shot-{width}x{height}.jpg"
        Image.new("RGB", (width, height), color).save(p)
        return p
    return make


def test_a_strip_sheet_holds_two_strips_side_by_side(shot, tmp_path):
    layout = Layout.from_profile({"paper": "5x15", "per_sheet": 2})
    out = compose_sheet(shot(1000, 3000), layout, tmp_path / "blatt.jpg")
    with Image.open(out) as im:
        # 10x15 hochkant bei 300 dpi: zwei hochkante Streifen nebeneinander
        assert im.size == (1200, 1800)
        # Beide Hälften zeigen dasselbe: der Schnitt in der Mitte ergibt zwei gleiche Streifen.
        links = im.crop((0, 0, 600, 1800)).resize((40, 120))
        rechts = im.crop((600, 0, 1200, 1800)).resize((40, 120))
        # JPEG rechnet nicht auf das Pixel genau; ein paar Stufen Unterschied sind normal.
        l, r = links.convert("RGB").tobytes(), rechts.convert("RGB").tobytes()
        assert max(abs(a - b) for a, b in zip(l, r)) <= 6


def test_each_strip_cell_has_the_shape_of_a_strip(shot, tmp_path):
    """Sonst wird aus einem vierteiligen Streifen ein beschnittener Zweiteiler.

    Genau das war der erste Anlauf: zwei Zellen im Querformat, in die ein
    hochkanter Streifen nicht passt. Auf dem Bildschirm sah es plausibel aus,
    auf dem Papier fehlte die Hälfte.
    """
    from boothcore.printing.layout import PAPERS

    layout = Layout.from_profile({"paper": "5x15", "per_sheet": 2})
    w, h = sheet_size_px(layout, portrait=True)
    cell_ratio = h / (w / 2)
    strip_ratio = PAPERS["5x15"][0] / PAPERS["5x15"][1]
    assert abs(cell_ratio - strip_ratio) < 0.02, (cell_ratio, strip_ratio)


def test_the_printer_stays_on_the_bigger_paper(shot, tmp_path):
    """Die Box baut das Blatt; der Drucker bleibt auf 10x15 eingestellt."""
    layout = Layout.from_profile({"paper": "5x15", "per_sheet": 2})
    assert layout.sheet_paper == "10x15"
    assert "10x15" in describe(layout) and "schneiden" in describe(layout)


def test_portrait_shots_turn_the_paper_not_the_picture(shot, tmp_path):
    layout = Layout.from_profile({"paper": "10x15"})
    out = compose_sheet(shot(1200, 1800), layout, tmp_path / "hoch.jpg")
    with Image.open(out) as im:
        assert im.size == (1200, 1800)          # Papier steht hochkant

    out = compose_sheet(shot(1800, 1200), layout, tmp_path / "quer.jpg")
    with Image.open(out) as im:
        assert im.size == (1800, 1200)


def test_contain_keeps_everything_and_adds_a_border(shot, tmp_path):
    layout = Layout.from_profile({"paper": "10x15", "fit": "contain", "background": "#ffffff"})
    out = compose_sheet(shot(1000, 1000, (10, 10, 10)), layout, tmp_path / "rand.jpg")
    with Image.open(out) as im:
        assert im.getpixel((5, 5)) == (255, 255, 255)        # Rand ist weiß
        assert im.getpixel((im.width // 2, im.height // 2))[0] < 60   # Bild in der Mitte


def test_cover_fills_the_sheet_completely(shot, tmp_path):
    layout = Layout.from_profile({"paper": "10x15", "fit": "cover"})
    out = compose_sheet(shot(1000, 1000, (10, 10, 10)), layout, tmp_path / "voll.jpg")
    with Image.open(out) as im:
        assert im.getpixel((5, 5))[0] < 60                   # kein weißer Rand


def test_margins_are_kept_in_millimetres(shot, tmp_path):
    layout = Layout.from_profile({"paper": "10x15", "margin_mm": 5, "dpi": 300, "background": "#ffffff"})
    out = compose_sheet(shot(1800, 1200, (10, 10, 10)), layout, tmp_path / "margin.jpg")
    with Image.open(out) as im:
        # 5 mm bei 300 dpi sind rund 59 Pixel
        assert im.getpixel((20, 20)) == (255, 255, 255)
        assert im.getpixel((im.width // 2, im.height // 2))[0] < 60


def test_every_paper_size_produces_a_plausible_sheet():
    from boothcore.printing.layout import PAPERS
    for paper in PAPERS:
        layout = Layout.from_profile({"paper": paper})
        w, h = sheet_size_px(layout, portrait=False)
        assert 300 < w < 3000 and 300 < h < 2500, (paper, w, h)


def test_nonsense_falls_back_to_the_standard():
    layout = Layout.from_profile({"paper": "din-a4", "per_sheet": 7, "fit": "quetschen", "margin_mm": 500})
    assert layout.paper == "10x15" and layout.per_sheet == 1
    assert layout.fit == "cover" and layout.margin_mm == 20


def test_the_booth_only_rebuilds_the_sheet_when_it_has_to(tmp_path, monkeypatch):
    """Standardformat ohne Rand: die Aufnahme wird nicht neu aufgebaut.

    Seit dem Nachschärfen entsteht in diesem Fall zwar eine zweite Datei —
    aber kein neues Blatt. Für diesen Test steht das Schärfen deshalb aus.
    """
    from boothcore.app import Booth

    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    booth = Booth(base=tmp_path, start_threads=False)
    booth.store.profile["print"] = {**booth.profile["print"], "sharpen": "off"}
    try:
        src = tmp_path / "bild.jpg"
        Image.new("RGB", (1800, 1200), (30, 30, 30)).save(src)
        assert booth.sheet_for(src) == src

        booth.store.profile["print_layout"].update({"paper": "5x15", "per_sheet": 2})
        sheet = booth.sheet_for(src)
        assert sheet != src and sheet.exists()
        with Image.open(sheet) as im:
            assert im.size == (1200, 1800)          # zwei Streifen, hochkantes Blatt
    finally:
        booth.mail.stop()


def test_a_broken_layout_still_prints_something(tmp_path, monkeypatch):
    """Lieber unpassend drucken als einen Gast ohne Bild stehen lassen."""
    from boothcore.app import Booth
    from boothcore.printing import layout as layout_mod

    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    booth = Booth(base=tmp_path, start_threads=False)
    booth.store.profile["print"] = {**booth.profile["print"], "sharpen": "off"}
    try:
        src = tmp_path / "bild.jpg"
        Image.new("RGB", (1800, 1200)).save(src)
        booth.store.profile["print_layout"].update({"paper": "5x15", "per_sheet": 2})
        monkeypatch.setattr(layout_mod, "compose_sheet",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("Platte voll")))
        assert booth.sheet_for(src) == src
    finally:
        booth.mail.stop()
