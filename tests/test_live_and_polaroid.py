"""Live badge with "x neue Fotos" and the polaroid layout (no. 59).

Both are switches (``live_badge``, ``layout: polaroid``) and off by default.
"""
import io
import json
import secrets
from datetime import date, datetime, timedelta, timezone

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import db, ecosystem, gallery_settings as gsm, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _event(client, *, event_date=None, password="", **gs_values):
    key = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"Live {key}", slug=f"live-{key}", upload_token=f"tok-{key}")
        f = {"published": 1, "gallery_json": json.dumps(gsm.clean(gs_values))}
        if event_date:
            f["date"] = event_date
        db.update_event(eid, f)
        if password:
            db.set_event_password(eid, password)
        return db.get_event_by_id(eid)


def _add(client, event, minutes_ago=0):
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (90, 90, 200)).save(buf, "JPEG")
    buf.seek(0)
    with client.application.app_context():
        mid = media.save_upload(event["id"], FileStorage(buf, filename="l.jpg"))["id"]
        if minutes_ago:
            con = db.get_db()
            stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
            con.execute("UPDATE media SET created_at=? WHERE id=?", (stamp, mid))
            con.commit()
            con.close()
        return mid


TODAY = date.today().isoformat()


def test_no_badge_without_the_switch(client):
    event = _event(client, event_date=TODAY)
    _add(client, event)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert "data-live" not in page and "liveNew" not in page


def test_the_badge_shows_while_the_event_runs_and_photos_arrive(client):
    event = _event(client, event_date=TODAY, live_badge=True)
    _add(client, event)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert 'data-live="1"' in page


def test_no_badge_on_another_day(client):
    old = (date.today() - timedelta(days=5)).isoformat()
    event = _event(client, event_date=old, live_badge=True)
    _add(client, event)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert 'data-live="1"' not in page


def test_no_badge_when_nothing_arrived_for_a_while(client):
    event = _event(client, event_date=TODAY, live_badge=True)
    _add(client, event, minutes_ago=180)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert 'data-live="1"' not in page


def test_the_feed_counts_new_photos_since_the_page_was_loaded(client):
    event = _event(client, event_date=TODAY, live_badge=True)
    first = _add(client, event)
    _add(client, event)
    _add(client, event)
    data = client.get(f"/gallery/{event['slug']}/live.json?since={first}").get_json()
    assert data["new"] == 2 and data["live"] is True


def test_the_feed_is_closed_without_the_switch_and_behind_the_password(client):
    plain = _event(client, event_date=TODAY)
    assert client.get(f"/gallery/{plain['slug']}/live.json").status_code == 404
    locked = _event(client, event_date=TODAY, live_badge=True, password="geheim-123")
    assert client.get(f"/gallery/{locked['slug']}/live.json").status_code == 403


def test_the_feed_does_not_count_pictures_waiting_for_approval(client):
    event = _event(client, event_date=TODAY, live_badge=True, moderation=True)
    _add(client, event)
    data = client.get(f"/gallery/{event['slug']}/live.json?since=0").get_json()
    assert data["new"] == 0


def test_polaroid_is_a_layout_and_unknown_values_fall_back():
    assert gsm.clean({"layout": "polaroid"})["layout"] == "polaroid"
    assert gsm.clean({"layout": "zickzack"})["layout"] == "masonry"


def test_the_polaroid_layout_frames_each_photo_with_its_time(client):
    event = _event(client, layout="polaroid")
    mid = _add(client, event)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert "photo-grid--polaroid" in page
    assert 'class="pola-cap"' in page
    assert "--pola-tilt:" in page


def test_the_tilt_is_the_same_on_every_visit(client):
    event = _event(client, layout="polaroid")
    _add(client, event)
    a = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    b = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    import re
    assert re.findall(r"--pola-tilt:[^;\"]+", a) == re.findall(r"--pola-tilt:[^;\"]+", b)
