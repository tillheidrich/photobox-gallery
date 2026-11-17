"""Trash (Paket D) and the file-name rule ``event_datetime`` (event-wahl).

Shots of the same second are numbered ``_2``, ``_3`` … by capture time and
then by the oldest id. A picture in the trash has left ``media``, so while it
lies there it is not counted: the names of its same-second siblings move up
(documented in business_logic/index.md). A restore puts back the same id and
the same capture time, so every name is what it was before the deletion, in
a single download and in a ZIP alike.
"""
import io
import json
import secrets

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import dateinamen, db, ecosystem, gallery_settings as gsm, media, trash


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("trash_days", "7")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
    yield
    with client.application.app_context():
        db.set_setting("trash_days", "0")


def _event_with_three_shots_in_one_second(client):
    key = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"Namen {key}", slug=f"namen-{key}", upload_token=f"tok-{key}")
        db.update_event(eid, {"published": 1,
                              "gallery_json": json.dumps(gsm.clean({"file_naming": "event_datetime"}))})
        ids = []
        for colour in ((200, 10, 10), (10, 200, 10), (10, 10, 200)):
            buf = io.BytesIO()
            Image.new("RGB", (60, 40), colour).save(buf, "JPEG")
            buf.seek(0)
            ids.append(int(media.save_upload(eid, FileStorage(buf, filename="a.jpg"))["id"]))
        con = db.get_db()
        con.execute(f"UPDATE media SET taken_at='2026-07-20 21:14:03' WHERE id IN ({','.join('?' * 3)})", ids)
        con.commit()
        con.close()
        return db.get_event_by_id(eid), ids


def test_a_restored_picture_gets_its_name_back_and_its_siblings_theirs(client):
    event, (a, b, c) = _event_with_three_shots_in_one_second(client)
    with client.application.app_context():
        before = dateinamen.event_datetime_namen(event)
        assert before[a].endswith("_21-14-03") and before[b].endswith("_21-14-03_2") \
            and before[c].endswith("_21-14-03_3")

    assert client.post(f"/admin/media/{a}/delete", data={"csrf_token": "t"}).status_code in (200, 302)
    with client.application.app_context():
        assert db.get_media(a) is None                       # in the trash, not in media
        during = dateinamen.event_datetime_namen(event)
        # Not counted while in the trash: the siblings move up (documented).
        assert a not in during and during[b] == before[a] and during[c] == before[b]
        entry = [e for e in trash.entries(event["id"]) if int(e.get("media_id") or 0) == a]
        assert entry, trash.entries(event["id"])
        assert trash.restore(int(entry[0]["id"]))
        assert dateinamen.event_datetime_namen(event) == before
