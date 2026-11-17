"""Who may see or add pictures is a ``photos`` decision, not a ``manage`` one.

Final review, finding A1: with only ``read manage`` an assistant could give a
gallery a password of its own choice and publish it (``configure_event``,
``setup_event``), or move its visibility dates, and so open the guests'
pictures to anyone — although its rights say "no photos". Since then every
change of who can see or add pictures needs ``photos`` as well: publishing,
the password, visible-from and expiry, guest upload, access requests, the
password in the QR link, and switching off silent mode or moderation
(switching them on hides pictures and stays with ``manage``). A new, empty,
unpublished event stays ``manage``. Giving a client more events needs
``photos`` too (the client's code opens them).
"""
import json
import secrets

import pytest

from app import db, ecosystem


@pytest.fixture()
def tool(client):
    client.get("/healthz")
    ecosystem.init_schema()
    cid = "pbc_" + secrets.token_hex(6)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    con.commit()
    con.close()

    def token(scope):
        from app import accounts, oauth
        con = db.get_db()
        answer = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp", accounts.session_epoch())
        con.commit()
        con.close()
        return answer["access_token"]

    def call(scope, tool_name, **arguments):
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": tool_name, "arguments": arguments}},
                           headers={"Authorization": f"Bearer {token(scope)}"})
    yield call
    con = db.get_db()
    con.execute("DELETE FROM oauth_clients WHERE client_id=?", (cid,))
    con.commit()
    con.close()


MANAGE = "read manage"
PHOTOS = "read manage photos"


def _data(r):
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    result = r.get_json()["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def _closed_event(**gallery):
    """A draft gallery with a password: nobody outside sees it."""
    k = secrets.token_hex(3)
    eid = db.create_event(name=f"Zu {k}", slug=f"zu-{k}", upload_token=f"t-{k}", password="Geheim-123")
    fields = {"published": 0, "status": "draft"}
    if gallery:
        from app import gallery_settings as gsm
        gs = gsm.for_event(db.get_event_by_id(eid))
        gs.update(gallery)
        fields["gallery_json"] = json.dumps(gsm.clean(gs))
    db.update_event(eid, fields)
    return eid


def _snapshot(eid):
    ev = db.get_event_by_id(eid)
    return (ev.get("published"), ev.get("password_hash"), ev.get("scheduled_live_at"), ev.get("gallery_json"))


@pytest.mark.parametrize("arguments", [
    {"published": True},
    {"password": "Meins-12345"},
    {"password": ""},
    {"visible_from": "2026-01-01T10:00"},
    {"expires_at": "2099-12-31T23:00"},
    {"settings": {"guest_upload": True}},
    {"settings": {"access_request": True}},
    {"settings": {"link_password": True}},
])
def test_configure_event_needs_photos_to_change_who_sees_the_pictures(tool, arguments):
    eid = _closed_event()
    before = _snapshot(eid)
    r = tool(MANAGE, "configure_event", event_id=eid, **arguments)
    assert r.status_code == 403
    assert 'error="insufficient_scope"' in r.headers["WWW-Authenticate"]
    assert "photos" in r.headers["WWW-Authenticate"]
    assert _snapshot(eid) == before
    _data(tool(PHOTOS, "configure_event", event_id=eid, **arguments))


def test_the_reviewers_proof_publish_with_an_own_password_is_refused(tool, client):
    eid = _closed_event()
    slug = db.get_event_by_id(eid)["slug"]
    r = tool(MANAGE, "configure_event", event_id=eid, published=True, password="Meins-12345")
    assert r.status_code == 403
    assert client.get(f"/gallery/{slug}").status_code == 404
    ev = db.get_event_by_id(eid)
    assert not ev["published"]
    from werkzeug.security import check_password_hash
    assert not check_password_hash(ev["password_hash"], "Meins-12345")


def test_configure_event_without_visibility_changes_stays_manage(tool):
    eid = _closed_event()
    out = _data(tool(MANAGE, "configure_event", event_id=eid, name="Neuer Name", mode="proofing",
                     settings={"watermark": True}))
    assert out["name"] == "Neuer Name"
    # Repeating the current values is no change.
    _data(tool(MANAGE, "configure_event", event_id=eid, published=False, settings={"guest_upload": False}))


@pytest.mark.parametrize("changes", [{"guest_upload": True}, {"access_request": True},
                                     {"link_password": True}])
def test_set_event_settings_needs_photos_for_the_doors(tool, changes):
    eid = _closed_event()
    before = _snapshot(eid)
    assert tool(MANAGE, "set_event_settings", event_id=eid, changes=changes).status_code == 403
    assert _snapshot(eid) == before
    _data(tool(PHOTOS, "set_event_settings", event_id=eid, changes=changes))
    # Off again is a change of who may add or ask, too.
    off = {k: False for k in changes}
    assert tool(MANAGE, "set_event_settings", event_id=eid, changes=off).status_code == 403


@pytest.mark.parametrize("switch", ["silent", "moderation"])
def test_hiding_stays_manage_showing_again_needs_photos(tool, switch):
    eid = _closed_event()
    _data(tool(MANAGE, "set_event_settings", event_id=eid, changes={switch: True}))
    assert tool(MANAGE, "set_event_settings", event_id=eid, changes={switch: False}).status_code == 403
    _data(tool(PHOTOS, "set_event_settings", event_id=eid, changes={switch: False}))


@pytest.mark.parametrize("arguments", [
    {"published": True},
    {"password": "Meins-12345"},
    {"visible_from": "2026-01-01T10:00"},
    {"settings": {"guest_upload": True}},
    {"settings": {"access_request": True}},
])
def test_setup_event_needs_photos_for_an_open_gallery(tool, arguments):
    name = "Setup " + secrets.token_hex(3)
    r = tool(MANAGE, "setup_event", name=name, **arguments)
    assert r.status_code == 403
    assert not [e for e in db.list_events() if e["name"] == name]
    _data(tool(PHOTOS, "setup_event", name=name, **arguments))


def test_a_new_empty_event_stays_manage_and_is_not_published(tool, client):
    out = _data(tool(MANAGE, "setup_event", name="Leer " + secrets.token_hex(3)))
    assert out["published"] is False
    assert not db.get_event_by_id(out["id"])["published"]
    assert client.get(f"/gallery/{out['slug']}").status_code == 404

    made = _data(tool(MANAGE, "create_event", name="Leer " + secrets.token_hex(3)))
    assert not db.get_event_by_id(made["id"])["published"]
    assert client.get(f"/gallery/{made['slug']}").status_code == 404


def test_giving_a_client_events_needs_photos(tool):
    eid = _closed_event()
    assert tool(MANAGE, "create_client", name="K " + secrets.token_hex(3), event_ids=[eid]).status_code == 403
    client_id = _data(tool(MANAGE, "create_client", name="K " + secrets.token_hex(3)))["id"]
    assert tool(MANAGE, "assign_client_events", client_id=client_id, add=[eid]).status_code == 403
    _data(tool(PHOTOS, "assign_client_events", client_id=client_id, add=[eid]))
    # Taking an event away opens nothing.
    _data(tool(MANAGE, "assign_client_events", client_id=client_id, remove=[eid]))
