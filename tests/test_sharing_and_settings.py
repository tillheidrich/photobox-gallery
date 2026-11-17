"""Freigabe-Links (Ablauf, Widerruf, Passphrase, Zählung), Farbmarkierungen, Galerie-Einstellungen."""
import io
import json
import secrets

from PIL import Image


def _event_with_photo(client):
    client.get("/healthz")
    from app import db, ecosystem
    slug = "share-" + secrets.token_hex(3)
    eid = db.create_event(name="Teilen Test", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (eid,)); con.commit(); con.close()
    dev = db.create_device(name="B")
    ecosystem.save_device_profile(dev["id"], {}, eid)
    buf = io.BytesIO(); Image.new("RGB", (400, 500), (10, 120, 200)).save(buf, "JPEG"); buf.seek(0)
    r = client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]}, content_type="multipart/form-data",
                    data={"uid": "shr" + secrets.token_hex(4), "kind": "photo", "variant": "web", "event_slug": slug,
                          "file": (buf, "a.jpg")})
    assert r.status_code == 200
    return db.get_event_by_slug(slug), r.get_json()["id"]


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"


def test_share_link_lifecycle(client):
    event, mid = _event_with_photo(client)
    from app import sharelinks
    link = sharelinks.create(event["id"], media_ids=[mid], title="Familie", days=30)
    page = client.get(f"/l/{link['slug']}")
    assert page.status_code == 200 and b"Familie" in page.data
    assert page.headers["X-Robots-Tag"].startswith("noindex")
    f = client.get(f"/l/{link['slug']}/file/{mid}?download=1")
    assert f.status_code == 200
    assert client.get(f"/l/{link['slug']}/file/{mid + 999}").status_code == 404   # fremde ID
    row = sharelinks.get(link["slug"])
    assert row["views"] == 1 and row["downloads"] == 1 and row["visitors"] == 1
    sharelinks.revoke(link["id"], event["id"])
    assert client.get(f"/l/{link['slug']}").status_code == 410


def test_share_link_passphrase_and_expiry(client):
    event, mid = _event_with_photo(client)
    from app import sharelinks, db
    link = sharelinks.create(event["id"], password="geheim", days=0)
    assert client.get(f"/l/{link['slug']}").status_code == 401
    assert client.get(f"/l/{link['slug']}/file/{mid}").status_code == 401
    assert client.post(f"/l/{link['slug']}", data={"pw": "falsch"}).status_code == 401
    ok = client.post(f"/l/{link['slug']}", data={"pw": "geheim"})
    assert ok.status_code == 200
    assert client.get(f"/l/{link['slug']}/file/{mid}").status_code == 200   # Cookie-Nachweis
    exp = sharelinks.create(event["id"], days=5)
    con = db.get_db(); con.execute("UPDATE share_links SET expires_at='2000-01-01 00:00:00' WHERE id=?", (exp["id"],)); con.commit(); con.close()
    assert client.get(f"/l/{exp['slug']}").status_code == 410


def test_share_days_validation():
    from app import sharelinks
    import pytest
    for bad in ("abc", -1, 99999, [7]):
        with pytest.raises(ValueError):
            sharelinks.read_days(bad)


def test_tags_guest_only_in_proofing(client):
    event, mid = _event_with_photo(client)
    r = client.post(f"/api/photo/{mid}/tag", json={"tag": "red"})
    assert r.status_code == 403
    from app import db
    con = db.get_db(); con.execute("UPDATE events SET gallery_json=? WHERE id=?", (json.dumps({"mode": "proofing"}), event["id"])); con.commit(); con.close()
    assert client.post(f"/api/photo/{mid}/tag", json={"tag": "red"}).status_code == 200
    assert client.post(f"/api/photo/{mid}/tag", json={"tag": "pink"}).status_code == 400
    # Präsentation blendet Rot aus
    con = db.get_db(); con.execute("UPDATE events SET gallery_json=? WHERE id=?", (json.dumps({"mode": "presentation"}), event["id"])); con.commit(); con.close()
    g = client.get(f"/gallery/{event['slug']}")
    assert f'data-id="{mid}"'.encode() not in g.data


def test_settings_toggle_sharing_and_expiry(client):
    event, mid = _event_with_photo(client)
    _admin(client)
    r = client.post(f"/admin/events/{event['id']}/gallery-settings", data={"csrf_token": "tok", "layout": "grid", "likes": "1"})
    assert r.status_code == 302
    with client.session_transaction() as s:
        s["admin_logged_in"] = False
    g = client.get(f"/gallery/{event['slug']}")
    assert b"photo-grid--grid" in g.data and b'title="WhatsApp"' not in g.data and b"lbDownload\" hidden" in g.data
    assert client.get(f"/gallery/{event['slug']}/photo/{mid}/download").status_code == 403
    from app import db
    con = db.get_db(); con.execute("UPDATE events SET gallery_json=? WHERE id=?", (json.dumps({"expires_at": "2000-01-01T00:00"}), event["id"])); con.commit(); con.close()
    assert client.get(f"/gallery/{event['slug']}").status_code == 410


def test_watermark_route(client):
    event, mid = _event_with_photo(client)
    from app import db
    con = db.get_db(); con.execute("UPDATE events SET gallery_json=? WHERE id=?", (json.dumps({"watermark": True}), event["id"])); con.commit(); con.close()
    g = client.get(f"/gallery/{event['slug']}")
    assert f"/wm/{mid}/thumb.jpg".encode() in g.data
    w = client.get(f"/gallery/{event['slug']}/wm/{mid}/full.jpg")
    assert w.status_code == 200 and w.mimetype == "image/jpeg"


def test_admin_event_page_shows_new_sections(client):
    event, mid = _event_with_photo(client)
    _admin(client)
    r = client.get(f"/admin/events/{event['id']}/edit")
    assert b"Galerie-Einstellungen" in r.data and b"Freigabe-Links" in r.data
