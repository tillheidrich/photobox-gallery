"""The booth learns that its event sets the layouts, and learns it in time.

The booth's templates page (owner, 16.11.2026) shows a switch per layout. An
event with "Layouts für dieses Event" ticked wins over the box (it overrides
the box profile in ``profile_for_box``), so the box must say "vom Event
vorgegeben" instead of letting a change vanish at the next sync. The gallery
says so in the profile: ``event.layouts_fixed``.

And a changed choice on the event page has to reach the boxes on that event:
the gallery sends a profile only when the box's profile version changes, and
saving the event page did not change it — a box that had synced kept the old
layouts until something else touched its profile.
"""
import secrets

import pytest

from app import db, ecosystem


@pytest.fixture()
def admin(client):
    client.get("/healthz")
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _event():
    k = secrets.token_hex(3)
    eid = db.create_event(name=f"Layouts {k}", slug=f"ly-{k}", upload_token=f"t-{k}")
    return db.get_event_by_id(eid)


def _box(event):
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    ecosystem.save_device_profile(dev["id"], {}, event["id"])
    return db.get_device_by_id(dev["id"])


def _save(admin, event, **fields):
    data = {"csrf_token": "test-token", "name": event["name"], "slug": event["slug"], "ev_layouts_form": "1"}
    data.update(fields)
    return admin.post(f"/admin/events/{event['id']}/edit", data=data)


def test_an_event_without_a_layout_choice_leaves_the_box_its_say(admin):
    event = _event()
    box = _box(event)
    _save(admin, event)
    assert ecosystem.profile_for_box(db.get_device_by_id(box["id"]), "https://x.de")["event"]["layouts_fixed"] is False


def test_an_event_with_ticked_layouts_tells_the_box(admin):
    event = _event()
    box = _box(event)
    _save(admin, event, ev_collage_builtins=["grid4", "schlicht-1-weiss"])
    prof = ecosystem.profile_for_box(db.get_device_by_id(box["id"]), "https://x.de")
    assert prof["collage_exact"] is True and prof["event"]["layouts_fixed"] is True


def test_a_box_without_an_event_decides_itself():
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://x.de")
    assert prof["event"].get("layouts_fixed", False) is False


def test_saving_the_event_page_sends_the_boxes_on_it_a_new_profile(admin):
    event = _event()
    box = _box(event)
    andere = _box(_event())
    vorher = db.get_device_by_id(box["id"])["profile_version"]
    andere_vorher = db.get_device_by_id(andere["id"])["profile_version"]
    _save(admin, event, ev_collage_builtins=["grid4"])
    assert db.get_device_by_id(box["id"])["profile_version"] > vorher
    assert db.get_device_by_id(andere["id"])["profile_version"] == andere_vorher
