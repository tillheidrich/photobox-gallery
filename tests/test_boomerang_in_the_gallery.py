"""A 9:16 boomerang from the booth, all the way through the gallery.

Checked for the boomerang work of 15.11.2026: upload with its poster, the
tile and the lightbox keep the portrait shape (the poster's 1080x1920),
the run page plays it, "Meine Aufnahmen" lists it in its shape (the
<video> carries width and height, so a portrait film does not first take a
landscape box and jump), and the download keeps the .mp4.
"""
import io
import re

from PIL import Image


def _jpeg(size):
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 90, 160)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _setup(client):
    client.get("/healthz")
    import secrets
    from app import db, ecosystem
    slug = "boom-" + secrets.token_hex(3)
    eid = db.create_event(name="Boomerang Abend", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
    con.commit()
    con.close()
    made = db.create_device(name="Box B")
    ecosystem.save_device_profile(made["id"], {"modes": ["boomerang"]}, eid)
    return db.get_event_by_id(eid), made["api_key"]


def _upload(client, event, key, uid=""):
    import secrets
    uid = uid or "bo" + secrets.token_hex(4)
    r = client.post("/api/v2/booth/media", headers={"X-API-Key": key}, content_type="multipart/form-data",
                    data={"uid": uid, "kind": "boomerang", "variant": "web", "event_slug": event["slug"],
                          "file": (io.BytesIO(b"\x00\x00\x00 ftypisom boomerang"), "boomerang.mp4"),
                          "poster": (_jpeg((1080, 1920)), "poster.jpg")})
    assert r.status_code == 200, r.data
    from app import ecosystem
    return f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"


def test_a_portrait_boomerang_keeps_its_shape_in_tile_and_lightbox(client):
    event, key = _setup(client)
    _upload(client, event, key)
    from app import db
    m = db.list_media(event["id"])[0]
    assert m["kind"] == "boomerang" and (m["width"], m["height"]) == (1080, 1920)
    html = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    tile = re.search(r'<div class="photo-item[^>]*data-id="%d"[^>]*>' % m["id"], html, re.S).group(0)
    assert 'data-video="1"' in tile
    assert 'data-pswp-width="1080"' in tile and 'data-pswp-height="1920"' in tile


def test_the_run_page_and_meine_aufnahmen_show_it_as_a_portrait_film(client):
    event, key = _setup(client)
    code = _upload(client, event, key)
    page = client.get(f"/p/{code}").get_data(as_text=True)
    assert "<video" in page
    mine = client.get("/meine").get_data(as_text=True)
    video = re.search(r"<video[^>]*>", mine).group(0)
    assert 'width="1080"' in video and 'height="1920"' in video


def test_the_download_is_the_film(client):
    event, key = _setup(client)
    code = _upload(client, event, key)
    from app import db
    m = db.list_media(event["id"])[0]
    r = client.get(f"/gallery/{event['slug']}/photo/{m['id']}/download?size=original&code={code}")
    assert r.status_code == 200
    disp = r.headers.get("Content-Disposition", "")
    assert ".mp4" in disp, disp
