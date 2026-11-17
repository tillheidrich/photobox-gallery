"""The consent choice per run can be set from the gallery, and both sides agree on it."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
    assert config.DEFAULT_PROFILE["consent"] == ecosystem.DEFAULT_PROFILE["consent"]
    assert ecosystem.DEFAULT_PROFILE["consent"]["enabled"] is False


def test_a_new_device_has_the_step_off(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Ohne Wahl")["id"]
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["consent"]["enabled"] is False


def test_the_form_saves_the_choice_and_the_note(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Hochzeit")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    for name in ("consent_enabled", "consent_print_only", "consent_days", "consent_note_de", "consent_note_en"):
        assert f'name="{name}"' in body, name
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": _token(client, dev_id), "mode_photo": "on",
        "consent_enabled": "on", "consent_print_only": "on", "consent_days": "14",
        "consent_note_de": "Galerie mit Passwort, {tage} Tage.", "consent_note_en": "",
    }, follow_redirects=True)
    c = ecosystem.device_profile(db.get_device_by_id(dev_id))["consent"]
    assert c["enabled"] is True and c["print_only"] is True and c["days"] == 14
    assert c["note"]["de"] == "Galerie mit Passwort, {tage} Tage."
    # An emptied field falls back to the default text instead of showing nothing.
    assert c["note"]["en"] == ecosystem.DEFAULT_PROFILE["consent"]["note"]["en"]
