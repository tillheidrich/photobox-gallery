"""Second review R2-U4: what the first metadata cleaning left in a guest upload.

* A JPEG with a second image or a video appended after its end marker (MPF,
  "Motion Photo"): the appended part kept its own GPS, owner and serial.
* IPTC (APP13: by-line, city, contact) and the comment segment (COM) stayed.
* An animated WebP or PNG with metadata came back as its first frame only.

The segments are read here by hand: no exiftool in the tests.
"""
import io

from PIL import Image

from app import uploads

SECRET_GPS = (53.0, 17.0, 30.0)
OWNER = "Erika Mustermann"


def _exif():
    exif = Image.Exif()
    exif[0x0112] = 1
    gps = exif.get_ifd(0x8825)
    gps[2] = SECRET_GPS
    gps[1] = "N"
    exif.get_ifd(0x8769)[0xA430] = OWNER
    return exif


def _jpeg(exif=None, size=(40, 30), colour=(200, 120, 60)) -> bytes:
    buf = io.BytesIO()
    params = {"exif": exif.tobytes()} if exif is not None else {}
    Image.new("RGB", size, colour).save(buf, "JPEG", quality=85, **params)
    return buf.getvalue()


def _insert_after_soi(jpeg: bytes, marker: int, payload: bytes) -> bytes:
    seg = bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload
    return jpeg[:2] + seg + jpeg[2:]


def _segments(data: bytes):
    """(marker, payload) of every segment before the first scan."""
    i, out = 2, []
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA:
            break
        length = int.from_bytes(data[i + 2:i + 4], "big")
        out.append((marker, data[i + 4:i + 2 + length]))
        i += 2 + length
    return out


def test_an_appended_second_image_with_location_is_cut_off(tmp_path):
    primary = _jpeg()
    secondary = _jpeg(_exif(), size=(20, 20), colour=(10, 10, 10))
    mpf = _insert_after_soi(primary, 0xE2, b"MPF\x00" + b"\x00" * 20)
    path = tmp_path / "motion.jpg"
    path.write_bytes(mpf + secondary)
    assert uploads.strip_gps(path) is True
    data = path.read_bytes()
    assert data.endswith(b"\xff\xd9") and data.count(b"\xff\xd8") == 1
    assert OWNER.encode() not in data
    assert not any(m == 0xE2 and p.startswith(b"MPF\x00") for m, p in _segments(data))
    with Image.open(path) as im:
        assert im.size == (40, 30)


def test_an_appended_video_is_cut_off(tmp_path):
    path = tmp_path / "motion-photo.jpg"
    path.write_bytes(_jpeg() + b"\x00\x00\x00\x18ftypmp42" + b"GPS +53.2917+009.8683/" * 10)
    assert uploads.strip_gps(path) is True
    assert b"ftypmp42" not in path.read_bytes()


def test_iptc_and_comments_are_removed_and_the_picture_is_untouched(tmp_path):
    plain = _jpeg()
    iptc = b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x10\x1c\x02\x50\x00\x0cBy Erika M."
    data = _insert_after_soi(_insert_after_soi(plain, 0xFE, b"Erika Mustermann, Musterstadt"), 0xED, iptc)
    path = tmp_path / "iptc.jpg"
    path.write_bytes(data)
    assert uploads.strip_gps(path) is True
    cleaned = path.read_bytes()
    markers = [m for m, _ in _segments(cleaned)]
    assert 0xED not in markers and 0xFE not in markers
    assert b"Erika" not in cleaned
    assert cleaned[cleaned.index(b"\xff\xda"):] == plain[plain.index(b"\xff\xda"):]   # not re-encoded


def test_a_progressive_jpeg_survives_with_all_its_scans(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 160, 30)).save(buf, "JPEG", progressive=True, exif=_exif().tobytes())
    path = tmp_path / "progressive.jpg"
    path.write_bytes(buf.getvalue() + b"trailing secret")
    assert uploads.strip_gps(path) is True
    data = path.read_bytes()
    assert data.count(b"\xff\xda") == buf.getvalue().count(b"\xff\xda") > 1
    assert b"trailing secret" not in data
    with Image.open(path) as im:
        im.load()
        assert 0x8825 not in im.getexif()


def _animated(fmt: str, path):
    frames = [Image.new("RGBA", (24, 24), c) for c in ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255))]
    frames[0].save(path, fmt, save_all=True, append_images=frames[1:], duration=[120, 240, 360], loop=2,
                   exif=_exif().tobytes(), **({"lossless": True} if fmt == "WEBP" else {}))


def _frames(path):
    with Image.open(path) as im:
        out = []
        for k in range(im.n_frames):
            im.seek(k)
            out.append((im.info.get("duration"), im.convert("RGB").getpixel((5, 5))))
        return out, im.info.get("loop"), im.getexif()


def test_an_animated_webp_keeps_its_frames_and_loses_its_location(tmp_path):
    path = tmp_path / "anim.webp"
    _animated("WEBP", path)
    before, loop, _ = _frames(path)
    assert len(before) == 3
    assert uploads.strip_gps(path) is True
    after, loop_after, exif = _frames(path)
    assert [d for d, _ in after] == [d for d, _ in before]
    for (_, a), (_, b) in zip(after, before):          # WebP may be re-encoded: close, not equal
        assert max(abs(x - y) for x, y in zip(a, b)) <= 8
    assert loop_after == loop
    assert 0x8825 not in exif and OWNER.encode() not in path.read_bytes()


def test_an_animated_png_keeps_its_frames_and_loses_its_location(tmp_path):
    path = tmp_path / "anim.png"
    _animated("PNG", path)
    before, loop, _ = _frames(path)
    assert len(before) == 3
    assert uploads.strip_gps(path) is True
    after, loop_after, exif = _frames(path)
    assert after == before and loop_after == loop
    assert 0x8825 not in exif and OWNER.encode() not in path.read_bytes()
