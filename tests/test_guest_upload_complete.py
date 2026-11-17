"""Guest upload, complete (no. 58): PIN, grace period, per-file feedback, table sign.

Every part is a switch of its own and off by default: an event that sets
none of them uploads exactly as before.
"""
import io
import json
import secrets
from datetime import date, timedelta

import pytest
from PIL import Image

from app import db, ecosystem, gallery_settings as gsm


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("guest_upload_enabled", "0")
    yield


def _event(client, *, event_date=None, **gs_values):
    key = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"Upload {key}", slug=f"upl-{key}", upload_token=f"tok-{key}")
        fields = {"published": 1, "gallery_json": json.dumps(gsm.clean(gs_values))}
        if event_date:
            fields["date"] = event_date
        db.update_event(eid, fields)
        return db.get_event_by_id(eid)


def _picture():
    buf = io.BytesIO()
    Image.new("RGB", (80, 100), (150, 140, 120)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _upload(client, event, **extra):
    data = {"file": (_picture(), "guest.jpg"), "event_slug": event["slug"]}
    data.update(extra)
    return client.post("/api/guest-upload", data=data, content_type="multipart/form-data")


def _set_pin(client, event, pin):
    from app import guest_upload
    with client.application.app_context():
        guest_upload.set_pin(event["id"], pin)


# ── PIN ───────────────────────────────────────────────────────────────

def test_without_a_pin_the_upload_works_as_before(client):
    event = _event(client, guest_upload=True)
    assert _upload(client, event).status_code == 200


def test_with_a_pin_an_upload_without_it_is_refused_with_a_reason(client):
    event = _event(client, guest_upload=True)
    _set_pin(client, event, "4711")
    answer = _upload(client, event)
    assert answer.status_code == 403
    assert answer.get_json()["error"] == "pin"


def test_the_right_pin_opens_the_upload_and_a_wrong_one_does_not(client):
    event = _event(client, guest_upload=True)
    _set_pin(client, event, "4711")
    assert _upload(client, event, upload_pin="1234").status_code == 403
    assert _upload(client, event, upload_pin="4711").status_code == 200


def test_the_pin_is_stored_hashed_never_in_plain_text(client):
    event = _event(client, guest_upload=True)
    _set_pin(client, event, "4711")
    with client.application.app_context():
        row = db.get_event_by_id(event["id"])
    assert row["upload_pin_hash"] and "4711" not in row["upload_pin_hash"]
    assert "4711" not in (row.get("gallery_json") or "")


def test_wrong_pins_are_braked(client):
    event = _event(client, guest_upload=True)
    _set_pin(client, event, "4711")
    codes = [_upload(client, event, upload_pin=f"{1000 + i}").status_code for i in range(12)]
    assert codes[-1] == 429
    # Braked means braked, even for the right PIN from the same address.
    assert _upload(client, event, upload_pin="4711").status_code == 429


def test_only_four_to_eight_digits_are_a_pin(client):
    from app import guest_upload
    assert guest_upload.valid_pin("4711") and guest_upload.valid_pin("12345678")
    assert not guest_upload.valid_pin("123") and not guest_upload.valid_pin("abcd")
    assert not guest_upload.valid_pin("123456789")


def test_the_gallery_asks_for_the_pin_only_when_one_is_set(client):
    plain = _event(client, guest_upload=True)
    assert 'name="upload_pin"' not in client.get(f"/gallery/{plain['slug']}").get_data(as_text=True)
    locked = _event(client, guest_upload=True)
    _set_pin(client, locked, "4711")
    page = client.get(f"/gallery/{locked['slug']}").get_data(as_text=True)
    assert 'name="upload_pin"' in page and 'inputmode="numeric"' in page


# ── Grace period ("Nachfrist") ────────────────────────────────────────

def test_without_a_grace_period_an_old_event_still_takes_uploads(client):
    old = (date.today() - timedelta(days=40)).isoformat()
    event = _event(client, guest_upload=True, event_date=old)
    assert _upload(client, event).status_code == 200


def test_after_the_grace_period_the_upload_is_closed_with_a_notice(client):
    old = (date.today() - timedelta(days=10)).isoformat()
    event = _event(client, guest_upload=True, event_date=old, upload_grace_days="3")
    answer = _upload(client, event)
    assert answer.status_code == 403 and answer.get_json()["error"] == "closed"
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert 'id="guestFileInput"' not in page
    assert 'data-upload-closed' in page


def test_within_the_grace_period_uploads_are_open(client):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    event = _event(client, guest_upload=True, event_date=yesterday, upload_grace_days="3")
    assert _upload(client, event).status_code == 200


def test_the_last_day_of_the_grace_period_is_still_open(client):
    edge = (date.today() - timedelta(days=3)).isoformat()
    event = _event(client, guest_upload=True, event_date=edge, upload_grace_days="3")
    assert _upload(client, event).status_code == 200


def test_grace_days_must_be_a_small_number(client):
    assert gsm.clean({"upload_grace_days": "abc"})["upload_grace_days"] == ""
    assert gsm.clean({"upload_grace_days": "400"})["upload_grace_days"] == ""
    assert gsm.clean({"upload_grace_days": "7"})["upload_grace_days"] == "7"


# ── Feedback per file, moderation ─────────────────────────────────────

def test_the_upload_form_lists_files_one_by_one(client):
    event = _event(client, guest_upload=True)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert 'id="uploadList"' in page


def test_a_moderated_event_says_the_picture_waits_for_approval(client):
    event = _event(client, guest_upload=True, moderation=True)
    answer = _upload(client, event)
    assert answer.status_code == 200 and answer.get_json().get("pending") is True
    with client.application.app_context():
        row = db.get_media(answer.get_json()["id"])
    assert int(row["hidden"]) == 1


# ── Table sign ────────────────────────────────────────────────────────

def _login(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def test_the_table_sign_is_for_the_admin_only(client):
    event = _event(client, guest_upload=True)
    assert client.get(f"/admin/events/{event['id']}/table-sign").status_code in (302, 401, 403)


def test_the_table_sign_shows_name_qr_and_steps(client):
    event = _event(client, guest_upload=True)
    _login(client)
    page = client.get(f"/admin/events/{event['id']}/table-sign?size=a6").get_data(as_text=True)
    assert event["name"] in page
    assert f"/gallery/{event['slug']}/qr.png" in page
    assert "@page" in page and "A6" in page


def test_the_table_sign_prints_the_pin_only_when_it_is_the_right_one(client, formular):
    event = _event(client, guest_upload=True)
    _set_pin(client, event, "4711")
    _login(client)
    path = f"/admin/events/{event['id']}/table-sign"
    good = formular(path, {"pin": "4711"}).get_data(as_text=True)
    assert 'class="ts-pin"' in good and "4711" in good
    bad = formular(path, {"pin": "9999"}).get_data(as_text=True)
    assert 'class="ts-pin"' not in bad and 'data-pin-wrong' in bad
    # Typed into a form, never into the address (logs, history).
    assert client.get(f"{path}?pin=4711").get_data(as_text=True).count("4711") == 0


def test_setting_a_pin_needs_the_form_token(client, formular):
    event = _event(client, guest_upload=True)
    _login(client)
    path = f"/admin/events/{event['id']}/upload-pin"
    assert client.post(path, data={"pin": "4711"}).status_code == 400
    formular(path, {"pin": "4711"}, von=f"/admin/events/{event['id']}/table-sign")
    from app import guest_upload
    with client.application.app_context():
        assert guest_upload.check_pin(db.get_event_by_id(event["id"]), "4711")
