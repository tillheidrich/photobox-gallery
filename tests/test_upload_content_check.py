"""Uploads are judged by their content, and /data never serves a page.

Security review of 11.11.2026, findings S2 and S7: no upload path checked
what it stored, the stored name came from the client, and ``/data`` picked
the content type from the file extension. A page saved as ``x.html`` (or a
script in an ``.svg``) ran under the gallery's own origin.
"""
import io
import json
import secrets

import pytest
from PIL import Image

from app import db, ecosystem, gallery_settings as gsm, media

HTML = b"<!doctype html><html><body><script>alert(document.cookie)</script></body></html>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (60, 80), (90, 120, 150)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _event(**gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Upl {k}", slug=f"upl-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
    return db.get_event_by_id(eid)


def _box(event_id):
    _made = db.create_device(name="Box U")
    did = _made["id"]
    ecosystem.save_device_profile(did, {}, event_id)
    return {**db.get_device_by_id(did), "api_key": _made["api_key"]}


@pytest.mark.parametrize("payload,name", [(HTML, "foto.jpg"), (HTML, "seite.html"), (SVG, "bild.svg")])
def test_the_device_upload_refuses_what_is_not_a_picture(client, payload, name):
    event = _event()
    box = _box(event["id"])
    r = client.post("/api/upload", headers={"X-API-Key": box["api_key"]},
                    data={"event_slug": event["slug"], "file": (io.BytesIO(payload), name)},
                    content_type="multipart/form-data")
    assert r.status_code == 415, r.data
    assert db.count_media(event["id"]) == 0
    assert not list((media.event_storage_dir(event["slug"]) / "orig").iterdir())


@pytest.mark.parametrize("payload,name", [(HTML, "gast.jpg"), (SVG, "gast.svg")])
def test_a_guest_cannot_upload_a_page(client, payload, name):
    event = _event(guest_upload=True)
    r = client.post("/api/guest-upload", data={"event_slug": event["slug"],
                                               "file": (io.BytesIO(payload), name)},
                    content_type="multipart/form-data")
    assert r.status_code == 415, r.data
    assert db.count_media(event["id"]) == 0


def test_the_booth_upload_refuses_a_page_named_like_a_photo(client):
    event = _event()
    box = _box(event["id"])
    r = client.post("/api/v2/booth/media", headers={"X-API-Key": box["api_key"]},
                    data={"uid": "page1234ab", "kind": "photo", "variant": "web",
                          "event_slug": event["slug"], "file": (io.BytesIO(HTML), "x.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 415, r.data
    assert db.count_media(event["id"]) == 0


def test_the_stored_name_is_chosen_by_the_server(client):
    event = _event()
    box = _box(event["id"])
    r = client.post("/api/upload", headers={"X-API-Key": box["api_key"]},
                    data={"event_slug": event["slug"], "file": (_jpeg(), "Mein <b>Foto</b>.html")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    row = db.get_media(r.get_json()["id"])
    assert row["path_original"].endswith(".jpg")
    assert "Foto" not in row["path_original"] and "html" not in row["path_original"]


def test_a_guest_upload_has_a_tighter_size_limit(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_GUEST_UPLOAD_MB", "1")
    event = _event(guest_upload=True)
    big = io.BytesIO(b"\xff\xd8\xff" + b"0" * (2 * 1024 * 1024))
    r = client.post("/api/guest-upload", data={"event_slug": event["slug"], "file": (big, "gross.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 413
    assert db.count_media(event["id"]) == 0


def test_requests_have_a_size_cap(app):
    assert app.config["MAX_CONTENT_LENGTH"] == 450 * 1024 * 1024


@pytest.mark.parametrize("name,payload", [("alt.html", HTML), ("alt.svg", SVG), ("alt.htm", HTML)])
def test_data_never_serves_an_old_file_as_a_page(client, name, payload):
    """Files stored before the check (or by another path) are downloads, not pages."""
    event = _event()
    pfad = media.event_storage_dir(event["slug"]) / "orig" / name
    pfad.write_bytes(payload)
    r = client.get(f"/data/storage/{event['slug']}/orig/{name}")
    assert r.status_code == 200
    assert r.mimetype == "application/octet-stream"
    assert r.headers.get("Content-Disposition", "").startswith("attachment")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_data_serves_a_disguised_jpg_as_an_image_and_forbids_sniffing(client):
    event = _event()
    pfad = media.event_storage_dir(event["slug"]) / "orig" / "alt.jpg"
    pfad.write_bytes(HTML)
    r = client.get(f"/data/storage/{event['slug']}/orig/alt.jpg")
    assert r.mimetype == "image/jpeg"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
