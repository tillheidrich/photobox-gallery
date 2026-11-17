"""Canon über WLAN (CCAPI) — gegen eine nachgebaute Kamera.

Was hier geprüft wird, ist das Protokoll und das Verhalten bei Ärger. Ob eine
echte EOS sich genauso verhält, zeigt erst der Dauerlauf an der Hardware.
"""
import pytest

from boothcore.camera.base import CameraError
from boothcore.camera.ccapi import CcapiCamera
from tests.fake_ccapi import FakeCamera


@pytest.fixture()
def camera():
    fake = FakeCamera()
    url = fake.start()
    cam = CcapiCamera(url)
    yield fake, cam
    try:
        cam.close()
    finally:
        fake.stop()


def test_it_finds_the_camera_and_its_functions(camera):
    fake, cam = camera
    cam.open()
    assert cam.info()["model"] == "EOS R50"
    # Die höhere Fassung gewinnt, wenn die Kamera beide anbietet.
    assert cam._api["shutter"] == "/ccapi/ver110/shooting/control/shutterbutton"


def test_a_shot_lands_on_disk(camera, tmp_path):
    fake, cam = camera
    cam.open()
    target = tmp_path / "bild.jpg"
    out = cam.capture(target)
    assert out.exists() and out.read_bytes().startswith(b"\xff\xd8")
    assert fake.shots == 1


def test_it_waits_until_the_camera_reports_the_file(camera, tmp_path):
    """Die Kamera meldet die Datei erst nach ein paar Abfragen — das ist normal."""
    fake, cam = camera
    cam.open()
    fake.delay_polls = 3
    assert cam.capture(tmp_path / "spaet.jpg").exists()


def test_a_busy_camera_is_tried_again_instead_of_failing(camera, tmp_path):
    fake, cam = camera
    cam.open()
    fake.busy_times = 2
    assert cam.capture(tmp_path / "beschaeftigt.jpg").exists()
    assert fake.shots == 1


def test_a_full_card_says_so_in_plain_words(camera, tmp_path):
    fake, cam = camera
    cam.open()
    fake.fail_shutter = "Card not available"
    with pytest.raises(CameraError) as e:
        cam.capture(tmp_path / "keine-karte.jpg")
    assert "Speicherkarte" in str(e.value)


def test_the_wrong_mode_names_the_dial(camera, tmp_path):
    fake, cam = camera
    cam.open()
    fake.fail_shutter = "Mode not supported"
    with pytest.raises(CameraError) as e:
        cam.capture(tmp_path / "modus.jpg")
    assert "Rad" in str(e.value)


def test_live_view_starts_with_the_booth(camera):
    fake, cam = camera
    cam.open()
    assert fake.liveview == {"liveviewsize": "medium", "cameradisplay": "on"}
    assert cam.preview_jpeg().startswith(b"\xff\xd8")


def test_live_view_can_be_left_off(camera):
    """Im vollen WLAN ist ein Standbild besser als ein ruckelndes Live-Bild."""
    fake, cam = camera
    cam.liveview_size = "off"
    cam.open()
    assert fake.liveview is None


def test_exposure_settings_report_what_worked(camera):
    fake, cam = camera
    cam.open()
    result = cam.apply_settings({"iso": "800", "aperture": "4", "shutter": "kaputt-egal", "wb": ""})
    assert result["applied"]["iso"] == "800"
    assert fake.settings["iso"] == "800" and fake.settings["av"] == "4"
    assert "wb" not in result["applied"]            # leere Werte bleiben unangetastet

    bad = cam.apply_settings({"iso": "kaputt"})
    assert "iso" in bad["failed"] and "nicht angenommen" in bad["failed"]["iso"]


def test_an_unreachable_camera_explains_itself(tmp_path):
    cam = CcapiCamera("http://127.0.0.1:9")     # dort lauscht nichts
    with pytest.raises(CameraError) as e:
        cam.open()
    assert "nicht erreichbar" in str(e.value)


def test_a_missing_address_is_not_a_crash():
    with pytest.raises(CameraError) as e:
        CcapiCamera("").open()
    assert "Adresse" in str(e.value)


def test_a_camera_without_the_needed_functions_is_refused():
    fake = FakeCamera()
    fake._listing = lambda: {"ver100": [{"url": "http://x/ccapi/ver100/deviceinformation",
                                         "get": True, "post": False, "put": False, "delete": False}]}
    url = fake.start()
    try:
        with pytest.raises(CameraError) as e:
            CcapiCamera(url).open()
        assert "nötigen Funktionen" in str(e.value)
    finally:
        fake.stop()


def test_the_file_is_fetched_from_the_address_we_reached(camera, tmp_path):
    """Die Kamera nennt ihre eigene Adresse. Steht die Box woanders, zeigt die ins Leere."""
    fake, cam = camera
    cam.open()
    cam.capture(tmp_path / "pfad.jpg")
    holt = [r for r in fake.requests if "/contents/" in r]
    assert holt and holt[0].startswith("GET /ccapi/")


def test_the_doctor_checks_the_camera_on_the_network(tmp_path, monkeypatch):
    """Vor dem Event will der Betreiber wissen, ob die Kamera am Netz hängt."""
    import tempfile
    from pathlib import Path

    from boothcore.app import Booth
    from boothcore.doctor import Doctor

    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    booth = Booth(base=tmp_path, start_threads=False)
    try:
        # Ohne Adresse bleibt die Prüfung still.
        check = next(c for c in Doctor(booth).run() if c.name == "Canon über WLAN")
        assert check.state == "skip"

        fake = FakeCamera()
        url = fake.start()
        try:
            booth.store.local["camera"]["backend"] = "ccapi"
            booth.store.local["camera"]["ccapi_url"] = url
            check = next(c for c in Doctor(booth).run() if c.name == "Canon über WLAN")
            assert check.state == "ok" and "ver100" in check.message
        finally:
            fake.stop()

        # Kamera weg: der Hinweis nennt die wahrscheinliche Ursache.
        check = next(c for c in Doctor(booth).run() if c.name == "Canon über WLAN")
        assert check.state == "fail" and "selben Netz" in check.hint
    finally:
        booth.mail.stop()


def test_the_dry_run_tool_knows_the_backend(tmp_path, monkeypatch):
    from boothcore.tools import cameratest

    fake = FakeCamera()
    url = fake.start()
    try:
        monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
        from boothcore.config import Store
        store = Store(tmp_path)
        store.save_local({"camera": {"backend": "ccapi", "ccapi_url": url}})
        assert cameratest.run(shots=2, backend="", live=True, keep=False, pause=0.0) == 0
        assert fake.shots == 2
    finally:
        fake.stop()
