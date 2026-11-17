"""No camera alarm while the camera is still starting; all-clear as soon as it is ready.

Seen on the real booth 17.11.2026: critical "camera not answering" one second
before "camera connected", and afterwards the chip was green while the text stayed.
"""
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-anlauf-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def _key(b, key):
    return next((n for n in b.meldungen.offen() if n["key"] == key), None)


def test_starting_camera_raises_nothing_within_the_grace_period(booth):
    booth.camera.state = "starting"
    booth._status_payload()
    assert _key(booth, "kamera") is None


def test_starting_camera_raises_the_alarm_after_the_grace_period(booth):
    booth.camera.state = "starting"
    booth._gestartet -= booth.CAMERA_START_GRACE_S + 1
    booth._status_payload()
    assert _key(booth, "kamera")["stufe"] == "kritisch"


def test_a_failed_camera_is_not_covered_by_the_grace_period(booth):
    booth.camera.state = "error"
    booth._status_payload()
    assert _key(booth, "kamera") is not None


def test_ready_withdraws_the_camera_notice_in_the_heartbeat(booth):
    booth.camera.state = "error"
    booth._status_payload()
    assert _key(booth, "kamera")
    booth.camera.state = "ready"
    st = booth._status_payload()
    assert _key(booth, "kamera") is None
    assert not [n for n in st["notices"] if n["quelle"] == "kamera"]


def test_ready_withdraws_it_at_the_kiosk_without_waiting_for_a_heartbeat(booth):
    booth.camera.state = "error"
    booth._status_payload()
    booth.camera.state = "ready"
    booth._geraete_abgleichen()
    assert booth.meldungen.fuer_die_box() is None


def test_printer_notice_is_withdrawn_when_a_printer_becomes_ready(booth):
    booth._drucker_melden(1, 0, "Papier leer")
    assert _key(booth, "drucker")["titel"] == "Kein Drucker ist bereit"
    booth._drucker_melden(1, 1, "")
    assert _key(booth, "drucker") is None
