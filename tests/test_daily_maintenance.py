"""Retention is enforced without anybody setting up a cron job.

Gap L3 of the audit of 11.11.2026: "Without the daily maintenance run,
retention is a form field; production has no such run." The gallery now
starts the run itself, at most once a day across all workers, and the
dashboard says when it last ran. Defaults for retention were 30 days in the
database and for booth events, 90 in the admin form; they are 30 everywhere.
"""
import datetime as dt
import re
import secrets

import pytest

from app import db, ecosystem, maintenance


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting(maintenance.LAST_RUN_KEY, "")
        db.set_setting(maintenance.CLAIM_KEY, "")
    yield


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"


def test_only_one_worker_gets_the_daily_run(app):
    now = dt.datetime(2026, 11, 11, 3, 0, 0)
    assert maintenance.claim(now) is True
    assert maintenance.claim(now) is False, "a second worker must not start it again"
    assert maintenance.claim(now + dt.timedelta(hours=23)) is False
    assert maintenance.claim(now + dt.timedelta(hours=25)) is True


def test_a_run_from_outside_counts_too(app):
    maintenance.run_and_record()
    assert maintenance.claim(maintenance._utcnow()) is False
    assert maintenance.status()["last_run"]


def test_the_run_actually_empties_an_expired_gallery(app):
    k = secrets.token_hex(3)
    eid = db.create_event(name="Alt", slug=f"alt-wartung-{k}", upload_token=f"t-{k}", retention_days=1)
    db.update_event(eid, date="2026-01-01")
    db.add_media(event_id=eid, kind="photo", filename="a.jpg", path_original=f"storage/x{k}/orig/a.jpg",
                 path_webp=None, path_thumb=None, size_bytes=1, sha256=k, share_token=k)
    assert maintenance.maybe_start(app, background=False, now=maintenance._utcnow()) is True
    assert db.count_media(eid) == 0
    assert maintenance.maybe_start(app, background=False, now=maintenance._utcnow()) is False


def test_it_can_be_switched_off(app, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_MAINTENANCE", "off")
    assert maintenance.maybe_start(app, background=False, now=maintenance._utcnow()) is False


def test_status_warns_when_the_run_is_older_than_36_hours(app):
    assert maintenance.status()["stale"] is True, "never run is stale"
    db.set_setting(maintenance.LAST_RUN_KEY,
                   (maintenance._utcnow() - dt.timedelta(hours=37)).isoformat(timespec="seconds"))
    assert maintenance.status()["stale"] is True
    db.set_setting(maintenance.LAST_RUN_KEY,
                   (maintenance._utcnow() - dt.timedelta(hours=2)).isoformat(timespec="seconds"))
    assert maintenance.status()["stale"] is False


def test_the_dashboard_shows_the_last_run(client):
    _admin(client)
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert 'id="maintenance-status"' in page and "maintenance-stale" in page
    maintenance.run_and_record()
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert 'id="maintenance-status"' in page and "maintenance-stale" not in page


def test_the_new_event_form_suggests_30_days(client):
    _admin(client)
    page = client.get("/admin/events/new").get_data(as_text=True)
    feld = re.search(r'name="retention_days"[^>]*value="(\d+)"|value="(\d+)"[^>]*name="retention_days"', page, re.S)
    assert feld and "30" in feld.groups()


def test_an_event_without_a_retention_value_gets_30_days(client):
    _admin(client)
    name = "Frist " + secrets.token_hex(3)
    client.post("/admin/events/new", data={"name": name, "csrf_token": "x", "retention_days": ""})
    event = next(e for e in db.list_events() if e["name"] == name)
    assert int(event["retention_days"]) == 30
