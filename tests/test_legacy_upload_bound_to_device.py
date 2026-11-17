"""The old upload path (/api/upload) obeys the same assignment as the v2 API.

Security review of 11.11.2026, finding S4: any valid device key could upload
into any event by naming its slug or id, and the key was also taken from the
query string, where it lands in proxy logs and browser history. The only
client still using this path is the old Windows uploader
(``windows_uploader/photobox_uploader_v2.py``): key in ``X-API-Key``, event
by slug, after ``/api/events/ensure``.
"""
import io
import secrets

import pytest
from PIL import Image

from app import db, ecosystem


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (100, 50, 25)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _event():
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Alt {k}", slug=f"alt-{k}", upload_token=f"t-{k}")
    return db.get_event_by_id(eid)


def _box(event_id=None):
    _made = db.create_device(name="Alte Box")
    did = _made["id"]
    if event_id:
        ecosystem.save_device_profile(did, {}, event_id)
    return {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _upload(client, *, headers=None, query="", **form):
    data = {"file": (_jpeg(), "a.jpg"), **form}
    return client.post(f"/api/upload{query}", headers=headers or {}, data=data,
                       content_type="multipart/form-data")


def test_a_box_cannot_upload_into_a_foreign_event(client):
    mine, foreign = _event(), _event()
    box = _box(mine["id"])
    r = _upload(client, headers={"X-API-Key": box["api_key"]}, event_slug=foreign["slug"])
    assert r.status_code == 403
    assert db.count_media(foreign["id"]) == 0


def test_a_box_uploads_into_its_own_event(client):
    event = _event()
    box = _box(event["id"])
    r = _upload(client, headers={"X-API-Key": box["api_key"]}, event_slug=event["slug"])
    assert r.status_code == 200, r.data
    assert db.count_media(event["id"]) == 1


def test_the_key_is_not_taken_from_the_address(client):
    event = _event()
    box = _box(event["id"])
    r = _upload(client, query=f"?api_key={box['api_key']}", event_slug=event["slug"])
    assert r.status_code == 401


def test_the_key_is_not_taken_from_the_form(client):
    event = _event()
    box = _box(event["id"])
    r = _upload(client, api_key=box["api_key"], event_slug=event["slug"])
    assert r.status_code == 401


def test_the_old_uploader_flow_still_works(client):
    """ensure creates the event for this box; the upload that follows is accepted."""
    box = _box()
    h = {"X-Api-Key": box["api_key"]}
    r = client.post("/api/events/ensure", json={"name": "Sommerfest Alt " + secrets.token_hex(3)}, headers=h)
    assert r.status_code == 201, r.data
    slug = r.get_json()["slug"]
    up = _upload(client, headers={"X-API-Key": box["api_key"]}, event_slug=slug)
    assert up.status_code == 200, up.data


def test_the_event_chosen_in_the_old_device_form_counts_as_assigned(client):
    """The legacy device form offers one assignment: "Zugewiesenes Event"."""
    event = _event()
    box = _box()
    db.update_device(box["id"], {"landing_event_id": event["id"]})
    r = _upload(client, headers={"X-API-Key": box["api_key"]}, event_id=str(event["id"]))
    assert r.status_code == 200, r.data
