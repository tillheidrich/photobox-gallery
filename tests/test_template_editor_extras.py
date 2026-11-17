"""Template editor extras: duplicate a template, one template as a package
from the editor, and fonts that travel inside a package.

Fonts used to be packed (``medien_von`` names them) but thrown away when the
package was read. Now TTF/OTF come along, checked by their first bytes like
nothing else that executes could, and with a size limit of their own.
"""
import io
import json

import pytest

from app import vorlagen_paket

TTF = b"\x00\x01\x00\x00" + b"\x00" * 60
OTF = b"OTTO" + b"\x00" * 60


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    return client


def _saved(admin, name="Original"):
    r = admin.post("/admin/vorlagen/speichern", headers={"X-CSRF-Token": "test-token"},
                   json={"name": name, "slots": [{"x": 60, "y": 60, "w": 1000, "h": 1200}]})
    return r.get_json()["asset"]["file"]


def test_a_template_can_be_duplicated(admin):
    from app import vorlagen
    from app.blueprints.admin_booth import _assets_dir
    datei = _saved(admin)
    r = admin.post("/admin/vorlagen/duplizieren", json={"datei": datei}, headers={"X-CSRF-Token": "test-token"})
    assert r.status_code == 200, r.get_data(as_text=True)
    neu = r.get_json()["asset"]["file"]
    assert neu != datei
    namen = {e["asset"]["file"]: e["template"]["name"] for e in vorlagen.eigene(_assets_dir())}
    assert namen[datei] == "Original" and namen[neu] == "Original (Kopie)"
    # A second copy does not overwrite the first one.
    zweite = admin.post("/admin/vorlagen/duplizieren", json={"datei": datei},
                        headers={"X-CSRF-Token": "test-token"}).get_json()["asset"]["file"]
    assert zweite not in (datei, neu)


def test_only_known_templates_are_duplicated(admin):
    r = admin.post("/admin/vorlagen/duplizieren", json={"datei": "../../x.json"},
                   headers={"X-CSRF-Token": "test-token"})
    assert r.status_code == 404


def test_the_editor_offers_export_and_duplicate_for_a_saved_template(admin):
    datei = _saved(admin)
    html = admin.get(f"/admin/vorlagen/editor?datei={datei}").get_data(as_text=True)
    assert f"/admin/vorlagen/export?datei={datei}" in html
    assert 'id="vorlageDuplizieren"' in html and 'id="vorlageImport"' in html


def _vorlage_mit_schrift(schrift):
    return {"id": "mit-schrift", "name": "Mit Schrift", "slots": [{"x": 0, "y": 0, "w": 400, "h": 400}],
            "texts": [{"value": "Hallo", "x": 10, "y": 10, "font": schrift}]}


def test_fonts_travel_in_a_package(tmp_path):
    daten = vorlagen_paket.packen([_vorlage_mit_schrift("hand.ttf")], lambda n: TTF if n == "hand.ttf" else None)
    paket = vorlagen_paket.lesen(daten)
    assert paket.medien["hand.ttf"] == TTF
    daten = vorlagen_paket.packen([_vorlage_mit_schrift("hand.otf")], lambda n: OTF)
    assert "hand.otf" in vorlagen_paket.lesen(daten).medien


def _paket_mit(name, roh):
    puffer = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("paket.json", json.dumps({"format": 1}))
        z.writestr("vorlagen/x.json", json.dumps(_vorlage_mit_schrift(name)))
        z.writestr(f"medien/{name}", roh)
    return puffer.getvalue()


def test_a_font_that_is_no_font_stays_out():
    paket = vorlagen_paket.lesen(_paket_mit("boese.ttf", b"MZ\x90\x00" + b"\x00" * 60))
    assert "boese.ttf" not in paket.medien
    assert any("boese.ttf" in h for h in paket.hinweise)


def test_a_font_that_is_too_large_stays_out():
    paket = vorlagen_paket.lesen(_paket_mit("riesig.ttf", TTF + b"\x00" * (vorlagen_paket.MAX_SCHRIFT_BYTES + 1)))
    assert "riesig.ttf" not in paket.medien


def test_the_gallery_files_an_imported_font_as_a_font(admin):
    from app import ecosystem
    daten = vorlagen_paket.packen([_vorlage_mit_schrift("hand.ttf")], lambda n: TTF)
    r = admin.post("/admin/vorlagen/import", data={"csrf_token": "test-token",
                                                    "paket": (io.BytesIO(daten), "x.abzug")},
                   content_type="multipart/form-data")
    assert r.status_code in (302, 303)
    assert any(a["file"] == "hand.ttf" for a in ecosystem.list_assets("font"))
    assert not any(a["file"] == "hand.ttf" for a in ecosystem.list_assets("overlay"))


# ── Copy and paste, shortcut overview (vorlagen-editor.js) ────────────

from tests.test_template_editor_ui import JS, run  # noqa: E402 — the same Node runner


def test_pasted_copies_land_offset_with_ids_of_their_own():
    out = run("""
      var v = {canvas: [1200, 1800], slots: [{id: "s1", x: 100, y: 100, w: 400, h: 300, shot: 0}],
               texts: [{id: "t1", x: 1180, y: 1790, value: "Hallo"}]};
      sichereIds(v);
      var neu = kopienEinfuegen(v, [{art: "slot", el: v.slots[0]}, {art: "text", el: v.texts[0]}]);
      out = {n: neu.length, slot: v.slots[1], text: v.texts[1], ids: v.slots.map(function (s) { return s.id; })};
    """)
    assert out["n"] == 2
    assert (out["slot"]["x"], out["slot"]["y"]) == (140, 140) and out["slot"]["shot"] == 0
    assert out["text"]["x"] <= 1200 and out["text"]["y"] <= 1800, "a copy stays on the sheet"
    assert len(set(out["ids"])) == 2


def test_pasting_respects_the_limits_and_skips_the_qr_code():
    out = run("""
      var v = {canvas: [1200, 1800], slots: [], qr: {x: 0, y: 0, size: 200}};
      for (var i = 0; i < 12; i++) v.slots.push({x: 0, y: 0, w: 100, h: 100, shot: 0});
      sichereIds(v);
      var neu = kopienEinfuegen(v, [{art: "slot", el: v.slots[0]}, {art: "qr", el: v.qr}]);
      out = {n: neu.length, slots: v.slots.length};
    """)
    assert out == {"n": 0, "slots": 12}


def test_copy_paste_and_the_overview_are_wired_to_the_keys():
    assert '=== "c"' in JS and '=== "v"' in JS and 'e.key === "?"' in JS
    assert "geaendert();                               // one undo step" in JS
    for sprache in ("Tastenkürzel", "Keyboard shortcuts"):
        assert sprache in JS
