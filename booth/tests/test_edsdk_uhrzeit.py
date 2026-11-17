"""The camera gets the PC's clock when the booth connects to it.

Seen on the EOS M50 on 15.11.2026: on a mains adapter (dummy battery) the
camera loses its clock and every picture says 2017. The booth now sets the
camera's date and time from the PC each time it opens the camera. That must
never cost the connection: a body that refuses the property, or a call that
fails inside the DLL, is written to the log once and otherwise ignored.
"""
import logging
import time

import pytest

from fake_edsdk import FakeEdsdk, bereitstellen

DATETIME = 0x00000006


@pytest.fixture()
def treiber(monkeypatch):
    from boothcore.camera import edsdk as modul
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    monkeypatch.setattr(modul.EdsdkCamera, "_clock_warned", False, raising=False)
    return modul, sdk


def _uhr(sdk):
    return [(size, wert) for prop, size, wert in sdk.gesetzt if prop == DATETIME]


def test_the_camera_clock_is_set_on_connect(treiber):
    modul, sdk = treiber
    vorher = time.time()
    cam = modul.EdsdkCamera()
    cam.open()
    try:
        gesetzt = _uhr(sdk)
        assert len(gesetzt) == 1, "the clock is set exactly once per connect"
        size, wert = gesetzt[0]
        assert size == 28, "EdsTime: seven 32-bit fields"
        stamp = time.mktime((wert["year"], wert["month"], wert["day"], wert["hour"],
                             wert["minute"], wert["second"], 0, 0, -1))
        assert vorher - 2 <= stamp <= time.time() + 2
    finally:
        cam.close()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset to change the zone")
def test_the_clock_is_the_local_time_not_utc(treiber, monkeypatch):
    modul, sdk = treiber
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")      # UTC+14: another day than UTC
    time.tzset()
    try:
        cam = modul.EdsdkCamera()
        cam.open()
        cam.close()
        wert = _uhr(sdk)[0][1]
        jetzt = time.localtime()
        assert (wert["year"], wert["month"], wert["day"], wert["hour"]) == \
            (jetzt.tm_year, jetzt.tm_mon, jetzt.tm_mday, jetzt.tm_hour)
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_a_camera_that_refuses_the_clock_still_connects(treiber, caplog):
    modul, sdk = treiber
    sdk.eigenschaft_fehler[DATETIME] = 0x00000029     # EDS_ERR_NOT_SUPPORTED-like answer
    caplog.set_level(logging.INFO, logger="booth.camera.edsdk")
    for _ in range(3):                                # reconnects must not fill the log
        cam = modul.EdsdkCamera()
        cam.open()
        assert cam._evf_on, "the live view runs: the camera is connected"
        cam.close()
    meldungen = [r for r in caplog.records if "Uhrzeit" in r.getMessage()]
    assert len(meldungen) == 1


def test_an_error_inside_the_sdk_call_does_not_break_the_connection(treiber):
    modul, sdk = treiber
    sdk.eigenschaft_wirft.add(DATETIME)
    cam = modul.EdsdkCamera()
    cam.open()
    try:
        assert cam._evf_on
        ziel_props = [p for p, *_ in sdk.gesetzt]
        assert 0x0000000B in ziel_props, "SaveTo=Host still set after the clock failed"
    finally:
        cam.close()
