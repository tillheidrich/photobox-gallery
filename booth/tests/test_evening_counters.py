"""Counters of an evening, per event and day, that survive a restart.

Runs per mode, cancelled runs (button, time-out), retakes, prints and failed
prints, prints from the stand-in printer, the consent choice, camera errors
and reconnections, the big QR code and runs per hour (for the rush hour).
Only numbers: no names, no file names, no run ids, no times of single runs.
"""
import json
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boothcore.abendzahlen import ERLAUBT, Abendzahlen


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-counters-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["event"].update({"slug": "hochzeit", "name": "Hochzeit", "share_key": "k",
                                     "base_url": "https://galerie.example"})
    b.store.profile["print"].update({"enabled": True, "auto": False})
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
    return TestClient(create_app(booth))


def _shoot(booth, mode="photo", consent=None):
    s = booth.start_session(mode, "4:5", None, None, consent=consent)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _today(booth):
    return booth.zahlen.tag("hochzeit")


def _warte_auf_druck(booth):
    for _ in range(100):
        if booth.printer.pending == 0:
            return
        time.sleep(0.05)


def test_a_finished_run_counts_by_mode_and_hour(booth):
    _shoot(booth)
    _shoot(booth, "collage")
    z = _today(booth)
    assert z["runs.photo"] == 1 and z["runs.collage"] == 1
    assert z[f"hour.{time.localtime().tm_hour:02d}"] == 2


def test_the_counters_survive_a_restart(booth):
    _shoot(booth)
    from boothcore.app import Booth
    zweite = Booth(base=booth.data, start_threads=False)
    assert Abendzahlen(booth.data / "library.sqlite").tag("hochzeit")["runs.photo"] == 1
    assert zweite.zahlen.tag("hochzeit").get("runs.photo") == 1
    zweite.mail.stop()


def test_cancel_button_and_retake_are_counted(booth, client):
    s = booth.start_session("photo", "4:5", None, None)
    assert client.post(f"/api/session/{s.uid}/abort").status_code == 200
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.take_shot(s, retake=True)
    z = _today(booth)
    assert z["aborted.cancel"] == 1 and z["retakes"] == 1


def test_a_run_nobody_finished_counts_as_timed_out(booth):
    s = booth.start_session("photo", "4:5", None, None)
    s.created -= 4000
    booth._gc_sessions()
    assert _today(booth)["aborted.timeout"] == 1


def test_the_consent_choice_is_summed(booth):
    booth.store.profile["consent"].update({"enabled": True})
    _shoot(booth, consent={"option": "gallery", "lang": "de"})
    _shoot(booth, consent={"option": "print", "lang": "de"})
    z = _today(booth)
    assert z["consent.gallery"] == 1 and z["consent.print"] == 1


def test_prints_and_failed_prints_are_counted_test_sheets_are_not(booth):
    s = _shoot(booth)
    booth.print_uid(s.uid, 1)
    _warte_auf_druck(booth)
    booth.TESTDRUCK_WARTEN = 5
    booth.test_print({})
    assert _today(booth)["prints"] == 1

    from boothcore.printing.service import PrintJob
    st = booth.printer.stations[0]
    st.backend.print_file = lambda *a, **k: (_ for _ in ()).throw(OSError("Papierstau"))
    booth.printer.submit(PrintJob(Path(booth.library.get(s.uid)["print_file"]), 1, s.uid))
    for _ in range(100):
        if _today(booth).get("print_errors"):
            break
        time.sleep(0.05)
    assert _today(booth)["print_errors"] >= 1
    assert _today(booth)["prints"] == 1


def test_camera_errors_and_reconnects_come_from_the_camera_service(booth):
    booth._status_payload()
    booth.camera.stats["errors"] += 2
    booth.camera.stats["reconnects"] += 1
    booth._status_payload()
    booth._status_payload()
    z = _today(booth)
    assert z["camera.errors"] == 2 and z["camera.reconnects"] == 1


def test_the_big_qr_code_is_counted_and_nothing_else_can_be(booth, client):
    assert client.post("/api/count/qr_big").status_code == 200
    assert client.post("/api/count/irgendwas").status_code == 404
    assert _today(booth)["qr_big"] == 1


def test_the_heartbeat_carries_only_numbers(booth):
    _shoot(booth)
    st = booth._status_payload()
    bericht = st["booth_stats"]
    assert bericht["event_slug"] == "hochzeit" and bericht["day"] == time.strftime("%Y-%m-%d")
    assert bericht["today"]["runs.photo"] == 1 and bericht["event_total"]["runs.photo"] == 1
    for teil in ("today", "event_total"):
        assert set(bericht[teil]) <= ERLAUBT
        assert all(isinstance(v, int) for v in bericht[teil].values())
    text = json.dumps(bericht)
    assert booth.sessions and not any(uid in text for uid in booth.sessions)


def test_counting_goes_on_in_the_data_minimal_mode(booth):
    booth.store.profile["privacy"] = {**(booth.profile.get("privacy") or {}), "mode": "print_only"}
    assert booth.ephemeral
    _shoot(booth)
    assert _today(booth)["runs.photo"] == 1


def test_an_unknown_key_is_refused(tmp_path):
    z = Abendzahlen(tmp_path / "x.sqlite")
    z.zaehle("e", "guest.name", 1)
    z.zaehle("e", "prints", 2)
    assert z.tag("e") == {"prints": 2}
