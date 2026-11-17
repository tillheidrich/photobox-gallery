"""Scopes of the MCP door: ``read`` looks, ``manage`` changes.

A token shows only the tools it may use, and a call is checked again — a
client that remembers a tool from another token gets 403, not a result.
The static key for scripts is one more way in with the full scope; it can be
switched off without being deleted, and a new installation starts without it.
"""
import json
import secrets
from types import SimpleNamespace

import pytest

READ_TOOLS = {"list_events", "list_booths", "booth_stats", "get_booth_profile", "booth_diagnostics",
              "list_assets", "event_stats", "get_event_settings", "list_templates", "list_software"}
MANAGE_TOOLS = {"create_event", "update_booth_profile", "send_booth_job", "add_asset", "set_event_settings"}


@pytest.fixture()
def door(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()

    def token(scope):
        from app import accounts, db, oauth
        cid = "pbc_" + secrets.token_hex(6)
        con = db.get_db()
        con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                    "VALUES (?,?,?,datetime('now'))", (cid, "Testassistent", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
        answer = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp", accounts.session_epoch())
        con.commit()
        con.close()
        return answer["access_token"]

    def call(credential, method, params=None, header="Authorization"):
        value = f"Bearer {credential}" if header == "Authorization" else credential
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                           headers={header: value})

    return SimpleNamespace(token=token, call=call)


def _names(r):
    return {t["name"] for t in r.get_json()["result"]["tools"]}


def test_every_tool_says_what_it_needs():
    from app import mcp
    from app import oauth
    for name, spec in mcp.TOOLS.items():
        assert spec["scope"] in oauth.SCOPES, name
    assert READ_TOOLS <= {n for n, s in mcp.TOOLS.items() if s["scope"] == "read"}
    assert MANAGE_TOOLS <= {n for n, s in mcp.TOOLS.items() if s["scope"] == "manage"}


def test_a_read_token_sees_only_what_it_may_use(door):
    names = _names(door.call(door.token("read"), "tools/list"))
    assert READ_TOOLS <= names
    assert not names & MANAGE_TOOLS


def test_a_manage_token_sees_every_read_and_manage_tool_and_no_extra_one(door):
    from app import mcp
    assert _names(door.call(door.token("read manage"), "tools/list")) == {
        n for n, s in mcp.TOOLS.items() if s["scope"] in ("read", "manage") and not s.get("also")}


def test_a_call_beyond_the_scope_is_refused_with_a_step_up_hint(door):
    from app import db
    dev = db.create_device(name="Scope-Box")
    r = door.call(door.token("read"), "tools/call",
                  {"name": "send_booth_job", "arguments": {"booth_id": dev["id"], "job": "test_print"}})
    assert r.status_code == 403
    header = r.headers["WWW-Authenticate"]
    assert 'error="insufficient_scope"' in header and 'scope="read manage"' in header
    assert "resource_metadata=" in header
    from app import ecosystem
    assert not ecosystem.recent_jobs(dev["id"], 5)


def test_read_tools_are_marked_as_read_only(door):
    from app import mcp
    tools = door.call(door.token("read manage"), "tools/list").get_json()["result"]["tools"]
    for t in tools:
        assert t["annotations"]["readOnlyHint"] is (mcp.TOOLS[t["name"]]["scope"] == "read"), t["name"]


def test_the_static_key_has_read_and_manage_unless_given_more(door):
    from app import mcp
    key = mcp.new_key()
    assert _names(door.call(key, "tools/list", header="X-API-Key")) == {
        n for n, s in mcp.TOOLS.items() if s["scope"] in ("read", "manage") and not s.get("also")}


def test_the_static_key_can_be_switched_off_without_losing_it(door, client):
    from app import db, mcp
    key = mcp.new_key()
    db.set_setting(mcp.STATIC_SWITCH, "0")
    assert door.call(key, "ping", header="X-API-Key").status_code == 401
    assert mcp.current_key() == mcp._key_hash(key)        # only the fingerprint is stored (S-12)
    db.set_setting(mcp.STATIC_SWITCH, "1")
    assert door.call(key, "ping", header="X-API-Key").status_code == 200


def test_a_new_installation_has_no_static_door():
    from app import db, mcp
    db.delete_setting("mcp_api_key")
    db.delete_setting(mcp.KEY_HASH)
    db.delete_setting(mcp.STATIC_SWITCH)
    assert not mcp.static_key_enabled()


def test_an_old_installation_keeps_its_key_working():
    from app import db, mcp
    db.delete_setting(mcp.KEY_HASH)
    db.set_setting("mcp_api_key", "pbmcp_" + secrets.token_urlsafe(8))
    db.delete_setting(mcp.STATIC_SWITCH)
    assert mcp.static_key_enabled()


def test_the_admin_switches_the_static_key(door, client, formular):
    from app import accounts, mcp
    key = mcp.new_key()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
    r = formular("/admin/settings/mcp", {"action": "static_off"})
    assert r.status_code == 302
    assert door.call(key, "ping", header="X-API-Key").status_code == 401
    r = formular("/admin/settings/mcp", {"action": "static_on"})
    assert door.call(key, "ping", header="X-API-Key").status_code == 200


# ── Secrets in the booth profile stay out of the chat ────────────────

def test_profile_secrets_are_masked(door):
    from app import db, ecosystem
    dev = db.create_device(name="Geheim-Box")
    ecosystem.save_device_profile(dev["id"], {"mail": {"password": "smtp-geheim"},
                                              "webhooks": {"secret": "hook-geheim"},
                                              "http_trigger": {"token": "trigger-geheim"},
                                              "access": {"pin": "918273"}}, None)
    r = door.call(door.token("read"), "tools/call", {"name": "get_booth_profile", "arguments": {"booth_id": dev["id"]}})
    text = r.get_json()["result"]["content"][0]["text"]
    for secret in ("smtp-geheim", "hook-geheim", "trigger-geheim", "918273"):
        assert secret not in text


@pytest.mark.parametrize("changes", [{"mail": {"bcc": "copy@evil.example"}},
                                     {"webhooks": {"enabled": True, "url": "https://evil.example/hook"}},
                                     {"http_trigger": {"enabled": True}},
                                     {"access": {"pin": "135792"}}])
def test_sections_that_lead_to_guests_or_secrets_cannot_be_changed(door, changes):
    from app import db, ecosystem
    dev = db.create_device(name="Sperr-Box")
    r = door.call(door.token("read manage"), "tools/call",
                  {"name": "update_booth_profile", "arguments": {"booth_id": dev["id"], "changes": changes}})
    result = r.get_json()["result"]
    assert result.get("isError") is True
    stored = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    section = next(iter(changes))
    for key, value in changes[section].items():
        assert stored[section].get(key) != value
