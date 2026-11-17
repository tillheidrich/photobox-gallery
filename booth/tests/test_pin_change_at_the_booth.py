"""The operator changes the PIN at the booth, and the gallery does not undo it.

Owner's decision of 16.11.2026: four-digit PINs stay allowed, the operator
changes his PIN himself ("PIN ändern" in the menu behind the corner), and a
profile from the gallery never erases or resets a PIN set at the booth. Four
digits are acceptable only because the brake (pinbrake.py) survives a restart.
"""
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boothcore.config import weak_pin_reason
from boothcore.pinbrake import PinBrake


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-pinchange-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "4711"})
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


@pytest.mark.parametrize("pin", ["4711", "8024", "1379", "468213"])
def test_four_digits_are_enough(pin):
    assert weak_pin_reason(pin) == ""


@pytest.mark.parametrize("pin", ["2580", "1234", "4321", "0000", "123", "12a4", ""])
def test_guessable_four_digit_pins_are_still_refused(pin):
    assert weak_pin_reason(pin)


def test_the_operator_changes_the_pin_with_the_current_one(booth, client):
    r = client.post("/api/pin/change", json={"current_pin": "4711", "new_pin": "8024"})
    assert r.status_code == 200 and r.json()["in_force"] is True
    assert booth.current_pin() == "8024"
    assert client.post("/api/admin/login", json={"pin": "4711"}).status_code == 401
    assert client.post("/api/admin/login", json={"pin": "8024"}).status_code == 200


def test_a_wrong_current_pin_changes_nothing_and_counts_for_the_brake(booth, client):
    for _ in range(5):
        r = client.post("/api/pin/change", json={"current_pin": "0000", "new_pin": "8024"})
        assert r.status_code == 401
    assert booth.current_pin() == "4711"
    r = client.post("/api/pin/change", json={"current_pin": "4711", "new_pin": "8024"})
    assert r.status_code == 429, "the change is no way round the brake"


def test_a_weak_new_pin_is_refused_with_the_reason(booth, client):
    r = client.post("/api/pin/change", json={"current_pin": "4711", "new_pin": "1234"})
    assert r.status_code == 400 and "1234" in r.json()["detail"]
    assert booth.current_pin() == "4711"


def test_old_sign_ins_end_with_the_change(booth, client):
    token = client.post("/api/admin/login", json={"pin": "4711"}).json()["token"]
    client.post("/api/pin/change", json={"current_pin": "4711", "new_pin": "8024"})
    assert client.get("/api/admin/config", headers={"X-Admin-Token": token}).status_code == 401


def _gallery_profile(booth, pin):
    profile = json.loads(json.dumps(booth.profile))
    profile["access"]["pin"] = pin
    profile["version"] = int(profile.get("version", 0)) + 1
    return profile


@pytest.mark.parametrize("sent", ["", "••••", "****", None])
def test_a_profile_with_an_empty_or_masked_pin_keeps_the_booth_pin(booth, sent):
    booth.set_admin_pin("8024")
    profile = _gallery_profile(booth, sent)
    booth._apply_server_profile(profile)
    assert booth.current_pin() == "8024"
    assert booth.store.local["admin_pin"] == "8024"


def test_the_gallery_pin_replaced_at_the_booth_does_not_come_back(booth, client):
    booth._apply_server_profile(_gallery_profile(booth, "5937"))
    assert booth.current_pin() == "5937", "a gallery PIN is in force until the booth changes it"
    r = client.post("/api/pin/change", json={"current_pin": "5937", "new_pin": "8024"})
    assert r.status_code == 200 and r.json()["in_force"] is True
    # The next sync still carries the gallery's old PIN: it must not win again.
    booth._apply_server_profile(_gallery_profile(booth, "5937"))
    assert booth.current_pin() == "8024"
    # A new PIN typed in the gallery afterwards is a decision: it wins.
    booth._apply_server_profile(_gallery_profile(booth, "6291"))
    assert booth.current_pin() == "6291"


def test_the_brake_survives_a_restart(tmp_path):
    clock = [1000.0]
    wall = [5000.0]
    brake = PinBrake(path=tmp_path / "brake.json", clock=lambda: clock[0], wall=lambda: wall[0])
    for _ in range(5):
        brake.failed()
    assert brake.wait_seconds() == 60
    # Restart: a new process, a new monotonic clock, thirty seconds later.
    wall[0] += 30
    again = PinBrake(path=tmp_path / "brake.json", clock=lambda: 7.0, wall=lambda: wall[0])
    assert again.wait_seconds() == 30
    # And the next round after a restart is the doubled one.
    later = PinBrake(path=tmp_path / "brake.json", clock=lambda: 7.0, wall=lambda: wall[0] + 31)
    for _ in range(5):
        later.failed()
    assert later.wait_seconds() == 120
    later.succeeded()
    assert PinBrake(path=tmp_path / "brake.json", wall=lambda: wall[0] + 31).wait_seconds() == 0


def test_a_clock_set_far_ahead_on_disk_cannot_lock_the_operator_out_forever(tmp_path):
    (tmp_path / "brake.json").write_text(json.dumps({"failures": 0, "rounds": 3, "until_wall": 10 ** 12}))
    assert PinBrake(path=tmp_path / "brake.json").wait_seconds() <= 900


def test_the_brake_file_holds_no_pin(booth, client):
    client.post("/api/admin/login", json={"pin": "0815"})
    text = (booth.data / "pin_brake.json").read_text()
    assert "0815" not in text and "4711" not in text


def test_the_operator_menu_offers_the_change():
    web = Path(__file__).resolve().parents[1] / "boothcore" / "web"
    assert 'data-action="menu-pin"' in (web / "index.html").read_text(encoding="utf-8")
    js = (web / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/pin/change" in js and "current_pin" in js


def test_pairing_offers_the_booth_profile_without_secrets(booth):
    booth.store.profile["controls"]["mouse_start"] = "left"
    booth.store.profile["access"]["pin"] = "5937"
    booth.store.profile["mail"]["password"] = "geheim-im-test"
    booth._store_pairing("https://galerie.example", "schluessel-im-test", "Box 1")
    seed = booth.profile_seed()
    assert seed["controls"]["mouse_start"] == "left"
    assert seed["access"]["pin"] == "" and seed["mail"]["password"] == ""
    assert "event" not in seed and "version" not in seed
    booth.profile_seed_answered("kept")
    assert booth.profile_seed() == {}
