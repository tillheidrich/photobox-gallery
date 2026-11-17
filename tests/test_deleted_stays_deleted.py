"""Deleted stays deleted — even when the booth delivers the rest of the run later.

The booth uploads the small web version first and the originals (the full-size
result, the raw shots) afterwards, sometimes hours later. Until 11.11.2026 a
deletion in between only removed what was already there: the next upload of
the same run simply created the rows again. The full-size original was back on
the server, and with ``upload_originals`` the raw shots were visible again.

The rule now: a run (or a single item) that was deleted is never accepted
again. The upload answers 200 with ``gone`` — success on purpose, otherwise the
booth would retry forever — and stores nothing.
"""
import io
import json
import secrets

from PIL import Image


def _jpeg(size=(800, 1000)):
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 120, 160)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _box(client, moderation=False):
    client.get("/healthz")
    from app import db, ecosystem, gallery_settings as gsm
    ecosystem.init_schema()
    slug = "gone-" + secrets.token_hex(3)
    eid = db.create_event(name="Gone", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=1, gallery_json=? WHERE id=?",
                (json.dumps(gsm.clean({"moderation": moderation})), eid))
    con.commit()
    con.close()
    _made = db.create_device(name="Box Gone")
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo", "collage"]}, eid)
    return db.get_event_by_id(eid), {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _upload(client, dev, event, uid, session_uid=None, kind="photo", variant="web", size=(800, 1000)):
    return client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                       content_type="multipart/form-data",
                       data={"uid": uid, "session_uid": session_uid or uid, "kind": kind,
                             "variant": variant, "event_slug": event["slug"],
                             "file": (_jpeg(size), "result.jpg"),
                             "meta": json.dumps({"taken_at": "2026-11-11T20:00:00"})})


def _code(event, uid):
    from app import ecosystem
    return f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"


def _rows(uid):
    """Every row of the run, hidden ones included — the original is hidden."""
    from app import db
    con = db.get_db()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM media WHERE session_uid=? OR uid=? OR uid LIKE ?", (uid, uid, uid + ".%"))]
    con.close()
    return rows


def _files(event, uid):
    from app import media
    root = media.event_storage_dir(event["slug"])
    return [p for p in root.rglob(f"{uid}*") if p.is_file()]


def _late_arrivals(client, dev, event, uid):
    """What the booth sends after the web version: the original and a raw shot."""
    big = _upload(client, dev, event, uid, variant="original", size=(1600, 2000))
    raw = _upload(client, dev, event, f"{uid}.1", session_uid=uid, kind="original", variant="original")
    return big, raw


def _assert_nothing_came_back(big, raw, event, uid):
    for answer in (big, raw):
        assert answer.status_code == 200, answer.data
        assert answer.get_json() == {"ok": True, "gone": True}
    assert _rows(uid) == []
    assert _files(event, uid) == []


def test_what_the_guest_deleted_does_not_come_back_with_the_original(client):
    event, dev = _box(client)
    uid = "gast" + secrets.token_hex(4)
    assert _upload(client, dev, event, uid).status_code == 200
    assert client.post(f"/p/{_code(event, uid)}/delete").status_code == 200
    assert _rows(uid) == []

    big, raw = _late_arrivals(client, dev, event, uid)
    _assert_nothing_came_back(big, raw, event, uid)


def test_the_guest_delete_also_takes_the_hidden_original_already_there(client):
    """The QR page only lists visible items; the hidden original goes too."""
    event, dev = _box(client)
    uid = "hidn" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    _upload(client, dev, event, uid, variant="original", size=(1600, 2000))
    assert len(_rows(uid)) == 2
    client.post(f"/p/{_code(event, uid)}/delete")
    assert _rows(uid) == []
    assert _files(event, uid) == []


def test_retention_takes_hidden_originals_too(client):
    event, dev = _box(client)
    uid = "rtho" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    _upload(client, dev, event, uid, variant="original", size=(1600, 2000))
    from app import retention
    retention.purge_event(event["id"], reason="retention", actor="system")
    assert _rows(uid) == []
    assert _files(event, uid) == []


def test_what_the_admin_deleted_does_not_come_back(client):
    event, dev = _box(client)
    uid = "admn" + secrets.token_hex(4)
    media_id = _upload(client, dev, event, uid).get_json()["id"]
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    r = client.post(f"/admin/media/{media_id}/delete", data={"csrf_token": "x"},
                    headers={"X-CSRF-Token": "x"})
    assert r.status_code == 200
    assert _rows(uid) == []

    big, raw = _late_arrivals(client, dev, event, uid)
    _assert_nothing_came_back(big, raw, event, uid)


def test_what_the_admin_rejected_in_review_does_not_come_back(client):
    event, dev = _box(client, moderation=True)
    uid = "rjct" + secrets.token_hex(4)
    media_id = _upload(client, dev, event, uid).get_json()["id"]
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/events/{event['id']}/review/{media_id}/reject", data={"csrf_token": "x"})
    assert _rows(uid) == []

    big, raw = _late_arrivals(client, dev, event, uid)
    _assert_nothing_came_back(big, raw, event, uid)


def test_what_retention_removed_does_not_come_back(client):
    event, dev = _box(client)
    uid = "rten" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    from app import retention
    retention.purge_event(event["id"], reason="retention", actor="system")
    assert _rows(uid) == []

    big, raw = _late_arrivals(client, dev, event, uid)
    _assert_nothing_came_back(big, raw, event, uid)


def test_what_the_booth_deleted_does_not_come_back(client):
    event, dev = _box(client)
    uid = "slip" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    client.post("/api/v2/booth/sync", json={"deleted": [uid]}, headers={"X-API-Key": dev["api_key"]})
    assert _rows(uid) == []

    big, raw = _late_arrivals(client, dev, event, uid)
    _assert_nothing_came_back(big, raw, event, uid)


def test_every_sub_item_of_a_deleted_run_is_refused(client):
    """Collage parts, GIF and boomerang carry ``<uid>.<suffix>``; the base decides."""
    event, dev = _box(client)
    uid = "subs" + secrets.token_hex(4)
    _upload(client, dev, event, uid, kind="collage")
    client.post(f"/p/{_code(event, uid)}/delete")
    for suffix, kind in ((".f1", "photo"), (".gif", "gif"), (".boom", "photo")):
        # Even without session_uid the base of the uid gives it away.
        r = client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                        content_type="multipart/form-data",
                        data={"uid": uid + suffix, "kind": kind, "variant": "web",
                              "event_slug": event["slug"], "file": (_jpeg(), "part.jpg")})
        assert r.get_json() == {"ok": True, "gone": True}
    assert _rows(uid) == []


def test_a_foreign_booth_cannot_block_a_run_it_does_not_own(client):
    """A deletion slip from another device must not become a tombstone."""
    event, dev = _box(client)
    _, fremd = _box(client)
    uid = "frmd" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    client.post("/api/v2/booth/sync", json={"deleted": [uid]}, headers={"X-API-Key": fremd["api_key"]})
    big = _upload(client, dev, event, uid, variant="original", size=(1600, 2000))
    assert big.get_json().get("gone") is not True
    assert len(_rows(uid)) == 2


def test_an_untouched_run_still_uploads_normally(client):
    event, dev = _box(client)
    uid = "norm" + secrets.token_hex(4)
    first = _upload(client, dev, event, uid).get_json()
    assert first["ok"] and "gone" not in first
    big = _upload(client, dev, event, uid, variant="original", size=(1600, 2000)).get_json()
    assert big["ok"] and "gone" not in big
    assert len(_rows(uid)) == 2


def test_tombstones_go_with_the_event_row(client):
    """Entries live as long as the event exists — no table that only grows."""
    event, dev = _box(client)
    uid = "evnt" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    client.post(f"/p/{_code(event, uid)}/delete")
    from app import db, ecosystem, retention
    assert ecosystem.is_deleted(uid)
    retention.purge_event(event["id"], reason="event", actor="admin", delete_event_row=True)
    assert db.get_event_by_id(event["id"]) is None
    con = db.get_db()
    left = con.execute("SELECT COUNT(*) FROM deleted_uids WHERE event_id=?", (event["id"],)).fetchone()[0]
    con.close()
    assert left == 0
