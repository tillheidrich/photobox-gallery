"""PIN und Sperre lassen sich aus der Galerie setzen."""
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
    assert config.DEFAULT_PROFILE["access"] == ecosystem.DEFAULT_PROFILE["access"]


def test_pin_and_switches_survive_the_form(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="PIN-Box")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on",
        "access_pin": "135790", "lock_on_booth": "on", "hotspot_seconds": "5",
    }, follow_redirects=True)
    access = ecosystem.device_profile(db.get_device_by_id(dev_id))["access"]
    assert access == {"pin": "135790", "lock_on_booth": True, "hotspot_seconds": 5, "lock_icon": False}


def test_a_too_short_pin_is_refused_rather_than_stored(client):
    """Eine dreistellige PIN wäre schlechter als gar keine."""
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="PIN-Box 2")["id"]
    token = _token(client, dev_id)
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "access_pin": "12",
    }, follow_redirects=True)
    assert "mindestens 4" in r.get_data(as_text=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["access"]["pin"] == ""


def test_the_factory_pin_and_guessable_pins_are_refused_from_the_gallery(client):
    """Same rule as at the booth: four digits or more, not 2580, no 0000 or 1234."""
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="PIN-Box 5")["id"]
    for weak in ("2580", "471", "1234", "000000", "123456", "987654"):
        token = _token(client, dev_id)
        r = client.post(f"/admin/devices/{dev_id}/booth", data={
            "csrf_token": token, "mode_photo": "on", "access_pin": weak,
        }, follow_redirects=True)
        assert "nicht übernommen" in r.get_data(as_text=True), weak
        assert ecosystem.device_profile(db.get_device_by_id(dev_id))["access"]["pin"] == "", weak


def test_an_existing_short_pin_survives_saving_the_form_unchanged(client):
    """Profiles from before the current rule must not lose their PIN on the next save."""
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="PIN-Box 6")["id"]
    prof = ecosystem.device_profile(db.get_device_by_id(dev_id))
    prof["access"]["pin"] = "1111"
    ecosystem.save_device_profile(dev_id, prof, None)
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "access_pin": "1111",
    }, follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["access"]["pin"] == "1111"


def test_letters_are_stripped_with_a_word_of_explanation(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="PIN-Box 3")["id"]
    token = _token(client, dev_id)
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "access_pin": "geheim",
    }, follow_redirects=True)
    assert "nur Ziffern" in r.get_data(as_text=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["access"]["pin"] == ""


def test_the_form_shows_the_fields(client):
    _admin(client)
    from app import db
    dev_id = db.create_device(name="PIN-Box 4")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'name="access_pin"' in body and 'name="lock_on_booth"' in body
    assert 'name="hotspot_seconds"' in body


def test_gallery_and_booth_judge_pins_alike():
    from app.blueprints.admin_booth import _weak_pin_reason
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore.config import weak_pin_reason
    for pin in ("2580", "4711", "12345", "000000", "123456", "654321", "890123", "468213",
                "135790", "11112222", "24682468"):
        assert bool(_weak_pin_reason(pin)) == bool(weak_pin_reason(pin)), pin
