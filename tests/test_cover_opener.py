"""Cover picture and opener: a gallery starts with its best picture.

Rules (page 4, section 18): the photographer picks the cover among the
gallery's own pictures (``cover_path``); an opener (off by default) shows it
full width with title, date and an optional subtitle; the title typeface comes
from the shipped faces or device stacks - nothing is fetched from elsewhere.
"""
import json
import secrets

import pytest
from PIL import Image

from app import db, gallery_settings as gsm, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    db.set_setting("silent_mode", "0")
    yield


def _event(**gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Auftakt {k}", slug=f"af-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "date": "2026-11-08", "gallery_json": json.dumps(gsm.clean(gs))})
    return db.get_event_by_id(eid)


def _photo(event):
    d = media.event_storage_dir(event["slug"])
    stem = secrets.token_hex(4)
    orig, webp, thumb = d / "orig" / f"{stem}.jpg", d / "webp" / f"{stem}.webp", d / "thumb" / f"{stem}.webp"
    for p, size in ((orig, (60, 40)), (webp, (60, 40)), (thumb, (30, 20))):
        Image.new("RGB", size).save(p, "JPEG" if p.suffix == ".jpg" else "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
        " share_token) VALUES (?,?,?,?,?,?,?,?,?)",
        (event["id"], "photo", "a.jpg", rel(orig), rel(webp), rel(thumb), 1, stem, secrets.token_hex(8)))
    con.commit()
    con.close()
    return cur.lastrowid, rel(webp)


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
        s["admin_epoch"] = accounts.session_epoch()


def test_the_photographer_picks_the_cover_from_the_gallery(client):
    ev, other = _event(), _event()
    mid, webp = _photo(ev)
    foreign, _ = _photo(other)
    _admin(client)
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert f'action="/admin/events/{ev["id"]}/cover"' in page
    assert client.post(f"/admin/events/{ev['id']}/cover", data={"csrf_token": "test-token",
                                                                "media_id": str(foreign)}).status_code == 404
    client.post(f"/admin/events/{ev['id']}/cover", data={"csrf_token": "test-token", "media_id": str(mid)})
    assert db.get_event_by_id(ev["id"])["cover_path"] == webp


def test_the_opener_is_off_by_default(client):
    assert gsm.DEFAULTS["opener"] is False
    ev = _event()
    _photo(ev)
    assert 'class="gallery-opener' not in client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)


def test_the_opener_shows_cover_title_date_and_subtitle(client):
    ev = _event(opener=True, opener_subtitle="Ein Tag am Hafen", title_font="display", theme="dark")
    mid, webp = _photo(ev)
    db.update_event(ev["id"], cover_path=webp)
    page = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'class="gallery-opener' in page and f"/data/{webp}" in page
    assert "Ein Tag am Hafen" in page and "08.11.2026" in page and "title-font-display" in page
    assert "fonts.googleapis" not in page and "fonts.gstatic" not in page


def test_unknown_title_fonts_fall_back(client):
    assert gsm.clean({"title_font": "Comic Sans"})["title_font"] == ""
