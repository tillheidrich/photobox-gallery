"""Every change through the MCP door lands in the admin change log.

Who (which assistant), which tool, and the arguments — shortened, without
passwords, PINs or file contents. Looking does not fill the log.
"""
import json
import secrets
from types import SimpleNamespace

import pytest


@pytest.fixture()
def door(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()

    def token(scope="read manage", name="Claude"):
        from app import accounts, db, oauth
        cid = "pbc_" + secrets.token_hex(6)
        con = db.get_db()
        con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                    "VALUES (?,?,?,datetime('now'))", (cid, name, json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
        answer = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp", accounts.session_epoch())
        con.commit()
        con.close()
        return answer["access_token"]

    def call(credential, method, params=None, header="Authorization"):
        value = f"Bearer {credential}" if header == "Authorization" else credential
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                           headers={header: value})

    return SimpleNamespace(token=token, call=call)


def _latest():
    from app import protokoll
    return protokoll.liste(limit=1)[0]


def test_a_change_is_logged_with_assistant_tool_and_arguments(door):
    from app import db, protokoll
    name = "Claude-" + secrets.token_hex(3)
    eid = db.create_event(name="Protokoll-Fest", slug="mcp-prot-" + secrets.token_hex(3),
                          upload_token=secrets.token_hex(8))
    before = protokoll.anzahl()
    r = door.call(door.token(name=name), "tools/call",
                  {"name": "set_event_settings", "arguments": {"event_id": eid, "changes": {"silent": True}}})
    assert r.status_code == 200 and not r.get_json()["result"].get("isError")
    assert protokoll.anzahl() == before + 1
    entry = _latest()
    assert name in entry["wer"]
    assert "set_event_settings" in entry["was"]
    aufruf = next(a for a in entry["aenderungen"] if a["gegenstand"] == "Aufruf")["nachher"]
    assert json.loads(aufruf) == {"event_id": eid, "changes": {"silent": True}}


def test_looking_leaves_no_trace(door):
    from app import protokoll
    token = door.token()
    before = protokoll.anzahl()
    door.call(token, "initialize", {"protocolVersion": "2025-06-18"})
    door.call(token, "tools/list")
    door.call(token, "tools/call", {"name": "list_events", "arguments": {}})
    assert protokoll.anzahl() == before


def test_secrets_and_file_contents_stay_out_of_the_log(door):
    import base64
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 300).decode()
    door.call(door.token(), "tools/call",
              {"name": "add_asset", "arguments": {"kind": "background", "filename": "wand.png",
                                                  "content_base64": png}})
    shown = json.dumps(_latest()["aenderungen"], ensure_ascii=False)
    assert png not in shown and png[:60] not in shown
    assert "wand.png" in shown


def test_a_password_argument_is_masked(door):
    from app import mcp, protokoll
    text = mcp.args_for_log({"name": "x", "password": "streng-geheim-123", "nested": {"pin": "918273"},
                             "long": "y" * 500})
    assert "streng-geheim-123" not in text and "918273" not in text
    assert protokoll.MASKE in text and len(text) <= 400


def test_the_static_key_is_named_as_such(door):
    from app import db, mcp
    key = mcp.new_key()
    eid = db.create_event(name="Schlüssel-Fest", slug="mcp-key-" + secrets.token_hex(3),
                          upload_token=secrets.token_hex(8))
    door.call(key, "tools/call", {"name": "set_event_settings",
                                  "arguments": {"event_id": eid, "changes": {"kiosk": True}}}, header="X-API-Key")
    assert "Schlüssel" in _latest()["wer"]


def test_a_refused_call_is_logged_as_refused(door):
    from app import db
    dev = db.create_device(name="Protokoll-Box")
    r = door.call(door.token("read"), "tools/call",
                  {"name": "send_booth_job", "arguments": {"booth_id": dev["id"], "job": "test_print"}})
    assert r.status_code == 403
    entry = _latest()
    assert "send_booth_job" in entry["was"] and "403" in entry["ergebnis"]


def test_the_assistants_page_shows_last_access_and_scope(door, client):
    from app import accounts
    name = "Seen-" + secrets.token_hex(3)
    token = door.token("read", name=name)
    door.call(token, "tools/list")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
    body = client.get("/admin/settings/assistants").get_data(as_text=True)
    row = body[body.index(name):][:600]
    assert "read" in row and "20" in row          # a date such as 2026-11-11 12:00:00
