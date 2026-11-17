"""A run that ends in the kiosk's error screen leaves one line in the log.

On 15.11.2026 a GIF run at the real booth ended in the error screen at shot 4
of 6 and the booth's log had nothing about it: the failing request was a 409
(two runs feeding one session), and only camera exceptions were ever logged —
without run, mode or shot. Now every failed shot, clip or finish logs one
line with uid, mode, shot number and cause; a camera failure also counts in
the evening counters (``camera.errors``). A run the guest threw away is not a
failure and stays at INFO.
"""
import logging
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-failtrace-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["event"].update({"slug": "hochzeit", "name": "Hochzeit", "share_key": "k",
                                     "base_url": "https://galerie.example"})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth), raise_server_exceptions=False)


def _lines(caplog, uid):
    return [r for r in caplog.records if uid in r.getMessage() and "fehlgeschlagen" in r.getMessage()]


def test_a_shot_beyond_the_series_is_logged_with_run_mode_and_number(booth, client, caplog):
    s = booth.start_session("gif", "4:5", None, None)
    for _ in range(s.shots_needed):
        assert client.post(f"/api/session/{s.uid}/shot", json={}).status_code == 200
    with caplog.at_level(logging.INFO, logger="booth"):
        r = client.post(f"/api/session/{s.uid}/shot", json={})
    assert r.status_code == 409
    lines = _lines(caplog, s.uid)
    assert len(lines) == 1
    msg = lines[0].getMessage()
    assert lines[0].levelno == logging.WARNING
    assert "(gif)" in msg and f"Aufnahme {s.shots_needed + 1}/{s.shots_needed}" in msg and "409" in msg


def test_a_camera_failure_is_an_error_with_its_cause_and_is_counted(booth, client, caplog, monkeypatch):
    s = booth.start_session("photo", "4:5", None, None)

    def kaputt(target, timeout=0):
        raise RuntimeError("Kamera belegt (0x81)")
    monkeypatch.setattr(booth.camera, "capture", kaputt)
    with caplog.at_level(logging.INFO, logger="booth"):
        r = client.post(f"/api/session/{s.uid}/shot", json={})
    assert r.status_code == 503
    lines = _lines(caplog, s.uid)
    assert len(lines) == 1, [l.getMessage() for l in caplog.records]
    assert lines[0].levelno == logging.ERROR
    msg = lines[0].getMessage()
    assert "(photo)" in msg and "Aufnahme 1/1" in msg and "Kamera belegt (0x81)" in msg
    assert booth.zahlen.tag("hochzeit").get("camera.errors") == 1


def test_an_unknown_run_is_logged_too(booth, client, caplog):
    with caplog.at_level(logging.INFO, logger="booth"):
        r = client.post("/api/session/gibtsnicht/shot", json={})
    assert r.status_code == 404
    assert len(_lines(caplog, "gibtsnicht")) == 1


def test_a_finish_that_fails_is_logged(booth, client, caplog):
    s = booth.start_session("collage", "4:5", None, None)
    with caplog.at_level(logging.INFO, logger="booth"):
        r = client.post(f"/api/session/{s.uid}/finish", json={})
    assert r.status_code == 409
    lines = _lines(caplog, s.uid)
    assert len(lines) == 1 and "Fertigstellen" in lines[0].getMessage()


def test_a_run_the_guest_threw_away_is_not_a_warning(booth, client, caplog):
    s = booth.start_session("photo", "4:5", None, None)
    assert client.post(f"/api/session/{s.uid}/abort").status_code == 200
    with caplog.at_level(logging.INFO, logger="booth"):
        client.post(f"/api/session/{s.uid}/shot", json={})
    lines = _lines(caplog, s.uid)
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert "camera.errors" not in booth.zahlen.tag("hochzeit")
