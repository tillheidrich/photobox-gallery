"""The template model grows, old templates keep printing.

The editor, the MCP tools and a booth sending its own templates all go
through ``vorlagen.pruefe``. New properties are optional, their defaults give
the old behaviour, and the checks sit in that one place.
"""
import json

import pytest

from app import vorlagen
from tests.test_template_editor_ui import run


def _basis(**extra):
    v = {"name": "T", "paper": "10x15",
         "slots": [{"x": 60, "y": 60, "w": 500, "h": 500, "shot": 0},
                   {"x": 600, "y": 60, "w": 500, "h": 500, "shot": 1}],
         "texts": [{"value": "{headline}", "x": 60, "y": 1500, "size": 58}],
         "qr": {"x": 900, "y": 1500, "size": 200}}
    v.update(extra)
    return v


# ── E2: layers ────────────────────────────────────────────

def test_every_layer_gets_an_id_and_the_old_order_by_default():
    v, _ = vorlagen.pruefe(_basis())
    assert [s["id"] for s in v["slots"]] == ["slot-1", "slot-2"]
    assert v["texts"][0]["id"] == "text-1"
    assert v["order"] == ["slot-1", "slot-2", "overlay", "text-1", "qr"]


def test_a_chosen_order_is_kept_and_nonsense_in_it_is_dropped():
    v, _ = vorlagen.pruefe(_basis(order=["text-1", "nix", "slot-2", "slot-2", "qr", "slot-1"]))
    assert v["order"][:4] == ["text-1", "slot-2", "qr", "slot-1"]
    assert sorted(v["order"]) == sorted(["slot-1", "slot-2", "overlay", "text-1", "qr"])


def test_names_visibility_and_lock_survive_the_check():
    roh = _basis()
    roh["slots"][0].update(name="Großes Foto", hidden=True, locked=True)
    roh["qr"]["locked"] = True
    v, _ = vorlagen.pruefe(roh)
    s = v["slots"][0]
    assert (s["name"], s["hidden"], s["locked"]) == ("Großes Foto", True, True)
    assert v["qr"]["locked"] is True
    assert "hidden" not in v["slots"][1], "defaults are not written into the file"


def test_ids_are_unique_and_clean():
    roh = _basis()
    roh["slots"][0]["id"] = "Mein Feld!"
    roh["slots"][1]["id"] = "mein-feld-"
    v, _ = vorlagen.pruefe(roh)
    ids = [s["id"] for s in v["slots"]]
    assert len(set(ids)) == 2 and all(i == i.lower() and " " not in i for i in ids)


def test_the_editor_names_layers_like_the_check_does():
    """A template opened in the editor and saved again keeps its layer ids."""
    roh = _basis()
    roh["slots"][1]["id"] = "slot-1"           # a clash both sides must resolve alike
    out = run("var v = %s; sichereIds(v); out = v.slots.map(function(s){return s.id;})"
              ".concat(v.texts.map(function(t){return t.id;}));" % json.dumps(roh))
    v, _ = vorlagen.pruefe(roh)
    assert out == [s["id"] for s in v["slots"]] + [t["id"] for t in v["texts"]]


def test_the_editor_and_the_renderer_agree_on_the_default_order():
    roh = _basis()
    out = run("var v = %s; sichereIds(v); out = ebenenFolge(v);" % json.dumps(roh))
    v, _ = vorlagen.pruefe(roh)
    assert out == v["order"]


@pytest.mark.parametrize("schritt,erwartet", [
    ("nachVorn(o, 'slot-1')", ["slot-2", "slot-1", "overlay", "text-1", "qr"]),
    ("nachHinten(o, 'qr')", ["slot-1", "slot-2", "overlay", "qr", "text-1"]),
    ("ganzVorn(o, 'slot-1')", ["slot-2", "overlay", "text-1", "qr", "slot-1"]),
    ("ganzHinten(o, 'qr')", ["qr", "slot-1", "slot-2", "overlay", "text-1"]),
    ("verschiebe(o, 'text-1', 0)", ["text-1", "slot-1", "slot-2", "overlay", "qr"]),
    ("nachVorn(o, 'qr')", ["slot-1", "slot-2", "overlay", "text-1", "qr"]),
])
def test_layers_move_one_step_or_all_the_way(schritt, erwartet):
    out = run("var o = ['slot-1','slot-2','overlay','text-1','qr']; out = %s;" % schritt)
    assert out == erwartet


def test_the_mcp_tools_describe_the_new_fields():
    from app import mcp
    beschreibung = mcp._TEMPLATE_DESC
    for feld in ("order", "hidden"):
        assert feld in beschreibung


# ── E3: rotation ──────────────────────────────────────────

def test_rotation_is_kept_in_degrees_and_zero_stays_out_of_the_file():
    roh = _basis()
    roh["slots"][0]["rotation"] = -90
    roh["slots"][1]["rotation"] = 720
    roh["texts"][0]["rotation"] = 12.345
    roh["qr"]["rotation"] = "45"
    v, _ = vorlagen.pruefe(roh)
    assert v["slots"][0]["rotation"] == 270
    assert "rotation" not in v["slots"][1]
    assert v["texts"][0]["rotation"] == 12.3
    assert v["qr"]["rotation"] == 45


def test_nonsense_rotation_is_no_rotation():
    roh = _basis()
    roh["slots"][0]["rotation"] = "schief"
    v, _ = vorlagen.pruefe(roh)
    assert "rotation" not in v["slots"][0]


@pytest.mark.parametrize("roh,umschalt,erwartet", [
    (44.4, False, 44), (1.5, False, 0), (88.7, False, 90), (-30, False, 330),
    (30, True, 45), (66, True, 45), (68, True, 90), (359, True, 0),
])
def test_the_rotation_handle_snaps(roh, umschalt, erwartet):
    out = run("out = rasteWinkel(%s, %s);" % (roh, "true" if umschalt else "false"))
    assert out == erwartet


def test_resizing_a_turned_element_keeps_its_corner_in_place():
    """Dragging the corner of an element turned by 90° downwards makes it longer, not taller."""
    out = run("""
      var a = {x: 100, y: 100, w: 400, h: 200};
      var b = groesseGedreht(a, 0, 100, 90, 80);
      function ecke(o, winkel) {
        var r = winkel * Math.PI / 180, cx = o.x + o.w / 2, cy = o.y + o.h / 2;
        return [Math.round(cx + (-o.w / 2) * Math.cos(r) - (-o.h / 2) * Math.sin(r)),
                Math.round(cy + (-o.w / 2) * Math.sin(r) + (-o.h / 2) * Math.cos(r))];
      }
      out = {w: b.w, h: b.h, vorher: ecke(a, 90), nachher: ecke(b, 90)};
    """)
    assert (out["w"], out["h"]) == (500, 200)
    assert out["vorher"] == out["nachher"]


# ── E5: shapes, pictures, fonts ───────────────────────────

def test_shapes_and_pictures_are_checked_and_layered():
    roh = _basis(shapes=[{"kind": "rect", "x": 10, "y": 10, "w": 300, "h": 100, "fill": "@akzent",
                          "stroke": "rot", "stroke_width": 999, "radius": 20, "opacity": 140},
                         {"kind": "stern", "x": 0, "y": 0, "w": 10, "h": 10}],
                 images=[{"file": "logo.png", "x": 0, "y": 0, "w": 200, "h": 200, "opacity": 60},
                         {"file": "../../etc/passwd", "x": 0, "y": 0, "w": 10, "h": 10},
                         {"file": "https://fremd.example/bild.png", "x": 0, "y": 0, "w": 10, "h": 10}])
    v, hinweise = vorlagen.pruefe(roh)
    form = v["shapes"][0]
    assert form["kind"] == "rect" and form["fill"] == "@akzent" and form["stroke"] == ""
    assert form["stroke_width"] <= 200 and form["opacity"] == 100
    assert v["shapes"][1]["kind"] == "rect", "an unknown shape becomes a rectangle"
    assert [b["file"] for b in v["images"]] == ["logo.png"], "only plain file names of the media"
    assert v["images"][0]["opacity"] == 60
    assert hinweise
    assert v["order"] == ["slot-1", "slot-2", "overlay", "image-1", "shape-1", "shape-2", "text-1", "qr"]


def test_text_font_line_spacing_and_opacity_survive_the_check():
    roh = _basis()
    roh["texts"][0].update(font="display", line_height=1.4, opacity=80)
    roh["texts"].append({"value": "x", "font": "eigene.ttf", "line_height": 9})
    roh["texts"].append({"value": "y", "font": "../böse.ttf"})
    v, _ = vorlagen.pruefe(roh)
    t = v["texts"]
    assert (t[0]["font"], t[0]["line_height"], t[0]["opacity"]) == ("display", 1.4, 80)
    assert t[1]["font"] == "eigene.ttf" and t[1]["line_height"] == 3
    assert "font" not in t[2]


def test_a_template_names_its_pictures_and_fonts_so_they_travel():
    from app import vorlagen_paket
    v, _ = vorlagen.pruefe(_basis(images=[{"file": "logo.png", "x": 0, "y": 0, "w": 100, "h": 100}],
                                  texts=[{"value": "a", "font": "haus.otf"}]))
    assert {"logo.png", "haus.otf"} <= set(vorlagen_paket.medien_von(v))


def test_the_editor_names_new_kinds_like_the_check():
    roh = _basis(shapes=[{"kind": "rect", "x": 0, "y": 0, "w": 10, "h": 10}],
                 images=[{"file": "a.png", "x": 0, "y": 0, "w": 10, "h": 10}])
    out = run("var v = %s; sichereIds(v); out = ebenenFolge(v);" % json.dumps(roh))
    v, _ = vorlagen.pruefe(roh)
    assert out == v["order"]


def test_a_box_gets_the_pictures_its_templates_use(client):
    from app import db, ecosystem
    from app.blueprints.admin_booth import _assets_dir
    import secrets
    client.get("/healthz")
    ecosystem.init_schema()
    ordner = _assets_dir()
    ordner.mkdir(parents=True, exist_ok=True)
    name = f"logo-{secrets.token_hex(3)}.png"
    (ordner / name).write_bytes(b"\x89PNG" + b"x" * 40)
    ecosystem.add_asset("overlay", name, name, "b" * 64, 44)
    v, _ = vorlagen.pruefe(_basis(name="Mit Logo " + secrets.token_hex(2),
                                  images=[{"file": name, "x": 0, "y": 0, "w": 100, "h": 100}]))
    eintrag = vorlagen.speichern(v, ordner)
    dev = db.create_device(name="Logo-Box " + secrets.token_hex(2))
    ecosystem.save_device_profile(dev["id"], {"collage_templates": [eintrag["file"]]}, None)
    profil = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://example.test")
    assert any(a["file"] == name for a in ecosystem.assets_for_profile(profil))


def test_the_editor_offers_the_uploaded_pictures_and_fonts(client):
    import re
    import secrets
    from app import ecosystem
    from app.blueprints.admin_booth import _assets_dir
    client.get("/healthz")
    ecosystem.init_schema()
    bild, schrift = f"bild-{secrets.token_hex(3)}.png", f"schrift-{secrets.token_hex(3)}.ttf"
    for name, art in ((bild, "overlay"), (schrift, "font")):
        (_assets_dir() / name).write_bytes(b"x" * 20)
        ecosystem.add_asset(art, name, name, "c" * 64, 20)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
    seite = client.get("/admin/vorlagen/editor").get_data(as_text=True)
    daten = json.loads(re.search(r'id="pbEditorDaten">(.*?)</script>', seite, re.S).group(1))
    assert any(m["file"] == bild and m["url"].endswith(bild) for m in daten["medien"])
    assert any(f["file"] == schrift for f in daten["schriften"])


# ── E6: safe area ─────────────────────────────────────────

def test_the_safe_margin_is_kept_and_bounded():
    assert vorlagen.pruefe(_basis(safe_area=4.25))[0]["safe_area"] == 4.2
    assert vorlagen.pruefe(_basis(safe_area=80))[0]["safe_area"] == 15
    assert "safe_area" not in vorlagen.pruefe(_basis())[0], "without it the editor's default of 3 % applies"
