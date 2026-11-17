"""Der Vorlageneditor: eigene Anordnungen, geprüft und verteilt.

Der größte Punkt aus dem Abgleich mit dslrBooth. Wichtig ist dabei weniger die
Zeichenfläche als das, was dahinter passiert: Was der Editor schickt, wird
geprüft, bevor es auf einer Box landet — ein Bildfeld außerhalb des Blattes
oder eine Aufnahme Nummer sieben in einer Vorlage für vier wäre an der Box ein
Abend voller kaputter Drucke.
"""
import io
import json
import secrets
from pathlib import Path

import pytest

from app import vorlagen


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    return client


def _post(admin, daten):
    return admin.post("/admin/vorlagen/speichern", json=daten,
                      headers={"X-CSRF-Token": "test-token"})


# ── Prüfung ───────────────────────────────────────────────

def test_nothing_sticks_out_of_the_sheet():
    v, _ = vorlagen.pruefe({"name": "Test", "paper": "10x15",
                            "slots": [{"x": -50, "y": 1700, "w": 4000, "h": 900, "shot": 0}]})
    breite, hoehe = v["canvas"]
    s = v["slots"][0]
    assert s["x"] >= 0 and s["y"] >= 0
    assert s["x"] + s["w"] <= breite and s["y"] + s["h"] <= hoehe


def test_the_shot_numbers_are_counted_without_gaps():
    """Sonst wartet die Box auf eine Aufnahme, die keinen Platz hat."""
    v, hinweise = vorlagen.pruefe({"name": "T", "slots": [
        {"x": 0, "y": 0, "w": 400, "h": 400, "shot": 3},
        {"x": 0, "y": 500, "w": 400, "h": 400, "shot": 7}]})
    assert sorted(s["shot"] for s in v["slots"]) == [0, 1]
    assert v["shots"] == 2
    assert hinweise, "geradegerückt wird gesagt, nicht verschwiegen"


def test_a_template_without_a_slot_gets_one():
    v, hinweise = vorlagen.pruefe({"name": "Leer", "slots": []})
    assert len(v["slots"]) == 1 and hinweise


def test_colours_are_either_tokens_or_real():
    v, _ = vorlagen.pruefe({"name": "T", "texts": [
        {"value": "a", "color": "@akzent"}, {"value": "b", "color": "#ff0000"},
        {"value": "c", "color": "rot bitte"}]})
    assert [t["color"] for t in v["texts"]] == ["@akzent", "#ff0000", "@schrift"]


def test_the_name_becomes_a_usable_file_name():
    v, _ = vorlagen.pruefe({"name": "Müllers Große Feier!"})
    assert v["id"] == "muellers-grosse-feier"
    assert vorlagen.dateiname(v) == "muellers-grosse-feier.json"


def test_only_known_paper_formats():
    v, hinweise = vorlagen.pruefe({"name": "T", "paper": "din-a4"})
    assert v["paper"] == "10x15" and hinweise


# ── Weg durch die Verwaltung ──────────────────────────────

def test_the_editor_saves_a_template_that_the_booth_can_get(admin):
    antwort = _post(admin, {"name": "Hochzeit Mia " + secrets.token_hex(2), "paper": "10x15-quer",
                            "slots": [{"x": 60, "y": 60, "w": 800, "h": 900, "shot": 0},
                                      {"x": 900, "y": 60, "w": 800, "h": 900, "shot": 1}],
                            "texts": [{"value": "{headline}", "x": 60, "y": 1020, "size": 60}],
                            "qr": {"x": 1500, "y": 980, "size": 180}})
    assert antwort.status_code == 200
    daten = antwort.get_json()
    assert daten["ok"] and daten["template"]["shots"] == 2

    from app import ecosystem
    datei = daten["asset"]["file"]
    assert any(a["file"] == datei for a in ecosystem.list_assets("template"))

    # Und sie liegt wirklich als Datei da, wo die Box sie abholt.
    from app.blueprints.admin_booth import _assets_dir
    auf_platte = json.loads((_assets_dir() / datei).read_text(encoding="utf-8"))
    assert auf_platte["canvas"] == [1800, 1200]


def test_saving_twice_replaces_instead_of_piling_up(admin):
    from app import ecosystem
    name = "Doppelt " + secrets.token_hex(2)
    _post(admin, {"name": name, "slots": [{"x": 0, "y": 0, "w": 600, "h": 600, "shot": 0}]})
    _post(admin, {"name": name, "slots": [{"x": 0, "y": 0, "w": 900, "h": 900, "shot": 0}]})
    passende = [a for a in ecosystem.list_assets("template") if a["name"] == name]
    assert len(passende) == 1

    from app.blueprints.admin_booth import _assets_dir
    inhalt = json.loads((_assets_dir() / passende[0]["file"]).read_text(encoding="utf-8"))
    assert inhalt["slots"][0]["w"] == 900, "die zweite Fassung gilt"


def test_saving_tells_the_boxes_to_fetch_it(admin, client):
    from app import db, ecosystem
    dev = db.create_device(name="Vorlagen-Box " + secrets.token_hex(2))
    vorher = db.get_device_by_id(dev["id"])["profile_version"]
    _post(admin, {"name": "Neu " + secrets.token_hex(2),
                  "slots": [{"x": 0, "y": 0, "w": 600, "h": 600, "shot": 0}]})
    assert db.get_device_by_id(dev["id"])["profile_version"] > vorher


def test_a_template_reaches_a_box_that_uses_it(admin, client):
    """Der Weg, auf den es ankommt: gespeichert → im Profil → beim Sync dabei."""
    from app import db, ecosystem
    antwort = _post(admin, {"name": "Für die Box " + secrets.token_hex(2),
                            "slots": [{"x": 0, "y": 0, "w": 600, "h": 600, "shot": 0}]})
    datei = antwort.get_json()["asset"]["file"]
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    ecosystem.save_device_profile(dev["id"], {"collage_templates": [datei]}, None)
    profil = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://example.test")
    assert datei in profil["collage_templates"]
    assert any(a["file"] == datei for a in ecosystem.assets_for_profile(profil))


def test_without_a_token_nothing_is_written(admin):
    antwort = admin.post("/admin/vorlagen/speichern", json={"name": "Ohne Zeichen"})
    assert antwort.status_code == 400


def test_the_editor_page_opens_for_new_and_for_existing(admin):
    assert admin.get("/admin/vorlagen").status_code == 200
    assert admin.get("/admin/vorlagen/editor").status_code == 200
    assert admin.get("/admin/vorlagen/editor?von=grid4").status_code == 200


def test_deleting_removes_file_and_entry(admin):
    from app import ecosystem
    from app.blueprints.admin_booth import _assets_dir
    datei = _post(admin, {"name": "Weg damit " + secrets.token_hex(2),
                          "slots": [{"x": 0, "y": 0, "w": 600, "h": 600, "shot": 0}]}).get_json()["asset"]["file"]
    admin.post("/admin/vorlagen/loeschen", data={"csrf_token": "test-token", "datei": datei},
               follow_redirects=True)
    assert not any(a["file"] == datei for a in ecosystem.list_assets("template"))
    assert not (_assets_dir() / datei).exists()


def test_the_editor_is_one_file_for_both_sides():
    """An der Box läuft derselbe Editor — nicht ein zweiter, der auseinanderläuft."""
    galerie = Path("app/static/js/vorlagen-editor.js")
    box = Path("booth/boothcore/web/static/vorlagen-editor.js")
    assert galerie.is_file() and box.is_file()
    assert galerie.read_bytes() == box.read_bytes(), \
        "tools/sync_editor.py laufen lassen — die Datei ist an beiden Stellen dieselbe"


# ── Der Weg von der Box in die Galerie ────────────────────

def test_a_template_built_at_the_booth_arrives_in_the_gallery(admin, client):
    """Am Abend an der Box gebaut, am Montag im Web-Admin — ohne USB-Stick."""
    from app import db, ecosystem
    from app.blueprints.admin_booth import _assets_dir
    dev = db.create_device(name="Abend-Box " + secrets.token_hex(2))

    antwort = client.post("/api/v2/booth/sync", json={
        "status": {"camera": {"state": "ready"}},
        "templates": [{"file": "abendvorlage.json", "template": {
            "name": "Abendvorlage " + secrets.token_hex(2), "paper": "10x15",
            "slots": [{"x": 60, "y": 60, "w": 1080, "h": 900, "shot": 0}],
            "texts": [{"value": "{headline}", "x": 60, "y": 1560, "size": 58}]}}],
    }, headers={"X-API-Key": dev["api_key"]})
    assert antwort.status_code == 200
    daten = antwort.get_json()
    assert daten["templates_ack"] == ["abendvorlage.json"], "erst die Quittung räumt den Zettel der Box"

    gespeichert = [a for a in ecosystem.list_assets("template") if a["name"].startswith("Abendvorlage")]
    assert gespeichert, "die Vorlage liegt jetzt in der Galerie"
    inhalt = json.loads((_assets_dir() / gespeichert[0]["file"]).read_text(encoding="utf-8"))
    assert inhalt["slots"][0]["w"] == 1080


def test_a_broken_template_does_not_cost_the_sync(client):
    from app import db
    dev = db.create_device(name="Kaputt-Box " + secrets.token_hex(2))
    antwort = client.post("/api/v2/booth/sync", json={
        "status": {"camera": {"state": "ready"}},
        "templates": ["kein Objekt", {"file": "x.json"}],
    }, headers={"X-API-Key": dev["api_key"]})
    assert antwort.status_code == 200
    assert not antwort.get_json().get("templates_ack")


def test_the_gallery_checks_what_a_box_sends(client):
    """Eine Box darf nichts hereinreichen, was die Verwaltung nicht anzeigen kann."""
    from app import db, ecosystem
    from app.blueprints.admin_booth import _assets_dir
    dev = db.create_device(name="Freche-Box " + secrets.token_hex(2))
    name = "Frech " + secrets.token_hex(2)
    client.post("/api/v2/booth/sync", json={
        "status": {"camera": {"state": "ready"}},
        "templates": [{"file": "frech.json", "template": {
            "name": name, "canvas": [99999, 5],
            "slots": [{"x": 0, "y": 0, "w": 99999, "h": 99999, "shot": 9}]}}],
    }, headers={"X-API-Key": dev["api_key"]})
    # Since 17.11.2026 the same gate as every other way in: refused, not
    # stored (tests/test_templates_pass_one_gate.py); a box's template that
    # only reaches over the edge is moved back onto the paper.
    assert not any(a["name"] == name for a in ecosystem.list_assets("template"))
    client.post("/api/v2/booth/sync", json={
        "status": {"camera": {"state": "ready"}},
        "templates": [{"file": "frech2.json", "template": {
            "name": name + " 2", "slots": [{"x": 1000, "y": 0, "w": 900, "h": 400, "shot": 9}]}}],
    }, headers={"X-API-Key": dev["api_key"]})
    eintrag = next(a for a in ecosystem.list_assets("template") if a["name"] == name + " 2")
    inhalt = json.loads((_assets_dir() / eintrag["file"]).read_text(encoding="utf-8"))
    breite, hoehe = inhalt["canvas"]
    assert breite <= 4000 and hoehe >= 400
    assert inhalt["slots"][0]["x"] + inhalt["slots"][0]["w"] <= breite and inhalt["slots"][0]["shot"] == 0


# ── Vorlagen aus- und einlesen ─────────────────────────────────────────
def test_the_gallery_hands_out_a_package(admin):
    """Ein Download, den man auf einen Stick legen und zur Box tragen kann."""
    from app import vorlagen as vorlagen_mod
    from app.blueprints.admin_booth import _assets_dir

    ordner = _assets_dir()
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "wiese.jpg").write_bytes(b"\x89PNG" + b"x" * 100)
    from app import ecosystem
    ecosystem.add_asset("background", "wiese.jpg", "wiese.jpg", "a" * 64, 104)
    vorlagen_mod.speichern(vorlagen_mod.pruefe({
        "id": "reise", "name": "Reisevorlage", "paper": "10x15",
        "slots": [{"x": 60, "y": 60, "w": 1080, "h": 1380, "shot": 0}]})[0], ordner)

    r = admin.get("/admin/vorlagen/export")
    assert r.status_code == 200
    assert r.mimetype == "application/zip"
    assert ".abzug" in r.headers.get("Content-Disposition", "")

    from app import vorlagen_paket
    paket = vorlagen_paket.lesen(r.data)
    assert any(v["id"] == "reise" for v in paket.vorlagen)
    assert "wiese.jpg" in paket.medien, "Der Hintergrund gehört ins Paket"
    # Andere Tests legen eigene Hintergründe an; entscheidend ist, dass unserer
    # als Hintergrund einsortiert ist und nicht als irgendetwas anderes.
    assert "wiese.jpg" in paket.hintergruende


def test_the_gallery_reads_a_package_back(admin):
    from app import ecosystem, vorlagen_paket
    from app.blueprints.admin_booth import _assets_dir

    vorlage = {"id": "vonderbox", "name": "Von der Box", "paper": "10x15", "canvas": [1200, 1800],
               "slots": [{"x": 60, "y": 60, "w": 1080, "h": 1380, "shot": 0}]}
    daten = vorlagen_paket.packen([vorlage], lambda n: b"\x89PNG" + b"y" * 90,
                                  hintergruende=["wald.jpg"])
    r = admin.post("/admin/vorlagen/import", data={
        "csrf_token": "test-token",
        "paket": (io.BytesIO(daten), "vorlagen.abzug"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    dateien = [a["file"] for a in ecosystem.list_assets("template")]
    assert any("vonderbox" in f for f in dateien)
    assert (_assets_dir() / "wald.jpg").is_file()
    assert any(a["file"] == "wald.jpg" for a in ecosystem.list_assets("background")), \
        "Ein Hintergrund muss als Hintergrund einsortiert werden, sonst findet ihn niemand"


def test_a_package_from_nowhere_does_not_break_the_page(admin):
    r = admin.post("/admin/vorlagen/import", data={
        "csrf_token": "test-token", "paket": (io.BytesIO(b"kein zip"), "urlaub.jpg"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    assert "Vorlagenpaket" in r.get_data(as_text=True) or "Vorlage" in r.get_data(as_text=True)
