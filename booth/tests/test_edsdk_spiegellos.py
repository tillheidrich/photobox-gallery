"""A mirrorless Canon wants the shutter button held, and says why it refuses.

Seen on a real EOS M50 on 14.11.2026: the live view ran at 17 pictures a
second, the shutter command came back "ok" — and the camera never fired. The
driver pressed the button and let go in the same breath. A DSLR has committed
to the shot by then; a mirrorless body is still focusing and takes the
released button as "never mind". In a bright room nobody notices, in a dim
one no picture ever arrives.

So: focus first (half press), then press without autofocus, and keep the
button down until the camera hands the picture over. And when the camera
refuses for a reason it names — lens retracted, dial on movie, no card — the
booth says that reason at once instead of "timeout" after 15 seconds.
"""
import time

import pytest

from fake_edsdk import FakeEdsdk, bereitstellen


@pytest.fixture()
def kamera(monkeypatch, tmp_path):
    from boothcore.camera import edsdk as modul
    sdk = FakeEdsdk()
    sdk.spiegellos = True
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    cam = modul.EdsdkCamera()
    cam.open()
    yield cam, sdk, tmp_path
    cam.close()


def test_a_mirrorless_body_gets_its_picture(kamera):
    cam, sdk, tmp = kamera
    ziel = tmp / "m50.jpg"
    cam.capture(ziel)
    assert ziel.exists()


def test_it_focuses_first_and_releases_only_after_the_picture(kamera):
    cam, sdk, tmp = kamera
    cam.capture(tmp / "a.jpg")
    drueck = [b for b in sdk.befehle if b != 0]
    assert drueck[0] == 0x00000001, "half press first: let the camera focus"
    assert 0x00010003 in drueck, "then fire without a second autofocus run"
    assert sdk.befehle[-1] == 0, "the button is let go in the end"
    assert sdk.ausloesungen == 1


def test_no_focus_in_a_dim_room_still_gives_a_picture(kamera):
    cam, sdk, tmp = kamera
    sdk.af_ng_mal = 99
    ziel = tmp / "dunkel.jpg"
    cam.capture(ziel)
    assert ziel.exists()


def test_the_button_is_always_let_go_even_when_no_picture_comes(kamera, monkeypatch):
    from boothcore.camera import edsdk as modul
    cam, sdk, tmp = kamera
    sdk.liefert = False
    monkeypatch.setattr(modul, "CAPTURE_TIMEOUT", 0.3, raising=False)
    with pytest.raises(Exception):
        cam.capture(tmp / "nichts.jpg")
    assert sdk.befehle[-1] == 0, "a button left down keeps the camera busy for good"


@pytest.mark.parametrize("code,wort", [
    (0x8D0F, "Objektiv"),        # collapsible kit lens still retracted
    (0x8D0E, "Film"),            # mode dial on movie
    (0x8D06, "Speicherkarte"),
    (0x8D0A, "Blitz"),
])
def test_a_refusal_the_camera_names_is_said_at_once(kamera, code, wort):
    cam, sdk, tmp = kamera
    sdk.aufnahmefehler = code
    t = time.monotonic()
    with pytest.raises(Exception) as e:
        cam.capture(tmp / "x.jpg")
    assert wort in str(e.value), str(e.value)
    assert "Timeout" not in str(e.value)
    assert time.monotonic() - t < 5, "not after the full 15 seconds"


def test_the_camera_is_set_to_single_shots(kamera, monkeypatch):
    """Held down in continuous drive the camera would fire a whole burst."""
    from boothcore.camera import edsdk as modul
    cam, sdk, tmp = kamera
    gesetzt = []
    sdk.EdsSetPropertyData = lambda _c, prop, *_a: gesetzt.append(prop) or 0
    cam.close()
    cam.open()
    assert 0x00000401 in gesetzt


def test_a_camera_that_is_stuck_busy_says_what_to_do(monkeypatch):
    """Seen on the EOS M50: after a shot that never came, the camera answered
    "busy" to everything, across program starts, until it was switched off
    and on. The booth said "SaveTo=Host fehlgeschlagen (0x00000081)"."""
    from boothcore.camera import edsdk as modul
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    sdk.EdsSetPropertyData = lambda *_a: 0x00000081
    cam = modul.EdsdkCamera()
    with pytest.raises(Exception) as e:
        cam.open()
    assert "aus- und wieder einschalten" in str(e.value)


def test_the_older_release_is_one_setting_away(monkeypatch, tmp_path):
    """The new sequence could not be tried on the real camera before it was
    committed (the camera was stuck). So the one that took 20 of 20 pictures
    in good light stays available: camera.edsdk_release = "classic"."""
    from boothcore.camera import edsdk as modul
    from boothcore.camera.service import create_backend
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    cam = create_backend({"backend": "edsdk", "edsdk_release": "classic"})
    cam.open()
    try:
        cam.capture(tmp_path / "klassisch.jpg")
        assert (tmp_path / "klassisch.jpg").exists()
        assert [b for b in sdk.befehle][:2] == [0x00000003, 0x00000000], "full press, release at once"
    finally:
        cam.close()
    assert create_backend({"backend": "edsdk"}).release == "hold"


def test_classic_release_fires_without_focus_when_the_camera_says_so_late(monkeypatch, tmp_path):
    """Real EOS M50, 14.11.2026, dim scene: the full press came back "ok" and
    the refusal "no focus" arrived a moment later as an event. The classic
    sequence only knew the refusal as a return value, so no picture was taken."""
    from boothcore.camera import edsdk as modul
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    sdk.af_ng_spaet = 1
    cam = modul.EdsdkCamera(release="classic")
    cam.open()
    try:
        cam.capture(tmp_path / "dunkel.jpg")
        assert (tmp_path / "dunkel.jpg").exists()
        assert 0x00010003 in sdk.befehle, "second try without autofocus"
    finally:
        cam.close()


# --- review of 14 Nov 2026: the default sequence met "no focus" worse than the fallback ---

def _hold(monkeypatch, sdk):
    from boothcore.camera import edsdk as modul
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    cam = modul.EdsdkCamera(release="hold")
    cam.open()
    return cam


def test_no_focus_at_the_half_press_does_not_cost_the_picture(monkeypatch, tmp_path):
    sdk = FakeEdsdk()
    sdk.halb_meldet_af_ng = True
    cam = _hold(monkeypatch, sdk)
    try:
        cam.capture(tmp_path / "dunkel.jpg")
        assert (tmp_path / "dunkel.jpg").exists()
    finally:
        cam.close()


def test_hold_release_tries_once_more_when_no_focus_comes_late(monkeypatch, tmp_path):
    sdk = FakeEdsdk()
    sdk.nonaf_af_ng_spaet = 1
    cam = _hold(monkeypatch, sdk)
    try:
        cam.capture(tmp_path / "dunkel.jpg")
        assert (tmp_path / "dunkel.jpg").exists()
        assert sdk.nonaf_versuche == 2
    finally:
        cam.close()


@pytest.mark.parametrize("code, key", [(0x8D07, "camera.card"), (0x8D06, "camera.card"), (0x8D08, "camera.card"),
                                       (0x8D0A, "camera.flash"), (0x8D0F, "camera.lens"), (0x8D0B, "camera.lens"),
                                       (0x8D0C, "camera.mode"), (0x8D01, "camera.focus")])
def test_the_cameras_reason_reaches_the_screen(monkeypatch, tmp_path, code, key):
    """Every refusal ended as "that did not work, try again" — for a full card, all evening."""
    from boothcore import errors
    sdk = FakeEdsdk()
    sdk.aufnahmefehler = code
    cam = _hold(monkeypatch, sdk)
    try:
        with pytest.raises(Exception) as e:
            cam.capture(tmp_path / "x.jpg")
    finally:
        cam.close()
    assert errors.camera_code(str(e.value)) == key, str(e.value)


def test_a_file_that_is_no_jpeg_is_a_camera_error_not_a_broken_collage(tmp_path):
    from boothcore import errors
    from boothcore.camera.base import CameraError, check_photo
    raw = tmp_path / "shot1.jpg"
    raw.write_bytes(b"\x00\x00\x00\x18ftypcrx " + b"\x00" * 400)       # a CR3 under a .jpg name
    with pytest.raises(CameraError) as e:
        check_photo(raw)
    assert errors.camera_code(str(e.value)) == "camera.format"
    from PIL import Image
    ok = tmp_path / "shot2.jpg"
    Image.new("RGB", (60, 40), "#888").save(ok, "JPEG")
    check_photo(ok)
    half = tmp_path / "shot3.jpg"
    half.write_bytes(ok.read_bytes()[:200])
    with pytest.raises(CameraError):
        check_photo(half)
