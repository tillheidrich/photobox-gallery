"""Source per photo (no. 60): where a picture came from, as a filter.

Stored in ``media.source`` at every insert path (guest, admin, the old
uploader, folder import, FTP intake); a box upload is known by its device.
Guests see the chips only with ``show_source_filter``; the admin and MCP
always get the source.
"""
import io
import json
import secrets

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import db, ecosystem, gallery_settings as gsm, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("guest_upload_enabled", "0")
    yield


def _event(client, **gs_values):
    key = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"Quelle {key}", slug=f"src-{key}", upload_token=f"tok-{key}")
        db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs_values))})
        return db.get_event_by_id(eid)


def _jpg():
    buf = io.BytesIO()
    Image.new("RGB", (50, 40), (200, 100, 50)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _login(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"


def test_a_guest_upload_is_marked_as_guest(client):
    event = _event(client, guest_upload=True)
    r = client.post("/api/guest-upload", data={"file": (_jpg(), "g.jpg"), "event_slug": event["slug"]},
                    content_type="multipart/form-data")
    with client.application.app_context():
        assert db.get_media(r.get_json()["id"])["source"] == "guest"


def test_an_admin_upload_is_marked_as_admin(client):
    event = _event(client)
    _login(client)
    r = client.post("/api/admin-upload", data={"file": (_jpg(), "a.jpg"), "event_id": str(event["id"]),
                                               "csrf_token": "t"}, content_type="multipart/form-data")
    with client.application.app_context():
        assert db.get_media(r.get_json()["id"])["source"] == "admin"


def test_save_upload_without_a_source_stores_none_as_before(client):
    event = _event(client)
    with client.application.app_context():
        res = media.save_upload(event["id"], FileStorage(_jpg(), filename="x.jpg"))
        assert db.get_media(res["id"])["source"] is None


def test_a_box_picture_is_named_after_its_box(client):
    from app import media_source
    event = _event(client)
    with client.application.app_context():
        con = db.get_db()
        cur = con.execute("INSERT INTO devices(name, api_key) VALUES (?, ?)", ("Box Saal", secrets.token_hex(8)))
        dev = cur.lastrowid
        con.commit()
        con.close()
        res = media.save_upload(event["id"], FileStorage(_jpg(), filename="b.jpg"))
        con = db.get_db()
        con.execute("UPDATE media SET device_id=? WHERE id=?", (dev, res["id"]))
        con.commit()
        con.close()
        rows = db.list_media(event["id"])
        summary = media_source.summarize(rows)
    assert summary["of"][res["id"]] == f"box:{dev}"
    assert any(c["label"] == "Box Saal" for c in summary["chips"])


def _two_sources(client, **gs):
    event = _event(client, guest_upload=True, **gs)
    client.post("/api/guest-upload", data={"file": (_jpg(), "g.jpg"), "event_slug": event["slug"]},
                content_type="multipart/form-data")
    with client.application.app_context():
        media.save_upload(event["id"], FileStorage(_jpg(), filename="a.jpg"), source="admin")
    return event


def test_guests_see_no_source_chips_unless_switched_on(client):
    event = _two_sources(client)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert 'data-filter="src:' not in page and "data-source=" not in page


def test_guests_see_source_chips_when_switched_on(client):
    event = _two_sources(client, show_source_filter=True)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    # Guests read their own words: the admin upload is "Fotograf" (final review U2).
    assert 'data-filter="src:guest"' in page and 'data-filter="src:photographer"' in page
    assert 'data-source="guest"' in page


def test_the_admin_sees_source_chips_on_the_event_page(client):
    event = _two_sources(client)
    _login(client)
    page = client.get(f"/admin/events/{event['id']}/edit").get_data(as_text=True)
    assert 'data-src-filter="guest"' in page and 'data-src-filter="admin"' in page


def test_mcp_listing_names_the_source(client):
    event = _two_sources(client)
    from app import mcp_more
    with client.application.test_request_context("/mcp"):
        rows = db.list_media(event["id"])
        views = [mcp_more._media_view(r) for r in rows]
    assert {"guest", "admin"} <= {v["source"] for v in views}
    assert all("source_label" in v for v in views)
