"""A photographer's client galleries are never eaten by the retention run.

"30 days after the event date" is right for a photo booth event and data
loss for a client gallery that stays for years and arrives from a move with
old dates. Rules (page 4, section 16): galleries have a kind, ``booth`` or
``client``; client galleries keep their pictures unless deletion is switched
on at that gallery; and the run itself refuses any gallery that is a
client's, assigned to a client or carries an old id, without that switch.
"""
import json
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from app import clients, db, folders, gallery_settings as gsm, legacy, retention

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    yield


def _event(kind=None, date="2020-01-01", **extra):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Alt {k}", slug=f"ret-{k}", upload_token=f"t-{k}", retention_days=30)
    db.update_event(eid, {"published": 1, "date": date, "gallery_json": json.dumps(gsm.clean({}))})
    if kind:
        retention.set_kind(eid, kind)
    for key, value in extra.items():
        retention.set_auto_delete(eid, value) if key == "auto_delete" else None
    con = db.get_db()
    con.execute("INSERT INTO media(event_id, kind, filename, path_original, size_bytes, sha256, share_token)"
                " VALUES (?,?,?,?,?,?,?)", (eid, "photo", "a.jpg", f"storage/ret-{k}/orig/a.jpg", 1, k,
                                             secrets.token_hex(8)))
    con.commit()
    con.close()
    return db.get_event_by_id(eid)


def _swept(event):
    return event["slug"] in retention.sweep()["events"]


def test_a_booth_event_is_deleted_as_before(client):
    ev = _event()
    assert retention.kind_of(ev) == "booth"
    assert _swept(ev)


def test_a_client_gallery_stays(client):
    ev = _event(kind="client")
    assert retention.deadline(ev) is None
    assert not _swept(ev) and db.count_media(ev["id"]) == 1


def test_a_client_gallery_goes_only_when_switched_on_there(client):
    ev = _event(kind="client", auto_delete=True)
    assert _swept(ev)


def test_folders_take_the_kind_of_their_parent(client):
    parent = _event(kind="client")
    child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    assert retention.kind_of(child) == "client"


@pytest.mark.parametrize("mark", ["assigned", "foreign_id", "parent_assigned"])
def test_the_run_refuses_marked_galleries_even_as_booth_kind(client, mark):
    ev = _event()
    target = ev
    if mark == "assigned":
        clients.set_events(clients.create("Paar"), [ev["id"]])
    elif mark == "foreign_id":
        legacy.set_event_id(ev["id"], secrets.token_hex(5))
    else:
        child_id = folders.create_child(ev["id"], "Party")
        db.update_event(child_id, date="2020-01-01")
        con = db.get_db()
        con.execute("INSERT INTO media(event_id, kind, filename, path_original, size_bytes, sha256, share_token)"
                    " VALUES (?,?,?,?,?,?,?)", (child_id, "photo", "b.jpg", "x/b.jpg", 1, "b", secrets.token_hex(8)))
        con.commit(); con.close()
        retention.set_kind(ev["id"], "booth")
        clients.set_events(clients.create("Firma"), [ev["id"]])
        target = db.get_event_by_id(child_id)
    target = db.get_event_by_id(target["id"])
    assert retention.protected(target)
    assert not _swept(target)
    # the hard stop holds even when someone hands the run a list
    assert retention.purge_allowed(target) is False


def test_explicitly_switched_on_overrides_the_mark(client):
    ev = _event(auto_delete=True)
    legacy.set_event_id(ev["id"], secrets.token_hex(5))
    assert _swept(ev)


def test_the_privacy_page_names_no_deadline_for_client_galleries(client):
    from app import legal
    ev = _event(kind="client")
    text = legal._retention_text(ev)
    assert "bleiben gespeichert" in text and "ab dem" not in text


def test_the_admin_chooses_the_kind_and_sees_the_fate(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
        s["admin_epoch"] = accounts.session_epoch()
    page = client.get("/admin/events/new").get_data(as_text=True)
    assert 'name="gallery_kind"' in page
    client.post("/admin/events/new", data={"csrf_token": "test-token", "name": "Kunde Neu",
                                           "slug": "kunde-neu-" + secrets.token_hex(2), "gallery_kind": "client"})
    ev = [e for e in db.list_events() if e["name"] == "Kunde Neu"][-1]
    assert retention.kind_of(ev) == "client"
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert 'id="aufbewahrung"' in page and "bleibt" in page
    booth = _event(date="2099-01-01")
    page = client.get(f"/admin/events/{booth['id']}/edit").get_data(as_text=True)
    assert "wird am" in page


def test_import_ids_and_mcp_make_client_galleries(client, tmp_path):
    ev = _event()
    path = tmp_path / "ids.csv"
    path.write_text(f"kennung,slug\nABCD{secrets.token_hex(3)},{ev['slug']}\n", encoding="utf-8")
    legacy.import_csv(path)
    assert retention.kind_of(db.get_event_by_id(ev["id"])) == "client"
    from app import mcp
    with client.application.test_request_context():
        out = mcp.TOOLS["setup_event"]["fn"](name="Kunde MCP")
    assert retention.kind_of(db.get_event_by_id(out["id"])) == "client"
