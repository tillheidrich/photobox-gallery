"""A very clean gallery: every extra on the guest pages can be switched off.

Asked for on 15.11.2026: besides the switches that existed (WhatsApp,
sharepic, share sheet, copy link, single download, likes, selection, upload,
guest delete, ZIP) the "QR-Code drucken" button, the photo notification
signup, the run page's "mit/ohne Rahmen", "Original" and "Meine Aufnahmen",
and sharing under /meine must follow a switch too. Defaults keep today's page.
A hidden button does not leave its route open where the gallery already
enforces the same switch on the server.
"""
import io
import json
import secrets

from PIL import Image

from app import gallery_settings as gsm

NEW_SWITCHES = ("print_qr", "notify_signup", "run_frame_choice", "my_shots_link")


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (900, 1200), (60, 90, 120)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _event(client, gallery=None, *, raw=False):
    """An event with one booth run: a framed photo, its unframed capture, the original."""
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    slug = "schlicht-" + secrets.token_hex(3)
    uid = "sl" + secrets.token_hex(5)
    eid = db.create_event(name="Schlicht", slug=slug, upload_token=secrets.token_hex(8))
    db.update_event(eid, published=1, gallery_json=json.dumps(gsm.clean(gallery or {})))
    device = db.create_device(name=f"Box {slug}")
    ecosystem.save_device_profile(device["id"], {}, eid)
    for item_uid, kind, variant in ((uid, "photo", "web"), (uid, "photo", "original"), (uid + ".1", "original", "web")):
        r = client.post("/api/v2/booth/media", headers={"X-API-Key": device["api_key"]},
                        data={"uid": item_uid, "session_uid": uid, "event_slug": slug, "kind": kind,
                              "variant": variant, "file": (_jpeg(), "f.jpg")},
                        content_type="multipart/form-data")
        assert r.status_code in (200, 201), r.data[:300]
    event = db.get_event_by_id(eid)
    code = f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"
    con = db.get_db()
    ids = {r["uid"]: r["id"] for r in con.execute(
        "SELECT id, uid FROM media WHERE event_id=? AND variant='web'", (eid,))}
    con.close()
    return event, code, ids[uid], ids[uid + ".1"]


def _off(*keys):
    return {k: False for k in keys}


def test_the_new_switches_exist_and_default_to_on():
    for key in NEW_SWITCHES:
        assert gsm.DEFAULTS[key] is True, key


def test_by_default_the_pages_are_what_they_were(client):
    event, code, framed, unframed = _event(client)
    html = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert f"/gallery/{event['slug']}/print" in html
    assert 'id="subscribeEmail"' in html
    run = client.get(f"/p/{code}").get_data(as_text=True)
    assert f"/photo/{unframed}/download" in run          # ohne Rahmen
    assert 'href="/meine"' in run
    assert 'onclick="shareItem(' in run
    assert client.get(f"/gallery/{event['slug']}/print").status_code == 200


def test_qr_print_off_hides_the_button_and_closes_the_page_for_guests(client):
    event, *_ = _event(client, _off("print_qr"))
    html = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert f"/gallery/{event['slug']}/print" not in html
    assert client.get(f"/gallery/{event['slug']}/print").status_code == 404
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    assert client.get(f"/gallery/{event['slug']}/print").status_code == 200


def test_notification_signup_off_hides_the_form_and_refuses_the_signup(client):
    event, *_ = _event(client, _off("notify_signup"))
    html = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert 'id="subscribeEmail"' not in html
    r = client.post("/api/notify/subscribe", json={"eventSlug": event["slug"], "email": "gast@example.org"})
    assert r.status_code == 403


def test_the_run_page_follows_single_download(client):
    event, code, framed, unframed = _event(client, _off("download_single"))
    run = client.get(f"/p/{code}").get_data(as_text=True)
    assert "/download?" not in run
    r = client.get(f"/gallery/{event['slug']}/photo/{framed}/download?size=large&code={code}")
    assert r.status_code == 403


def test_the_run_page_follows_the_original_switch(client):
    event, code, framed, unframed = _event(client, _off("download_original", "run_frame_choice"))
    run = client.get(f"/p/{code}").get_data(as_text=True)
    assert "size=original" not in run
    assert f"/photo/{framed}/download?size=" in run         # the scaled download stays
    r = client.get(f"/gallery/{event['slug']}/photo/{framed}/download?size=original&code={code}")
    assert r.status_code == 403


def test_frame_choice_off_offers_one_picture_and_closes_the_other(client):
    event, code, framed, unframed = _event(client, _off("run_frame_choice"))
    run = client.get(f"/p/{code}").get_data(as_text=True)
    assert f"/photo/{unframed}/" not in run
    assert f"/photo/{framed}/download" in run
    r = client.get(f"/gallery/{event['slug']}/photo/{unframed}/download?size=original&code={code}")
    assert r.status_code == 403


def test_my_shots_link_off_hides_it_on_the_run_page_and_in_the_gallery(client):
    event, code, *_ = _event(client, _off("my_shots_link"))
    run = client.get(f"/p/{code}").get_data(as_text=True)
    assert 'href="/meine"' not in run
    html = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)   # the run was scanned
    assert 'href="/meine"' not in html


def test_sharing_off_takes_the_share_button_off_the_run_page_and_meine(client):
    event, code, *_ = _event(client, _off("share_native", "share_link_copy"))
    run = client.get(f"/p/{code}").get_data(as_text=True)
    assert 'onclick="shareItem(' not in run
    mine = client.get("/meine").get_data(as_text=True)
    assert "Schlicht" in mine
    assert 'onclick="mineShare(' not in mine


def test_only_link_copy_still_shares_by_link(client):
    event, code, *_ = _event(client, _off("share_native"))
    run = client.get(f"/p/{code}").get_data(as_text=True)
    assert 'onclick="shareItem(' in run
    assert '"native": false' in run and '"copy": true' in run


def test_the_event_form_has_the_switches_and_the_presets(client):
    event, *_ = _event(client)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    html = client.get(f"/admin/events/{event['id']}/edit").get_data(as_text=True)
    for key in NEW_SWITCHES:
        assert f'name="{key}"' in html, key
    assert "Schlichte Galerie" in html and "Alles anzeigen" in html
    assert 'data-preset="schlicht"' in html and 'data-preset="alles"' in html


def test_saving_the_form_keeps_switches_that_were_not_touched(client):
    """The settings form stores the new switches like every other box."""
    from app import db
    event, *_ = _event(client)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/events/{event['id']}/gallery-settings",
                data={"csrf_token": "x", "print_qr": "on", "notify_signup": "", "run_frame_choice": "on",
                      "my_shots_link": "on", "share_native": "on"})
    gs = gsm.for_event(db.get_event_by_id(event["id"]))
    assert gs["print_qr"] and gs["run_frame_choice"] and gs["my_shots_link"]
    assert not gs["notify_signup"]
