"""A share link leads to the photos, so making or reading one needs the right "photos".

``create_share_link`` needed only ``manage`` and ``list_share_links`` only
``read``: an access that by its rights sees "no photos" could make a link to
the whole gallery and open it in any browser, or read the addresses of the
links already out. Since 15.11.2026 both also need ``photos``; revoking a link
gives nobody access and stays with ``manage``.
"""
import json
import secrets

import pytest

from app import db, ecosystem, mcp


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

    def call(scope, name, **arguments):
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": name, "arguments": arguments}},
                           headers={"Authorization": f"Bearer {token(scope)}"})
    yield call
    con = db.get_db()
    con.execute("DELETE FROM oauth_clients WHERE client_id=?", (cid,))
    con.commit()
    con.close()


def _event():
    k = secrets.token_hex(3)
    return db.create_event(name=f"Freigabe {k}", slug=f"fl-{k}", upload_token=f"t-{k}")


@pytest.mark.parametrize("name", ["create_share_link", "list_share_links"])
def test_without_photos_no_share_link_is_made_or_read(tool, name):
    eid = _event()
    r = tool("read manage", name, event_id=eid, days=7)
    assert r.status_code == 403
    assert 'error="insufficient_scope"' in r.headers["WWW-Authenticate"]
    assert not db.get_db().execute("SELECT 1 FROM share_links WHERE event_id=?", (eid,)).fetchone()


def test_with_photos_the_link_is_made_and_listed(tool):
    eid = _event()
    r = tool("read manage photos", "create_share_link", event_id=eid, days=7)
    assert r.status_code == 200 and not r.get_json()["result"].get("isError")
    listed = tool("read photos", "list_share_links", event_id=eid)
    assert listed.status_code == 200
    assert json.loads(listed.get_json()["result"]["content"][0]["text"])[0]["url"].endswith("/l/" + json.loads(
        r.get_json()["result"]["content"][0]["text"])["slug"])


def test_every_tool_that_hands_out_a_way_to_the_pictures_needs_photos():
    for name in ("create_share_link", "list_share_links"):
        spec = mcp.TOOLS[name]
        assert "photos" in (spec["scope"],) + tuple(spec.get("also") or ()), name
    # Revoking closes a door; it opens none.
    assert mcp.TOOLS["revoke_share_link"]["scope"] == "manage"
