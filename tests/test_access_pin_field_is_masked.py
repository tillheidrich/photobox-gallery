"""The gallery's booth form never shows the booth PIN (16.11.2026).

``access_pin`` was a text field filled with the stored PIN. Now it is masked
and empty. Empty means "unchanged" (the stored PIN stays), so saving the form
for any other reason never erases it — the same rule the booth follows for
an empty or masked PIN (T-C4). To hand the decision back to the booth's own
PIN, a separate box says so explicitly.
"""
import re

from app import db, ecosystem


def _admin(client):
    client.get("/healthz")
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _token(client, dev_id):
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)


def _box_with_pin(name, pin="135790"):
    dev_id = db.create_device(name=name)["id"]
    prof = ecosystem.device_profile(db.get_device_by_id(dev_id))
    prof["access"]["pin"] = pin
    ecosystem.save_device_profile(dev_id, prof, None)
    return dev_id


def _pin(dev_id):
    return ecosystem.device_profile(db.get_device_by_id(dev_id))["access"]["pin"]


def test_the_form_shows_a_masked_empty_field_and_never_the_pin(client):
    _admin(client)
    dev_id = _box_with_pin("Maske 1")
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    field = re.search(r'<input[^>]*name="access_pin"[^>]*>', body).group(0)
    assert 'type="password"' in field and 'value=""' in field and 'autocomplete="new-password"' in field
    assert "135790" not in body
    assert 'name="access_pin_clear"' in body


def test_saving_with_the_field_empty_keeps_the_pin(client):
    _admin(client)
    dev_id = _box_with_pin("Maske 2")
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": _token(client, dev_id), "mode_photo": "on", "access_pin": ""}, follow_redirects=True)
    assert _pin(dev_id) == "135790"


def test_a_refused_pin_keeps_the_old_one(client):
    _admin(client)
    dev_id = _box_with_pin("Maske 3")
    for wrong in ("1234", "geheim"):
        client.post(f"/admin/devices/{dev_id}/booth", data={
            "csrf_token": _token(client, dev_id), "mode_photo": "on", "access_pin": wrong}, follow_redirects=True)
        assert _pin(dev_id) == "135790", wrong


def test_a_new_pin_is_taken_and_the_box_can_be_handed_back_its_own(client):
    _admin(client)
    dev_id = _box_with_pin("Maske 4")
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": _token(client, dev_id), "mode_photo": "on", "access_pin": "864209"}, follow_redirects=True)
    assert _pin(dev_id) == "864209"
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": _token(client, dev_id), "mode_photo": "on", "access_pin": "",
        "access_pin_clear": "on"}, follow_redirects=True)
    assert _pin(dev_id) == ""
