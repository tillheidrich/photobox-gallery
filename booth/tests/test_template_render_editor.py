"""What the template editor shows, the booth prints.

Every property the editor offers (layer order, visibility, rotation, shapes,
pictures, opacity) is drawn by ``compose.render_template``. These tests
render a sheet and look at pixels, so a property the renderer ignores shows
up as a red test rather than as a surprised operator at the printer.
"""
import pytest
from PIL import Image

from boothcore.media import compose, vorlagen_kern

ROT, BLAU, GRUEN = (220, 30, 30), (30, 60, 220), (30, 200, 60)


def _shots(*farben):
    return [Image.new("RGB", (600, 400), f) for f in farben]


def _dunkel_in(bild, feld, grenze=200):
    """How many pixels in ``feld`` are dark (sum of RGB below ``grenze``)."""
    roh = bild.crop(feld).convert("RGB").tobytes()
    return sum(1 for i in range(0, len(roh), 3) if roh[i] + roh[i + 1] + roh[i + 2] < grenze)


def _nah(pixel, farbe, toleranz=40):
    return all(abs(int(a) - int(b)) <= toleranz for a, b in zip(pixel[:3], farbe))


def _vorlage(**extra):
    v = {"id": "t", "canvas": [1200, 1800], "background": "#ffffff",
         "slots": [{"x": 100, "y": 100, "w": 600, "h": 400, "shot": 0, "radius": 0},
                   {"x": 400, "y": 300, "w": 600, "h": 400, "shot": 1, "radius": 0}]}
    v.update(extra)
    return v


# ── E2: layers ────────────────────────────────────────────

def test_without_an_order_the_later_photo_lies_on_top_as_always():
    out = compose.render_template(_vorlage(), _shots(ROT, BLAU))
    assert _nah(out.getpixel((500, 400)), BLAU)


def test_the_layer_order_decides_which_photo_lies_on_top():
    v = _vorlage(order=["slot-2", "slot-1", "overlay"])
    out = compose.render_template(v, _shots(ROT, BLAU))
    assert _nah(out.getpixel((500, 400)), ROT)
    assert _nah(out.getpixel((900, 600)), BLAU), "the lower photo is still drawn where it is not covered"


def test_a_text_can_lie_under_a_photo():
    v = _vorlage(texts=[{"id": "gruss", "x": 450, "y": 350, "size": 120, "value": "MMMM", "color": "#000000"}])
    oben = compose.render_template(v, _shots(ROT, BLAU))
    v["order"] = ["gruss", "slot-1", "slot-2", "overlay"]
    unten = compose.render_template(v, _shots(ROT, BLAU))
    feld = (420, 320, 980, 680)                      # inside the blue photo
    dunkel = lambda im: _dunkel_in(im, feld, 150)
    assert dunkel(oben) > 500, "on top the text is visible"
    assert dunkel(unten) == 0, "under the photo nothing of it shows"


def test_a_hidden_layer_is_not_printed():
    v = _vorlage()
    v["slots"][1]["hidden"] = True
    out = compose.render_template(v, _shots(ROT, BLAU))
    assert _nah(out.getpixel((500, 400)), ROT)
    assert _nah(out.getpixel((900, 600)), (255, 255, 255))


def test_the_default_order_draws_exactly_what_the_old_renderer_drew():
    """Saved templates gain ids and an order; the sheet must not change by a pixel."""
    stil = {"preset": "hochzeit"}
    for t in compose.BUILTIN_TEMPLATES:
        ohne = compose.render_template(t, _shots(ROT, BLAU, GRUEN), qr_url="https://x.test/a",
                                       texts={"event_name": "Mia", "date": "1.1."}, style_cfg=stil)
        mit = dict(t, order=[e[1] for e in vorlagen_kern.ebenen(t)])
        neu = compose.render_template(mit, _shots(ROT, BLAU, GRUEN), qr_url="https://x.test/a",
                                      texts={"event_name": "Mia", "date": "1.1."}, style_cfg=stil)
        assert ohne.tobytes() == neu.tobytes(), t["id"]


def test_an_order_naming_unknown_layers_still_draws_everything():
    v = _vorlage(order=["gibt-es-nicht", "slot-2"])
    out = compose.render_template(v, _shots(ROT, BLAU))
    assert _nah(out.getpixel((200, 200)), ROT), "a layer the order forgot is still printed"


# ── E3: rotation ──────────────────────────────────────────

def _halb(oben, unten, w=600, h=400):
    """A photo whose top half and bottom half differ — shows which way is up."""
    bild = Image.new("RGB", (w, h), unten)
    bild.paste(Image.new("RGB", (w, h // 2), oben), (0, 0))
    return bild


def test_a_turned_photo_slot_turns_about_its_centre():
    # 600×200 at (300, 800): centre (600, 900). Turned by 90° it stands upright.
    v = {"canvas": [1200, 1800], "background": "#ffffff",
         "slots": [{"x": 300, "y": 800, "w": 600, "h": 200, "radius": 0, "rotation": 90, "fit": "beschneiden"}]}
    out = compose.render_template(v, [Image.new("RGB", (900, 300), ROT)])
    assert _nah(out.getpixel((600, 650)), ROT), "now it reaches up ..."
    assert _nah(out.getpixel((600, 1150)), ROT), "... and down"
    assert _nah(out.getpixel((380, 900)), (255, 255, 255)), "and no longer sideways"


def test_the_photo_is_cut_inside_the_turned_slot_and_turns_with_it():
    """Top of the picture follows the top edge of the slot: 90° clockwise puts it on the right."""
    v = {"canvas": [1200, 1800], "background": "#ffffff",
         "slots": [{"x": 300, "y": 700, "w": 600, "h": 400, "radius": 0, "rotation": 90}]}
    out = compose.render_template(v, [_halb(ROT, BLAU)])
    assert _nah(out.getpixel((750, 900)), ROT)
    assert _nah(out.getpixel((450, 900)), BLAU)


def test_a_turned_text_turns_about_its_middle():
    v = {"canvas": [1200, 1800], "background": "#ffffff", "slots": [],
         "texts": [{"x": 600, "y": 860, "size": 80, "align": "center", "value": "MMMMMMMMMM",
                    "color": "#000000", "rotation": 90}]}
    out = compose.render_template(v, [])
    dunkel = lambda feld: _dunkel_in(out, feld)
    assert dunkel((550, 560, 650, 1260)) > 2000, "the word now runs down the sheet"
    assert dunkel((200, 860, 540, 950)) == 0, "and no longer across, left of its middle"
    assert dunkel((660, 860, 1000, 950)) == 0, "nor right of it"


def test_the_qr_code_turns_too():
    v = {"canvas": [1200, 1800], "background": "#808080", "slots": [],
         "qr": {"x": 400, "y": 400, "size": 400, "rotation": 45}}
    out = compose.render_template(v, [], qr_url="https://x.test/q")
    assert _nah(out.getpixel((402, 402)), (128, 128, 128), 10), "the corner of the box is empty when turned"
    assert out.getpixel((600, 335))[:3] != (128, 128, 128), "the turned corner reaches above the box"


def test_no_rotation_draws_like_before():
    v = _vorlage()
    gedreht = _vorlage()
    for s in gedreht["slots"]:
        s["rotation"] = 0
    assert compose.render_template(v, _shots(ROT, BLAU)).tobytes() == \
        compose.render_template(gedreht, _shots(ROT, BLAU)).tobytes()


# ── E5: shapes, pictures, opacity, fonts, line spacing ────

def _leer(**extra):
    v = {"canvas": [1200, 1800], "background": "#ffffff", "slots": []}
    v.update(extra)
    return v


def test_a_rectangle_is_drawn_with_fill_and_outline():
    v = _leer(shapes=[{"id": "r", "kind": "rect", "x": 100, "y": 100, "w": 400, "h": 200,
                       "fill": "#1e3cdc", "stroke": "#000000", "stroke_width": 10}])
    out = compose.render_template(v, [])
    assert _nah(out.getpixel((300, 200)), BLAU)
    assert _nah(out.getpixel((104, 200)), (0, 0, 0), 20), "the outline lies inside the box"
    assert _nah(out.getpixel((95, 200)), (255, 255, 255), 5), "and not outside it"


def test_rounded_corners_leave_the_corner_empty():
    v = _leer(shapes=[{"kind": "rect", "x": 100, "y": 100, "w": 400, "h": 200, "fill": "#1e3cdc", "radius": 80}])
    out = compose.render_template(v, [])
    assert _nah(out.getpixel((104, 104)), (255, 255, 255), 5)
    assert _nah(out.getpixel((300, 104)), BLAU)


def test_an_ellipse_fills_its_box_but_not_the_corners():
    v = _leer(shapes=[{"kind": "ellipse", "x": 100, "y": 100, "w": 400, "h": 400, "fill": "@akzent"}])
    out = compose.render_template(v, [], style_cfg={"preset": "clean"})
    assert _nah(out.getpixel((300, 300)), (0xE7, 0xAD, 0x59), 5), "tokens work for shapes too"
    assert _nah(out.getpixel((120, 120)), (255, 255, 255), 5)


def test_a_line_runs_through_the_middle_of_its_box():
    v = _leer(shapes=[{"kind": "line", "x": 100, "y": 500, "w": 600, "h": 40, "stroke": "#000000", "stroke_width": 8}])
    out = compose.render_template(v, [])
    assert _nah(out.getpixel((400, 520)), (0, 0, 0), 30)
    assert _nah(out.getpixel((400, 505)), (255, 255, 255), 5)


def test_opacity_lets_the_sheet_shine_through():
    v = _leer(shapes=[{"kind": "rect", "x": 100, "y": 100, "w": 400, "h": 400, "fill": "#000000", "opacity": 50}])
    r, g, b = compose.render_template(v, []).getpixel((300, 300))[:3]
    assert 118 <= r <= 138 and r == g == b


def test_a_picture_from_the_media_is_placed_and_fitted(tmp_path):
    logo = Image.new("RGBA", (200, 100), (30, 60, 220, 255))
    logo.save(tmp_path / "logo.png")
    v = _leer(images=[{"id": "logo", "file": "logo.png", "x": 100, "y": 100, "w": 400, "h": 400}])
    out = compose.render_template(v, [], assets_dir=tmp_path)
    assert _nah(out.getpixel((300, 300)), BLAU), "fitted into the box, centred"
    assert _nah(out.getpixel((300, 120)), (255, 255, 255), 5), "keeps its proportions: 2:1 in a square box"


def test_a_missing_picture_costs_nothing(tmp_path):
    v = _leer(images=[{"file": "gibt-es-nicht.png", "x": 100, "y": 100, "w": 400, "h": 400}])
    out = compose.render_template(v, [], assets_dir=tmp_path)
    assert _nah(out.getpixel((300, 300)), (255, 255, 255), 5)


def test_a_picture_with_half_opacity(tmp_path):
    Image.new("RGB", (100, 100), (0, 0, 0)).save(tmp_path / "schwarz.png")
    v = _leer(images=[{"file": "schwarz.png", "x": 0, "y": 0, "w": 400, "h": 400, "opacity": 50}])
    r = compose.render_template(v, [], assets_dir=tmp_path).getpixel((200, 200))[0]
    assert 118 <= r <= 138


def test_text_uses_the_chosen_font_family():
    def breite(familie):
        v = _leer(texts=[{"x": 100, "y": 100, "size": 80, "value": "Hochzeit", "color": "#000000",
                          "font": familie, "bold": True}])
        out = compose.render_template(v, [])
        spalten = [x for x in range(0, 1200) if any(sum(out.getpixel((x, y))[:3]) < 200 for y in range(110, 190, 4))]
        return spalten[-1] - spalten[0]
    assert breite("system") != breite("display")


def test_line_spacing_sets_the_distance_between_lines():
    def zeilen(abstand):
        v = _leer(texts=[{"x": 100, "y": 100, "size": 60, "value": "MMM\nMMM", "color": "#000000",
                          "line_height": abstand}])
        out = compose.render_template(v, [])
        reihen = [y for y in range(0, 600) if any(sum(out.getpixel((x, y))[:3]) < 200 for x in range(100, 250, 3))]
        return reihen[-1] - reihen[0]
    assert zeilen(2.0) - zeilen(1.0) == pytest.approx(60, abs=3)


def test_an_uploaded_font_file_is_used(tmp_path):
    import shutil
    shutil.copy(compose.FONT_DIR / "Archivo-Bold.ttf", tmp_path / "hausschrift.ttf")
    eigen = compose.render_template(_leer(texts=[{"x": 100, "y": 100, "size": 80, "value": "Hochzeit",
                                                  "color": "#000000", "font": "hausschrift.ttf"}]),
                                    [], assets_dir=tmp_path)
    archivo = compose.render_template(_leer(texts=[{"x": 100, "y": 100, "size": 80, "value": "Hochzeit",
                                                    "color": "#000000", "font": "display", "bold": True}]),
                                      [], assets_dir=tmp_path)
    assert eigen.tobytes() == archivo.tobytes()
