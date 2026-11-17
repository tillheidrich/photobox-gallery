"""Customer area as a hub (no. 63): per gallery the tiles that apply.

Account setting ``client_hub`` (default off): off, /kunde is the list of
galleries it always was.
"""
import json
import re
import secrets

import pytest

from app import clients, db, ecosystem, gallery_settings as gsm


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    ecosystem.init_schema()
    db.set_setting("silent_mode", "0")
    db.set_setting("client_hub", "0")
    yield


def _event(**gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Hub {k}", slug=f"hub-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
    return db.get_event_by_id(eid)


def _login(client, *events):
    cid = clients.create("Familie Muster")
    code = clients.new_code(cid)
    clients.set_events(cid, [e["id"] for e in events])
    page = client.get("/kunde").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)
    client.post("/kunde", data={"code": code, "csrf_token": token})
    return cid


def _hub(client):
    return client.get("/kunde").get_data(as_text=True)


def test_without_the_switch_the_customer_page_is_unchanged(client):
    ev = _event(guest_upload=True, download_zip=True)
    _login(client, ev)
    assert "hub-tiles" not in _hub(client)


def test_the_hub_shows_only_tiles_that_apply(client):
    db.set_setting("client_hub", "1")
    plain = _event()
    rich = _event(guest_upload=True, download_zip=True, review_mode="an", review_url="https://example.org/r")
    import io
    from PIL import Image
    from werkzeug.datastructures import FileStorage
    from app import media
    for ev in (plain, rich):
        buf = io.BytesIO()
        Image.new("RGB", (40, 30), (1, 2, 3)).save(buf, "JPEG")
        buf.seek(0)
        media.save_upload(ev["id"], FileStorage(buf, filename="h.jpg"))
    _login(client, plain, rich)
    page = _hub(client)
    blocks = dict(re.findall(r'data-hub="([^"]+)"(.*?)</ul>', page, re.S))
    assert set(blocks) == {plain["slug"], rich["slug"]}
    assert f"/gallery/{plain['slug']}\"" in blocks[plain["slug"]]
    assert "download.zip" not in blocks[plain["slug"]]
    assert "tisch-schild" not in blocks[plain["slug"]]
    assert f"/gallery/{rich['slug']}/download.zip" in blocks[rich["slug"]]
    assert f"/kunde/tisch-schild/{rich['slug']}" in blocks[rich["slug"]]
    assert f"/bewertung/{rich['slug']}" in blocks[rich["slug"]]


def test_contact_only_when_the_operator_gave_an_address(client):
    db.set_setting("client_hub", "1")
    ev = _event()
    _login(client, ev)
    db.set_setting("legal_contact_email", "")
    assert "mailto:" not in _hub(client)
    db.set_setting("legal_contact_email", "kontakt@example.org")
    assert "mailto:kontakt@example.org" in _hub(client)
    db.set_setting("legal_contact_email", "")


def test_the_customers_table_sign_is_theirs_only(client):
    db.set_setting("client_hub", "1")
    mine = _event(guest_upload=True)
    other = _event(guest_upload=True)
    no_upload = _event()
    _login(client, mine, no_upload)
    page = client.get(f"/kunde/tisch-schild/{mine['slug']}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert mine["name"] in html and 'name="pin"' not in html
    assert client.get(f"/kunde/tisch-schild/{other['slug']}").status_code == 404
    assert client.get(f"/kunde/tisch-schild/{no_upload['slug']}").status_code == 404


def test_the_table_sign_is_closed_without_the_hub(client):
    ev = _event(guest_upload=True)
    _login(client, ev)
    assert client.get(f"/kunde/tisch-schild/{ev['slug']}").status_code == 404


def test_the_switch_lives_on_the_gallery_defaults_page(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
    assert client.post("/admin/settings/galerie", data={"client_hub": "on"}).status_code == 400
    client.post("/admin/settings/galerie", data={"client_hub": "on", "csrf_token": "t"})
    assert db.get_setting("client_hub") == "1"
    client.post("/admin/settings/galerie", data={"csrf_token": "t"})
    assert db.get_setting("client_hub") == "0"
