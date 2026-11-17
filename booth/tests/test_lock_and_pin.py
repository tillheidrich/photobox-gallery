"""Sperren an der Box, PIN aus der Ferne.

Der heikle Teil ist nicht das Sperren, sondern dass die PIN im Profil steht:
sie darf niemals in der Gast-Oberfläche landen.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-lock-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def test_the_pin_never_reaches_the_guest_interface(booth, client):
    booth.store.profile["access"]["pin"] = "9317"
    state = client.get("/api/state").json()
    assert "pin" not in (state["profile"].get("access") or {})
    assert "9317" not in client.get("/api/state").text


def test_the_profile_pin_wins_over_the_local_one(booth):
    booth.store.local["admin_pin"] = "2580"
    assert booth.current_pin() == "2580"
    booth.store.profile["access"]["pin"] = "4711"
    assert booth.current_pin() == "4711"
    # Leeres Feld heißt: die Box behält ihre eigene PIN.
    booth.store.profile["access"]["pin"] = ""
    assert booth.current_pin() == "2580"


def test_locking_needs_the_pin(booth, client):
    booth.store.local["admin_pin"] = "2580"
    assert client.post("/api/lock", json={"pin": "0000", "locked": True}).status_code == 401
    assert booth.locked is False

    r = client.post("/api/lock", json={"pin": "2580", "locked": True})
    assert r.status_code == 200 and r.json()["locked"] is True
    assert booth.locked is True
    assert client.get("/api/state").json()["locked"] is True


def test_unlocking_works_the_same_way(booth, client):
    booth.store.local["admin_pin"] = "2580"
    client.post("/api/lock", json={"pin": "2580", "locked": True})
    client.post("/api/lock", json={"pin": "2580", "locked": False})
    assert booth.locked is False


def test_the_operator_can_forbid_locking_on_the_booth(booth, client):
    booth.store.profile["access"]["lock_on_booth"] = False
    r = client.post("/api/lock", json={"pin": "2580", "locked": True})
    assert r.status_code == 403
    assert booth.locked is False


def test_a_remote_lock_stays_out_of_reach(booth, client):
    """Was die Galerie gesperrt hat, darf vor Ort nicht weggetippt werden."""
    booth.remote_locked = True
    assert booth.locked is True
    state = client.get("/api/state").json()
    assert state["locked"] is True and state["remote_locked"] is True
    # Die Oberfläche blendet den Entsperren-Knopf anhand dieses Feldes aus.
    booth.remote_locked = False
    assert client.get("/api/state").json()["remote_locked"] is False


def test_the_admin_login_accepts_the_remote_pin(booth, client):
    booth.store.profile["access"]["pin"] = "135790"
    assert client.post("/api/admin/login", json={"pin": "2580"}).status_code == 401
    assert client.post("/api/admin/login", json={"pin": "135790"}).status_code == 200
