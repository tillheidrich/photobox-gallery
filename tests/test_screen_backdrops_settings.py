"""Backdrops per screen: both sides know them, the form writes them, the
pictures travel to the booth.

A picture chosen as backdrop is useless if the booth never receives the file,
so it has to be among the assets the sync hands out.
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


def test_both_sides_know_the_same_backdrops():
    assert config.DEFAULT_PROFILE["backdrops"] == ecosystem.DEFAULT_PROFILE["backdrops"]
    for key in ("bg_color", "dim"):
        assert config.DEFAULT_PROFILE["start"][key] == ecosystem.DEFAULT_PROFILE["start"][key], key


def test_the_form_writes_a_picture_a_colour_and_a_dim(admin_client, client):
    dev = _device(client)
    r = admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token",
        "bd_setup_color": "#112233", "bd_setup_dim": "30",
        "bd_capture_image": "gibt-es-nicht.jpg", "bd_capture_dim": "95",
        "bd_result_color": "rot",
        "start_bg_color": "#000000", "start_dim": "40",
    })
    assert r.status_code in (200, 302)
    b = _saved(dev)["backdrops"]
    assert b["setup"] == {"image": "", "color": "#112233", "dim": 30}
    assert b["capture"] == {"image": "", "color": "", "dim": 80}, "unknown file dropped, dim capped"
    assert b["result"]["color"] == "", "not a colour, not stored"
    start = _saved(dev)["start"]
    assert start["bg_color"] == "#000000" and start["dim"] == 40


def test_an_untouched_form_keeps_the_old_look(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={"csrf_token": "test-token"})
    p = _saved(dev)
    assert p["backdrops"] == config.DEFAULT_PROFILE["backdrops"]
    assert p["start"]["dim"] == -1 and p["start"]["bg_color"] == ""


def test_a_backdrop_picture_travels_to_the_booth(monkeypatch):
    files = [{"file": "nacht.jpg", "kind": "background"}, {"file": "andere.jpg", "kind": "start"}]
    monkeypatch.setattr(ecosystem, "list_assets", lambda kind=None: [a for a in files if not kind or a["kind"] == kind])
    profile = {"backdrops": {"setup": {"image": "nacht.jpg"}, "capture": {"image": ""}, "result": {}}}
    names = [a["file"] for a in ecosystem.assets_for_profile(profile)]
    assert "nacht.jpg" in names and "andere.jpg" not in names
