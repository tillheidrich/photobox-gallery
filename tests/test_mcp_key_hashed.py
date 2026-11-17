"""Security review G3, finding S-12 (15.11.2026): the static MCP key is stored hashed
and dies with the password.

The static key opens the whole administration. It sat in the settings table
in plain text and was printed on the settings page on every visit; and a
password change — the step an operator takes after a scare — left it valid.
"""
import hashlib


def _ping(client, key):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                       headers={"X-API-Key": key})


def test_only_the_hash_is_stored(client):
    from app import db, mcp
    client.get("/healthz")
    key = mcp.new_key()
    con = db.get_db()
    cells = " ".join(str(v) for row in con.execute("SELECT key, value FROM settings") for v in row)
    con.close()
    assert key not in cells
    assert hashlib.sha256(key.encode()).hexdigest() in cells
    assert _ping(client, key).status_code == 200


def test_an_old_plain_key_is_hashed_and_keeps_working(client):
    from app import db, mcp
    client.get("/healthz")
    db.set_setting("mcp_api_key_hash", "")
    db.set_setting("mcp_api_key", "pbmcp_altes-skript-schluessel")
    db.set_setting(mcp.STATIC_SWITCH, "1")
    assert _ping(client, "pbmcp_altes-skript-schluessel").status_code == 200
    assert (db.get_setting("mcp_api_key") or "") == ""


def test_the_settings_page_shows_the_key_once(client):
    from app import accounts, mcp
    client.get("/healthz")
    mcp.revoke_key()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "t"
    r = client.post("/admin/settings/mcp", data={"csrf_token": "t", "action": "rotate"})
    page = r.get_data(as_text=True)
    import re
    shown = re.findall(r"pbmcp_[A-Za-z0-9_\-]+", page)
    assert r.status_code == 200 and shown and _ping(client, shown[0]).status_code == 200
    assert r.headers.get("Cache-Control", "").startswith("no-store")
    assert shown[0] not in client.get("/admin/settings/mcp").get_data(as_text=True)


def test_a_new_password_ends_the_static_key(client):
    from app import accounts, mcp
    client.get("/healthz")
    key = mcp.new_key()
    assert _ping(client, key).status_code == 200
    accounts.set_password("ein-ganz-neues-passwort-77")
    assert _ping(client, key).status_code == 401
    assert not mcp.static_key_enabled()
