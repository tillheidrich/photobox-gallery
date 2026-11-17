"""A script line may set its "&" in a plainer font.

The swash ampersand of a calligraphic script reads like "Et" on a print. A
text element can name ``amp_font``: the ampersand of that line is set in
that font (a shipped family or an uploaded TTF/OTF), on the same baseline,
everything else stays in the line's own font. A renderer that does not know
the key simply ignores it and prints the script's own ampersand.
"""
from PIL import Image, ImageChops

from app import vorlagen_kern, vorlagen_paket, vorlagen_render


def _vorlage(**extra):
    text = {"x": 600, "y": 400, "size": 120, "value": "{event_name}", "align": "center", "font": "system",
            "bold": False, "color": "#000000"}
    text.update(extra)
    v, _ = vorlagen_kern.pruefe({"name": "Et", "paper": "10x15",
                                "slots": [{"x": 100, "y": 1000, "w": 1000, "h": 700}],
                                "texts": [text]})
    return v


def _render(v, name="Mia & Tom"):
    return vorlagen_render.render_template(v, [], texts={"event_name": name},
                                           style_cfg={"preset": "hell"})


def test_the_check_keeps_a_known_ampersand_font_and_drops_anything_else():
    assert _vorlage(amp_font="display")["texts"][0]["amp_font"] == "display"
    assert _vorlage(amp_font="DMSerifDisplay-Regular.ttf")["texts"][0]["amp_font"] == "DMSerifDisplay-Regular.ttf"
    assert "amp_font" not in _vorlage(amp_font="../../etc/passwd")["texts"][0]
    assert "amp_font" not in _vorlage(amp_font="Comic.exe")["texts"][0]


def test_only_the_ampersand_changes_font():
    mit, ohne = _render(_vorlage(amp_font="display")), _render(_vorlage())
    unterschied = ImageChops.difference(mit, ohne).getbbox()
    assert unterschied is not None                       # the ampersand looks different
    # A line without an ampersand is drawn exactly as before.
    assert ImageChops.difference(_render(_vorlage(amp_font="display"), "Mia"),
                                 _render(_vorlage(), "Mia")).getbbox() is None


def test_the_line_stays_centred_on_its_anchor():
    bild = _render(_vorlage(amp_font="display"))
    grau = bild.convert("L").point(lambda p: 255 if p < 128 else 0)
    links, oben, rechts, unten = grau.getbbox()
    assert abs((links + rechts) / 2 - 600) <= 6


def test_a_package_carries_the_uploaded_ampersand_font():
    v = _vorlage(font="PinyonScript-Regular.ttf", amp_font="DMSerifDisplay-Regular.ttf")
    assert "DMSerifDisplay-Regular.ttf" in vorlagen_paket.medien_von(v)
