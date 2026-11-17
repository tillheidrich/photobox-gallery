"""Deciding a guest's access request hands out the key to a gallery: ``photos``.

Final review, finding A2: ``decide_access_request`` approved a request and
mailed the link that opens without the password to an address the requester
had typed — with ``manage`` alone. Together with ``set_event_settings``
(switch ``access_request`` on) an assistant could ask for access itself and
grant it. Since then deciding needs ``manage`` and ``photos``, and switching
the button on or off needs ``photos`` (tests/test_mcp_visibility_needs_photos.py).
"Deny" sends a mail too, and the tool says so.
"""
import json
import secrets

import pytest

from app import db, ecosystem, gallery_settings as gsm


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

    def call(scope, tool_name, **arguments):
        from app import accounts, oauth
        con = db.get_db()
        token = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp",
                             accounts.session_epoch())["access_token"]
        con.commit()
        con.close()
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": tool_name, "arguments": arguments}},
                           headers={"Authorization": f"Bearer {token}"})
    yield call
    con = db.get_db()
    con.execute("DELETE FROM oauth_clients WHERE client_id=?", (cid,))
    con.commit()
    con.close()


def _request(client):
    from app import access
    k = secrets.token_hex(3)
    eid = db.create_event(name=f"Anfrage {k}", slug=f"anf-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean({"access_request": True}))})
    db.set_event_password(eid, "geheim-123")
    with client.application.test_request_context("/"):
        req = access.create(db.get_event_by_id(eid), email="angreifer@example.org", name="Ich")
    return eid, req


@pytest.mark.parametrize("decision", ["grant", "deny"])
def test_without_photos_no_access_request_is_decided(tool, client, monkeypatch, decision):
    from app import access, emailer
    mails = []
    monkeypatch.setattr(emailer, "send_text_mail", lambda **kw: mails.append(kw))
    eid, req = _request(client)
    r = tool("read manage", "decide_access_request", event_id=eid, request_id=req["id"], decision=decision)
    assert r.status_code == 403
    assert "photos" in r.headers["WWW-Authenticate"]
    assert access.get(req["id"], eid)["state"] == access.OPEN
    assert mails == []


def test_with_photos_the_decision_is_made(tool, client, monkeypatch):
    from app import access
    monkeypatch.setattr(access, "grant", lambda event, req, url: True)
    eid, req = _request(client)
    r = tool("read manage photos", "decide_access_request", event_id=eid, request_id=req["id"], decision="grant")
    assert r.status_code == 200
    assert json.loads(r.get_json()["result"]["content"][0]["text"])["decision"] == "grant"


def test_the_tool_says_that_deny_sends_a_mail_too():
    from app.mcp import TOOLS
    text = TOOLS["decide_access_request"]["description"]
    assert "still" not in text
    assert "Absage" in text and "Mail" in text
    assert TOOLS["decide_access_request"]["also"] == ("photos",)


def test_the_two_step_proof_of_the_review_fails_at_both_steps(tool, client):
    """Switch access requests on, then grant one's own request: neither with read manage."""
    k = secrets.token_hex(3)
    eid = db.create_event(name=f"Beweis {k}", slug=f"bew-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1})
    db.set_event_password(eid, "geheim-123")
    r = tool("read manage", "set_event_settings", event_id=eid, changes={"access_request": True})
    assert r.status_code == 403
    assert not gsm.for_event(db.get_event_by_id(eid)).get("access_request")
