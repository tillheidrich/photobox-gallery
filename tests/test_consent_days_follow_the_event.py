"""The number of days the booth promises is the number the gallery keeps.

The consent note at the booth says "gelöscht nach {tage} Tagen". The number
came from the booth profile (consent.days, default 30) while the gallery
deletes after the event's retention — a booth on a 14-day event promised 30.
Linked to an event, the booth now gets the event's number; consent.days is
only what a booth without a gallery event says.
"""
import secrets

from app import accounts


def _setup(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()


def test_a_linked_booth_promises_the_events_retention(client):
    _setup(client)
    from app import db, ecosystem
    eid = db.create_event(slug="kurz-" + secrets.token_hex(3), name="Kurz", upload_token=secrets.token_hex(6),
                          retention_days=14)
    dev = db.create_device(name="Fristbox")
    ecosystem.save_device_profile(dev["id"], {"consent": {"enabled": True, "days": 30}}, eid)
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://galerie.example")
    assert prof["consent"]["days"] == 14
    assert prof["consent"]["enabled"] is True


def test_without_an_event_the_profile_number_stands(client):
    _setup(client)
    from app import db, ecosystem
    dev = db.create_device(name="Ohne Event")
    ecosystem.save_device_profile(dev["id"], {"consent": {"enabled": True, "days": 45}}, None)
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://galerie.example")
    assert prof["consent"]["days"] == 45


def test_the_form_says_where_the_number_comes_from(client):
    _setup(client)
    from app import db
    dev = db.create_device(name="Formular")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "test-token"
    html = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert "Aufbewahrungsfrist des Events" in html
