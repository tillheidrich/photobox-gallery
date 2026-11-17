"""The camera battery: read from the Canon SDK, shown, reported, warned about.

kEdsPropID_BatteryLevel (0x08) is read at connect and every five minutes on
the camera thread, between live-view frames — never while a shot runs. It
goes into /api/state (``camera.battery``: percent, "ac" or null) and into the
heartbeat (``camera_battery``). At 20 % the operator gets a warning, at 10 %
a critical message (``op.camera_battery``); gone on mains power or above 20 %.
Only the EDSDK backend can tell; the others say "unknown" and warn nothing.
"""
import tempfile
import threading
import time
from pathlib import Path

import pytest

from fake_edsdk import FakeEdsdk, bereitstellen


# ── The SDK layer ─────────────────────────────────────────────────────

@pytest.fixture()
def kamera(monkeypatch):
    from boothcore.camera.edsdk import EdsdkCamera
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    cam = EdsdkCamera()
    cam.open()
    yield cam, sdk
    try:
        cam.close()
    except Exception:
        pass


def test_the_sdk_battery_level_is_read_as_percent(kamera):
    cam, sdk = kamera
    sdk.eigenschaften[0x00000008] = 57
    assert cam.battery_level() == 57
    assert 0x00000008 in sdk.gelesen


def test_mains_power_reads_as_ac(kamera):
    cam, sdk = kamera
    sdk.eigenschaften[0x00000008] = 0xFFFFFFFF
    assert cam.battery_level() == "ac"


def test_an_unreadable_battery_is_unknown(kamera):
    cam, sdk = kamera
    sdk.lese_fehler[0x00000008] = 0x00000050
    assert cam.battery_level() is None


def test_other_backends_do_not_know():
    from boothcore.camera.base import CameraBackend
    from boothcore.camera.mock import MockCamera
    assert CameraBackend().battery_level() is None
    assert MockCamera().battery_level() is None


# ── The camera service ────────────────────────────────────────────────

class Messbar:
    """A mock camera that records whether the battery was asked during a shot."""

    def __init__(self):
        from boothcore.camera.mock import MockCamera
        self.cam = MockCamera()
        self.cam.battery_value = 64
        self.reads = 0
        self.in_shot = False
        self.read_during_shot = False

    def __getattr__(self, name):
        return getattr(self.cam, name)

    def battery_level(self):
        self.reads += 1
        if self.in_shot:
            self.read_during_shot = True
        return self.cam.battery_value

    def capture(self, target):
        self.in_shot = True
        try:
            time.sleep(0.3)
            return self.cam.capture(target)
        finally:
            self.in_shot = False


def _wait(cond, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_the_service_reads_at_connect_and_then_every_interval_never_during_a_shot(tmp_path, monkeypatch):
    from boothcore.camera.service import CameraService
    backend = Messbar()
    svc = CameraService(lambda: backend)
    monkeypatch.setattr(svc, "BATTERY_EVERY_S", 0.2)
    svc.start()
    try:
        assert _wait(lambda: svc.state == "ready" and svc.battery == 64), "read right after connecting"
        assert svc.info()["battery"] == 64
        backend.cam.battery_value = 15
        assert _wait(lambda: svc.battery == 15), "read again after the interval"
        for i in range(4):
            svc.capture(tmp_path / f"shot{i}.jpg", timeout=10)
        assert backend.reads >= 3
        assert not backend.read_during_shot
    finally:
        svc.stop()


def test_the_default_interval_is_five_minutes():
    from boothcore.camera.service import CameraService
    assert CameraService.BATTERY_EVERY_S == 300.0


# ── The booth: /api/state, heartbeat, operator message ────────────────

@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-akku-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def _notice(b, key="kamera_akku"):
    return next((n for n in b.meldungen.offen() if n.get("key") == key), None)


@pytest.mark.parametrize("value, level", [(18, "warnung"), (20, "warnung"), (10, "kritisch"), (4, "kritisch")])
def test_low_battery_raises_the_operator_message(booth, value, level):
    booth.camera.battery = value
    st = booth._status_payload()
    assert st["camera_battery"] == str(value)
    n = _notice(booth)
    assert n and n["stufe"] == level and n["code"] == "op.camera_battery"
    assert n["werte"]["percent"] == str(value)


@pytest.mark.parametrize("value", [21, 80, "ac"])
def test_enough_battery_or_mains_clears_it(booth, value):
    booth.camera.battery = 9
    booth._status_payload()
    assert _notice(booth)
    booth.camera.battery = value
    st = booth._status_payload()
    assert _notice(booth) is None
    assert st["camera_battery"] == str(value)


def test_unknown_battery_says_nothing_and_travels_as_nothing(booth):
    booth.camera.battery = None
    st = booth._status_payload()
    assert "camera_battery" not in st
    assert _notice(booth) is None
    assert booth.camera.info()["battery"] is None


def test_the_operator_message_has_words_in_both_languages():
    from boothcore.app import OPERATOR_CODES
    assert "op.camera_battery" in OPERATOR_CODES
    root = Path(__file__).resolve().parents[2]
    js = (root / "booth/boothcore/web/static/i18n.js").read_text(encoding="utf-8")
    py = (root / "app/booth_texts.py").read_text(encoding="utf-8")
    for key in ("op.camera_battery.title", "op.camera_battery.text"):
        assert js.count(f'"{key}"') == 2, key            # de and en
        assert py.count(f'"{key}"') == 2, key            # German list and English table
