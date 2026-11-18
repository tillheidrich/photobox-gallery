"""Jede Wahlmöglichkeit des Gastes muss sich einzeln abschalten lassen.

Auf einer Firmenfeier will der Betreiber oft genau ein Format und keinen Filter —
und auf einem Kinderfest soll niemand vor einer Auswahl stehen.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "booth" / "boothcore" / "web" / "static" / "app.js"


def _admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _token(client, dev_id) -> str:
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        return s.get("csrf_token") or ""


def test_gallery_and_booth_ship_the_same_block():
    from app import ecosystem
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore import config
    assert config.DEFAULT_PROFILE["guest_choice"] == ecosystem.DEFAULT_PROFILE["guest_choice"]


def test_all_four_switches_survive_the_form(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Wahlbox")["id"]
    token = _token(client, dev_id)

    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on",
        "choice_format": "on", "choice_look": "on",        # Vorlage und Hintergrund aus
    }, follow_redirects=True)
    choice = ecosystem.device_profile(db.get_device_by_id(dev_id))["guest_choice"]
    assert choice == {"format": True, "look": True, "collage_template": False, "background": False}


def test_modes_can_be_switched_off_one_by_one(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Wahlbox 2")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "mode_collage": "on",   # GIF, Boomerang, Video aus
    }, follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["modes"] == ["photo", "collage"]


def test_the_booth_asks_only_when_choice_is_allowed_and_there_is_something_to_choose():
    """Beide Bedingungen stehen im Kiosk zusammen — sonst erschiene eine leere Auswahl."""
    js = APP_JS.read_text(encoding="utf-8")
    assert "const mayFormat = may.format !== false" in js
    # Photo and boomerang: the general switch (the boomerang and the GIF also by
    # their own, boomerang.guest_choice / gif.guest_choice); the layout: collage,
    # or a boomerang with its sheet.
    assert re.search(r"showFormat = (?:!flow\.photoTemplate && )?p\.formats\.length > 1 && \(\(mayFormat && FORMAT_MODES\.includes\(mode\)\)", js)
    assert re.search(r'\|\| \(mode === "gif" && !!\(p\.gif \|\| \{\}\)\.guest_choice\)\);', js)
    assert re.search(r"showTemplate = mayTemplate && sheetModes && (?:S\.templates|tpls)\.length > 1", js)
    assert "const mayLook = !S.profile.guest_choice || S.profile.guest_choice.look !== false" in js


def test_the_form_shows_the_switches(client):
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Wahlbox 3")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    for name in ("choice_format", "choice_look", "choice_template", "choice_background"):
        assert f'name="{name}"' in body
