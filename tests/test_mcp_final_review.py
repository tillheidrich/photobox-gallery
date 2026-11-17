"""Smaller MCP findings of the final review (15.11.2026), one test group each.

* A3 — pointing a booth at an event decides where its guests' pictures go:
  ``assign_booth_event`` needs ``secure`` besides ``manage``.
* A6 — the Wi-Fi password the booth shows guests as a QR code is a secret:
  masked in every answer, set only through the ``secure`` tool.
* A7 — a JSON-RPC batch answers each message on its own: a call lacking a
  right gets its own error, the calls that ran keep their results.
* A8 — reading share links (``read`` + ``photos``) is written to the change log.
* A13 — the booth PIN rule over MCP is the booth's: at least four digits (six until 16.11.2026).
* A14 — jobs that only work with a payload are not offered by ``send_booth_job``.
"""
import json
import secrets
from types import SimpleNamespace

import pytest

from app import db, ecosystem


@pytest.fixture()
def door(client):
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

    def post(scope, payload):
        return client.post("/mcp", json=payload, headers={"Authorization": f"Bearer {token(scope)}"})

    def call(scope, tool_name, **arguments):
        return post(scope, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": tool_name, "arguments": arguments}})

    def data(r):
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        result = r.get_json()["result"]
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])

    yield SimpleNamespace(call=call, post=post, data=data)
    con = db.get_db()
    con.execute("DELETE FROM oauth_clients WHERE client_id=?", (cid,))
    con.commit()
    con.close()


def _event():
    k = secrets.token_hex(3)
    return db.create_event(name=f"Review {k}", slug=f"rv-{k}", upload_token=f"t-{k}")


# ── A3 ────────────────────────────────────────────────────────────────

def test_assigning_a_booths_event_needs_secure(door):
    dev = db.create_device(name="Zuweisungs-Box")
    eid = _event()
    r = door.call("read manage", "assign_booth_event", booth_id=dev["id"], event_id=eid)
    assert r.status_code == 403 and "secure" in r.headers["WWW-Authenticate"]
    assert db.get_device_by_id(dev["id"]).get("event_id") != eid
    door.data(door.call("read manage secure", "assign_booth_event", booth_id=dev["id"], event_id=eid))
    assert db.get_device_by_id(dev["id"])["event_id"] == eid


# ── A6 ────────────────────────────────────────────────────────────────

def test_the_wifi_password_is_masked_and_set_only_with_secure(door):
    dev = db.create_device(name="WLAN-Box")
    wifi = "Gaeste-WLAN-" + secrets.token_hex(4)
    profile = ecosystem.device_profile(dev)
    profile["wifi_share"] = {**profile["wifi_share"], "enabled": True, "ssid": "Fest", "password": wifi}
    ecosystem.save_device_profile(dev["id"], profile, None)

    r = door.call("read", "get_booth_profile", booth_id=dev["id"])
    assert wifi not in r.get_data(as_text=True)
    assert door.data(r)["wifi_share"]["password"] == "••••"

    refused = door.call("read manage", "update_booth_profile", booth_id=dev["id"],
                        changes={"wifi_share": {"password": "Fremd-12345"}})
    assert refused.get_json()["result"].get("isError")
    stored = ecosystem.device_profile(db.get_device_by_id(dev["id"]))["wifi_share"]
    assert stored["password"] == wifi

    out = door.data(door.call("read secure", "update_booth_profile_secure", booth_id=dev["id"],
                              changes={"wifi_share": {"password": "••••", "ssid": "Fest2"}}, **{"return": "full"}))
    assert out["profile"]["wifi_share"]["password"] == "••••"
    stored = ecosystem.device_profile(db.get_device_by_id(dev["id"]))["wifi_share"]
    assert stored["password"] == wifi and stored["ssid"] == "Fest2"


# ── A7 ────────────────────────────────────────────────────────────────

def test_a_batch_answers_each_call_and_keeps_the_ones_that_ran(door):
    eid = _event()
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "set_event_settings", "arguments": {"event_id": eid, "changes": {"watermark": True}}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "list_media", "arguments": {"event_id": eid}}},
    ]
    r = door.post("read manage", batch)
    assert r.status_code == 200
    answers = {a["id"]: a for a in r.get_json()}
    assert not answers[1]["result"].get("isError")
    from app import gallery_settings as gsm
    assert gsm.for_event(db.get_event_by_id(eid))["watermark"] is True
    assert answers[2]["error"]["code"] == -32003
    assert "photos" in answers[2]["error"]["message"]
    assert answers[2]["error"]["data"]["scope"] == "read photos"


def test_a_single_call_lacking_a_right_still_gets_403(door):
    r = door.call("read manage", "list_media", event_id=_event())
    assert r.status_code == 403


# ── A8 ────────────────────────────────────────────────────────────────

def test_reading_share_links_is_written_to_the_change_log(door):
    from app import protokoll
    eid = _event()
    before = protokoll.anzahl()
    door.data(door.call("read photos", "list_share_links", event_id=eid))
    assert protokoll.anzahl() == before + 1
    assert "list_share_links" in protokoll.liste(limit=1)[0]["was"]


# ── A13 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pin", ["471", "12"])
def test_a_pin_shorter_than_four_digits_is_refused_with_the_rule_it_breaks(door, pin):
    dev = db.create_device(name="PIN-Box")
    r = door.call("read secure", "update_booth_profile_secure", booth_id=dev["id"],
                  changes={"access": {"pin": pin}})
    result = r.get_json()["result"]
    assert result.get("isError")
    assert "4" in result["content"][0]["text"]
    assert not (ecosystem.device_profile(db.get_device_by_id(dev["id"])).get("access") or {}).get("pin")


@pytest.mark.parametrize("pin", ["938271", "4711"])
def test_a_good_pin_of_four_digits_or_more_is_taken(door, pin):
    dev = db.create_device(name="PIN-Box-gut")
    door.data(door.call("read secure", "update_booth_profile_secure", booth_id=dev["id"],
                        changes={"access": {"pin": pin}}))


# ── A14 ───────────────────────────────────────────────────────────────

def test_jobs_that_need_a_payload_are_not_offered():
    from app import mcp
    offered = set(mcp.TOOLS["send_booth_job"]["schema"]["properties"]["job"]["enum"])
    assert not offered & {"remote_control", "set_sync_config", "browse_remote", "set_local_config"}
    assert {"test_print", "sync_now", "unlock", "doctor"} <= offered
    # Second review R2-U2: these act under a guest and belong to live control.
    assert not offered & {"lock", "restart_app", "test_capture"}


def test_a_payload_job_is_refused_by_name(door):
    dev = db.create_device(name="Auftrags-Box")
    r = door.call("read manage", "send_booth_job", booth_id=dev["id"], job="set_sync_config")
    assert r.get_json()["result"].get("isError")


def test_the_pin_pattern_is_the_booths_rule():
    import re
    from app import mcp_more
    assert re.fullmatch(mcp_more.PIN_PATTERN, "938271")
    assert re.fullmatch(mcp_more.PIN_PATTERN, "9382")
    assert not re.fullmatch(mcp_more.PIN_PATTERN, "938")
    assert not re.fullmatch(mcp_more.PIN_PATTERN, "938271938")
