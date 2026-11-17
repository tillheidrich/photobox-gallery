"""/data answers by the same gates as the gallery, and guests' GPS stays home.

Security review of 11.11.2026, finding S8: the file route checked publication,
password, watermark, moderation, schedule and expiry, but not silent mode,
not "original download off" and not hidden recordings. A guest who knew (or
had once seen) a file address got it anyway.
Business logic page 4 (business_logic/current/models/galerie-sichtbarkeit-
und-zugang.md): silent mode closes the gallery for guests, "auch mit
Passwort"; downloads have their own switches.
"""
import io
import json
import secrets

import pytest
from PIL import Image

from app import db, ecosystem, gallery_settings as gsm, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("silent_mode", "0")
    yield


def _event(**gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Gate {k}", slug=f"gate-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
    return db.get_event_by_id(eid)


def _photo(event, *, uid=None, kind="photo", hidden=0, video=False):
    """A recording as the booth stores it: original plus web version and thumb."""
    d = media.event_storage_dir(event["slug"])
    stem = uid or secrets.token_hex(5)
    if video:
        orig = d / "orig" / f"{stem}.mp4"
        orig.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    else:
        orig = d / "orig" / f"{stem}.jpg"
        Image.new("RGB", (40, 30)).save(orig, "JPEG")
    webp = d / "webp" / f"{stem}.webp"
    thumb = d / "thumb" / f"{stem}.webp"
    Image.new("RGB", (40, 30)).save(webp, "WEBP")
    Image.new("RGB", (20, 15)).save(thumb, "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes,"
        " sha256, share_token, uid, session_uid, variant, hidden) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event["id"], "video" if video else kind, orig.name, rel(orig), None if video else rel(webp),
         rel(thumb), 1, secrets.token_hex(8), secrets.token_hex(8), uid, uid, "web", hidden))
    con.commit()
    con.close()
    return {"orig": "/data/" + rel(orig), "webp": "/data/" + rel(webp), "thumb": "/data/" + rel(thumb),
            "id": cur.lastrowid}


def test_silent_mode_closes_the_files_too(client):
    event = _event(silent=True)
    p = _photo(event)
    assert client.get(p["thumb"]).status_code == 404
    assert client.get(p["webp"]).status_code == 404


def test_silent_mode_closes_them_even_for_the_guest_with_the_qr_code(client):
    event = _event(silent=True)
    p = _photo(event, uid="qrsilent01")
    with client.session_transaction() as s:
        s["p_uids"] = ["qrsilent01"]
    assert client.get(p["thumb"]).status_code == 404


def test_global_silent_mode_closes_the_files(client):
    event = _event()
    p = _photo(event)
    db.set_setting("silent_mode", "1")
    assert client.get(p["thumb"]).status_code == 404


def test_the_original_is_closed_when_original_download_is_off(client):
    event = _event(download_original=False)
    p = _photo(event)
    assert client.get(p["webp"]).status_code == 200
    assert client.get(p["thumb"]).status_code == 200
    assert client.get(p["orig"]).status_code == 403


def test_the_original_is_open_when_original_download_is_on(client):
    event = _event(download_original=True)
    p = _photo(event)
    assert client.get(p["orig"]).status_code == 200


def test_a_video_is_played_from_its_original_file_whatever_the_switch(client):
    event = _event(download_original=False)
    p = _photo(event, video=True)
    assert client.get(p["orig"]).status_code == 200


def test_a_hidden_recording_is_not_served_to_guests(client):
    event = _event()
    p = _photo(event, hidden=1)
    assert client.get(p["thumb"]).status_code == 404
    assert client.get(p["orig"]).status_code == 404


def test_the_operator_still_gets_everything(client):
    event = _event(silent=True, download_original=False)
    p = _photo(event, hidden=1)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    assert client.get(p["orig"]).status_code == 200
    assert client.get(p["thumb"]).status_code == 200


# ── GPS ───────────────────────────────────────────────────────────────

def _jpeg_with_gps():
    im = Image.new("RGB", (60, 40), (10, 120, 200))
    exif = Image.Exif()
    exif[0x0112] = 6                       # Orientation: rotate 90° CW
    exif[0x010F] = "PhoneMaker"
    gps = exif.get_ifd(0x8825)
    gps.update({1: "N", 2: (53.0, 14.0, 30.0), 3: "E", 4: (9.0, 52.0, 10.0)})
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif, quality=90)
    buf.seek(0)
    return buf


def test_a_guest_upload_loses_its_gps_position_but_keeps_its_orientation(client):
    event = _event(guest_upload=True)
    r = client.post("/api/guest-upload", data={"event_slug": event["slug"],
                                               "file": (_jpeg_with_gps(), "handy.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    row = db.get_media(r.get_json()["id"])
    with Image.open(media.data_dir() / row["path_original"]) as im:
        exif = im.getexif()
        assert not dict(exif.get_ifd(0x8825)), "GPS must be gone"
        assert 0x8825 not in exif
        assert exif.get(0x0112) == 6, "orientation must survive"
        assert im.size == (60, 40), "the picture itself is not touched"
