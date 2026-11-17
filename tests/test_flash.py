"""Aufsteckblitz: Ladezeit, Live-Bild, Betriebsart.

Der teuerste Fehler einer Fotobox mit Blitz ist das zweite Bild einer Collage,
das dunkel wird, weil der Blitz noch lud. Man merkt es erst auf dem Druck.
"""
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
    assert config.DEFAULT_PROFILE["flash"] == ecosystem.DEFAULT_PROFILE["flash"]


def test_the_form_saves_the_flash_settings(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Blitzbox")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on",
        "flash_enabled": "on", "flash_recycle": "3.5", "flash_gain": "40", "flash_mode": "M",
    }, follow_redirects=True)
    flash = ecosystem.device_profile(db.get_device_by_id(dev_id))["flash"]
    assert flash == {"enabled": True, "recycle_seconds": 3.5, "preview_gain": 40, "preview_gain_auto": False,
                     "expected_mode": "M"}


def test_the_form_saves_the_live_view_exposure_and_the_automatic_gain(client):
    """15.11.2026: with flash the live view gets its own exposure ("auto" = ISO
    raised until the frame is bright), and the gain can follow each frame."""
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Blitzbox Live")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    for name in ("cam_preview_iso", "cam_preview_aperture", "cam_preview_shutter", "flash_gain_auto"):
        assert f'name="{name}"' in body, name
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "flash_enabled": "on", "flash_gain_auto": "on",
        "cam_iso": "200", "cam_shutter": "1/125", "cam_aperture": "5.6",
        "cam_preview_iso": "3200", "cam_preview_aperture": "", "cam_preview_shutter": "1/30",
    }, follow_redirects=True)
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["flash"]["preview_gain_auto"] is True
    assert p["camera"] == {"iso": "200", "aperture": "5.6", "shutter": "1/125", "wb": "",
                           "preview_iso": "3200", "preview_aperture": "", "preview_shutter": "1/30",
                           "preview_iso_max": "12800", "preview_shutter_min": "1/30"}


def test_the_form_saves_how_far_the_automatic_live_view_may_go(client):
    """15.11.2026: auto raises the ISO up to a cap, then slows the shutter down
    to a floor; both are choices in the form, an unknown value is the default."""
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Blitzbox Leiter")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    for name in ("cam_preview_iso_max", "cam_preview_shutter_min"):
        assert f'name="{name}"' in body, name
    for option in ("3200", "6400", "12800", "25600", "1/60", "1/15"):
        assert f">{option}<" in body, option
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "flash_enabled": "on",
        "cam_preview_iso_max": "25600", "cam_preview_shutter_min": "1/15",
    }, follow_redirects=True)
    cam = ecosystem.device_profile(db.get_device_by_id(dev_id))["camera"]
    assert cam["preview_iso_max"] == "25600" and cam["preview_shutter_min"] == "1/15"
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "cam_preview_iso_max": "51200", "cam_preview_shutter_min": "1",
    }, follow_redirects=True)
    cam = ecosystem.device_profile(db.get_device_by_id(dev_id))["camera"]
    assert cam["preview_iso_max"] == "12800" and cam["preview_shutter_min"] == "1/30"


def test_an_unknown_live_view_value_falls_back_to_auto_iso(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Blitzbox Live 2")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "cam_preview_iso": "sehr hell", "cam_preview_shutter": "1/7",
    }, follow_redirects=True)
    cam = ecosystem.device_profile(db.get_device_by_id(dev_id))["camera"]
    assert cam["preview_iso"] == "auto" and cam["preview_shutter"] == ""


def test_both_modes_are_offered_and_neither_is_forced(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Blitzbox 2")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    for value in ("any", "M", "Av"):
        assert f'value="{value}"' in body
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "flash_enabled": "on", "flash_mode": "Av",
    }, follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["flash"]["expected_mode"] == "Av"


def test_a_gif_faster_than_the_flash_is_flagged_when_saving(client):
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Blitzbox 3")["id"]
    token = _token(client, dev_id)
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "mode_gif": "on", "gif_interval": "1",
        "flash_enabled": "on", "flash_recycle": "2.5",
    }, follow_redirects=True)
    assert "schneller auf, als der Blitz nachlädt" in r.get_data(as_text=True)


def test_the_booth_waits_between_shots_but_only_when_another_follows():
    js = APP_JS.read_text(encoding="utf-8")
    assert "if (fl.enabled && index < needed)" in js
    # Die Ansicht des letzten Bildes wird angerechnet, sonst wartet der Gast doppelt.
    assert "(Number(fl.recycle_seconds) || 0) * 1000 - review_ms" in js


def test_the_preview_gain_is_not_counted_twice_by_the_page():
    # Since 15.11.2026 the booth brightens the live frames it serves
    # (booth/tests/test_flash_live_view.py); a CSS filter on top would double it.
    js = APP_JS.read_text(encoding="utf-8")
    assert "function applyPreviewGain()" in js
    assert 'el.style.filter = "";' in js
    assert "brightness(${1 + gain / 100})" not in js
