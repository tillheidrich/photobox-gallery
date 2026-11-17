"""Vorlagen mitnehmen — mit allem, was dazugehört.

Eine Vorlage, die auf ein Hintergrundbild zeigt, ist ohne dieses Bild nur die
halbe Vorlage. Das Austauschformat (.abzug) packt beides zusammen. Geprüft
wird vor allem das, was schiefgehen kann: fremde Dateien, Pfade, die aus dem
Ordner führen, Bilder, die nicht mitgekommen sind.
"""
import io
import json
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

from boothcore.media import vorlagen_paket as vp


def _vorlage(tid="fest", overlay="rahmen.png"):
    return {"id": tid, "name": f"Vorlage {tid}", "canvas": [1200, 1800], "paper": "10x15",
            "overlay": overlay, "shots": 2,
            "slots": [{"x": 60, "y": 60, "w": 1080, "h": 700, "shot": 0},
                      {"x": 60, "y": 800, "w": 1080, "h": 700, "shot": 1}]}


BILD = b"\x89PNG\r\n\x1a\n" + b"x" * 200


def test_the_picture_travels_with_the_template():
    daten = vp.packen([_vorlage()], lambda n: BILD if n == "rahmen.png" else None)
    paket = vp.lesen(daten)
    assert [v["id"] for v in paket.vorlagen] == ["fest"]
    assert paket.medien["rahmen.png"] == BILD
    assert paket.hinweise == []


def test_the_collage_backgrounds_come_along_too():
    """Der Hintergrund gehört der Box, nicht der Vorlage — mitgehen muss er trotzdem."""
    daten = vp.packen([_vorlage()], lambda n: BILD, hintergruende=["wiese.jpg", "wald.jpg"])
    paket = vp.lesen(daten)
    assert set(paket.medien) == {"rahmen.png", "wiese.jpg", "wald.jpg"}
    assert paket.hintergruende == ["wald.jpg", "wiese.jpg"]
    assert "rahmen.png" not in paket.hintergruende, "Eine Überlagerung ist kein Hintergrund"


def test_it_is_an_ordinary_zip_anyone_can_open():
    """Wer weitergibt, soll nachsehen können, was er weitergibt."""
    daten = vp.packen([_vorlage()], lambda n: BILD)
    with zipfile.ZipFile(io.BytesIO(daten)) as z:
        namen = z.namelist()
        assert "paket.json" in namen
        assert "vorlagen/fest.json" in namen and "medien/rahmen.png" in namen
        kopf = json.loads(z.read("paket.json"))
        assert kopf["format"] == vp.FORMAT and kopf["medien"] == ["rahmen.png"]
        # Die Vorlage selbst ist lesbar, nicht verpackt
        assert json.loads(z.read("vorlagen/fest.json"))["name"] == "Vorlage fest"


def test_a_missing_picture_is_said_out_loud_twice():
    """Beim Packen und beim Einlesen — sonst fällt es erst auf dem Abzug auf."""
    daten = vp.packen([_vorlage()], lambda n: None)
    with zipfile.ZipFile(io.BytesIO(daten)) as z:
        assert json.loads(z.read("paket.json"))["fehlende_medien"] == ["rahmen.png"]
    paket = vp.lesen(daten)
    assert paket.medien == {}
    assert any("rahmen.png" in h for h in paket.hinweise)


def test_several_templates_share_one_package():
    daten = vp.packen([_vorlage("a"), _vorlage("b", "zweiter.png")], lambda n: BILD)
    paket = vp.lesen(daten)
    assert {v["id"] for v in paket.vorlagen} == {"a", "b"}
    assert set(paket.medien) == {"rahmen.png", "zweiter.png"}


def test_a_colour_is_not_a_file():
    """@hintergrund und #ffffff sind Farben aus dem Stil, keine Bilder."""
    assert vp.medien_von({"background": "@hintergrund"}) == []
    assert vp.medien_von({"background": "#ffffff"}) == []


# ── Was ein fremdes Paket alles versuchen kann ─────────────────────────
def _basteln(eintraege):
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        for name, inhalt in eintraege:
            z.writestr(name, inhalt)
    return puffer.getvalue()


def test_a_path_that_leads_out_of_the_folder_is_refused():
    daten = _basteln([("vorlagen/x.json", json.dumps(_vorlage())),
                      ("medien/../../etc/passwd", "root:x:0:0")])
    paket = vp.lesen(daten)
    assert paket.medien == {}, "Nichts darf neben den Medienordner geschrieben werden"
    assert any("unzul" in h for h in paket.hinweise)


def test_nothing_that_gets_executed_comes_along():
    daten = _basteln([("vorlagen/x.json", json.dumps(_vorlage())),
                      ("medien/hilfe.exe", "MZ"), ("medien/lauf.sh", "#!/bin/sh")])
    paket = vp.lesen(daten)
    assert paket.medien == {}
    assert sum("keine Bilddatei" in h for h in paket.hinweise) == 2


def test_a_broken_template_does_not_take_the_others_with_it():
    daten = _basteln([("vorlagen/gut.json", json.dumps(_vorlage("gut"))),
                      ("vorlagen/kaputt.json", "{ das ist kein json")])
    paket = vp.lesen(daten)
    assert [v["id"] for v in paket.vorlagen] == ["gut"]
    assert any("nicht lesbar" in h for h in paket.hinweise)


def test_a_wrong_file_says_so_plainly():
    with pytest.raises(ValueError):
        vp.lesen(b"das ist ein Urlaubsfoto")
    with pytest.raises(ValueError):
        vp.lesen(_basteln([("liesmich.txt", "hallo")]))


def test_a_template_from_a_newer_version_is_not_guessed():
    daten = _basteln([("paket.json", json.dumps({"format": vp.FORMAT + 1})),
                      ("vorlagen/x.json", json.dumps(_vorlage()))])
    assert any("neueren" in h for h in vp.lesen(daten).hinweise)


def test_what_comes_in_is_checked_like_anything_else():
    """Ein Paket ist eine fremde Datei — die Prüfung gilt genauso."""
    schraeg = _vorlage("schraeg")
    schraeg["slots"][0]["x"] = 99999          # weit außerhalb des Blattes: abgelehnt (17.11.2026)
    ueber = _vorlage("ueber")
    ueber["slots"][0]["x"] = ueber["canvas"][0] - 100   # nur über den Rand: zurückgerückt
    daten = _basteln([("vorlagen/x.json", json.dumps(schraeg)), ("vorlagen/y.json", json.dumps(ueber))])
    paket = vp.lesen(daten)
    assert [v["id"] for v in paket.vorlagen] == ["ueber"]
    v = paket.vorlagen[0]
    assert v["slots"][0]["x"] + v["slots"][0]["w"] <= v["canvas"][0]
    assert any("x.json" in h and "slots[0].x" in h for h in paket.hinweise), "Abgelehntes wird gesagt"


def test_the_file_name_is_recognisable_in_a_folder():
    eine = vp.paketname([_vorlage("hochzeit")])
    viele = vp.paketname([_vorlage("a"), _vorlage("b")])
    assert eine.startswith("hochzeit-") and eine.endswith(".abzug")
    assert viele.startswith("vorlagen-2-") and time.strftime("%Y") in viele


# ── Beide Seiten, dieselbe Datei ───────────────────────────────────────
def test_gallery_and_booth_use_the_very_same_module():
    hier = Path(vp.__file__).read_bytes()
    dort = (Path(__file__).resolve().parents[2] / "app" / "vorlagen_paket.py").read_bytes()
    assert hier == dort, "tools/sync_editor.py laufen lassen"


# ── Der ganze Weg: Box → Datei → Galerie und zurück ────────────────────
def test_a_template_really_walks_from_one_box_to_the_next(monkeypatch):
    """Stick rein, Datei drauf, an der anderen Box einlesen — ohne Netz."""
    from fastapi.testclient import TestClient
    from boothcore.app import Booth, create_app

    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    erste = Booth(base=Path(tempfile.mkdtemp(prefix="booth-paket-a-")), start_threads=False)
    zweite = Booth(base=Path(tempfile.mkdtemp(prefix="booth-paket-b-")), start_threads=False)
    try:
        c1, c2 = TestClient(create_app(erste)), TestClient(create_app(zweite))
        for c, b in ((c1, erste), (c2, zweite)):
            b.store.save_local({"admin_pin": "1234"})
        def anmelden(c):
            return {"X-Admin-Token": c.post("/api/admin/login", json={"pin": "1234"}).json()["token"]}

        kopf1, kopf2 = anmelden(c1), anmelden(c2)
        # An der ersten Box eine Vorlage anlegen und ein Hintergrundbild ablegen
        erste.assets_dir.mkdir(parents=True, exist_ok=True)
        (erste.assets_dir / "wiese.jpg").write_bytes(BILD)
        assert c1.post("/api/admin/templates", json=_vorlage("reise"), headers=kopf1).status_code == 200

        # Ausgeben
        r = c1.get("/api/admin/templates/export", headers=kopf1)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/zip")
        assert ".abzug" in r.headers.get("content-disposition", "")
        datei = r.content

        # An der zweiten Box einlesen
        r2 = c2.post("/api/admin/templates/import", headers=kopf2,
                     files={"paket": ("vorlagen.abzug", datei, "application/zip")})
        assert r2.status_code == 200, r2.text
        d = r2.json()
        assert "Vorlage reise" in d["vorlagen"]
        assert "wiese.jpg" in d["medien"], "Der Hintergrund muss mitkommen"
        assert (zweite.assets_dir / "wiese.jpg").read_bytes() == BILD
        # Und sie ist dort auch in Gebrauch, nicht nur abgelegt
        gefunden = [p_.name for p_ in zweite.assets_dir.glob("*.json")]
        assert any("reise" in n for n in gefunden)
        assert any("reise" in n for n in zweite.profile.get("collage_templates") or [])
    finally:
        erste.mail.stop()
        zweite.mail.stop()


def test_a_package_that_is_not_one_is_refused_politely(monkeypatch):
    from fastapi.testclient import TestClient
    from boothcore.app import Booth, create_app

    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    b = Booth(base=Path(tempfile.mkdtemp(prefix="booth-paket-c-")), start_threads=False)
    try:
        c = TestClient(create_app(b))
        b.store.save_local({"admin_pin": "1234"})
        kopf = {"X-Admin-Token": c.post("/api/admin/login", json={"pin": "1234"}).json()["token"]}
        r = c.post("/api/admin/templates/import", headers=kopf,
                   files={"paket": ("urlaub.jpg", b"kein zip", "image/jpeg")})
        assert r.status_code == 400
        assert "Vorlagenpaket" in r.json()["detail"] or "Vorlage" in r.json()["detail"]
    finally:
        b.mail.stop()
