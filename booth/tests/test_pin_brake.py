"""A brake on the booth PIN.

Five wrong guesses, then a minute's pause; every further round doubles it, up
to fifteen minutes. The right PIN resets everything, and an operator who types
it correctly fifty times in an evening never waits.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boothcore.pinbrake import PinBrake


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-brake-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    # The 0.8 s pause per wrong PIN stays in the product; here it only slows the suite.
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.local["admin_pin"] = "4711"
    b.pin_brake.clock = Clock()
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def _login(client, pin):
    return client.post("/api/admin/login", json={"pin": pin})


def test_five_wrong_pins_close_the_door_for_a_minute(booth, client):
    for _ in range(5):
        assert _login(client, "0000").status_code == 401
    r = _login(client, "4711")
    assert r.status_code == 429, "not even the right PIN while the pause runs"
    assert r.headers.get("Retry-After") == "60"
    booth.pin_brake.clock.now += 61
    assert _login(client, "4711").status_code == 200


def test_the_right_pin_resets_the_count(booth, client):
    for _ in range(4):
        _login(client, "0000")
    assert _login(client, "4711").status_code == 200
    for _ in range(4):
        assert _login(client, "0000").status_code == 401
    assert _login(client, "4711").status_code == 200


def test_correct_pins_are_never_throttled(client):
    for _ in range(50):
        assert _login(client, "4711").status_code == 200


def test_locking_on_the_booth_shares_the_brake(booth, client):
    for _ in range(3):
        _login(client, "0000")
    for _ in range(2):
        assert client.post("/api/lock", json={"pin": "1234", "locked": True}).status_code == 401
    assert client.post("/api/lock", json={"pin": "4711", "locked": True}).status_code == 429
    assert booth.locked is False
    booth.pin_brake.clock.now += 61
    assert client.post("/api/lock", json={"pin": "4711", "locked": True}).status_code == 200


def test_the_pause_doubles_up_to_fifteen_minutes():
    clock = Clock()
    brake = PinBrake(clock=clock)
    pauses = []
    for _ in range(7):
        for _ in range(5):
            assert brake.wait_seconds() == 0
            brake.failed()
        pauses.append(brake.wait_seconds())
        clock.now += pauses[-1]
    assert pauses == [60, 120, 240, 480, 900, 900, 900]


def test_success_after_a_pause_starts_again_at_one_minute():
    clock = Clock()
    brake = PinBrake(clock=clock)
    for _ in range(10):
        brake.failed()
    assert brake.wait_seconds() == 120
    clock.now += 120
    brake.succeeded()
    for _ in range(5):
        brake.failed()
    assert brake.wait_seconds() == 60


# ── Self-diagnosis: reachable from the network with the factory PIN ──
def _access(booth):
    from boothcore.doctor import Doctor
    return next(c for c in Doctor(booth).run() if c.name == "Zugang")


def test_factory_pin_on_the_network_is_a_failure(booth):
    booth.store.local["admin_pin"] = "2580"
    booth.listen_host = "0.0.0.0"
    check = _access(booth)
    assert check.state == "fail"
    assert "2580" in check.message
    assert "PIN" in check.hint and "Verwaltung" in check.hint


def test_own_pin_on_the_network_is_fine(booth):
    booth.store.local["admin_pin"] = "2580"
    booth.store.profile["access"]["pin"] = "9317"           # set from the gallery
    booth.listen_host = "0.0.0.0"
    assert _access(booth).state == "ok"


def test_factory_pin_on_loopback_only_is_a_failure_too(booth):
    """The touchscreen is right there: loopback does not make 2580 safe."""
    booth.store.local["admin_pin"] = "2580"
    for host in ("127.0.0.1", "localhost", "::1"):
        booth.listen_host = host
        assert _access(booth).state == "fail", host
