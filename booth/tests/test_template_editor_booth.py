"""Der Vorlageneditor an der Box — mit und ohne Galerie.

Am Abend steht der Betreiber an der Box, nicht am Laptop. Also muss die Vorlage
dort entstehen können: sofort wirksam, auch ohne Netz. Und wenn eine Galerie da
ist, soll sie am Montag im Web-Admin liegen und auf der zweiten Box — ohne dass
jemand eine Datei herumträgt.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-vorlagen-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.profile["access"]["pin"] = "135790"
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    token = c.post("/api/admin/login", json={"pin": "135790"}).json()["token"]
    c.headers.update({"X-Admin-Token": token})
    return c


EINFACH = {"name": "Abendvorlage", "paper": "10x15",
           "slots": [{"x": 60, "y": 60, "w": 1080, "h": 700, "shot": 0},
                     {"x": 60, "y": 800, "w": 1080, "h": 700, "shot": 1}],
           "texts": [{"value": "{headline}", "x": 60, "y": 1560, "size": 58}]}


def test_the_editor_page_is_there(client):
    assert client.get("/admin/vorlagen").status_code == 200


def test_without_the_pin_nothing_is_saved(booth):
    from boothcore.app import create_app
    roh = TestClient(create_app(booth))
    assert roh.post("/api/admin/templates", json=EINFACH).status_code == 401


def test_a_template_built_at_the_booth_works_at_once(client, booth):
    antwort = client.post("/api/admin/templates", json=EINFACH)
    assert antwort.status_code == 200
    datei = antwort.json()["asset"]["file"]
    assert (booth.assets_dir / datei).is_file()
    # Sie steht sofort in der Auswahl der Box — sonst ändert sich für den
    # Betreiber nichts und er versteht nicht, warum.
    assert any(t["id"] == "abendvorlage" for t in booth.templates())
    assert datei in (booth.profile.get("collage_templates") or [])


def test_the_booth_checks_the_same_things_as_the_web(client):
    """Ein Bildfeld außerhalb des Blattes wäre ein Abend voller kaputter Drucke.

    Since 17.11.2026 the same gate as the web (vorlagen_kern.annehmen): a
    position off the paper or a slot wider than the sheet is refused with the
    fields named; what only reaches over the edge is moved back and said.
    """
    antwort = client.post("/api/admin/templates", json={
        "name": "Daneben", "slots": [{"x": -100, "y": 0, "w": 5000, "h": 400, "shot": 3}]})
    assert antwort.status_code == 400
    assert "slots[0].x" in antwort.text and "slots[0].w" in antwort.text
    antwort = client.post("/api/admin/templates", json={
        "name": "Ueber den Rand", "slots": [{"x": 1000, "y": 0, "w": 900, "h": 400, "shot": 3}]})
    v = antwort.json()["template"]
    assert v["slots"][0]["x"] >= 0
    assert v["slots"][0]["x"] + v["slots"][0]["w"] <= v["canvas"][0]
    assert v["slots"][0]["shot"] == 0, "lückenlos gezählt"


def test_a_new_template_waits_for_the_gallery(client, booth):
    client.post("/api/admin/templates", json=EINFACH)
    offen = booth.store.local.get("template_push") or []
    assert offen, "ohne Galerie bleibt der Zettel liegen, die Vorlage wirkt trotzdem"
    mit_inhalt = booth.templates_to_push()
    assert mit_inhalt and mit_inhalt[0]["template"]["name"] == "Abendvorlage"


def test_the_note_is_cleared_only_on_receipt(client, booth):
    client.post("/api/admin/templates", json=EINFACH)
    datei = (booth.store.local.get("template_push") or [])[0]
    booth.templates_acked([])                     # keine Quittung: nichts vergessen
    assert booth.store.local.get("template_push") == [datei]
    booth.templates_acked([datei])
    assert booth.store.local.get("template_push") == []


def test_deleting_takes_it_out_of_the_selection(client, booth):
    datei = client.post("/api/admin/templates", json=EINFACH).json()["asset"]["file"]
    client.post("/api/admin/templates/delete", json={"file": datei})
    assert not (booth.assets_dir / datei).exists()
    assert datei not in (booth.profile.get("collage_templates") or [])


def test_the_listing_shows_builtins_to_copy_from(client):
    daten = client.get("/api/admin/templates").json()
    assert len(daten["builtin"]) >= 14
    assert all("slots" in t for t in daten["builtin"]), "zum Kopieren braucht der Editor die Maße"
