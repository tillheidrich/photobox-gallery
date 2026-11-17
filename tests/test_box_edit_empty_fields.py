"""An empty Watchpath or Glob stays empty on the box edit page.

Found 15.11.2026: a box without a watch path showed the word "None" in the
field, and saving the page unchanged stored the text "None" - for the old
uploader a folder called None.
"""
import re


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"


def _value(html, name):
    m = re.search(r'name="%s"[^>]*?value="([^"]*)"' % name, html, re.S)
    assert m, name
    return m.group(1)


def test_empty_fields_show_empty_and_save_empty(client):
    from app import db
    client.get("/healthz")
    device = db.create_device(name="Box ohne Pfad")
    _admin(client)
    page = client.get(f"/admin/devices/{device['id']}/edit").get_data(as_text=True)
    assert _value(page, "default_watch_path") == ""
    assert _value(page, "default_event_glob") == ""
    form = {"csrf_token": "x", "name": "Box ohne Pfad", "active": "1",
            "default_watch_path": _value(page, "default_watch_path"),
            "default_event_glob": _value(page, "default_event_glob")}
    client.post(f"/admin/devices/{device['id']}/edit", data=form)
    stored = db.get_device_by_id(device["id"])
    assert stored["default_watch_path"] is None and stored["default_event_glob"] is None


def test_a_none_saved_by_the_old_page_is_cleaned_up(client):
    from app import db
    client.get("/healthz")
    device = db.create_device(name="Box mit None")
    db.update_device(device["id"], {"default_watch_path": "None", "default_event_glob": "None"})
    _admin(client)
    page = client.get(f"/admin/devices/{device['id']}/edit").get_data(as_text=True)
    assert _value(page, "default_watch_path") == ""
    client.post(f"/admin/devices/{device['id']}/edit",
                data={"csrf_token": "x", "name": "Box mit None", "active": "1",
                      "default_watch_path": "None", "default_event_glob": "Event_*"})
    stored = db.get_device_by_id(device["id"])
    assert stored["default_watch_path"] is None and stored["default_event_glob"] == "Event_*"
