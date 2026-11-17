"""Kiosk sizes and the screensaver fit, from the gallery form (Paket A: 12, 14, 21, 23).

* ``start.gallery_style``: "pill" (as before) or "prominent" — the gallery
  button as a filled button in the accent colour, larger, no thumbnail.
* ``attract.hint_size``: the screensaver hint "Bildschirm berühren …" in
  percent (100 = as before, up to 250).
* ``layout.gallery_size`` and ``layout.back_size``: tiles of the booth's
  gallery and the "Zurück" buttons, m (as before) / l / xl.
* ``attract.fit`` "auto": filled when picture and screen share an
  orientation, the whole picture otherwise.
Every default is today's behaviour, except ``attract.fit``: its default is
"auto" (owner's decision 15.11.2026); both profile copies agree.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "booth"))


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


def test_defaults_are_todays_behaviour_on_both_sides_and_the_screensaver_fits_by_orientation():
    from app import ecosystem
    from boothcore import config
    for prof in (ecosystem.DEFAULT_PROFILE, config.DEFAULT_PROFILE):
        assert prof["start"]["gallery_style"] == "pill"
        assert prof["attract"]["hint_size"] == 100
        # Owner's decision 15.11.2026: the screensaver fits by orientation by default.
        assert prof["attract"]["fit"] == "auto"
        assert prof["layout"]["gallery_size"] == "m" and prof["layout"]["back_size"] == "m"
    for block in ("start", "attract", "layout"):
        assert config.DEFAULT_PROFILE[block] == ecosystem.DEFAULT_PROFILE[block], block



def test_a_stored_profile_that_says_cover_keeps_cover(client):
    # The new default must not rewrite a choice an operator already made.
    _admin(client)
    import json
    from app import db, ecosystem
    from boothcore import config
    dev_id = db.create_device(name="Altbox")["id"]
    ecosystem.save_device_profile(dev_id, {"attract": {"fit": "cover"}}, None)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["attract"]["fit"] == "cover"
    assert config._merge(config.DEFAULT_PROFILE, {"attract": {"fit": "cover"}})["attract"]["fit"] == "cover"
    # A stored profile without the key gets the new default.
    assert config._merge(config.DEFAULT_PROFILE, json.loads('{"attract": {"dim": 30}}'))["attract"]["fit"] == "auto"

def test_every_new_setting_survives_the_form(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Größenbox")["id"]
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": _token(client, dev_id), "mode_photo": "on",
        "gallery_style": "prominent", "attract_hint_size": "180", "attract_fit": "auto",
        "gallery_size": "xl", "back_size": "l"}, follow_redirects=True)
    assert r.status_code == 200
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["start"]["gallery_style"] == "prominent"
    assert p["attract"]["hint_size"] == 180 and p["attract"]["fit"] == "auto"
    assert p["layout"]["gallery_size"] == "xl" and p["layout"]["back_size"] == "l"


def test_strange_values_fall_back_to_the_default(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Größenbox 2")["id"]
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": _token(client, dev_id), "mode_photo": "on",
        "gallery_style": "riesig", "attract_hint_size": "999", "attract_fit": "strecken",
        "gallery_size": "xxl", "back_size": "0"}, follow_redirects=True)
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["start"]["gallery_style"] == "pill"
    assert p["attract"]["hint_size"] == 250 and p["attract"]["fit"] == "auto"
    assert p["layout"]["gallery_size"] == "m" and p["layout"]["back_size"] == "m"


def test_the_booth_admin_offers_and_bounds_the_same_values():
    js = (ROOT / "booth/boothcore/web/static/settings.js").read_text(encoding="utf-8")
    assert '"attract.fit": ["cover", "contain", "auto"]' in js
    assert '"start.gallery_style": ["pill", "prominent"]' in js
    assert '"layout.gallery_size": ["m", "l", "xl"]' in js and '"layout.back_size": ["m", "l", "xl"]' in js
    assert '"attract.hint_size": [100, 250, 10]' in js
    from boothcore.app import PROFILE_LIMITS
    assert PROFILE_LIMITS["attract.hint_size"] == (100, 250)
    for key in ("start.gallery_style", "attract.hint_size", "layout.gallery_size", "layout.back_size"):
        assert f'"{key}": "' in js, f"German label for {key}"


def test_the_kiosk_applies_them():
    js = (ROOT / "booth/boothcore/web/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "booth/boothcore/web/static/app.css").read_text(encoding="utf-8")
    assert "gallery_style" in js and "hint_size" in js and "gallery_size" in js and "back_size" in js
    assert '[data-gallery-style="prominent"]' in css
    assert '[data-gallery-size="l"]' in css and '[data-gallery-size="xl"]' in css
    assert '[data-back-size="l"]' in css and '[data-back-size="xl"]' in css
    assert "--attract-hint-scale" in css
    # More tiles than fit in one row: rows as high as the 4:5 tiles, no overlap.
    assert ".gallery{grid-auto-rows:max-content}" in css
    # "auto" decides per picture: filled when picture and screen share an orientation.
    assert "attractFitFor" in js
