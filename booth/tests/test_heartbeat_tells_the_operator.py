"""The heartbeat carries what the booth found about itself (register C3).

Before, the self-check and the operator messages stayed at the booth: the
gallery's light was green while the box knew its logo was missing or its
ribbon was empty.
"""
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-hb-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    b.camera.start()
    yield b
    b.camera.stop()
    b.mail.stop()


def _wait_for_doctor(b, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        st = b._status_payload()
        if st.get("doctor", {}).get("at"):
            return st
        time.sleep(0.2)
    raise AssertionError("no self-check in the heartbeat")


def test_the_heartbeat_carries_the_findings_of_the_self_check(booth):
    booth.store.profile["start"]["logo"] = "logo-fehlt.png"
    st = _wait_for_doctor(booth)
    doctor = st["doctor"]
    assert doctor["worst"] in ("warn", "fail")
    names = {f["name"] for f in doctor["findings"]}
    assert "Medien aus dem Profil" in names
    assert all(f["state"] in ("warn", "fail") for f in doctor["findings"])   # only what is not ok
    # The gallery check says nothing to the gallery that it does not know better itself.
    assert "Galerie" not in names


def test_the_heartbeat_carries_open_operator_messages(booth):
    booth.meldungen.melden("drucker-leer", "kritisch", "Farbband leer", "Drucker 1", quelle="drucker")
    booth.meldungen.melden("nur-info", "info", "Läuft")
    notices = booth._status_payload()["notices"]
    titles = [n["titel"] for n in notices]
    assert "Farbband leer" in titles and "Läuft" not in titles      # info is no open message
    ribbon = next(n for n in notices if n["titel"] == "Farbband leer")
    assert ribbon["stufe"] == "kritisch" and ribbon["quelle"] == "drucker"
    assert all(set(n) <= {"stufe", "titel", "text", "quelle", "at"} for n in notices)
