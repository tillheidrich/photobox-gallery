"""A line of text never runs off its field or the paper, and an absent QR code leaves nothing.

Seen on a print from the booth on 15.11.2026: a one-photo landscape sheet,
the couple's names in the narrow column beside the photo, and the second
name cut after four letters by the edge of the paper. A long line is now
set smaller (down to 60 %), then in two lines if the field is tall enough,
then smaller still — never cut, never shortened with "…".

The second sheet of that set had an empty pale square in its corner: the
overlay is one picture with a window punched out for the QR code, and the
booth was not paired, so there was no code to put into it.
"""
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

from app import vorlagen_kern, vorlagen_render as vr

NAMEN = {8: "Mia & Tom", 17: "Mara & Jonas Berg", 30: "Maximiliane & Jonas Oberwasser",
         45: "Maximiliane Wilhelmina & Jonas Bartholomäus K"}
BREIT = "WWMMWW & MMWWMM"


def _blatt(texts, **extra):
    v = {"id": "t", "paper": "10x15", "canvas": [1200, 1800], "background": "#ffffff", "slots": [],
         "texts": texts}
    v.update(extra)
    return v


def _tinte(bild, box):
    """Is anything but the white sheet inside ``box``?"""
    return ImageChops.difference(bild.crop(box).convert("RGB"),
                                 Image.new("RGB", (box[2] - box[0], box[3] - box[1]), "#ffffff")).getbbox()


@pytest.mark.parametrize("name", list(NAMEN.values()) + [BREIT])
def test_a_name_in_a_narrow_column_stays_inside_the_paper_margin(name):
    # The owner's sheet: a left-aligned name at 84 % of a landscape sheet.
    v = _blatt([{"x": 1510, "y": 120, "size": 46, "value": "{event_name}", "font": "serif", "color": "#000000"}],
               paper="10x15-quer", canvas=[1800, 1200])
    bild = vr.render_template(v, [], texts={"event_name": name})
    rand = vr.sicherer_rand(v)
    assert rand == 47
    assert not _tinte(bild, (1800 - rand, 0, 1800, 1200)), "nothing may reach into the right margin"
    satz = vr.textsatz(v["texts"][0], vr.werte({"event_name": name}, vr.style()), vr.style(), v)
    # Every word is printed: nothing cut off, no ellipsis.
    assert " ".join(satz["zeilen"]).split() == name.split()
    assert "…" not in "".join(satz["zeilen"])
    assert satz["breite_gesetzt"] <= satz["platz"] + 0.5


def test_a_line_that_fits_is_drawn_exactly_as_before():
    t = {"x": 600, "y": 400, "size": 80, "value": "{event_name}", "align": "center", "color": "#000000"}
    bild = vr.render_template(_blatt([t]), [], texts={"event_name": "Mia & Tom"})
    erwartet = Image.new("RGB", (1200, 1800), "#ffffff")
    ImageDraw.Draw(erwartet).text((600, 400), "Mia & Tom", font=vr.load_font(80, True, "system"),
                                  fill="#000000", anchor="ma")
    assert ImageChops.difference(bild, erwartet).getbbox() is None


def test_a_line_slightly_too_long_is_set_smaller_but_not_below_sixty_percent():
    t = {"x": 600, "y": 400, "size": 120, "value": "{event_name}", "align": "center", "color": "#000000"}
    v = _blatt([t])
    st = vr.style()
    satz = vr.textsatz(t, vr.werte({"event_name": "Maximiliane & Jonas"}, st), st, v)
    assert satz["geaendert"] and satz["stufe"] == "kleiner"
    assert 72 <= satz["size"] < 120
    assert satz["zeilen"] == ["Maximiliane & Jonas"]
    assert satz["breite_gesetzt"] <= 1200 - 2 * 47


def test_a_long_name_goes_to_two_lines_when_the_field_is_tall_enough():
    t = {"x": 100, "y": 200, "size": 60, "value": "{event_name}", "color": "#000000", "w": 420, "h": 200}
    st = vr.style()
    satz = vr.textsatz(t, vr.werte({"event_name": NAMEN[30]}, st), st, _blatt([t]))
    assert satz["stufe"] == "zweizeilig"
    assert len(satz["zeilen"]) == 2 and satz["size"] >= 36
    assert satz["breite_gesetzt"] <= 420.5


def test_a_name_does_not_wrap_into_the_line_beneath_it():
    name = {"x": 100, "y": 200, "size": 60, "value": "{event_name}", "color": "#000000", "w": 420}
    datum = {"x": 100, "y": 270, "size": 30, "value": "{date}", "color": "#000000"}
    st = vr.style()
    satz = vr.textsatz(name, vr.werte({"event_name": NAMEN[30]}, st), st, _blatt([name, datum]))
    assert satz["feld"]["unten"] == 270, "the field ends where the date begins"
    a, d = satz["font"].getmetrics()
    assert 200 + (len(satz["zeilen"]) - 1) * satz["abstand"] + a + d <= 270, "the name stays above the date"
    assert satz["breite_gesetzt"] <= 420.5
    # Two lines would need 60 % of 60 px or more; that does not fit above the date.
    assert len(satz["zeilen"]) == 1 or satz["size"] < 36


def test_a_text_anchored_off_the_sheet_is_pulled_back_onto_it():
    t = {"x": 1190, "y": 300, "size": 50, "value": "Mara & Jonas", "color": "#000000"}
    bild = vr.render_template(_blatt([t]), [])
    assert not _tinte(bild, (1200 - 47, 0, 1200, 1800))
    assert _tinte(bild, (0, 300, 1200, 400)), "the line is still printed"


def test_a_line_never_runs_onto_the_qr_code_beside_it():
    t = {"x": 60, "y": 1600, "size": 54, "value": "{event_name}", "color": "#000000"}
    v = _blatt([t], qr={"x": 950, "y": 1560, "size": 200})
    st = vr.style()
    satz = vr.textsatz(t, vr.werte({"event_name": NAMEN[45]}, st), st, v)
    assert satz["feld"]["rechts"] <= 950 - 13
    assert 60 + satz["breite_gesetzt"] <= 950


def test_the_check_says_which_line_does_not_fit_and_how_wide_it_is():
    roh = {"name": "Schmal", "paper": "10x15-quer",
           "slots": [{"x": 40, "y": 120, "w": 1440, "h": 960}],
           "texts": [{"x": 1510, "y": 120, "size": 46, "value": "{event_name}", "font": "serif"}]}
    _v, hinweise = vorlagen_kern.pruefe(roh, texte={"event_name": "Mara & Jonas Berg"})
    meldung = [h for h in hinweise if h.startswith("Text passt nicht in sein Feld")]
    assert meldung, hinweise
    assert "Mara & Jonas Berg" in meldung[0] and " px breit, Platz sind 243 px" in meldung[0]
    _v, hinweise = vorlagen_kern.pruefe(roh, texte={"event_name": "Mia"})
    assert not [h for h in hinweise if h.startswith("Text passt nicht")]


def test_the_check_keeps_the_new_fields_and_a_paper_change_scales_them():
    roh = {"name": "Feld", "paper": "10x15",
           "texts": [{"x": 100, "y": 100, "size": 40, "value": "QR-Code scannen", "w": 400, "h": 120,
                      "nur_mit_qr": True}],
           "shapes": [{"kind": "rect", "x": 10, "y": 10, "w": 50, "h": 50, "fill": "#ffffff", "nur_mit_qr": True}],
           "qr": {"x": 900, "y": 1500, "size": 200}}
    v, _ = vorlagen_kern.pruefe(roh)
    t = v["texts"][0]
    assert (t["w"], t["h"], t["nur_mit_qr"]) == (400, 120, True)
    assert v["shapes"][0]["nur_mit_qr"] is True
    gross, _ = vorlagen_kern.fuer_papier(v, "15x23", anschnitt_mm=0)
    assert gross["texts"][0]["w"] == 600 and gross["texts"][0]["h"] == 180


# ── The QR code that is not there ─────────────────────────

def _gestanzt(tmp_path: Path):
    ov = Image.new("RGBA", (1200, 1800), (246, 241, 232, 255))
    d = ImageDraw.Draw(ov)
    d.rectangle((60, 60, 1140, 780), fill=(0, 0, 0, 0))
    d.rectangle((990, 1590, 1139, 1739), fill=(0, 0, 0, 0))          # the window for the code
    ov.save(tmp_path / "rahmen.png")
    return {"id": "gestanzt", "paper": "10x15", "canvas": [1200, 1800], "background": "#ffffff",
            "overlay": "rahmen.png", "slots": [{"x": 60, "y": 60, "w": 1080, "h": 720, "shot": 0}],
            "texts": [{"x": 60, "y": 1700, "size": 26, "value": "QR-Code scannen", "color": "#2b2723",
                       "bold": False, "nur_mit_qr": True},
                      {"x": 600, "y": 1300, "size": 60, "value": "{event_name}", "align": "center",
                       "color": "#2b2723"}],
            "qr": {"x": 990, "y": 1590, "size": 150}}


def test_without_a_link_the_qr_window_of_an_overlay_is_closed(tmp_path):
    v = _gestanzt(tmp_path)
    bild = vr.render_template(v, vr.beispielbilder(1), assets_dir=tmp_path, qr_url=None,
                              texts={"event_name": "Mara & Jonas"})
    fenster = bild.crop((990, 1590, 1140, 1740))
    assert fenster.getcolors() == [(150 * 150, (246, 241, 232))], "no pale box where the code would be"
    # The caption that belongs to the code is gone with it; the name stays.
    assert bild.crop((60, 1700, 400, 1740)).getcolors() == [(340 * 40, (246, 241, 232))]


def test_with_a_link_the_code_and_its_caption_are_printed(tmp_path):
    v = _gestanzt(tmp_path)
    bild = vr.render_template(v, vr.beispielbilder(1), assets_dir=tmp_path, qr_url="https://example.invalid/x",
                              texts={"event_name": "Mara & Jonas"})
    fenster = bild.crop((990, 1590, 1140, 1740)).convert("L")
    assert fenster.getextrema() == (0, 255), "a black and white code"
    assert len(bild.crop((60, 1700, 400, 1740)).getcolors(100000)) > 1


def test_a_hidden_qr_layer_counts_as_no_code():
    assert not vr.qr_gedruckt({"qr": {"x": 0, "y": 0, "size": 100, "hidden": True}}, "https://x.invalid")
    assert not vr.qr_gedruckt({"qr": {"x": 0, "y": 0, "size": 100}}, None)
    assert not vr.qr_gedruckt({}, "https://x.invalid")
    assert vr.qr_gedruckt({"qr": {"x": 0, "y": 0, "size": 100}}, "https://x.invalid")
