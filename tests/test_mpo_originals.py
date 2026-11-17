"""A camera original is a JPEG even when Pillow calls it MPO.

Canon bodies (EOS M50 and others) and some phones write a "multi-picture"
JPEG: the photo plus an embedded preview behind it, announced by an MPF
segment. Pillow reports such a file as format "MPO" with two frames. The
upload gate only knew "JPEG", so the booth's own originals were refused with
415. MPO is accepted and stored as ``.jpg``, never treated as an animation,
and the location/owner clean-up still applies to it.
"""
import io

from PIL import Image

from app import uploads
from tests.test_booth_v2 import _setup
from tests.test_upload_metadata_location_and_owner import LENS, OWNER, SERIAL, XMP, _exif


def _mpo_bytes(size=(120, 90), with_meta=False):
    buf = io.BytesIO()
    main = Image.new("RGB", size, (200, 80, 60))
    preview = Image.new("RGB", (40, 30), (10, 20, 30))
    kw = {"exif": _exif()} if with_meta else {}
    main.save(buf, format="MPO", save_all=True, append_images=[preview], **kw)
    data = buf.getvalue()
    if with_meta:
        # Pillow writes no XMP for MPO; splice one in like a camera would.
        seg = b"http://ns.adobe.com/xap/1.0/\x00" + XMP
        data = data[:2] + b"\xff\xe1" + (len(seg) + 2).to_bytes(2, "big") + seg + data[2:]
    return data


def test_the_fixture_really_is_an_mpo_with_two_frames():
    with Image.open(io.BytesIO(_mpo_bytes())) as im:
        assert im.format == "MPO"
        assert getattr(im, "n_frames", 1) == 2


def test_sniff_accepts_an_mpo_as_a_jpeg(tmp_path):
    path = tmp_path / "shot1.jpg"
    path.write_bytes(_mpo_bytes())
    assert uploads.sniff(path) == ("image", ".jpg")


def test_an_mpo_is_served_as_image_jpeg(tmp_path):
    path = tmp_path / "x.jpg"
    path.write_bytes(_mpo_bytes())
    _, ext = uploads.sniff(path)
    assert uploads.serve_type(tmp_path / f"x{ext}") == ("image/jpeg", False)


def test_strip_gps_cleans_an_mpo_and_cuts_the_embedded_preview(tmp_path):
    path = tmp_path / "g.jpg"
    path.write_bytes(_mpo_bytes(with_meta=True))
    raw = path.read_bytes()
    assert SERIAL.encode() in raw and b"GPSLatitude" in raw      # the fixture does carry them
    assert uploads.strip_gps(path) is True
    raw = path.read_bytes()
    assert b"GPSLatitude" not in raw
    for secret in (SERIAL, OWNER, LENS):
        assert secret.encode() not in raw
    with Image.open(path) as im:
        assert im.format == "JPEG"                       # the MPF index and the second picture are gone
        assert getattr(im, "n_frames", 1) == 1
        assert im.size == (120, 90)
        exif = im.getexif()
        assert exif.get(0x0112) == 6                      # orientation stays
        assert not dict(exif.get_ifd(0x8825))
    assert uploads.sniff(path) == ("image", ".jpg")


def test_strip_gps_leaves_a_clean_mpo_alone_without_crashing(tmp_path):
    path = tmp_path / "c.jpg"
    path.write_bytes(_mpo_bytes())
    uploads.strip_gps(path)                               # may cut the preview, must not raise
    with Image.open(path) as im:
        assert im.size == (120, 90)


def test_booth_upload_stores_an_mpo_original_and_makes_derivatives(client):
    from app import db, media
    event, dev = _setup(client)
    data = _mpo_bytes(size=(600, 400))
    r = client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                    data={"uid": "mpo1", "kind": "photo", "variant": "original",
                          "file": (io.BytesIO(data), "shot1.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["ok"] is True
    row = db.get_db().execute("SELECT * FROM media WHERE uid=?", ("mpo1",)).fetchone()
    assert row["filename"].endswith(".jpg")
    assert (row["width"], row["height"]) == (600, 400)
    root = media.data_dir()
    for col in ("path_original", "path_webp", "path_thumb"):
        assert (root / row[col]).is_file(), col
    with Image.open(root / row["path_thumb"]) as th:
        assert th.size[0] > 0
