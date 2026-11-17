"""Camera, printer and storage of a box from the gallery form.

These settings live at the box (not in the profile) and reach it as the job
``set_local_config``. Until now only the assistant interface (MCP) could send
that job; the operator at a venue, on a phone, could not. The form sends only
what the operator filled in: the gallery does not know the box's current values,
so "leave as it is" is the default of every field.
"""
import json
import re

import pytest


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


def _box(name="Hardwarebox"):
    from app import db
    return db.create_device(name=name)["id"]


def _jobs(dev_id):
    from app import db
    with db.get_db() as con:
        rows = con.execute("SELECT type, payload_json FROM device_jobs WHERE device_id=? ORDER BY id", (dev_id,)).fetchall()
    return [(r["type"], json.loads(r["payload_json"] or "{}")) for r in rows]


def _post(client, dev_id, **fields):
    data = {"csrf_token": _token(client, dev_id)}
    data.update(fields)
    return client.post(f"/admin/devices/{dev_id}/booth/hardware", data=data, follow_redirects=True)


def test_the_form_sends_only_what_was_filled_in(client):
    _admin(client)
    dev_id = _box()
    r = _post(client, dev_id, hw_camera_rotate="90", hw_camera_mirror_preview="aus",
              hw_printer_sheets_total="18", hw_printers_1_brightness="-5", hw_booth_name="  Bühne  ",
              hw_camera_backend="", hw_kiosk="")
    assert r.status_code == 200
    jobs = _jobs(dev_id)
    assert [t for t, _ in jobs] == ["set_local_config"]
    assert jobs[0][1] == {"camera.rotate": 90, "camera.mirror_preview": False,
                          "printer.sheets_total": 18, "printers.1.brightness": -5, "booth_name": "Bühne"}
    assert "warten auf die Box" in r.get_data(as_text=True)


def test_a_switch_set_to_on_is_true(client):
    _admin(client)
    dev_id = _box()
    _post(client, dev_id, hw_camera_mirror_preview="ein", hw_kiosk="ein", hw_storage_originals="aus")
    assert _jobs(dev_id)[0][1] == {"camera.mirror_preview": True, "kiosk": True, "storage.originals": False}


def test_an_empty_form_sends_no_job(client):
    _admin(client)
    dev_id = _box()
    r = _post(client, dev_id)
    assert _jobs(dev_id) == []
    assert "nichts" in r.get_data(as_text=True).lower()


def test_a_wrong_value_is_named_and_the_rest_still_goes(client):
    _admin(client)
    dev_id = _box()
    r = _post(client, dev_id, hw_camera_rotate="45", hw_printer_sheets_total="viele",
              hw_printers_2_contrast="999", hw_print_routing="failover")
    assert _jobs(dev_id)[0][1] == {"print_routing": "failover"}
    page = r.get_data(as_text=True)
    for label in ("Bild drehen", "Blätter", "Kontrast"):
        assert label in page


def test_a_field_the_box_may_not_change_is_ignored(client):
    _admin(client)
    dev_id = _box()
    _post(client, dev_id, hw_admin_pin="123456", hw_server_url="https://x.example", hw_camera_rotate="180")
    assert _jobs(dev_id)[0][1] == {"camera.rotate": 180}


def test_without_the_csrf_token_nothing_is_sent(client):
    _admin(client)
    dev_id = _box()
    r = client.post(f"/admin/devices/{dev_id}/booth/hardware", data={"hw_camera_rotate": "90"})
    assert r.status_code == 400
    assert _jobs(dev_id) == []


def test_an_unknown_box_is_404(client):
    _admin(client)
    token = _token(client, _box())
    r = client.post("/admin/devices/99999/booth/hardware", data={"csrf_token": token, "hw_camera_rotate": "90"})
    assert r.status_code == 404


def test_the_card_is_outside_the_profile_form(client):
    """A form inside a form is dropped by the browser (see test_admin_nested_forms)."""
    _admin(client)
    dev_id = _box()
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    card = page.split('id="hardware"', 1)[1]
    assert f'action="/admin/devices/{dev_id}/booth/hardware"' in card
    assert "Einstellungen an die Box schicken" in card
    # Fields are in German words, not in key names.
    assert "Live-Bild spiegeln" in card and "Bild drehen" in card
    assert re.search(r'name="hw_camera_rotate"', card)
