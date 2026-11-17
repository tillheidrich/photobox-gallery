"""Colour tags in the large view, and an EXIF line without GPS.

Rules (page 4, section 19): whoever may tag on the tile may tag in the large
view. The EXIF line (camera, focal length, exposure, aperture, ISO) is off by
default (gallery setting ``exif``), answers by the gallery's gates, and never
carries a position.
"""
import io
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


def _event(password="", **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Exif {k}", slug=f"ex-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
    if password:
        db.set_event_password(eid, password)
    return db.get_event_by_id(eid)


def _photo_with_exif(event):
    d = media.event_storage_dir(event["slug"])
    stem = secrets.token_hex(4)
    orig = d / "orig" / f"{stem}.jpg"
    img = Image.new("RGB", (60, 40))
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "Canon EOS R6"
    ifd = exif.get_ifd(0x8769)
    ifd[0x920A] = 35.0          # focal length
    ifd[0x829A] = 1 / 250       # exposure time
    ifd[0x829D] = 2.8           # f-number
    ifd[0x8827] = 400           # ISO
    gps = exif.get_ifd(0x8825)
    gps[2] = (53.0, 32.0, 0.0)
    gps[1] = "N"
    img.save(orig, "JPEG", exif=exif.tobytes())
    webp = d / "webp" / f"{stem}.webp"
    Image.new("RGB", (60, 40)).save(webp, "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
        " share_token) VALUES (?,?,?,?,?,?,?,?,?)",
        (event["id"], "photo", "IMG_1.jpg", rel(orig), rel(webp), rel(webp), 1, stem, secrets.token_hex(8)))
    con.commit()
    con.close()
    return cur.lastrowid


def test_the_exif_line_is_off_by_default(client):
    assert gsm.DEFAULTS["exif"] is False
    ev = _event()
    mid = _photo_with_exif(ev)
    assert client.get(f"/gallery/{ev['slug']}/photo/{mid}/exif.json").status_code == 404
    assert 'id="lbExif"' not in client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)


def test_the_exif_line_names_camera_and_settings_and_no_position(client):
    ev = _event(exif=True)
    mid = _photo_with_exif(ev)
    r = client.get(f"/gallery/{ev['slug']}/photo/{mid}/exif.json")
    data = r.get_json()
    assert data["camera"] == "Canon EOS R6" and data["focal"] == "35 mm"
    assert data["exposure"] == "1/250 s" and data["aperture"] == "f/2.8" and data["iso"] == "ISO 400"
    assert "53" not in json.dumps(data) and not any("gps" in k.lower() for k in data)
    assert 'id="lbExif"' in client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)


def test_the_exif_line_answers_by_the_gallery_gates(client):
    ev = _event(password="geheim1234", exif=True)
    mid = _photo_with_exif(ev)
    assert client.get(f"/gallery/{ev['slug']}/photo/{mid}/exif.json").status_code == 403
    other = _event(exif=True)
    assert client.get(f"/gallery/{other['slug']}/photo/{mid}/exif.json").status_code == 404


def test_tagging_in_the_large_view_for_whoever_may_tag(client):
    ev = _event(mode="proofing")
    _photo_with_exif(ev)
    assert 'id="lbTag"' in client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    plain = _event()
    _photo_with_exif(plain)
    assert 'id="lbTag"' not in client.get(f"/gallery/{plain['slug']}").get_data(as_text=True)
