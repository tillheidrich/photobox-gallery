"""What is deleted in the gallery disappears at the booth on the next sync.

Until 11.11.2026 deletion travelled one way only, booth -> gallery. A guest who
deleted their run on the QR page still smiled from the kiosk gallery at the
booth for the rest of the evening.

Now the sync response lists the deleted runs of this device's events
(``deleted_sessions``). The booth confirms them in its next call
(``deleted_seen``); only then does the gallery stop sending them. A booth that
never confirms (an old version) just keeps getting the same bounded list.
"""
import io
import json
import secrets

from PIL import Image


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (600, 750), (40, 160, 90)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _box(client, name="Box Reach"):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    slug = "reach-" + secrets.token_hex(3)
    eid = db.create_event(name="Reach", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (eid,)); con.commit(); con.close()
    _made = db.create_device(name=name)
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo"]}, eid)
    return db.get_event_by_id(eid), {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _upload(client, dev, event, uid):
    return client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                       content_type="multipart/form-data",
                       data={"uid": uid, "session_uid": uid, "kind": "photo", "variant": "web",
                             "event_slug": event["slug"], "file": (_jpeg(), "r.jpg"),
                             "meta": json.dumps({})})


def _code(event, uid):
    from app import ecosystem
    return f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"


def _sync(client, dev, **body):
    return client.post("/api/v2/booth/sync", json=body, headers={"X-API-Key": dev["api_key"]}).get_json()


def test_a_guest_deletion_is_listed_until_the_booth_confirms_it(client):
    event, dev = _box(client)
    uid = "reach" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    client.post(f"/p/{_code(event, uid)}/delete")

    first = _sync(client, dev)
    assert uid in first["deleted_sessions"]
    again = _sync(client, dev)                       # not confirmed yet: sent again
    assert uid in again["deleted_sessions"]
    _sync(client, dev, deleted_seen=[uid])
    assert uid not in _sync(client, dev)["deleted_sessions"]


def test_admin_and_retention_deletions_reach_the_booth_too(client):
    event, dev = _box(client)
    a, r = "adm" + secrets.token_hex(4), "ret" + secrets.token_hex(4)
    media_id = _upload(client, dev, event, a).get_json()["id"]
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/media/{media_id}/delete", data={"csrf_token": "x"}, headers={"X-CSRF-Token": "x"})
    _upload(client, dev, event, r)
    from app import retention
    retention.purge_event(event["id"], reason="retention", actor="system")
    listed = _sync(client, dev)["deleted_sessions"]
    assert a in listed and r in listed


def test_only_runs_of_this_devices_events_are_listed(client):
    event, dev = _box(client)
    _, other = _box(client, "Box Other")
    uid = "mine" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    client.post(f"/p/{_code(event, uid)}/delete")
    assert uid not in _sync(client, other)["deleted_sessions"]


def test_a_former_event_of_the_device_still_counts(client):
    event, dev = _box(client)
    uid = "form" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    from app import db, ecosystem
    new = db.create_event(name="Next", slug="next-" + secrets.token_hex(3), upload_token=secrets.token_hex(8))
    ecosystem.save_device_profile(dev["id"], {"modes": ["photo"]}, new)
    client.post(f"/p/{_code(event, uid)}/delete")
    assert uid in _sync(client, dev)["deleted_sessions"]


def test_what_the_booth_deleted_itself_is_not_sent_back(client):
    event, dev = _box(client)
    uid = "self" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    _sync(client, dev, deleted=[uid])
    assert uid not in _sync(client, dev)["deleted_sessions"]


def test_a_single_sub_item_is_not_a_run_deletion(client):
    """The admin deletes only the GIF of a collage: the booth keeps the run."""
    event, dev = _box(client)
    uid = "part" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    gif = client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                      content_type="multipart/form-data",
                      data={"uid": uid + ".gif", "session_uid": uid, "kind": "gif", "variant": "web",
                            "event_slug": event["slug"], "file": (_jpeg(), "a.jpg")}).get_json()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/media/{gif['id']}/delete", data={"csrf_token": "x"}, headers={"X-CSRF-Token": "x"})
    listed = _sync(client, dev)["deleted_sessions"]
    assert uid not in listed and uid + ".gif" not in listed


def test_an_old_booth_that_never_confirms_breaks_nothing(client):
    """It ignores the key; the list stays bounded and the sync stays healthy."""
    event, dev = _box(client)
    for _ in range(3):
        uid = "old" + secrets.token_hex(4)
        client.post(f"/p/{_code(event, uid)}/delete")
    for _ in range(3):
        answer = _sync(client, dev, deleted_seen="not-a-list")
        assert answer["ok"] and len(answer["deleted_sessions"]) <= 200
