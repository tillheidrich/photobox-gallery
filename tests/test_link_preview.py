"""Link previews (Open Graph) for WhatsApp, iMessage, Signal & Co.

A chat app fetches the gallery link without any cookie. What it gets - the
meta tags and /gallery/<slug>/og.jpg - must never show a guest photo of a
gallery that the bare link does not open (password, draft, silent, expired),
and never a hidden, pending, red-hidden or deleted picture. See
app/link_preview.py and SECURITY.md ("Link previews").
"""
import io
import json
import re
import secrets

import pytest
from PIL import Image

from app import db, ecosystem, gallery_settings as gsm, link_preview, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    db.set_setting("silent_mode", "0")
    db.set_setting("app_base_url", "https://fotos.example.test")
    yield
    db.set_setting("app_base_url", "")


def _event(published=1, password="", **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Sommerfest {k}", slug=f"og-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": published, "gallery_json": json.dumps(gsm.clean(gs)),
                          "description": "Fotos vom Sommerfest im Garten"})
    if password:
        db.update_event(eid, password=password)
    return db.get_event_by_id(eid)


#: One colour per picture, so a test can tell from the card which one it shows.
def _photo(event, colour, **cols):
    d = media.event_storage_dir(event["slug"])
    stem = secrets.token_hex(5)
    orig, webp, thumb = d / "orig" / f"{stem}.jpg", d / "webp" / f"{stem}.webp", d / "thumb" / f"{stem}.webp"
    for p, size in ((orig, (900, 600)), (webp, (900, 600)), (thumb, (300, 200))):
        Image.new("RGB", size, colour).save(p, "JPEG" if p.suffix == ".jpg" else "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
        " share_token) VALUES (?,?,?,?,?,?,?,?,?)",
        (event["id"], "photo", f"IMG_{stem}.jpg", rel(orig), rel(webp), rel(thumb), 1, stem, secrets.token_hex(8)))
    mid = cur.lastrowid
    for k, v in cols.items():
        con.execute(f"UPDATE media SET {k}=? WHERE id=?", (v, mid))
    con.commit()
    con.close()
    return db.get_media(mid)


def _og(html):
    return dict(re.findall(r'<meta (?:property|name)="((?:og|twitter):[a-z:_]+)" content="([^"]*)"', html))


def _card(client, slug):
    r = client.get(f"/gallery/{slug}/og.jpg")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    return r, Image.open(io.BytesIO(r.data)).convert("RGB")


def _has_colour(img, colour, tol=40):
    """True when a good part of the card is this colour (a photo, not a speck)."""
    small = img.resize((60, 32))
    raw = small.tobytes()
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    hits = sum(1 for px in pixels if all(abs(a - b) <= tol for a, b in zip(px, colour)))
    return hits > 120


RED_PIC, BLUE_PIC, GREEN_PIC = (220, 30, 30), (30, 60, 220), (40, 200, 60)


def test_a_public_gallery_names_title_description_and_an_absolute_image(client):
    ev = _event()
    _photo(ev, BLUE_PIC)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    og = _og(html)
    assert og["og:title"] == ev["name"]
    assert og["og:description"] == "Fotos vom Sommerfest im Garten"
    assert og["og:type"] == "website"
    assert og["og:image"].startswith(f"https://fotos.example.test/gallery/{ev['slug']}/og.jpg?v=")
    assert og["og:image:width"] == "1200" and og["og:image:height"] == "630"
    assert og["twitter:card"] == "summary_large_image"
    assert og["og:url"] == f"https://fotos.example.test/gallery/{ev['slug']}"


def test_the_card_is_a_small_1200_by_630_jpeg_from_a_visible_photo(client):
    ev = _event()
    _photo(ev, BLUE_PIC)
    r, img = _card(client, ev["slug"])
    assert img.size == (1200, 630)
    assert len(r.data) < 300_000
    assert _has_colour(img, BLUE_PIC)
    assert "public" in r.headers["Cache-Control"]
    assert "Set-Cookie" not in r.headers           # a bot gets no cookie either


def test_hidden_pending_and_red_pictures_never_reach_the_card(client):
    ev = _event(hide_red_in_presentation=True)
    _photo(ev, RED_PIC, hidden=1)
    _photo(ev, GREEN_PIC, hidden=1, review_state="pending")
    _photo(ev, (240, 200, 20), color_tag="red")
    _, img = _card(client, ev["slug"])
    # Nothing visible left: the brand card, no photo colour at all.
    for c in (RED_PIC, GREEN_PIC, (240, 200, 20)):
        assert not _has_colour(img, c)
    assert link_preview.decide(db.get_event_by_id(ev["id"]))[0] == "brand"


def test_a_cover_that_was_hidden_afterwards_is_not_used(client):
    ev = _event()
    pic = _photo(ev, RED_PIC)
    _photo(ev, BLUE_PIC)
    db.update_event(ev["id"], cover_path=pic["path_webp"])
    _, img = _card(client, ev["slug"])
    assert _has_colour(img, RED_PIC)
    con = db.get_db()
    con.execute("UPDATE media SET hidden=1, review_state='pending' WHERE id=?", (pic["id"],))
    con.commit()
    con.close()
    _, img = _card(client, ev["slug"])
    assert not _has_colour(img, RED_PIC) and _has_colour(img, BLUE_PIC)


def test_a_deleted_picture_leaves_the_card(client):
    ev = _event()
    pic = _photo(ev, RED_PIC)
    _, img = _card(client, ev["slug"])
    assert _has_colour(img, RED_PIC)
    db.delete_media(pic["id"])
    _, img = _card(client, ev["slug"])
    assert not _has_colour(img, RED_PIC)


@pytest.mark.parametrize("closed", ["password", "draft", "link_password", "expired", "watermark"])
def test_a_closed_gallery_shows_no_photo_and_no_description(client, closed):
    if closed == "password":
        ev = _event(password="geheim")
    elif closed == "link_password":
        ev = _event(password="geheim", link_password=True)
    elif closed == "draft":
        ev = _event(published=0)
    elif closed == "expired":
        ev = _event(expires_at="2020-01-01T00:00")
    else:
        ev = _event(watermark=True)
    _photo(ev, RED_PIC)
    db.update_event(ev["id"], cover_path=_photo(ev, RED_PIC)["path_webp"])
    _, img = _card(client, ev["slug"])
    assert not _has_colour(img, RED_PIC)
    meta = link_preview.meta(db.get_event_by_id(ev["id"]), "https://fotos.example.test")
    # A draft answers like an unknown name since 18.11.2026 (test_og_hides_unpublished_galleries.py).
    assert meta["mode"] == ("neutral" if closed == "draft" else "brand")
    assert "Sommerfest im Garten" not in meta["description"]


def test_the_password_page_a_bot_gets_carries_the_neutral_tags(client):
    ev = _event(password="geheim")
    _photo(ev, RED_PIC)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    og = _og(html)
    assert og["og:title"] == ev["name"]
    assert og["og:description"] == "Passwortgeschützte Fotogalerie"
    assert "Sommerfest im Garten" not in html.split("</head>")[0]


def test_an_uploaded_image_shows_for_a_closed_gallery_only_when_ticked(client):
    ev = _event(password="geheim")
    _photo(ev, RED_PIC)
    d = media.event_assets_dir(ev["slug"])
    Image.new("RGB", (1200, 630), GREEN_PIC).save(d / "og-test.jpg", "JPEG")
    rel = (d / "og-test.jpg").relative_to(media.data_dir()).as_posix()
    db.update_event(ev["id"], og_image_path=rel)
    _, img = _card(client, ev["slug"])
    assert not _has_colour(img, GREEN_PIC)          # not ticked: brand card
    db.update_event(ev["id"], og_image_public=1)
    _, img = _card(client, ev["slug"])
    assert _has_colour(img, GREEN_PIC) and not _has_colour(img, RED_PIC)


def test_a_public_gallery_prefers_the_admins_image(client):
    ev = _event()
    _photo(ev, RED_PIC)
    d = media.event_assets_dir(ev["slug"])
    Image.new("RGB", (1600, 900), GREEN_PIC).save(d / "og-x.jpg", "JPEG")
    db.update_event(ev["id"], og_image_path=(d / "og-x.jpg").relative_to(media.data_dir()).as_posix())
    _, img = _card(client, ev["slug"])
    assert _has_colour(img, GREEN_PIC) and not _has_colour(img, RED_PIC)


def test_silent_and_unknown_galleries_get_the_same_neutral_card(client):
    ev = _event(silent=True)
    _photo(ev, RED_PIC)
    a, _ = _card(client, ev["slug"])
    b, _ = _card(client, "gibt-es-nicht-" + secrets.token_hex(3))
    assert a.data == b.data
    assert not _has_colour(Image.open(io.BytesIO(a.data)).convert("RGB"), RED_PIC)


def test_the_admin_session_does_not_change_the_card(client):
    ev = _event(password="geheim")
    _photo(ev, RED_PIC)
    anon = client.get(f"/gallery/{ev['slug']}/og.jpg").data
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    assert client.get(f"/gallery/{ev['slug']}/og.jpg").data == anon


def test_the_key_changes_when_the_title_changes(client):
    ev = _event()
    _photo(ev, BLUE_PIC)
    first = link_preview.meta(db.get_event_by_id(ev["id"]), "https://x.test")["image"]
    db.update_event(ev["id"], name="Neuer Name")
    second = link_preview.meta(db.get_event_by_id(ev["id"]), "https://x.test")["image"]
    assert first != second


def test_a_folder_of_a_protected_gallery_shows_no_photo(client):
    from app import folders
    parent = _event(password="geheim")
    child = _event()
    folders.set_parent(child["id"], parent["id"])
    _photo(child, RED_PIC)
    _, img = _card(client, child["slug"])
    assert not _has_colour(img, RED_PIC)


def test_run_pages_and_share_links_never_show_photos(client):
    ev = _event()
    _photo(ev, RED_PIC)
    meta = link_preview.meta(db.get_event_by_id(ev["id"]), "https://x.test", force_brand=True)
    assert meta["mode"] == "brand"
    r = client.get(meta["image"].replace("https://x.test", ""))
    assert not _has_colour(Image.open(io.BytesIO(r.data)).convert("RGB"), RED_PIC)


def test_the_admin_form_stores_the_image_and_the_switch(client, formular):
    ev = _event()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), GREEN_PIC).save(buf, "PNG")
    buf.seek(0)
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert "Vorschaubild für Link-Vorschauen" in page and "auch ohne Passwort zeigen" in page
    token = re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)
    r = client.post(f"/admin/events/{ev['id']}/edit", content_type="multipart/form-data", data={
        "csrf_token": token, "action": "save", "name": ev["name"], "slug": ev["slug"], "published": "1",
        "og_form": "1", "og_image_public": "1", "og_image": (buf, "vorschau.png")})
    assert r.status_code in (302, 303)
    row = db.get_event_by_id(ev["id"])
    assert row["og_image_public"] == 1 and row["og_image_path"].endswith(".jpg")
    with Image.open(media.data_dir() / row["og_image_path"]) as im:
        assert im.size == (1200, 630)
