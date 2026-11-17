"""Every picture the booth makes carries the PC's time of the shot.

Seen on the real booth on 15.11.2026: the EOS M50 on a mains adapter had lost
its clock, and the pictures said 2017. The booth knows when it took a picture
whatever the camera thinks, so:

* the files it renders (result, web version, thumbnail, print card, the
  single pictures of a collage, the stills of a GIF run) get the date and
  time of the run in their EXIF;
* the camera's own original is never re-encoded. When its EXIF date is off
  by more than a few minutes, the date fields are overwritten in place:
  same length, same file size, the picture data byte for byte the same.
"""
import io
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import aufnahmezeit

ORIGINAL, DIGITIZED, DATETIME, OFFSET_ORIGINAL = 0x9003, 0x9004, 0x0132, 0x9011
EVENING = time.mktime((2026, 11, 18, 21, 30, 5, 0, 0, -1))     # local time
EVENING_TEXT = "2026:11:18 21:30:05"


def _daten(path):
    with Image.open(path) as im:
        exif = im.getexif()
        sub = exif.get_ifd(0x8769)
    return exif.get(DATETIME), sub.get(ORIGINAL), sub.get(DIGITIZED), sub.get(OFFSET_ORIGINAL)


def _kamerabild(path: Path, datum="2017:01:01 00:03:12", offset=None):
    """What an EOS writes: a JPEG with camera EXIF and a wrong clock."""
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "Canon EOS M50"
    exif[DATETIME] = datum
    sub = exif.get_ifd(0x8769)
    sub[ORIGINAL] = datum
    sub[DIGITIZED] = datum
    if offset:
        sub[OFFSET_ORIGINAL] = offset
    Image.new("RGB", (600, 400), (120, 80, 40)).save(path, "JPEG", quality=90, exif=exif.tobytes())
    return path


def _bilddaten(data: bytes) -> bytes:
    return data[data.index(b"\xff\xda"):]                # start of scan to the end


# ── the helper on its own ─────────────────────────────────

def test_rendered_files_get_the_time_of_the_run(tmp_path):
    out = tmp_path / "r.jpg"
    Image.new("RGB", (100, 100)).save(out, "JPEG", exif=aufnahmezeit.exif_for(EVENING))
    dt_, original, digitized, offset = _daten(out)
    assert dt_ == original == digitized == EVENING_TEXT
    assert offset and offset[0] in "+-" and offset[3] == ":"


def test_a_wrong_camera_date_is_overwritten_without_re_encoding(tmp_path):
    shot = _kamerabild(tmp_path / "shot1.jpg", offset="+00:00")
    vorher = shot.read_bytes()
    assert aufnahmezeit.stamp_original(shot, EVENING) is True
    nachher = shot.read_bytes()
    assert len(nachher) == len(vorher)
    assert _bilddaten(nachher) == _bilddaten(vorher), "the picture itself is untouched"
    dt_, original, digitized, offset = _daten(shot)
    assert dt_ == original == digitized == EVENING_TEXT
    assert offset == aufnahmezeit.offset_text(EVENING)
    with Image.open(shot) as im:
        assert im.getexif().get(0x0110) == "Canon EOS M50", "the rest of the EXIF stays"


def test_a_camera_that_is_right_is_left_alone(tmp_path):
    richtig = time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(EVENING + 40))
    shot = _kamerabild(tmp_path / "shot1.jpg", datum=richtig)
    vorher = shot.read_bytes()
    assert aufnahmezeit.stamp_original(shot, EVENING) is False
    assert shot.read_bytes() == vorher


@pytest.mark.parametrize("inhalt", [
    "ohne-exif", "kaputt", "kein-jpeg",
])
def test_what_cannot_be_patched_is_left_as_it_is(tmp_path, inhalt):
    shot = tmp_path / "shot1.jpg"
    if inhalt == "ohne-exif":
        Image.new("RGB", (60, 40)).save(shot, "JPEG")
    elif inhalt == "kaputt":
        data = _kamerabild(shot).read_bytes()
        i = data.index(b"Exif\x00\x00") + 6
        shot.write_bytes(data[:i + 4] + b"\xff\xff\xff\x7f" + data[i + 8:])   # IFD0 far outside
    else:
        shot.write_bytes(b"not a picture at all")
    vorher = shot.read_bytes()
    assert aufnahmezeit.stamp_original(shot, EVENING) is False
    assert shot.read_bytes() == vorher


# ── at the booth ─────────────────────────────────────────

@pytest.fixture()
def booth():
    d = Path(tempfile.mkdtemp(prefix="booth-zeit-"))
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "collage", "gif"]
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _lauf(booth, mode, vorlage=None):
    s = booth.start_session(mode, "4:5", vorlage, None)
    s.created = EVENING
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def test_photo_results_carry_the_time_of_the_run(booth):
    s = _lauf(booth, "photo")
    for name in ("result.jpg", "result_web.jpg", "thumb.jpg", "print.jpg"):
        assert _daten(s.folder / name)[1] == EVENING_TEXT, name


def test_collage_results_and_singles_carry_the_time_of_the_run(booth):
    booth.store.profile["collage_upload"] = "both"
    s = _lauf(booth, "collage", "grid4")
    for name in ("result.jpg", "result_web.jpg", "print.jpg", "einzel1.jpg", "einzel1_web.jpg"):
        assert _daten(s.folder / name)[1] == EVENING_TEXT, name


def test_gif_stills_carry_the_time_of_the_run(booth):
    s = _lauf(booth, "gif")
    for name in ("print.jpg", "thumb.jpg", "poster.jpg"):
        assert _daten(s.folder / name)[1] == EVENING_TEXT, name


def test_the_camera_original_gets_the_pc_time_of_the_shot(booth, monkeypatch):
    def capture(target):
        return _kamerabild(Path(target))
    monkeypatch.setattr(booth.camera, "capture", capture)
    s = booth.start_session("photo", "4:5", None, None)
    vorher = time.time()
    shot = booth.take_shot(s)
    stamp = time.mktime(time.strptime(_daten(shot)[1], "%Y:%m:%d %H:%M:%S"))
    assert vorher - 2 <= stamp <= time.time() + 2


def test_a_shot_is_never_lost_over_its_date(booth, monkeypatch):
    def capture(target):
        return _kamerabild(Path(target))
    monkeypatch.setattr(booth.camera, "capture", capture)

    def kaputt(*_a, **_k):
        raise RuntimeError("disk said no")
    monkeypatch.setattr(aufnahmezeit, "stamp_original", kaputt)
    s = booth.start_session("photo", "4:5", None, None)
    assert booth.take_shot(s).exists()
