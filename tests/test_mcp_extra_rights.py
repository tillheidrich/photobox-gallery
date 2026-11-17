"""Extra rights of the MCP door: control, photos, secure, updates (15.11.2026).

The owner wants everything the booth can do to be possible through MCP —
and to decide per access how far that goes. These tests are that promise:

* a new access (OAuth or static key) has exactly the old ``read``/``manage``
  tools, nothing more;
* an extra right comes only ticked, with the admin password, and lands in the
  change log; it can be withdrawn on its own;
* every tool is offered and callable exactly with the rights it names
  (the full matrix);
* destructive tools are two-step, with a token bound to access, tool and
  arguments, used once, valid two minutes;
* no tool answer ever carries a secret;
* control jobs reach only their own booth, expire, are rate-limited, and the
  fast poll switches on and off; a waiting call never holds both workers.
"""
import json
import re
import secrets
import threading
import time
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

PASSWORD = "Richtig-Langes-Passwort-42"
BASE_TOOLS = None


@pytest.fixture()
def door(client):
    client.environ_base["REMOTE_ADDR"] = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    db.set_setting("admin_password_hash", generate_password_hash(PASSWORD))

    def token(scope, name="Testassistent"):
        from app import accounts, oauth
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

    def tool(credential, name, **arguments):
        r = call(credential, "tools/call", {"name": name, "arguments": arguments})
        assert r.status_code == 200, (name, r.status_code, r.get_data(as_text=True)[:300])
        return r.get_json()["result"]

    def data(result):
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])

    return SimpleNamespace(token=token, call=call, tool=tool, data=data, client=client)


def _names(r):
    return {t["name"] for t in r.get_json()["result"]["tools"]}


def _old_tools():
    from app import mcp
    return {n for n, s in mcp.TOOLS.items() if s["scope"] in ("read", "manage") and not s.get("also")}


# ── A new access has no new rights ───────────────────────────────────

def test_a_new_oauth_access_sees_exactly_the_read_and_manage_tools(door):
    assert _names(door.call(door.token("read manage"), "tools/list")) == _old_tools()


def test_a_new_static_key_has_read_and_manage_only(door):
    from app import mcp
    key = mcp.new_key()
    assert _names(door.call(key, "tools/list", header="X-API-Key")) == _old_tools()
    r = door.call(key, "tools/call", {"name": "list_media", "arguments": {"event_id": 1}}, header="X-API-Key")
    assert r.status_code == 403


def test_the_old_tools_did_not_change_their_right():
    """Behaviour of existing accesses stays: every tool of before keeps read or manage."""
    from app import mcp
    # create_share_link and list_share_links also need "photos" since
    # 15.11.2026: a share link opens the pictures (tests/test_mcp_share_links_need_photos.py).
    before = {"list_events", "event_stats", "get_event_settings", "list_booths", "booth_stats",
              "get_booth_profile", "get_booth_texts", "booth_diagnostics", "list_assets", "list_templates",
              "get_template", "validate_template", "list_software", "create_event", "setup_event",
              "configure_event", "set_event_settings", "revoke_share_link",
              "update_booth_profile", "set_booth_texts", "send_booth_job", "set_booth_hardware", "add_asset",
              "save_template", "list_clients", "create_client", "assign_client_events", "list_selections",
              "create_subgallery"}
    for name in before:
        assert mcp.TOOLS[name]["scope"] in ("read", "manage"), name
        assert not mcp.TOOLS[name].get("also"), name


# ── The full matrix: every tool × every right ────────────────────────

SCOPE_SETS = ["read", "read manage", "read control", "read photos", "read secure", "read updates",
              "read control photos", "read manage control photos secure updates"]


@pytest.mark.parametrize("scopes", SCOPE_SETS)
def test_every_tool_is_offered_and_callable_exactly_with_its_rights(door, scopes):
    from app import mcp, oauth
    granted = oauth.parse_scope(scopes)
    credential = door.token(scopes)
    offered = _names(door.call(credential, "tools/list"))
    assert offered == {n for n, s in mcp.TOOLS.items() if mcp.may_use(s, granted)}
    for name, spec in mcp.TOOLS.items():
        # Empty arguments: an allowed tool answers with a tool error about the
        # missing fields (or runs a harmless listing) — never 403; a tool
        # beyond the rights answers 403 and does nothing.
        r = door.call(credential, "tools/call", {"name": name, "arguments": {}})
        if mcp.may_use(spec, granted):
            assert r.status_code == 200, (scopes, name, r.status_code)
        else:
            assert r.status_code == 403, (scopes, name, r.status_code)
            header = r.headers["WWW-Authenticate"]
            assert 'error="insufficient_scope"' in header


def test_extra_rights_include_nothing_else():
    from app import mcp
    assert not mcp.allowed("photos", {"read", "manage", "control", "secure", "updates"})
    assert not mcp.allowed("control", {"read", "manage", "photos"})
    assert not mcp.allowed("manage", {"read", "control", "photos", "secure", "updates"})
    assert mcp.allowed("read", {"manage"})


def test_deleting_a_run_at_the_booth_needs_control_and_photos(door):
    from app import db
    dev = db.create_device(name="Zwei-Rechte-Box")
    for scopes, status in (("read control", 403), ("read photos", 403)):
        r = door.call(door.token(scopes), "tools/call",
                      {"name": "booth_delete_run", "arguments": {"booth_id": dev["id"], "uid": "abc"}})
        assert r.status_code == status


def test_destructive_tools_are_marked_and_all_have_a_confirm_step():
    from app import mcp
    destructive = {n for n, s in mcp.TOOLS.items() if s.get("destructive")}
    assert {"delete_media", "delete_gallery_run", "purge_event_photos", "delete_event", "rotate_booth_key",
            "delete_booth", "confirm_pairing_request", "booth_quit", "booth_delete_run",
            "install_booth_update", "release_booth_software"} <= destructive
    for name in destructive:
        assert mcp.TOOLS[name]["confirm"] is not None
        assert "confirm_token" in mcp.TOOLS[name]["schema"]["properties"]


def test_tools_carrying_guest_or_booth_text_say_it_is_data(door):
    from app import mcp
    for name in ("list_media", "get_photo", "booth_control", "get_job", "list_pending_uploads",
                 "list_guest_addresses", "list_pairing_requests"):
        assert mcp.TOOLS[name]["description"].startswith(mcp.UNTRUSTED_NOTE), name


# ── Granting: ticked, with the password, in the change log ───────────

CLAUDE = "https://claude.ai/api/mcp/auth_callback"


def _consent_page(door, scope="read manage"):
    import base64
    import hashlib
    from app import accounts
    web = door.client
    r = web.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CLAUDE],
                                          "grant_types": ["authorization_code", "refresh_token"],
                                          "response_types": ["code"], "token_endpoint_auth_method": "none"})
    cid = r.get_json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with web.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s.setdefault("csrf_token", secrets.token_hex(24))
        csrf = s["csrf_token"]
    page = web.get("/oauth/authorize", query_string={
        "response_type": "code", "client_id": cid, "redirect_uri": CLAUDE, "code_challenge": challenge,
        "code_challenge_method": "S256", "scope": scope, "state": "s", "resource": "http://localhost/mcp"})
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    rid = re.search(r'name="request_id" value="([^"]+)"', html).group(1)
    return SimpleNamespace(cid=cid, verifier=verifier, csrf=csrf, rid=rid, html=html)


def _token_from(door, c, location):
    from urllib.parse import parse_qs, urlsplit
    code = parse_qs(urlsplit(location).query)["code"][0]
    r = door.client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": c.cid,
                                               "redirect_uri": CLAUDE, "code_verifier": c.verifier,
                                               "resource": "http://localhost/mcp"})
    return r.get_json()


def test_the_consent_page_offers_the_extra_rights_unticked(door):
    c = _consent_page(door)
    for s in ("control", "photos", "secure", "updates"):
        assert re.search(rf'name="scope_{s}" value="1"\s*>', c.html), s      # present, not checked
    assert 'name="password"' in c.html


def test_without_ticking_nothing_extra_is_granted_and_no_password_is_needed(door):
    c = _consent_page(door)
    r = door.client.post("/oauth/authorize", data={"csrf_token": c.csrf, "request_id": c.rid,
                                                   "decision": "allow", "manage": "1"})
    assert r.status_code == 302
    assert _token_from(door, c, r.headers["Location"])["scope"] == "read manage"


def test_an_extra_right_without_the_right_password_grants_nothing(door):
    from app import db
    c = _consent_page(door)
    r = door.client.post("/oauth/authorize", data={"csrf_token": c.csrf, "request_id": c.rid, "decision": "allow",
                                                   "manage": "1", "scope_photos": "1", "password": "falsch"})
    assert r.status_code == 200                                  # the page again, with the error
    assert "Passwort stimmt nicht" in r.get_data(as_text=True)
    con = db.get_db()
    assert con.execute("SELECT COUNT(*) FROM oauth_codes WHERE client_id=?", (c.cid,)).fetchone()[0] == 0
    con.close()
    # The same request may be answered again, now with the password.
    r = door.client.post("/oauth/authorize", data={"csrf_token": c.csrf, "request_id": c.rid, "decision": "allow",
                                                   "manage": "1", "scope_photos": "1", "password": PASSWORD})
    assert r.status_code == 302
    assert _token_from(door, c, r.headers["Location"])["scope"] == "read manage photos"


def test_granting_an_extra_right_is_in_the_change_log(door):
    from app import protokoll
    c = _consent_page(door)
    door.client.post("/oauth/authorize", data={"csrf_token": c.csrf, "request_id": c.rid, "decision": "allow",
                                               "scope_control": "1", "scope_updates": "1", "password": PASSWORD})
    rows = protokoll.liste(limit=5)
    assert any("Zusatzrechte erteilt" in (r.get("was") or "") and "control" in (r.get("ergebnis") or "")
               and "updates" in (r.get("ergebnis") or "") for r in rows), rows


def test_extra_rights_can_be_withdrawn_one_access_at_a_time(door, formular):
    from app import accounts, oauth
    c = _consent_page(door)
    r = door.client.post("/oauth/authorize", data={"csrf_token": c.csrf, "request_id": c.rid, "decision": "allow",
                                                   "manage": "1", "scope_photos": "1", "password": PASSWORD})
    tokens = _token_from(door, c, r.headers["Location"])
    access = tokens["access_token"]
    assert "list_media" in _names(door.call(access, "tools/list"))
    formular("/admin/settings/assistants", {"action": "withdraw_extras", "client_id": c.cid})
    names = _names(door.call(access, "tools/list"))
    assert "list_media" not in names and "update_booth_profile" in names      # manage stays
    # A refresh cannot bring the right back.
    r = door.client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                               "client_id": c.cid, "resource": "http://localhost/mcp",
                                               "scope": "read manage photos"})
    assert r.status_code == 400


def test_a_new_password_ends_extra_rights_too(door):
    from app import accounts
    access = door.token("read manage control photos secure updates")
    assert door.call(access, "tools/list").status_code == 200
    accounts.set_password("Ganz-Neues-Passwort-77")
    assert door.call(access, "tools/list").status_code == 401


def test_the_static_key_gets_extra_rights_only_with_the_password(door, formular):
    from app import accounts, mcp
    key = mcp.new_key()
    with door.client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
    formular("/admin/settings/mcp", {"action": "key_extras", "scope_photos": "1", "password": "falsch"})
    assert mcp.key_scopes() == {"read", "manage"}
    formular("/admin/settings/mcp", {"action": "key_extras", "scope_photos": "1", "password": PASSWORD})
    assert mcp.key_scopes() == {"read", "manage", "photos"}
    assert "list_media" in _names(door.call(key, "tools/list", header="X-API-Key"))
    # Taking a right away needs no password; a new key starts without extras.
    formular("/admin/settings/mcp", {"action": "key_extras"})
    assert mcp.key_scopes() == {"read", "manage"}
    mcp.set_key_extras({"secure"})
    mcp.new_key()
    assert mcp.key_scopes() == {"read", "manage"}


# ── Pictures: pages, previews, signed links, two-step deletion ───────

def _event_with_pictures(n=3, name="Bilder-Event"):
    import io
    from PIL import Image
    from app import db, media
    slug = "mcp-" + secrets.token_hex(4)
    event_id = db.create_event(slug=slug, name=name, upload_token=secrets.token_urlsafe(8))
    folder = media.data_dir() / "events" / slug
    folder.mkdir(parents=True, exist_ok=True)
    ids = []
    for i in range(n):
        im = Image.new("RGB", (1600, 1200), (200, 40 + i, 90))
        path = folder / f"bild-{i}.jpg"
        im.save(path, "JPEG")
        rel = path.relative_to(media.data_dir()).as_posix()
        ids.append(db.add_media(event_id=event_id, kind="photo", filename=f"bild-{i}.jpg", path_original=rel,
                                path_webp=rel, path_thumb=rel, size_bytes=path.stat().st_size, sha256="0" * 64,
                                share_token=secrets.token_urlsafe(10)))
    return event_id, ids


def test_pictures_come_in_pages_of_at_most_fifty(door):
    event_id, ids = _event_with_pictures(55)
    access = door.token("read photos")
    first = door.data(door.tool(access, "list_media", event_id=event_id, page=1, per_page=500))
    assert first["per_page"] == 50 and len(first["items"]) == 50 and first["total"] == 55 and first["pages"] == 2
    second = door.data(door.tool(access, "list_media", event_id=event_id, page=2, per_page=500))
    assert len(second["items"]) == 5
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


def test_a_signed_link_works_ten_minutes_and_not_when_changed(door, monkeypatch):
    from app import mcp_more
    event_id, ids = _event_with_pictures(1)
    access = door.token("read photos")
    item = door.data(door.tool(access, "list_media", event_id=event_id))["items"][0]
    path = item["original_url"].split("localhost", 1)[-1]
    assert door.client.get(path).status_code == 200
    assert door.client.get(path.replace("/original?", "/thumb?")).status_code == 404     # sig is per variant
    assert door.client.get(re.sub(r"sig=[0-9a-f]+", "sig=" + "0" * 40, path)).status_code == 404
    other = path.replace(f"/media/{ids[0]}/", f"/media/{ids[0] + 1}/")
    assert door.client.get(other).status_code == 404
    real = time.time
    monkeypatch.setattr(mcp_more.time, "time", lambda: real() + mcp_more.LINK_SECONDS + 5)
    assert door.client.get(path).status_code == 404


def test_get_photo_returns_a_small_picture_as_image_content(door):
    import base64
    import io
    from PIL import Image
    event_id, ids = _event_with_pictures(1)
    result = door.tool(door.token("read photos"), "get_photo", media_id=ids[0], max_px=4000)
    images = [c for c in result["content"] if c["type"] == "image"]
    assert len(images) == 1 and images[0]["mimeType"] == "image/jpeg"
    im = Image.open(io.BytesIO(base64.b64decode(images[0]["data"])))
    assert max(im.size) <= 1024


def test_deleting_pictures_is_two_step_and_bound_to_access_tool_and_arguments(door):
    from app import db, retention
    event_id, ids = _event_with_pictures(3)
    access = door.token("read photos", name="Löschassistent")
    first = door.data(door.tool(access, "delete_media", media_ids=[ids[0]]))
    assert first["confirm_required"] is True and first["what_would_happen"]["pictures"] == 1
    assert db.get_media(ids[0])                                               # nothing happened yet
    token = first["confirm_token"]
    # Other arguments, another tool, another access, a made-up token: refused.
    assert door.tool(access, "delete_media", media_ids=[ids[1]], confirm_token=token).get("isError")
    assert door.tool(access, "delete_gallery_run", run="x", confirm_token=token).get("isError")
    assert door.tool(door.token("read photos"), "delete_media", media_ids=[ids[0]], confirm_token=token).get("isError")
    assert door.tool(access, "delete_media", media_ids=[ids[0]], confirm_token="pbc_falsch").get("isError")
    assert db.get_media(ids[0]) and db.get_media(ids[1])
    done = door.data(door.tool(access, "delete_media", media_ids=[ids[0]], confirm_token=token))
    assert done["deleted"] == [ids[0]] and not db.get_media(ids[0])
    # Once only.
    assert door.tool(access, "delete_media", media_ids=[ids[0]], confirm_token=token).get("isError")
    log = retention.entries(limit=5)
    assert any(r["actor"] == "assistant" and "Löschassistent" in (r.get("note") or "") for r in log), log


def test_an_expired_confirmation_is_refused(door, monkeypatch):
    from app import db, mcp
    event_id, ids = _event_with_pictures(1)
    access = door.token("read photos")
    token = door.data(door.tool(access, "delete_media", media_ids=ids))["confirm_token"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + mcp.CONFIRM_SECONDS + 1)
    assert door.tool(access, "delete_media", media_ids=ids, confirm_token=token).get("isError")
    monkeypatch.setattr(time, "time", real)
    assert db.get_media(ids[0])


def test_purging_an_event_keeps_the_event(door):
    from app import db
    event_id, ids = _event_with_pictures(2)
    access = door.token("read photos")
    token = door.data(door.tool(access, "purge_event_photos", event_id=event_id))["confirm_token"]
    door.data(door.tool(access, "purge_event_photos", event_id=event_id, confirm_token=token))
    assert db.get_event_by_id(event_id) and not any(db.get_media(i) for i in ids)


def test_hidden_pictures_go_to_the_review_queue_and_come_back(door):
    from app import db, ecosystem
    event_id, ids = _event_with_pictures(2)
    access = door.token("read photos")
    door.data(door.tool(access, "set_media_visibility", media_ids=[ids[0]], visible=False))
    assert db.get_media(ids[0])["hidden"] == 1 and ecosystem.pending_review_count(event_id) == 1
    door.data(door.tool(access, "set_media_visibility", media_ids=[ids[0]], visible=True))
    assert db.get_media(ids[0])["hidden"] == 0


def test_guest_addresses_only_through_their_own_tool(door):
    from app import db
    event_id, _ = _event_with_pictures(0)
    con = db.get_db()
    con.execute("INSERT INTO notify_subscriptions (event_id, email, token, confirmed_at) VALUES (?,?,?,datetime('now'))",
                (event_id, "gast@example.org", secrets.token_hex(8)))
    con.commit()
    con.close()
    access = door.token("read photos")
    counts = door.tool(access, "guest_contact_counts", event_id=event_id)
    assert "gast@example.org" not in json.dumps(counts) and door.data(counts)["confirmed"] == 1
    assert door.data(door.tool(access, "list_guest_addresses", event_id=event_id))["addresses"] == ["gast@example.org"]
    # Nothing in read or manage names an address.
    for name in ("list_events", "event_stats"):
        assert "gast@example.org" not in json.dumps(door.tool(door.token("read manage"), name, **(
            {"event_id": event_id} if name == "event_stats" else {})))


# ── Secrets go in, never out ─────────────────────────────────────────

SECRETS = {"pin": "918273", "smtp": "smtp-Geheim-55", "hook": "hook-Geheim-66", "trigger": "trigger-Geheim-77",
           "tg": "123456:tg-Geheim-88", "discord": "https://discord.example/api/webhooks/geheim-99"}


def test_secrets_can_be_set_but_no_tool_answer_ever_contains_one(door):
    from app import db, ecosystem, mcp
    dev = db.create_device(name="Geheimnis-Box")
    every = door.token("read manage control photos secure updates")
    result = door.tool(every, "update_booth_profile_secure", booth_id=dev["id"], changes={
        "access": {"pin": SECRETS["pin"]}, "mail": {"password": SECRETS["smtp"], "enabled": True},
        "webhooks": {"secret": SECRETS["hook"], "url": "https://hooks.example/x"},
        "http_trigger": {"token": SECRETS["trigger"]}})
    door.data(result)
    door.data(door.tool(every, "set_notification_settings",
                        changes={"notify_tg_token": SECRETS["tg"], "notify_discord_webhook": SECRETS["discord"]}))
    stored = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert stored["access"]["pin"] == SECRETS["pin"] and stored["mail"]["password"] == SECRETS["smtp"]
    assert db.get_setting("notify_tg_token") == SECRETS["tg"]
    # "••••" sent back keeps the stored value.
    door.data(door.tool(every, "update_booth_profile_secure", booth_id=dev["id"],
                        changes={"mail": {"password": "••••", "from_name": "Box"}}))
    assert ecosystem.device_profile(db.get_device_by_id(dev["id"]))["mail"]["password"] == SECRETS["smtp"]
    # Every tool that could show something, with every right: no secret anywhere.
    answers = [result]
    for name, args in (("get_booth_profile", {"booth_id": dev["id"]}), ("list_booths", {}),
                       ("booth_diagnostics", {"booth_id": dev["id"]}), ("get_notification_settings", {}),
                       ("get_booth_texts", {"booth_id": dev["id"]}), ("list_pairing_requests", {}),
                       ("update_booth_profile_secure", {"booth_id": dev["id"], "changes": {"http_trigger": {"enabled": True}}}),
                       ("set_notification_settings", {"changes": {"notify_quiet_from": "23:00"}})):
        answers.append(door.tool(every, name, **args))
    text = json.dumps(answers, ensure_ascii=False)
    for value in SECRETS.values():
        assert value not in text, value


def test_a_weak_pin_or_a_strange_address_is_refused(door):
    from app import db, mcp
    assert mcp.TOOLS["update_booth_profile_secure"]["scope"] == "secure"
    dev = db.create_device(name="Pin-Box")
    access = door.token("read secure")
    assert door.tool(access, "update_booth_profile_secure", booth_id=dev["id"],
                     changes={"access": {"pin": "123456"}}).get("isError")
    assert door.tool(access, "update_booth_profile_secure", booth_id=dev["id"],
                     changes={"qr_url_template": "javascript:alert(1)"}).get("isError")


def test_the_qr_target_moves_only_with_secure(door):
    from app import db
    event_id, _ = _event_with_pictures(0)
    assert door.call(door.token("read manage"), "tools/call", {"name": "set_event_qr_target", "arguments": {
        "event_id": event_id, "qr_target": "external", "external_url": "https://example.org/{uid}"}}).status_code == 403
    out = door.data(door.tool(door.token("read secure"), "set_event_qr_target", event_id=event_id,
                              qr_target="external", external_url="https://example.org/{uid}"))
    assert out["qr_target"] == "external" and db.get_event_by_id(event_id)["external_url"] == "https://example.org/{uid}"


def test_rotating_a_key_signs_the_booth_off_and_shows_no_key(door):
    from app import db
    dev = db.create_device(name="Rotations-Box")
    old_key = dev["api_key"]
    access = door.token("read secure")
    token = door.data(door.tool(access, "rotate_booth_key", booth_id=dev["id"]))["confirm_token"]
    answer = door.tool(access, "rotate_booth_key", booth_id=dev["id"], confirm_token=token)
    assert door.data(answer)["signed_off"] is True
    assert "pbk_" not in json.dumps(answer) and old_key not in json.dumps(answer)
    assert door.client.post("/api/v2/booth/jobs", json={}, headers={"X-API-Key": old_key}).status_code == 401


# ── Remote control: own booth only, fast poll, expiry, rate, waiting ──

def _booth(name="Steuer-Box"):
    from app import db
    dev = db.create_device(name=name)
    return dev


def _poll(door, dev, results=None):
    r = door.client.post("/api/v2/booth/jobs", json={"job_results": results or []}, headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200
    return r.get_json()


def _sync(door, dev):
    r = door.client.post("/api/v2/booth/sync", json={"status": {}, "profile_version": -1},
                         headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200
    return r.get_json()


def test_fast_polling_switches_on_with_a_session_and_off_again(door, monkeypatch):
    from app import remote_control
    monkeypatch.setattr(remote_control, "WAIT_SECONDS", 0.0)
    dev = _booth()
    access = door.token("read control")
    assert _sync(door, dev)["fast_seconds"] == 0
    door.data(door.tool(access, "remote_control_start", booth_id=dev["id"]))
    assert _sync(door, dev)["fast_seconds"] == remote_control.FAST_POLL_SECONDS
    assert _poll(door, dev)["fast_seconds"] == remote_control.FAST_POLL_SECONDS
    door.data(door.tool(access, "remote_control_stop", booth_id=dev["id"]))
    assert _poll(door, dev)["fast_seconds"] == 0 and _sync(door, dev)["fast_seconds"] == 0


def test_a_control_job_reaches_only_its_own_booth(door, monkeypatch):
    from app import remote_control
    monkeypatch.setattr(remote_control, "WAIT_SECONDS", 0.0)
    mine, other = _booth("Box A"), _booth("Box B")
    access = door.token("read control")
    out = door.data(door.tool(access, "booth_control", booth_id=mine["id"], action="status", wait=False))
    assert not _poll(door, other)["jobs"]
    jobs = _poll(door, mine)["jobs"]
    assert [j["type"] for j in jobs] == ["remote_control"] and jobs[0]["payload"]["action"] == "status"
    # B cannot answer A's job, and A's job is not visible through B.
    _poll(door, other, [{"id": out["job_id"], "ok": True, "result": "gefälscht"}])
    assert door.tool(access, "get_job", booth_id=other["id"], job_id=out["job_id"]).get("isError")
    assert door.data(door.tool(access, "get_job", booth_id=mine["id"], job_id=out["job_id"]))["state"] == "delivered"


def test_a_control_job_nobody_picked_up_expires_after_a_minute(door, monkeypatch):
    from app import db, ecosystem, remote_control
    monkeypatch.setattr(remote_control, "WAIT_SECONDS", 0.0)
    dev = _booth()
    out = door.data(door.tool(door.token("read control"), "booth_control", booth_id=dev["id"], action="trigger",
                              args={"mode": "photo"}, wait=False))
    con = db.get_db()
    con.execute("UPDATE device_jobs SET created_at=datetime('now', '-61 seconds') WHERE id=?", (out["job_id"],))
    con.commit()
    con.close()
    assert _poll(door, dev)["jobs"] == []
    assert ecosystem.job_result(dev["id"], out["job_id"])["state"] == "expired"


def test_control_jobs_are_rate_limited(door, monkeypatch):
    from app import remote_control
    monkeypatch.setattr(remote_control, "WAIT_SECONDS", 0.0)
    monkeypatch.setattr(remote_control, "RATE_PER_MINUTE", 3)
    dev = _booth()
    access = door.token("read control")
    for _ in range(3):
        door.data(door.tool(access, "booth_control", booth_id=dev["id"], action="status", wait=False))
    assert "Zu viele" in door.tool(access, "booth_control", booth_id=dev["id"], action="status",
                                   wait=False)["content"][0]["text"]


def test_a_tool_waits_for_the_booth_and_returns_its_answer(door, monkeypatch, app):
    """Round trip with a simulated booth polling the small door; measures the latency."""
    from app import remote_control
    monkeypatch.setattr(remote_control, "WAIT_SECONDS", 5.0)
    dev = _booth()
    access = door.token("read control")
    booth = app.test_client()
    stop = threading.Event()

    def simulated_booth():
        results = []
        while not stop.is_set():
            r = booth.post("/api/v2/booth/jobs", json={"job_results": results}, headers={"X-API-Key": dev["api_key"]})
            results = [{"id": j["id"], "ok": True, "result": json.dumps({"camera": "ready", "echo": j["payload"]["action"]})}
                       for j in r.get_json()["jobs"]]
            time.sleep(0.1)

    t = threading.Thread(target=simulated_booth, daemon=True)
    t.start()
    try:
        start = time.time()
        out = door.data(door.tool(access, "booth_control", booth_id=dev["id"], action="status"))
        took = time.time() - start
    finally:
        stop.set()
        t.join(2)
    assert out["state"] == "done" and out["ok"] is True and out["result"] == {"camera": "ready", "echo": "status"}
    assert took < 3, took
    print(f"control round trip (simulated booth, 0.1 s poll): {took:.2f} s")


def test_only_one_call_waits_so_a_worker_stays_free_for_the_booth(door, monkeypatch, app):
    """Two workers: one waits for the booth; a second control call does not wait, and the booth's poll is served."""
    from app import remote_control
    monkeypatch.setattr(remote_control, "WAIT_SECONDS", 2.0)
    dev = _booth()
    access = door.token("read control")
    first = {}

    def worker_one():
        c = app.test_client()
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "booth_control", "arguments": {"booth_id": dev["id"], "action": "status"}}},
                   headers={"Authorization": f"Bearer {access}"})
        first["answer"] = json.loads(r.get_json()["result"]["content"][0]["text"])

    t = threading.Thread(target=worker_one)
    t.start()
    time.sleep(0.4)                                          # worker one now holds the wait slot
    start = time.time()
    second = door.data(door.tool(access, "booth_control", booth_id=dev["id"], action="status"))
    assert time.time() - start < 1.0 and second["state"] in ("issued", "delivered")
    assert "ein anderer Aufruf wartet" in second["hint"]
    start = time.time()
    polled = _poll(door, dev)
    assert time.time() - start < 1.0 and len(polled["jobs"]) == 2
    _poll(door, dev, [{"id": j["id"], "ok": True, "result": "{}"} for j in polled["jobs"]])
    t.join(5)
    assert first["answer"]["state"] == "done"


def test_the_change_log_names_the_assistant_the_tool_and_the_result(door, monkeypatch):
    from app import protokoll, remote_control
    monkeypatch.setattr(remote_control, "WAIT_SECONDS", 0.0)
    dev = _booth()
    door.tool(door.token("read control", name="Protokoll-Assistent"), "booth_control", booth_id=dev["id"],
              action="lock", wait=False)
    row = protokoll.liste(limit=1)[0]
    assert "Protokoll-Assistent" in row["wer"] and "booth_control" in row["was"]


# ── Updates: only with the right, only what the gallery holds ────────

def test_an_update_is_installed_only_with_updates_and_a_stored_version(door):
    from app import db, ecosystem, software
    dev = _booth("Update-Box")
    db.record_heartbeat(dev["id"], {"booth_version": "photobox-booth 0.1.0"}, "green", "127.0.0.1")
    r = door.call(door.token("read manage control photos secure"), "tools/call",
                  {"name": "install_booth_update", "arguments": {"booth_id": dev["id"]}})
    assert r.status_code == 403
    access = door.token("read updates")
    assert door.tool(access, "install_booth_update", booth_id=dev["id"]).get("isError")    # nothing offered
    software.hinzufuegen(program="booth", version="9.9.9", dateiname="booth-9.9.9.zip", daten=b"PK\x03\x04neu",
                         released=True)
    first = door.data(door.tool(access, "install_booth_update", booth_id=dev["id"]))
    assert first["what_would_happen"]["installs"] == "9.9.9" and len(first["what_would_happen"]["sha256"]) == 64
    assert not [j for j in ecosystem.recent_jobs(dev["id"], 5) if j["type"] == "install_update"]
    door.data(door.tool(access, "install_booth_update", booth_id=dev["id"], confirm_token=first["confirm_token"]))
    assert [j for j in ecosystem.recent_jobs(dev["id"], 5) if j["type"] == "install_update"]
