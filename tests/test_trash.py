"""Trash with a grace period (no. 61).

Account setting ``trash_days``: 0 (default) deletes at once as before; N
keeps a deleted picture N days in a trash outside the public folders, from
where it can be restored or purged. Only the real purge writes the deletion
log. "Fotos löschen, Event behalten" and retention stay immediate.
"""
import io
import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import db, ecosystem, gallery_settings as gsm, media, retention


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
    yield


def _event(client, **gs_values):
    key = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"Trash {key}", slug=f"trash-{key}", upload_token=f"tok-{key}")
        db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs_values))})
        return db.get_event_by_id(eid)


def _photo(client, event):
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (10, 120, 200)).save(buf, "JPEG")
    buf.seek(0)
    with client.application.app_context():
        res = media.save_upload(event["id"], FileStorage(buf, filename="a.jpg"))
        return db.get_media(int(res["id"]))


def _days(client, n, guests=False):
    with client.application.app_context():
        db.set_setting("trash_days", str(n))
        db.set_setting("trash_guest_deletes", "1" if guests else "0")


def _delete(client, mid):
    return client.post(f"/admin/media/{mid}/delete", data={"csrf_token": "t"})


def _log_count(client):
    with client.application.app_context():
        return len(retention.entries(limit=2000))


def test_without_a_trash_a_delete_is_immediate_as_before(client):
    _days(client, 0)
    event = _event(client)
    row = _photo(client, event)
    assert _delete(client, row["id"]).status_code == 200
    from app import trash
    with client.application.app_context():
        assert db.get_media(row["id"]) is None
        assert not (media.data_dir() / row["path_original"]).exists()
        assert trash.entries() == [] or all(e["media_id"] != row["id"] for e in trash.entries())


def test_with_a_trash_the_picture_leaves_the_gallery_but_can_come_back(client):
    _days(client, 14)
    event = _event(client)
    row = _photo(client, event)
    before = _log_count(client)
    _delete(client, row["id"])
    from app import trash
    with client.application.app_context():
        assert db.get_media(row["id"]) is None
        assert row["id"] not in [m["id"] for m in db.list_media(event["id"])]
        # Out of the public folder: the old address no longer serves it.
        assert not (media.data_dir() / row["path_original"]).exists()
        entry = next(e for e in trash.entries() if e["media_id"] == row["id"])
    client.get("/admin/logout")
    assert client.get(f"/data/{row['path_thumb']}").status_code == 404
    # Moving to the trash is not a deletion yet: nothing in the deletion log.
    assert _log_count(client) == before
    with client.application.app_context():
        assert trash.restore(entry["id"]) is True
        back = db.get_media(row["id"])
        assert back and back["path_original"] == row["path_original"]
        assert (media.data_dir() / row["path_original"]).exists()
        assert row["id"] in [m["id"] for m in db.list_media(event["id"])]


def test_purging_from_the_trash_deletes_the_files_and_writes_the_log(client):
    _days(client, 14)
    event = _event(client)
    row = _photo(client, event)
    _delete(client, row["id"])
    before = _log_count(client)
    from app import trash
    with client.application.app_context():
        entry = next(e for e in trash.entries() if e["media_id"] == row["id"])
        trash.purge(entry["id"], actor="admin")
        assert all(e["id"] != entry["id"] for e in trash.entries())
        assert not list((media.data_dir() / "trash").glob(f"{row['id']}/*"))
    assert _log_count(client) == before + 1


def test_the_maintenance_run_purges_what_is_due(client):
    _days(client, 14)
    event = _event(client)
    row = _photo(client, event)
    _delete(client, row["id"])
    from app import maintenance, trash
    with client.application.app_context():
        entry = next(e for e in trash.entries() if e["media_id"] == row["id"])
        con = db.get_db()
        con.execute("UPDATE media_trash SET purge_after=? WHERE id=?",
                    ((datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1))
                     .isoformat(timespec="seconds"), entry["id"]))
        con.commit()
        con.close()
        report = maintenance.run(dry_run=False)
        assert report["trash"]["purged"] >= 1
        assert all(e["id"] != entry["id"] for e in trash.entries())


def test_restoring_a_booth_picture_lifts_its_tombstone(client):
    _days(client, 14)
    event = _event(client)
    row = _photo(client, event)
    with client.application.app_context():
        con = db.get_db()
        con.execute("UPDATE media SET uid='boxuid01', session_uid='boxuid01' WHERE id=?", (row["id"],))
        con.commit()
        con.close()
    _delete(client, row["id"])
    from app import trash
    with client.application.app_context():
        assert ecosystem.is_deleted("boxuid01") if hasattr(ecosystem, "is_deleted") else True
        entry = next(e for e in trash.entries() if e["media_id"] == row["id"])
        trash.restore(entry["id"])
        con = db.get_db()
        left = con.execute("SELECT COUNT(*) FROM deleted_uids WHERE uid='boxuid01'").fetchone()[0]
        con.close()
    assert left == 0


def test_a_restored_picture_in_a_moderated_event_keeps_its_approval(client):
    _days(client, 14)
    event = _event(client)
    row = _photo(client, event)
    with client.application.app_context():
        db.update_event(event["id"], {"gallery_json": json.dumps(gsm.clean({"moderation": True}))})
    _delete(client, row["id"])
    from app import trash
    with client.application.app_context():
        entry = next(e for e in trash.entries() if e["media_id"] == row["id"])
        trash.restore(entry["id"])
        assert int(db.get_media(row["id"])["hidden"] or 0) == 0


def test_delete_photos_keep_event_stays_immediate_and_empties_the_trash(client):
    _days(client, 14)
    event = _event(client)
    a = _photo(client, event)
    _photo(client, event)
    _delete(client, a["id"])
    from app import trash
    with client.application.app_context():
        assert any(e["event_id"] == event["id"] for e in trash.entries())
        retention.purge_event(event["id"], reason="admin", actor="admin")
        assert db.list_media(event["id"]) == []
        assert not any(e["event_id"] == event["id"] for e in trash.entries())


def test_guest_deletes_follow_their_own_switch(client):
    from app import trash
    with client.application.app_context():
        assert trash.for_guests() is False
    _days(client, 14, guests=True)
    with client.application.app_context():
        assert trash.for_guests() is True
    _days(client, 0, guests=True)
    with client.application.app_context():
        assert trash.for_guests() is False


def test_the_trash_page_is_admin_only_and_its_actions_need_the_token(client):
    _days(client, 14)
    event = _event(client)
    row = _photo(client, event)
    _delete(client, row["id"])
    page = client.get("/admin/trash").get_data(as_text=True)
    assert "Papierkorb" in page and event["name"] in page
    from app import trash
    with client.application.app_context():
        entry = next(e for e in trash.entries() if e["media_id"] == row["id"])
    assert client.post(f"/admin/trash/{entry['id']}/restore", data={}).status_code == 400
    assert client.post(f"/admin/trash/{entry['id']}/restore", data={"csrf_token": "t"}).status_code == 302
    with client.application.app_context():
        assert db.get_media(row["id"]) is not None
    client.get("/admin/logout")
    with client.session_transaction() as s:
        s.clear()
    assert client.get("/admin/trash").status_code == 302


def test_the_trash_days_setting_is_saved_from_the_trash_page(client):
    _days(client, 0)
    client.post("/admin/trash/settings", data={"csrf_token": "t", "trash_days": "7"})
    from app import trash
    with client.application.app_context():
        assert trash.days() == 7
    client.post("/admin/trash/settings", data={"csrf_token": "t", "trash_days": "999"})
    with client.application.app_context():
        assert trash.days() == 7


def test_disk_usage_counts_the_trash(client):
    _days(client, 14)
    event = _event(client)
    row = _photo(client, event)
    _delete(client, row["id"])
    from app import trash
    with client.application.app_context():
        assert trash.usage()["bytes"] > 0 and trash.usage()["count"] >= 1
