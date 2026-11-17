"""Own start picture: both sides know the switches, the form writes them."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "booth"))

from app import ecosystem                      # noqa: E402
from boothcore import config                   # noqa: E402


@pytest.fixture()
def admin_client(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _device(client):
    client.get("/healthz")
    from app import db
    ecosystem.init_schema()
    return db.get_device_by_id(db.create_device(name="Box")["id"])


def _saved(dev):
    from app import db
    return ecosystem.device_profile(db.get_device_by_id(dev["id"]))


def test_both_sides_know_the_same_switches():
    for key in ("show_title", "show_status", "fit"):
        assert config.DEFAULT_PROFILE["start"][key] == ecosystem.DEFAULT_PROFILE["start"][key], key


def test_the_form_writes_them(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "start_fit": "contain"})      # both boxes unticked = off
    s = _saved(dev)["start"]
    assert s["show_title"] is False and s["show_status"] is False and s["fit"] == "contain"


def test_an_unknown_fit_falls_back_to_the_old_drift(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "start_show_title": "on", "start_show_status": "on", "start_fit": "zoom"})
    s = _saved(dev)["start"]
    assert s["show_title"] is True and s["show_status"] is True and s["fit"] == "drift"
