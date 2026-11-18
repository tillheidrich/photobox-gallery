"""The boomerang lengths a guest may choose: gallery side (form, rules, MCP).

``boomerang.seconds_choices`` (1-15 s each, at most four) and
``boomerang.seconds_guest_choice``; see booth/tests/test_boomerang_seconds_choice.py
for what the booth does with them.
"""
import json
import secrets

import pytest
from werkzeug.security import generate_password_hash


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _setup(client, name="Box Dauer"):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    _admin(client)
    dev_id = db.create_device(name=name)["id"]
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    return dev_id, token


def test_the_defaults_keep_todays_behaviour():
    from app import ecosystem
    boom = ecosystem.DEFAULT_PROFILE["boomerang"]
    assert boom["seconds_choices"] == [] and boom["seconds_guest_choice"] is False
    assert boom["min_total_seconds"] == 15 and boom["max_total_seconds"] == 30


@pytest.mark.parametrize("choices", [[0.5, 2], [2, 16], [1, 2, 3, 4, 5], ["x", 2], [True, 2], [[2]]])
def test_the_rules_refuse_bad_lengths(choices):
    from app import ecosystem, profile_rules
    _clean, problems = profile_rules.check({"boomerang": {"seconds_choices": choices}}, ecosystem.DEFAULT_PROFILE)
    assert problems and problems[0][0] == "boomerang.seconds_choices"


def test_the_rules_take_good_lengths_as_numbers():
    from app import ecosystem, profile_rules
    clean, problems = profile_rules.check({"boomerang": {"seconds_choices": [2, "5", 10.0],
                                                         "seconds_guest_choice": True}},
                                          ecosystem.DEFAULT_PROFILE)
    assert problems == []
    assert clean["boomerang"]["seconds_choices"] == [2, 5, 10]
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in clean["boomerang"]["seconds_choices"])


def test_the_form_shows_and_saves_the_lengths(client):
    from app import db, ecosystem
    dev_id, token = _setup(client)
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'name="boomerang_seconds_choices"' in page and 'name="boomerang_seconds_guest_choice"' in page
    data = {"csrf_token": token, "mode_photo": "on", "mode_boomerang": "on", "format_9:16": "on",
            "default_format": "9:16", "boomerang_seconds": "2",
            "boomerang_seconds_choices": "2, 5; 10 , 99, x, 5", "boomerang_seconds_guest_choice": "on"}
    r = client.post(f"/admin/devices/{dev_id}/booth", data=data, follow_redirects=True)
    assert r.status_code == 200
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["boomerang"]["seconds_choices"] == [2, 5, 10]          # out of range, words and doubles drop
    assert p["boomerang"]["seconds_guest_choice"] is True
    assert p["boomerang"]["min_total_seconds"] == 15 and p["boomerang"]["max_total_seconds"] == 30
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'value="2, 5, 10"' in page


@pytest.fixture()
def door(client):
    client.environ_base["REMOTE_ADDR"] = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"
    client.get("/healthz")
    from app import accounts, db, ecosystem, oauth
    ecosystem.init_schema()
    db.set_setting("admin_password_hash", generate_password_hash("Richtig-Langes-Passwort-42"))
    cid = "pbc_" + secrets.token_hex(6)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Test", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    token = oauth._issue(con, cid, secrets.token_hex(6), "read manage", "http://localhost/mcp",
                         accounts.session_epoch())["access_token"]
    con.commit()
    con.close()
    device = db.create_device(name="Box MCP Dauer")

    def tool(**changes):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "update_booth_profile",
                                                 "arguments": {"booth_id": device["id"], "changes": changes}}},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        return r.get_json()["result"]

    yield tool, lambda: ecosystem.device_profile(db.get_device_by_id(device["id"]))
    con = db.get_db()
    con.execute("DELETE FROM oauth_clients WHERE client_id=?", (cid,))
    con.commit()
    con.close()


def test_mcp_takes_valid_lengths_and_refuses_bad_ones(door):
    tool, stored = door
    ok = tool(boomerang={"seconds_choices": [2, 5, 10], "seconds_guest_choice": True})
    assert not ok.get("isError"), ok
    assert stored()["boomerang"]["seconds_choices"] == [2, 5, 10]
    bad = tool(boomerang={"seconds_choices": [2, 20]})
    assert bad.get("isError") and "seconds_choices" in bad["content"][0]["text"]
    bad = tool(boomerang={"seconds_choices": [1, 2, 3, 4, 5]})
    assert bad.get("isError")
    assert stored()["boomerang"]["seconds_choices"] == [2, 5, 10]
