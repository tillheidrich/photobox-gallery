"""The operator can choose the sheet a GIF run is printed on.

Until now a GIF run of six pictures always went onto the built-in "sechs"
sheet. ``gif.print_template`` names one of the gallery's own templates
instead (for example a six-photo layout of the template library); empty =
the built-in sheet as before. The file travels to the booth like a collage
template, with the pictures and fonts it places.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "booth"))

from app import ecosystem                      # noqa: E402
from boothcore import config                   # noqa: E402


@pytest.fixture()
def admin_client(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _device(client):
    client.get("/healthz")
    from app import db
    ecosystem.init_schema()
    return db.get_device_by_id(db.create_device(name="Box")["id"])


def _saved(dev):
    from app import db
    return ecosystem.device_profile(db.get_device_by_id(dev["id"]))


def _vorlage_ablegen(name="sechs-eigen.json", bild="logo-sechs.png"):
    from app import media
    ordner = media.assets_root() / "booth"
    ordner.mkdir(parents=True, exist_ok=True)
    v = {"id": "sechs-eigen", "name": "Sechs eigen", "paper": "10x15-quer", "canvas": [1800, 1200],
         "slots": [{"x": 96 + c * 544, "y": 96 + r * 412, "w": 520, "h": 388, "shot": r * 3 + c}
                   for r in range(2) for c in range(3)],
         "images": [{"file": bild, "x": 0, "y": 0, "w": 100, "h": 100}], "texts": []}
    (ordner / name).write_text(json.dumps(v), encoding="utf-8")
    (ordner / bild).write_bytes(b"\x89PNG\r\n\x1a\n")
    vorhanden = {a["file"] for a in ecosystem.list_assets()}
    if name not in vorhanden:
        ecosystem.add_asset("template", "Sechs eigen", name, "x", 10)
    if bild not in vorhanden:
        ecosystem.add_asset("overlay", "Logo", bild, "y", 8)
    return name


def test_both_sides_start_with_the_built_in_sheet():
    assert config.DEFAULT_PROFILE["gif"]["print_template"] == ""
    assert ecosystem.DEFAULT_PROFILE["gif"]["print_template"] == ""
    assert "gif.print_template" in config.MEDIA_FIELDS
    assert config.MEDIA_FIELDS == ecosystem.MEDIA_FIELDS


def test_the_form_saves_an_own_template_and_refuses_anything_else(admin_client, client):
    dev = _device(client)
    datei = _vorlage_ablegen()
    admin_client.post(f"/admin/devices/{dev['id']}/booth",
                      data={"csrf_token": "test-token", "gif_print_template": datei})
    assert _saved(dev)["gif"]["print_template"] == datei
    admin_client.post(f"/admin/devices/{dev['id']}/booth",
                      data={"csrf_token": "test-token", "gif_print_template": "../../geheim.json"})
    assert _saved(dev)["gif"]["print_template"] == ""


def test_the_chosen_template_and_its_pictures_reach_the_booth(admin_client, client):
    dev = _device(client)
    datei = _vorlage_ablegen()
    admin_client.post(f"/admin/devices/{dev['id']}/booth",
                      data={"csrf_token": "test-token", "gif_print_template": datei})
    namen = {a["file"] for a in ecosystem.assets_for_profile(_saved(dev))}
    assert {datei, "logo-sechs.png"} <= namen


def test_the_booth_page_offers_the_choice(admin_client, client):
    dev = _device(client)
    _vorlage_ablegen()
    body = admin_client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert 'name="gif_print_template"' in body and "Sechs eigen" in body
