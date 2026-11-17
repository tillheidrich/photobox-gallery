"""Sicherheitsgrenzen: /data, CSRF, Box-Schlüssel nur für eigene Events, Ablauf."""
import io
import json

from tests.test_booth_v2 import _jpeg, _setup


def _upload(client, dev, uid, slug=None):
    data = {"uid": uid, "kind": "photo", "variant": "web", "file": (_jpeg(), "a.jpg")}
    if slug:
        data["event_slug"] = slug
    return client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]}, data=data,
                       content_type="multipart/form-data")


def test_data_route_never_serves_database_or_internal_files(client):
    client.get("/healthz")
    for path in ("gallery.sqlite3", ".secret_key", "cache/x", "assets/booth/x.png", "../etc/passwd"):
        assert client.get(f"/data/{path}").status_code == 404, path


def test_admin_booth_posts_require_csrf(client):
    event, dev = _setup(client)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"
    r = client.post(f"/admin/events/{event['id']}/qr-target", data={"qr_target": "external", "external_url": "https://evil.example"})
    assert r.status_code == 400
    r = client.post(f"/admin/events/{event['id']}/qr-target", data={"qr_target": "status", "csrf_token": "tok"})
    assert r.status_code in (200, 302)
    assert client.post("/api/photo/1/tag", data="tag=").status_code == 415


def test_device_cannot_touch_foreign_events(client):
    event_a, dev_a = _setup(client)
    event_b, dev_b = _setup(client)
    assert _upload(client, dev_a, "sec1").status_code == 200
    # fremdes Event per Slug
    assert _upload(client, dev_b, "sec2", slug=event_a["slug"]).status_code == 403
    # fremde uid überschreiben
    assert _upload(client, dev_b, "sec1").status_code == 409
    # fremde Session löschen
    r = client.post("/api/v2/booth/sync", json={"deleted": ["sec1"]}, headers={"X-API-Key": dev_b["api_key"]})
    assert r.status_code == 200
    from app import ecosystem
    assert ecosystem.media_by_uid("sec1", "web") is not None
    client.post("/api/v2/booth/sync", json={"deleted": ["sec1"]}, headers={"X-API-Key": dev_a["api_key"]})
    assert ecosystem.media_by_uid("sec1", "web") is None


def test_expired_gallery_blocks_downloads_and_files(client):
    from app import db, ecosystem
    event, dev = _setup(client)
    assert _upload(client, dev, "exp1").status_code == 200
    m = ecosystem.media_by_uid("exp1", "web")
    assert client.get(f"/data/{m['path_webp']}").status_code == 200
    con = db.get_db()
    con.execute("UPDATE events SET gallery_json=? WHERE id=?", (json.dumps({"expires_at": "2020-01-01"}), event["id"]))
    con.commit(); con.close()
    assert client.get(f"/data/{m['path_webp']}").status_code == 410
    assert client.get(f"/gallery/{event['slug']}/photo/{m['id']}/download").status_code == 410
