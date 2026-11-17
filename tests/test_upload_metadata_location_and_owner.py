"""A guest's upload keeps no location and no camera owner (final review V7).

The privacy template says location data is removed from guest uploads. Only
the EXIF GPS directory of JPEGs was taken out reliably; XMP in PNG and WebP
(where phones and editors repeat the position, ``exif:GPSLatitude``) stayed,
and so did the camera's serial number and owner name. Now ``strip_gps``
removes from JPEG, PNG and WebP: the EXIF GPS directory, XMP, and the EXIF
fields BodySerialNumber, LensSerialNumber, CameraOwnerName and the vendor's
MakerNote (where serials hide). Orientation and colour profile stay; a JPEG is
not re-encoded.
"""
import io

import pytest
from PIL import Image

from app import uploads

XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
       b'<rdf:Description xmlns:exif="http://ns.adobe.com/exif/1.0/" exif:GPSLatitude="53,17.5N" '
       b'exif:GPSLongitude="9,52.1E"/></rdf:RDF></x:xmpmeta>')
SERIAL, OWNER, LENS = "SN-0042-SECRET", "Erika Mustermann", "LENS-77"


def _exif():
    exif = Image.Exif()
    exif[0x0112] = 6                                   # orientation
    exif[0x010F] = "PhoneMaker"
    gps = exif.get_ifd(0x8825)
    gps[2] = (53.0, 17.0, 30.0)
    gps[1] = "N"
    sub = exif.get_ifd(0x8769)
    sub[0xA431] = SERIAL
    sub[0xA430] = OWNER
    sub[0xA435] = LENS
    return exif


def _image():
    return Image.new("RGB", (40, 30), (200, 120, 60))


def _check(path):
    raw = path.read_bytes()
    assert b"GPSLatitude" not in raw and b"53,17.5N" not in raw, "XMP location must be gone"
    for secret in (SERIAL, OWNER, LENS):
        assert secret.encode() not in raw, secret
    with Image.open(path) as im:
        exif = im.getexif()
        assert 0x8825 not in exif and not dict(exif.get_ifd(0x8825))
        sub = exif.get_ifd(0x8769)
        assert not ({0xA430, 0xA431, 0xA435, 0x927C} & set(sub))
        return exif


def test_jpeg_loses_location_serial_owner_and_xmp_without_reencoding(tmp_path):
    buf = io.BytesIO()
    _image().save(buf, "JPEG", exif=_exif(), xmp=XMP, quality=90)
    path = tmp_path / "a.jpg"
    path.write_bytes(buf.getvalue())
    assert uploads.strip_gps(path) is True
    exif = _check(path)
    assert exif.get(0x0112) == 6


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_png_and_webp_lose_xmp_location_even_without_exif_gps(tmp_path, fmt):
    path = tmp_path / f"a.{fmt.lower()}"
    params = {"xmp": XMP} if fmt == "WEBP" else {}
    if fmt == "PNG":
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_itxt("XML:com.adobe.xmp", XMP.decode())
        params["pnginfo"] = info
    _image().save(path, fmt, **params)
    assert b"GPSLatitude" in path.read_bytes()
    assert uploads.strip_gps(path) is True
    _check(path)


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_png_and_webp_lose_exif_location_serial_and_owner(tmp_path, fmt):
    path = tmp_path / f"b.{fmt.lower()}"
    _image().save(path, fmt, exif=_exif())
    assert uploads.strip_gps(path) is True
    _check(path)


def test_a_clean_picture_is_left_alone(tmp_path):
    path = tmp_path / "c.png"
    _image().save(path, "PNG")
    before = path.read_bytes()
    assert uploads.strip_gps(path) is False
    assert path.read_bytes() == before
