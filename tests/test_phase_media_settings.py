"""Media per phase of the run: both sides know the block, the form writes it.

The booth side (choice, timing, reduce motion) is pinned in
booth/tests/test_phase_media.py.
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


def test_both_sides_know_the_same_phase_media():
    assert config.DEFAULT_PROFILE["phase_media"] == ecosystem.DEFAULT_PROFILE["phase_media"]


def test_by_default_no_phase_shows_anything_and_nothing_takes_longer():
    pm = ecosystem.DEFAULT_PROFILE["phase_media"]
    assert all(c["media"] == "" for c in pm.values())
    assert pm["after"]["seconds"] == 0


def test_the_form_writes_medium_fit_text_loop_and_duration(admin_client, client, monkeypatch):
    files = [{"file": "danke.mp4", "kind": "start"}, {"file": "warten.gif", "kind": "background"},
             {"file": "vorlage.json", "kind": "start"}]
    monkeypatch.setattr(ecosystem, "list_assets", lambda kind=None: [a for a in files if not kind or a["kind"] == kind])
    dev = _device(client)
    r = admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token",
        "pm_after_media": "danke.mp4", "pm_after_fit": "contain", "pm_after_text": "on",
        "pm_after_seconds": "99",
        "pm_processing_media": "warten.gif", "pm_processing_loop": "on",
        "pm_error_media": "vorlage.json",
        "pm_between_media": "gibt-es-nicht.png", "pm_between_fit": "zoom",
    })
    assert r.status_code in (200, 302)
    pm = _saved(dev)["phase_media"]
    assert pm["after"] == {"media": "danke.mp4", "fit": "contain", "text": True, "loop": False, "seconds": 15}
    assert pm["processing"] == {"media": "warten.gif", "fit": "cover", "text": False, "loop": True}
    assert pm["error"]["media"] == "", "a template is no medium"
    assert pm["between"]["media"] == "" and pm["between"]["fit"] == "cover"


def test_the_texts_of_the_phases_are_in_the_catalogue():
    from app import booth_texts
    keys = {k for _, entries in booth_texts.GROUPS for k, _, _ in entries}
    assert {"phase.between", "phase.after"} <= keys
