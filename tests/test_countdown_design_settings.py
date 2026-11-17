"""Countdown design and the way out during capture: both sides, the form."""
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


def test_both_sides_know_the_same_countdown():
    assert config.DEFAULT_PROFILE["countdown"] == ecosystem.DEFAULT_PROFILE["countdown"]


def test_the_form_writes_style_colour_size_and_the_way_out(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "cd_display": "ring", "cd_color": "#ff8800", "cd_size": "xl",
        "cd_cancel": "on"})
    cd = _saved(dev)["countdown"]
    assert (cd["display"], cd["color"], cd["size"], cd["cancel"]) == ("ring", "#ff8800", "xl", True)


def test_nonsense_falls_back_to_todays_countdown(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "cd_display": "flipclock", "cd_color": "orange", "cd_size": "xxl"})
    cd = _saved(dev)["countdown"]
    assert (cd["display"], cd["color"], cd["size"], cd["cancel"]) == ("number", "", "l", False)
