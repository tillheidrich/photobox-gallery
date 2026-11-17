"""Second review R2-U1: a booth profile holds only values the kiosk can show safely.

The reviewer's proof: ``update_booth_profile`` with ``manage`` put
``photo" autofocus onfocus="…" x="`` into ``modes``; the booth merged it and
the kiosk wrote it into ``data-mode="…"``. Script ran on the booth's own
origin, which can read every guest picture. These tests hold the gallery
side: every writer refuses such a value, and nothing stored earlier reaches
a booth.
"""
import io
import json
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = 'photo" autofocus onfocus="fetch(\'/api/gallery\')" x="'


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

    def tool(credential, name, **arguments):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments}},
                        headers={"Authorization": f"Bearer {credential}"})
        assert r.status_code == 200
        return r.get_json()["result"]

    device = db.create_device(name="Box Test")
    yield SimpleNamespace(token=token, tool=tool, device=device, db=db, ecosystem=ecosystem, client=client)
    con = db.get_db()
    con.executemany("DELETE FROM oauth_clients WHERE client_id=?", [(cid,) for cid, _ in made.values()])
    con.commit()
    con.close()


def _stored(door):
    return door.ecosystem.device_profile(door.db.get_device_by_id(door.device["id"]))


@pytest.mark.parametrize("changes", [
    {"modes": ["photo", PAYLOAD]},
    {"looks": ["original", 'warm"><img src=x onerror=alert(1)>']},
    {"formats": ["4:5", '9:16" onclick="x']},
    {"default_format": "<b>"},
    {"guest_text": {"modes": [PAYLOAD]}},
    {"start": {"corner_size": 'l" onmouseover="x'}},
    {"theme": {"accent": "red;background:url(//evil)"}},
    {"draw": {"colors": ['#fff" onclick="x']}},
    {"attract": {"media": ['a.png" onerror="x']}},
    {"countdown": {"first": 999}},
    {"guest_text": {"enabled": "yes"}},
    {"start": {"steps": "immer\u0000"}},
])
def test_update_booth_profile_refuses_values_outside_the_rules(door, changes):
    before = _stored(door)
    result = door.tool(door.token("read manage"), "update_booth_profile", booth_id=door.device["id"], changes=changes)
    assert result.get("isError"), result
    assert "Profil nicht übernommen" in result["content"][0]["text"]
    assert _stored(door) == before


def test_valid_changes_still_go_through_and_unknown_keys_are_dropped(door):
    result = door.tool(door.token("read manage"), "update_booth_profile", booth_id=door.device["id"],
                       changes={"modes": ["photo", "gif"], "looks": ["original", "lut-warm-film"],
                                "theme": {"accent": "#ff8800", "unknown_key": "x"},
                                "countdown": {"first": 5}})
    assert not result.get("isError"), result
    stored = _stored(door)
    assert stored["modes"] == ["photo", "gif"] and stored["theme"]["accent"] == "#ff8800"
    assert "unknown_key" not in stored["theme"]


def test_a_profile_file_with_markup_is_refused_on_import(door):
    with door.client.session_transaction() as s:
        s["admin_logged_in"] = True
    door.client.get(f"/admin/devices/{door.device['id']}/booth")       # CSRF token into the session
    with door.client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    before = _stored(door)
    data = {"modes": ["photo", PAYLOAD]}
    r = door.client.post(f"/admin/devices/{door.device['id']}/booth/import",
                         data={"csrf_token": token, "profile": (io.BytesIO(json.dumps(data).encode()), "p.json")},
                         content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200 and "Profil nicht übernommen" in r.get_data(as_text=True)
    assert _stored(door) == before


def test_a_bad_value_stored_before_the_rules_never_reaches_a_booth(door):
    con = door.db.get_db()
    con.execute("UPDATE devices SET profile_json=? WHERE id=?",
                (json.dumps({"modes": ["photo", PAYLOAD], "looks": ["original", 'x"y']}), door.device["id"]))
    con.commit()
    con.close()
    prof = door.ecosystem.profile_for_box(door.db.get_device_by_id(door.device["id"]), "http://localhost")
    assert prof["modes"] == ["photo"] and prof["looks"] == ["original"]
    assert PAYLOAD not in json.dumps(prof)
    # and the old value does not block an unrelated change
    result = door.tool(door.token("read manage"), "update_booth_profile", booth_id=door.device["id"],
                       changes={"countdown": {"first": 4}})
    assert not result.get("isError"), result


def test_both_sides_use_the_same_rules_file():
    gallery = (ROOT / "app" / "profile_rules.py").read_bytes()
    booth = (ROOT / "booth" / "boothcore" / "profile_rules.py").read_bytes()
    assert gallery == booth


def test_the_rules_know_the_booths_number_limits():
    import ast
    from app import profile_rules
    src = (ROOT / "booth" / "boothcore" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    limits = next(ast.literal_eval(n.value) for n in tree.body
                  if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "PROFILE_LIMITS")
    assert limits == profile_rules.PROFILE_LIMITS


def test_the_default_profile_passes_its_own_rules():
    from app import ecosystem, profile_rules
    clean, problems = profile_rules.check(ecosystem.DEFAULT_PROFILE, ecosystem.DEFAULT_PROFILE)
    assert problems == [] and clean == ecosystem.DEFAULT_PROFILE
