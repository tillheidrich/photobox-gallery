"""Drawing on the picture: the gallery form switches it, saves it and shows it again.

The booth refuses colours it was not given, so the form keeps only #rrggbb
and never stores an empty palette.
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
    assert config.DEFAULT_PROFILE["draw"] == ecosystem.DEFAULT_PROFILE["draw"]
    assert ecosystem.DEFAULT_PROFILE["draw"]["enabled"] is False


def test_form_saves_and_shows_the_setting(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Zeichenbox")["id"]
    token = _token(client, dev_id)
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on",
        "draw_enabled": "on", "draw_mode_collage": "on",
        "draw_colors": "#FFFFFF, #111111; rot, #12345, #e5484d, #ffffff",
    }, follow_redirects=True)
    assert r.status_code == 200
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["draw"] == {"enabled": True, "modes": ["collage"],
                         "colors": ["#ffffff", "#111111", "#e5484d"]}

    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'name="draw_enabled" checked' in page
    assert 'name="draw_mode_collage" checked' in page
    assert 'name="draw_mode_photo" checked' not in page
    assert 'value="#ffffff, #111111, #e5484d"' in page


def test_form_without_a_valid_colour_keeps_the_shipped_palette(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Zeichenbox 2")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "draw_colors": "rot, blau",
    }, follow_redirects=True)
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))["draw"]
    assert p["enabled"] is False
    assert p["colors"] == ecosystem.DEFAULT_PROFILE["draw"]["colors"]
