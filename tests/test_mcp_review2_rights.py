"""Second review: rights the default MCP access (``read manage``) must not have.

* R2-U2 — ``send_booth_job`` sent ``restart_app``, ``lock`` and
  ``test_capture``; they belong to live control (right ``control``).
* R2-N1 — ``aufraeumen`` deletes guest pictures on the booth: changing it
  through MCP needs ``photos``.
* R2-N2 — ``get_booth_profile`` showed webhook URL, mail host, login, BCC and
  where operator warnings go: masked without ``secure``.
* R2-N3 — ``meldungen.mail.to`` could be redirected with ``manage``: one list
  of "where data goes" fields guards reading and writing.
"""
import json
import secrets
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture()
def door(client):
    client.environ_base["REMOTE_ADDR"] = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"
    client.get("/healthz")
    from app import accounts, db, ecosystem, oauth
    ecosystem.init_schema()
    db.set_setting("admin_password_hash", generate_password_hash("Richtig-Langes-Passwort-42"))

    made = {}

    def token(scope):
        # One client per scope, removed afterwards: the gallery takes at most
        # oauth.MAX_CLIENTS registrations, and other tests register for real.
        if scope in made:
            return made[scope][1]
        cid = "pbc_" + secrets.token_hex(6)
        con = db.get_db()
        con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                    "VALUES (?,?,?,datetime('now'))", (cid, "Test", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
        answer = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp", accounts.session_epoch())
        con.commit()
        con.close()
        made[scope] = (cid, answer["access_token"])
        return answer["access_token"]

    def tool(scope, name, **arguments):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments}},
                        headers={"Authorization": f"Bearer {token(scope)}"})
        assert r.status_code == 200
        return r.get_json()["result"]

    device = db.create_device(name="Box R2")
    yield SimpleNamespace(tool=tool, device=device, db=db, ecosystem=ecosystem)
    con = db.get_db()
    con.executemany("DELETE FROM oauth_clients WHERE client_id=?", [(cid,) for cid, _ in made.values()])
    con.commit()
    con.close()


def _text(result):
    return result["content"][0]["text"]


# ── R2-U2 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("job", ["restart_app", "lock", "test_capture"])
def test_manage_cannot_send_jobs_that_act_under_a_guest(door, job):
    from app import mcp
    assert job not in mcp.TOOLS["send_booth_job"]["schema"]["properties"]["job"]["enum"]
    result = door.tool("read manage", "send_booth_job", booth_id=door.device["id"], job=job)
    assert result.get("isError") and "Fernsteuerung" in _text(result)
    assert door.ecosystem.jobs_waiting(door.device["id"]) == 0


def test_harmless_jobs_still_go(door):
    result = door.tool("read manage", "send_booth_job", booth_id=door.device["id"], job="doctor")
    assert not result.get("isError"), result


# ── R2-N1 ────────────────────────────────────────────────────────────

CLEANUP = {"aufraeumen": {"enabled": True, "nur_bestaetigte": False}}


def test_cleanup_settings_need_photos(door):
    result = door.tool("read manage", "update_booth_profile", booth_id=door.device["id"], changes=CLEANUP)
    assert result.get("isError") and "photos" in _text(result)
    stored = door.ecosystem.device_profile(door.db.get_device_by_id(door.device["id"]))
    assert stored["aufraeumen"]["enabled"] is False and stored["aufraeumen"]["nur_bestaetigte"] is True


def test_with_photos_the_cleanup_can_be_set(door):
    result = door.tool("read manage photos", "update_booth_profile", booth_id=door.device["id"], changes=CLEANUP)
    assert not result.get("isError"), result


# ── R2-N2 / R2-N3 ────────────────────────────────────────────────────

def _with_destinations(door):
    p = door.ecosystem.device_profile(door.db.get_device_by_id(door.device["id"]))
    p["webhooks"].update(url="https://discord.example.org/api/webhooks/1/geheim")
    p["mail"].update(host="smtp.example.org", user="postfach@example.org", bcc="kopie@example.org",
                     from_addr="box@example.org")
    p["meldungen"]["mail"]["to"] = "betreiber@example.org"
    door.ecosystem.save_device_profile(door.device["id"], p, None)


def test_read_does_not_show_where_data_goes(door):
    _with_destinations(door)
    text = _text(door.tool("read", "get_booth_profile", booth_id=door.device["id"]))
    for secret in ("discord.example.org", "smtp.example.org", "postfach@", "kopie@", "betreiber@", "box@"):
        assert secret not in text, secret


def test_secure_shows_the_destinations_but_never_the_passwords(door):
    _with_destinations(door)
    text = _text(door.tool("read secure", "get_booth_profile", booth_id=door.device["id"]))
    assert "betreiber@example.org" in text and "discord.example.org" in text


def test_manage_cannot_redirect_operator_warnings(door):
    result = door.tool("read manage", "update_booth_profile", booth_id=door.device["id"],
                       changes={"meldungen": {"mail": {"to": "fremd@example.net"}}})
    assert result.get("isError") and "meldungen.mail.to" in _text(result)
    stored = door.ecosystem.device_profile(door.db.get_device_by_id(door.device["id"]))
    assert stored["meldungen"]["mail"]["to"] == ""


def test_manage_may_still_change_the_rest_of_the_warnings(door):
    result = door.tool("read manage", "update_booth_profile", booth_id=door.device["id"],
                       changes={"meldungen": {"wiederholen_minuten": 30}})
    assert not result.get("isError"), result


def test_secure_may_redirect_operator_warnings(door):
    result = door.tool("read secure", "update_booth_profile_secure", booth_id=door.device["id"],
                       changes={"meldungen": {"mail": {"to": "betreiber@example.org"}}})
    assert not result.get("isError"), result
    stored = door.ecosystem.device_profile(door.db.get_device_by_id(door.device["id"]))
    assert stored["meldungen"]["mail"]["to"] == "betreiber@example.org"


def test_one_list_guards_reading_and_writing():
    from app import mcp
    paths = {".".join(p) for p in mcp.PROFILE_DESTINATIONS}
    assert {"webhooks.url", "mail.bcc", "mail.user", "mail.host", "meldungen.mail.to"} <= paths
