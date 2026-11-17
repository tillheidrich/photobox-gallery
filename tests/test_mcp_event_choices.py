"""The new event settings through the MCP door, with the rights event settings already need.

Layout choice, file-name rule and "no automatic deletion" are set with
``configure_event`` / ``setup_event`` (manage) and read with
``get_event_settings`` (read), the same tools and rights as the other event
switches. Unknown layouts and rules are refused, not stored.
"""
import json
import secrets

import pytest

from app import db, ecosystem, retention


@pytest.fixture()
def tool(client):
    client.get("/healthz")
    ecosystem.init_schema()

    # One registered assistant for the whole test: the gallery keeps at most
    # 100 (oauth.MAX_CLIENTS) and the suite shares one database.
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

    def call(_tool, scope="read manage", **arguments):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": _tool, "arguments": arguments}},
                        headers={"Authorization": f"Bearer {token(scope)}"})
        if r.status_code != 200:
            return r.status_code, None
        result = r.get_json()["result"]
        text = result["content"][0]["text"]
        return ("error" if result.get("isError") else 200), (text if result.get("isError") else json.loads(text))
    yield call
    con = db.get_db()
    con.execute("DELETE FROM oauth_clients WHERE client_id=?", (cid,))
    con.commit()
    con.close()


def test_setup_and_configure_carry_the_three_new_settings(tool):
    st, ev = tool("setup_event", name="MCP Wahl " + secrets.token_hex(2), kind="booth",
                  file_naming="event_datetime", auto_delete=False,
                  layouts={"builtins": ["schlicht-1-weiss", "duo"], "templates": []})
    assert st == 200, ev
    assert ev["file_naming"] == "event_datetime" and ev["auto_delete"] is False
    assert ev["layouts"] == {"exact": True, "templates": [], "builtins": ["schlicht-1-weiss", "duo"]}
    event = db.get_event_by_id(ev["id"])
    assert not retention.auto_delete(event)
    over = ecosystem.event_profile_overrides(event)
    assert over["collage_exact"] is True and over["collage_builtins"] == ["schlicht-1-weiss", "duo"]

    st, ev = tool("configure_event", event_id=ev["id"], auto_delete=True, retention_days=14,
                  file_naming="original", layouts={"builtins": [], "templates": []})
    assert st == 200, ev
    assert ev["auto_delete"] is True and ev["retention_days"] == 14 and ev["file_naming"] == "original"
    assert ev["layouts"] == {"exact": False, "templates": [], "builtins": []}
    assert "collage_exact" not in ecosystem.event_profile_overrides(db.get_event_by_id(ev["id"]))


def test_get_event_settings_shows_them_with_read(tool):
    st, ev = tool("setup_event", name="MCP Lesen " + secrets.token_hex(2), file_naming="event_datetime")
    st, got = tool("get_event_settings", scope="read", event_id=ev["id"])
    assert st == 200
    assert got["file_naming"] == "event_datetime" and "auto_delete" in got and "layouts" in got


@pytest.mark.parametrize("bad", [{"file_naming": "kaputt"}, {"layouts": {"builtins": ["gibtsnicht"]}},
                                 {"layouts": {"templates": ["../x.json"]}}, {"auto_delete": "ja"},
                                 {"layouts": ["duo"]}])
def test_unknown_values_are_refused(tool, bad):
    st, ev = tool("setup_event", name="MCP Bad " + secrets.token_hex(2))
    st, msg = tool("configure_event", event_id=ev["id"], **bad)
    assert st == "error", msg


def test_read_alone_cannot_change_them(tool):
    st, ev = tool("setup_event", name="MCP Nur " + secrets.token_hex(2))
    st, _ = tool("configure_event", scope="read", event_id=ev["id"], file_naming="event_datetime")
    assert st == 403
