"""Boomerang format: the operator's setting, plus a switch whether guests may choose.

Found on 15.11.2026: the guest's format choice (``guest_choice.format``) only
ever applied to photos; the boomerang always took ``boomerang.format`` and
the set-up screen never offered a choice for it. Now ``boomerang.guest_choice``
(off by default) lets the guest pick among ``formats`` for the boomerang.
"""
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
    assert config.DEFAULT_PROFILE["boomerang"] == ecosystem.DEFAULT_PROFILE["boomerang"]
    assert ecosystem.DEFAULT_PROFILE["boomerang"]["guest_choice"] is False


def test_the_form_switches_it_and_shows_it_again(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Boomerangbox")["id"]
    token = _token(client, dev_id)
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_boomerang": "on", "boomerang_format": "4:5",
        "boomerang_guest_choice": "on"}, follow_redirects=True)
    assert r.status_code == 200
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["boomerang"]["format"] == "4:5" and p["boomerang"]["guest_choice"] is True
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'name="boomerang_guest_choice" checked' in page

    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_boomerang": "on", "boomerang_format": "4:5"}, follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["boomerang"]["guest_choice"] is False


def test_the_booth_editor_has_a_label_for_it():
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "settings.js").read_text(encoding="utf-8")
    assert '"boomerang.guest_choice":' in js
