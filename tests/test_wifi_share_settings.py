"""Download without internet: both sides know the block, the form writes it.

What the booth does with it (Wi-Fi code, guest page, its own listener) is
pinned in booth/tests/test_wifi_share.py.
"""
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


def test_both_sides_know_the_same_block_and_it_is_off():
    assert config.DEFAULT_PROFILE["wifi_share"] == ecosystem.DEFAULT_PROFILE["wifi_share"]
    assert ecosystem.DEFAULT_PROFILE["wifi_share"]["enabled"] is False


def test_the_form_writes_it(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "wifi_enabled": "on", "wifi_mode": "always",
        "wifi_ssid": "  Fotobox Gäste  ", "wifi_password": "a;b,c", "wifi_security": "WPA",
        "wifi_link_hours": "500"})
    w = _saved(dev)["wifi_share"]
    assert w == {"enabled": True, "mode": "always", "ssid": "Fotobox Gäste", "password": "a;b,c",
                 "security": "WPA", "hidden": False, "link_hours": 168}


def test_unknown_choices_fall_back(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "wifi_mode": "sometimes", "wifi_security": "WPA9"})
    w = _saved(dev)["wifi_share"]
    assert w["enabled"] is False and w["mode"] == "auto" and w["security"] == "WPA" and w["link_hours"] == 24


def test_the_texts_of_the_two_steps_are_in_the_catalogue():
    from app import booth_texts
    keys = {k for _, entries in booth_texts.GROUPS for k, _, _ in entries}
    assert {"result.wifi_join", "result.wifi_join_hint", "result.wifi_photo", "result.wifi_photo_hint",
            "result.wifi_ssid", "result.wifi_password"} <= keys
