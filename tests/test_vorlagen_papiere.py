"""One template design, several papers: aspect families, bleed, the two-up strip."""
import pytest
from PIL import Image

from app import vorlagen_kern, vorlagen_render
from app.vorlagen_kern import PAPIERE

#: What the editor offered before papers learned their families — must not move.
ALTE_PAPIERE = {
    "10x15": [1200, 1800], "10x15-quer": [1800, 1200], "streifen": [1200, 1800],
    "quadrat": [1200, 1200], "13x18": [1535, 2126], "13x18-quer": [2126, 1535],
}


def test_the_papers_that_existed_keep_their_names_and_canvases():
    for pid, canvas in ALTE_PAPIERE.items():
        assert PAPIERE[pid]["canvas"] == canvas
        assert PAPIERE[pid]["name"]
        assert PAPIERE[pid]["anschnitt_mm"] == 0      # the print of today does not change


@pytest.mark.parametrize("pid, canvas, familie", [
    ("streifen-2x6", [600, 1800], "1:3"),
    ("15x20", [1800, 2400], "3:4"),
    ("15x20-quer", [2400, 1800], "3:4"),
    ("15x23", [1800, 2700], "2:3"),
    ("11x20", [1350, 2400], "9:16"),
    ("20x25", [2400, 3000], "4:5"),
])
def test_the_event_printer_papers_exist_with_trim_size_family_and_margins(pid, canvas, familie):
    p = PAPIERE[pid]
    assert p["canvas"] == canvas
    assert p["familie"] == familie
    breite_mm, hoehe_mm = p["mm"]
    # The canvas is the trim size at 300 dpi.
    assert abs(breite_mm / 25.4 * 300 - canvas[0]) <= 2
    assert abs(hoehe_mm / 25.4 * 300 - canvas[1]) <= 2
    assert p["sicher_mm"] >= 4
    assert p["lage"] in ("hoch", "quer")


def test_every_paper_names_a_family_and_the_postcards_share_one():
    assert all(p.get("familie") for p in PAPIERE.values())
    assert PAPIERE["10x15"]["familie"] == PAPIERE["10x15-quer"]["familie"] == PAPIERE["15x23"]["familie"]


def test_a_saved_template_keeps_the_new_paper_and_its_canvas():
    v, hinweise = vorlagen_kern.pruefe({"name": "Streifen", "paper": "streifen-2x6",
                                       "slots": [{"x": 60, "y": 60, "w": 480, "h": 360}]})
    assert v["paper"] == "streifen-2x6" and v["canvas"] == [600, 1800]
    assert not hinweise


def _postkarte():
    v, _ = vorlagen_kern.pruefe({
        "name": "Karte", "paper": "10x15",
        "slots": [{"x": 96, "y": 96, "w": 1008, "h": 700, "radius": 0}],
        "texts": [{"x": 600, "y": 1500, "size": 60, "value": "{event_name}", "align": "center"}],
        "shapes": [{"id": "grund", "kind": "rect", "x": 0, "y": 0, "w": 1200, "h": 1800, "fill": "#faf8f3"}],
        "order": ["grund", "overlay", "slot-1", "text-1"],
    })
    return v


def test_a_postcard_design_fits_a_paper_of_its_family_without_redrawing():
    v = _postkarte()
    neu, hinweise = vorlagen_kern.fuer_papier(v, "15x23", anschnitt_mm=0)
    assert neu["paper"] == "15x23"
    assert neu["canvas"] == [1800, 2700]
    # Scaled by 1.5: the photo, the text and its size grow together.
    assert neu["slots"][0]["w"] == 1512 and neu["slots"][0]["x"] == 144
    assert neu["texts"][0]["size"] == 90 and neu["texts"][0]["x"] == 900
    assert v["canvas"] == [1200, 1800]               # the original is not touched
    # Without a bleed given, the paper's own (2 mm on 6×9) is added on every side.
    mit, _ = vorlagen_kern.fuer_papier(v, "15x23")
    assert mit["canvas"] == [1848, 2748]


def test_a_fitted_design_renders_at_the_size_of_the_paper_with_its_bleed():
    neu, _ = vorlagen_kern.fuer_papier(_postkarte(), "15x23")
    bild = vorlagen_render.render_template(neu, vorlagen_render.beispielbilder(1), texts={"event_name": "A & B"})
    assert bild.size == (1848, 2748)
    # The background reaches the outer edge of the bleed: no white line at the cut.
    assert bild.getpixel((0, 0)) == bild.getpixel((1847, 2747)) == (250, 248, 243)


def test_a_portrait_design_follows_the_paper_into_its_orientation_only_if_it_is_its_own():
    v = _postkarte()
    neu, _ = vorlagen_kern.fuer_papier(v, "10x15-quer")
    # Same family, other orientation: the design keeps its own orientation;
    # the printer turns the sheet, as it always did.
    assert neu["canvas"] == [1200, 1800]


def test_a_design_for_another_family_is_refused_with_a_reason():
    v = _postkarte()
    with pytest.raises(ValueError) as fehler:
        vorlagen_kern.fuer_papier(v, "15x20")
    text = str(fehler.value)
    assert "2:3" in text and "3:4" in text and "15×20" in text


def test_bleed_widens_the_sheet_and_carries_edge_elements_into_it():
    v = _postkarte()
    neu, _ = vorlagen_kern.fuer_papier(v, "10x15", anschnitt_mm=2.0)
    b = round(2.0 / 25.4 * 300)                      # 24 px
    assert neu["canvas"] == [1200 + 2 * b, 1800 + 2 * b]
    grund = next(f for f in neu["shapes"] if f["id"] == "grund")
    assert (grund["x"], grund["y"], grund["w"], grund["h"]) == (0, 0, 1200 + 2 * b, 1800 + 2 * b)
    # Things inside the sheet only move by the bleed.
    assert neu["slots"][0]["x"] == 96 + b and neu["slots"][0]["w"] == 1008


def test_a_template_without_paper_is_placed_in_a_family_by_its_canvas():
    assert vorlagen_kern.familie_von({"canvas": [1200, 1800]}) == "2:3"
    assert vorlagen_kern.familie_von({"canvas": [600, 1800]}) == "1:3"
    assert vorlagen_kern.familie_von({"canvas": [1000, 1000]}) == "1:1"


def test_the_strip_is_printed_twice_side_by_side_with_the_cut_in_the_middle():
    streifen = Image.new("RGB", (600, 1800), "#ffffff")
    streifen.paste((200, 30, 30), (0, 0, 300, 1800))  # left half red, right half white
    bogen = vorlagen_render.druckbogen(streifen, "streifen-2x6")
    assert bogen.size == (1200, 1800)
    assert bogen.getpixel((10, 900)) == (200, 30, 30)
    assert bogen.getpixel((610, 900)) == (200, 30, 30)      # second copy starts at the cut
    assert bogen.getpixel((590, 900)) == (255, 255, 255)
    assert bogen.crop((0, 0, 600, 1800)).tobytes() == bogen.crop((600, 0, 1200, 1800)).tobytes()


def test_papers_without_two_up_are_passed_through_untouched():
    bild = Image.new("RGB", (1200, 1800), "#123456")
    assert vorlagen_render.druckbogen(bild, "10x15") is bild
    assert vorlagen_render.druckbogen(bild, "streifen") is bild
    assert vorlagen_render.druckbogen(bild, None) is bild


def test_the_editor_offers_the_same_papers_as_the_check():
    import json
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "vorlagen-editor.js").read_text("utf-8")
    for pid, p in PAPIERE.items():
        treffer = re.search(r'"%s":\s*\{[^}]*canvas:\s*(\[[0-9, ]+\])' % re.escape(pid), js)
        assert treffer, pid
        assert json.loads(treffer.group(1)) == p["canvas"]
