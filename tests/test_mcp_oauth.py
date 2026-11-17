"""OAuth for the MCP door: what a Claude custom connector needs, and nothing that
lets a stranger in.

The door leads to the whole administration. Every test here is one of the
ways in that must stay shut: a redirect that was not registered, a code used
twice, a refresh token used twice, a token minted for another server, a
consent page inside someone else's frame.
"""
import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest

CLAUDE = "https://claude.ai/api/mcp/auth_callback"


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@pytest.fixture()
def web(client):
    """A client from its own address, so rate limits of other tests do not bleed in."""
    client.environ_base["REMOTE_ADDR"] = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    return client


def _register(web, uris=(CLAUDE,), name="Claude"):
    return web.post("/oauth/register", json={"client_name": name, "redirect_uris": list(uris),
                                             "grant_types": ["authorization_code", "refresh_token"],
                                             "response_types": ["code"],
                                             "token_endpoint_auth_method": "none"})


def _login(web):
    from app import accounts
    with web.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s.setdefault("csrf_token", secrets.token_hex(24))
        return s["csrf_token"]


def _authorize_params(cid, challenge, redirect=CLAUDE, scope="read manage", state="st4te", **extra):
    params = {"response_type": "code", "client_id": cid, "redirect_uri": redirect,
              "code_challenge": challenge, "code_challenge_method": "S256",
              "scope": scope, "state": state, "resource": "http://localhost/mcp"}
    params.update(extra)
    return {k: v for k, v in params.items() if v is not None}


def _consent(web, cid, challenge, *, manage=True, decision="allow", **kw):
    """Open the consent page and answer it. Returns the redirect response."""
    csrf = _login(web)
    page = web.get("/oauth/authorize", query_string=_authorize_params(cid, challenge, **kw))
    assert page.status_code == 200, page.get_data(as_text=True)
    rid = re.search(r'name="request_id" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    form = {"csrf_token": csrf, "request_id": rid, "decision": decision}
    if manage:
        form["manage"] = "1"
    return web.post("/oauth/authorize", data=form)


def _code(web, cid, challenge, **kw):
    r = _consent(web, cid, challenge, **kw)
    assert r.status_code == 302, r.get_data(as_text=True)
    return parse_qs(urlsplit(r.headers["Location"]).query)["code"][0]


def _exchange(web, cid, code, verifier, redirect=CLAUDE, **extra):
    data = {"grant_type": "authorization_code", "code": code, "client_id": cid,
            "redirect_uri": redirect, "code_verifier": verifier, "resource": "http://localhost/mcp"}
    data.update(extra)
    return web.post("/oauth/token", data={k: v for k, v in data.items() if v is not None})


def _refresh(web, cid, refresh_token):
    return web.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh_token,
                                          "client_id": cid, "resource": "http://localhost/mcp"})


def _connected(web, **kw):
    """Register, consent, exchange: the whole dance. Returns (client_id, token answer)."""
    cid = _register(web).get_json()["client_id"]
    verifier, challenge = _pkce()
    code = _code(web, cid, challenge, **kw)
    r = _exchange(web, cid, code, verifier)
    assert r.status_code == 200, r.get_data(as_text=True)
    return cid, r.get_json()


def _mcp(web, token, method="tools/list", params=None):
    return web.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                    headers={"Authorization": f"Bearer {token}"})


# ── Discovery ─────────────────────────────────────────────────────────

def test_the_resource_names_its_authorization_server(web):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        r = web.get(path)
        assert r.status_code == 200
        meta = r.get_json()
        assert meta["resource"] == "http://localhost/mcp"
        assert meta["authorization_servers"] == ["http://localhost"]
        assert set(meta["scopes_supported"]) == {"read", "manage", "control", "photos", "secure", "updates"}
        assert r.headers.get("Access-Control-Allow-Origin") == "*"


def test_the_authorization_server_describes_itself(web):
    meta = web.get("/.well-known/oauth-authorization-server").get_json()
    assert meta["issuer"] == "http://localhost"
    assert meta["authorization_endpoint"] == "http://localhost/oauth/authorize"
    assert meta["token_endpoint"] == "http://localhost/oauth/token"
    assert meta["registration_endpoint"] == "http://localhost/oauth/register"
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["token_endpoint_auth_methods_supported"] == ["none"]
    assert meta["response_types_supported"] == ["code"]
    assert set(meta["grant_types_supported"]) == {"authorization_code", "refresh_token"}
    assert meta["authorization_response_iss_parameter_supported"] is True


def test_without_a_token_the_answer_points_to_the_metadata(web):
    r = web.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    header = r.headers["WWW-Authenticate"]
    assert header.startswith("Bearer ")
    assert 'resource_metadata="http://localhost/.well-known/oauth-protected-resource/mcp"' in header
    assert 'scope="read manage"' in header


# ── Registration ──────────────────────────────────────────────────────

@pytest.mark.parametrize("uri", [CLAUDE, "http://localhost:3118/callback", "http://127.0.0.1/callback"])
def test_https_and_loopback_redirects_can_be_registered(web, uri):
    r = _register(web, uris=[uri])
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body["client_id"] and body["redirect_uris"] == [uri]
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body


@pytest.mark.parametrize("uri", ["http://evil.example/cb", "javascript:alert(1)", "https://claude.ai/cb#frag",
                                 "https://user:pw@claude.ai/cb", "/relative", "", "https://"])
def test_other_redirects_are_refused(web, uri):
    r = _register(web, uris=[uri])
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_redirect_uri"


def test_registration_is_rate_limited(web):
    answers = [_register(web).status_code for _ in range(12)]
    assert answers[0] == 201 and answers[-1] == 429


def test_a_client_asking_for_other_grants_is_refused(web):
    r = web.post("/oauth/register", json={"redirect_uris": [CLAUDE], "grant_types": ["client_credentials"]})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_client_metadata"


# ── Authorization ─────────────────────────────────────────────────────

def test_the_whole_flow_gives_a_working_token(web):
    cid = _register(web).get_json()["client_id"]
    verifier, challenge = _pkce()
    _login(web)
    page = web.get("/oauth/authorize", query_string=_authorize_params(cid, challenge))
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Claude" in text and "claude.ai" in text          # who asks, and where the answer goes
    r = _consent(web, cid, challenge)
    assert r.status_code == 302
    target = urlsplit(r.headers["Location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == CLAUDE
    query = parse_qs(target.query)
    assert query["state"] == ["st4te"] and query["iss"] == ["http://localhost"]
    answer = _exchange(web, cid, query["code"][0], verifier)
    assert answer.status_code == 200, answer.get_data(as_text=True)
    tokens = answer.get_json()
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert tokens["refresh_token"] and tokens["access_token"] != tokens["refresh_token"]
    assert set(tokens["scope"].split()) == {"read", "manage"}
    assert answer.headers.get("Cache-Control") == "no-store"
    r = _mcp(web, tokens["access_token"])
    assert r.status_code == 200 and r.get_json()["result"]["tools"]


def test_an_unregistered_redirect_is_never_followed(web):
    cid = _register(web).get_json()["client_id"]
    _, challenge = _pkce()
    _login(web)
    for redirect in ("https://evil.example/cb", CLAUDE + "/x", CLAUDE + "?x=1"):
        r = web.get("/oauth/authorize", query_string=_authorize_params(cid, challenge, redirect=redirect))
        assert r.status_code == 400 and "Location" not in r.headers


def test_an_unknown_client_is_never_redirected(web):
    _, challenge = _pkce()
    _login(web)
    r = web.get("/oauth/authorize", query_string=_authorize_params("pbc_nope", challenge))
    assert r.status_code == 400 and "Location" not in r.headers


def test_loopback_redirects_may_use_any_port(web):
    cid = _register(web, uris=["http://localhost/callback"]).get_json()["client_id"]
    _, challenge = _pkce()
    r = _consent(web, cid, challenge, redirect="http://localhost:54321/callback")
    assert r.status_code == 302 and r.headers["Location"].startswith("http://localhost:54321/callback?")


@pytest.mark.parametrize("change", [{"code_challenge": None}, {"code_challenge_method": "plain"},
                                    {"code_challenge_method": None}, {"code_challenge": "short"}])
def test_pkce_with_s256_is_required(web, change):
    cid = _register(web).get_json()["client_id"]
    _, challenge = _pkce()
    _login(web)
    r = web.get("/oauth/authorize", query_string=_authorize_params(cid, challenge, **change))
    assert r.status_code == 302
    query = parse_qs(urlsplit(r.headers["Location"]).query)
    assert query["error"] == ["invalid_request"] and "code" not in query


def test_a_token_for_another_server_cannot_be_asked_for(web):
    cid = _register(web).get_json()["client_id"]
    _, challenge = _pkce()
    _login(web)
    r = web.get("/oauth/authorize", query_string=_authorize_params(cid, challenge,
                                                                   resource="https://other.example/mcp"))
    assert parse_qs(urlsplit(r.headers["Location"]).query)["error"] == ["invalid_target"]


def test_consent_needs_the_admin_login_and_comes_back_after_it(web, formular):
    from app import accounts, db
    db.set_setting("admin_username", "betreiber")
    accounts.set_password("Ein-langes-Kennwort-7319")
    db.set_setting("admin_totp_enabled", "")
    cid = _register(web).get_json()["client_id"]
    _, challenge = _pkce()
    r = web.get("/oauth/authorize", query_string=_authorize_params(cid, challenge))
    assert r.status_code == 302 and urlsplit(r.headers["Location"]).path == "/admin/login"
    r = formular("/admin/login", {"username": "betreiber", "password": "Ein-langes-Kennwort-7319"})
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/oauth/authorize?") and cid in r.headers["Location"]


def test_consent_is_given_only_by_post_with_the_form_token(web):
    cid = _register(web).get_json()["client_id"]
    _, challenge = _pkce()
    _login(web)
    page = web.get("/oauth/authorize", query_string=_authorize_params(cid, challenge))
    rid = re.search(r'name="request_id" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    r = web.post("/oauth/authorize", data={"request_id": rid, "decision": "allow", "csrf_token": "falsch"})
    assert r.status_code == 400 and "Location" not in r.headers
    r = web.get("/oauth/authorize", query_string={**_authorize_params(cid, challenge), "decision": "allow"})
    assert r.status_code == 200 and "Location" not in r.headers


def test_the_consent_page_cannot_be_framed(web):
    cid = _register(web).get_json()["client_id"]
    _, challenge = _pkce()
    _login(web)
    page = web.get("/oauth/authorize", query_string=_authorize_params(cid, challenge))
    assert page.headers["X-Frame-Options"] == "DENY"
    csp = page.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    # The redirect after the POST must be allowed, otherwise Chrome blocks it.
    assert "form-action 'self' https://claude.ai" in csp
    assert page.headers.get("Cache-Control") == "no-store"


def test_denying_sends_the_client_away_without_a_code(web):
    cid = _register(web).get_json()["client_id"]
    _, challenge = _pkce()
    r = _consent(web, cid, challenge, decision="deny")
    query = parse_qs(urlsplit(r.headers["Location"]).query)
    assert query["error"] == ["access_denied"] and "code" not in query


def test_unticking_manage_gives_a_read_only_token(web):
    cid = _register(web).get_json()["client_id"]
    verifier, challenge = _pkce()
    code = _code(web, cid, challenge, manage=False)
    tokens = _exchange(web, cid, code, verifier).get_json()
    assert tokens["scope"] == "read"


# ── Codes ─────────────────────────────────────────────────────────────

def test_a_code_works_once_and_its_reuse_locks_what_it_gave(web):
    cid = _register(web).get_json()["client_id"]
    verifier, challenge = _pkce()
    code = _code(web, cid, challenge)
    first = _exchange(web, cid, code, verifier)
    assert first.status_code == 200
    second = _exchange(web, cid, code, verifier)
    assert second.status_code == 400 and second.get_json()["error"] == "invalid_grant"
    # Somebody else had the code: what it gave the first time is withdrawn.
    assert _mcp(web, first.get_json()["access_token"]).status_code == 401
    assert _refresh(web, cid, first.get_json()["refresh_token"]).status_code == 400


def test_a_code_lives_a_minute(web):
    from app import db
    cid = _register(web).get_json()["client_id"]
    verifier, challenge = _pkce()
    code = _code(web, cid, challenge)
    con = db.get_db()
    con.execute("UPDATE oauth_codes SET expires_at = expires_at - 61")
    con.commit()
    con.close()
    r = _exchange(web, cid, code, verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


@pytest.mark.parametrize("wrong", ["verifier", "redirect", "client"])
def test_a_code_is_bound_to_client_redirect_and_verifier(web, wrong):
    cid = _register(web).get_json()["client_id"]
    other = _register(web).get_json()["client_id"]
    verifier, challenge = _pkce()
    code = _code(web, cid, challenge)
    if wrong == "verifier":
        r = _exchange(web, cid, code, _pkce()[0])
    elif wrong == "redirect":
        r = _exchange(web, cid, code, verifier, redirect="https://claude.ai/api/mcp/other")
    else:
        r = _exchange(web, other, code, verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


# ── Tokens ────────────────────────────────────────────────────────────

def test_refresh_tokens_rotate_and_a_reused_one_locks_the_family(web):
    cid, tokens = _connected(web)
    r = _refresh(web, cid, tokens["refresh_token"])
    assert r.status_code == 200, r.get_data(as_text=True)
    fresh = r.get_json()
    assert fresh["refresh_token"] != tokens["refresh_token"]
    assert _mcp(web, fresh["access_token"]).status_code == 200
    # The old one again: someone copied it. Everything from this consent goes.
    again = _refresh(web, cid, tokens["refresh_token"])
    assert again.status_code == 400 and again.get_json()["error"] == "invalid_grant"
    assert _mcp(web, fresh["access_token"]).status_code == 401
    assert _refresh(web, cid, fresh["refresh_token"]).status_code == 400


def test_tokens_and_codes_are_stored_only_as_hashes(web):
    from app import db
    cid = _register(web).get_json()["client_id"]
    verifier, challenge = _pkce()
    code = _code(web, cid, challenge)
    tokens = _exchange(web, cid, code, verifier).get_json()
    con = db.get_db()
    stored = " ".join(str(v) for table in ("oauth_tokens", "oauth_codes")
                      for row in con.execute(f"SELECT * FROM {table}") for v in tuple(row))
    con.close()
    for secret in (code, tokens["access_token"], tokens["refresh_token"]):
        assert secret not in stored


def test_a_token_minted_for_another_server_is_refused(web):
    from app import db
    _, tokens = _connected(web)
    con = db.get_db()
    con.execute("UPDATE oauth_tokens SET resource='https://other.example/mcp'")
    con.commit()
    con.close()
    assert _mcp(web, tokens["access_token"]).status_code == 401


def test_an_expired_access_token_is_refused(web):
    from app import db
    _, tokens = _connected(web)
    con = db.get_db()
    con.execute("UPDATE oauth_tokens SET expires_at = expires_at - 3601 WHERE kind='access'")
    con.commit()
    con.close()
    r = _mcp(web, tokens["access_token"])
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["WWW-Authenticate"]


def test_a_new_admin_password_locks_every_token(web):
    from app import accounts
    cid, tokens = _connected(web)
    accounts.set_password("Ein-neues-Kennwort-5521")
    assert _mcp(web, tokens["access_token"]).status_code == 401
    assert _refresh(web, cid, tokens["refresh_token"]).status_code == 400


def test_a_token_in_the_address_is_not_accepted(web):
    _, tokens = _connected(web)
    r = web.post(f"/mcp?access_token={tokens['access_token']}",
                 json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401


def test_the_token_endpoint_is_rate_limited(web):
    answers = [web.post("/oauth/token", data={"grant_type": "authorization_code", "code": "x",
                                              "client_id": "y", "code_verifier": "z"}).status_code
               for _ in range(62)]
    assert answers[0] in (400, 401) and answers[-1] == 429


def test_token_errors_say_nothing_about_the_inside(web):
    r = web.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "' OR 1=1 --"})
    body = r.get_json()
    assert r.status_code in (400, 401) and set(body) <= {"error", "error_description"}
    assert "Traceback" not in r.get_data(as_text=True) and "sqlite" not in r.get_data(as_text=True).lower()
    r = web.post("/oauth/token", data={"grant_type": "password", "username": "a", "password": "b"})
    assert r.get_json()["error"] == "unsupported_grant_type"


def test_an_unknown_client_is_told_so_and_can_register_again(web):
    """Claude registers anew when the token endpoint answers 401 invalid_client."""
    from app import oauth
    cid, tokens = _connected(web)
    oauth.revoke_client(cid)
    r = _refresh(web, cid, tokens["refresh_token"])
    assert r.status_code == 401 and r.get_json()["error"] == "invalid_client"


def test_revocation_endpoint_withdraws_the_family(web):
    cid, tokens = _connected(web)
    r = web.post("/oauth/revoke", data={"token": tokens["refresh_token"], "client_id": cid})
    assert r.status_code == 200
    assert _mcp(web, tokens["access_token"]).status_code == 401


# ── The transport around it ───────────────────────────────────────────

def test_a_request_from_a_foreign_web_page_is_refused(web):
    _, tokens = _connected(web)
    r = web.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                 headers={"Authorization": f"Bearer {tokens['access_token']}", "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_get_on_the_endpoint_offers_no_stream(web):
    r = web.get("/mcp", headers={"Accept": "text/event-stream"})
    assert r.status_code == 405


def test_a_modern_client_learns_which_versions_are_spoken(web):
    _, tokens = _connected(web)
    r = web.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                 headers={"Authorization": f"Bearer {tokens['access_token']}",
                          "MCP-Protocol-Version": "2099-01-01"})
    assert r.status_code == 400
    error = r.get_json()["error"]
    assert error["code"] == -32022 and "2025-11-25" in error["data"]["supported"]


def test_initialize_answers_with_a_version_both_sides_speak(web):
    _, tokens = _connected(web)
    for asked, expected in (("2025-06-18", "2025-06-18"), ("2025-11-25", "2025-11-25"),
                            ("1999-01-01", "2025-11-25")):
        r = _mcp(web, tokens["access_token"], "initialize",
                 {"protocolVersion": asked, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        assert r.get_json()["result"]["protocolVersion"] == expected


# ── Switch and admin ──────────────────────────────────────────────────

def test_the_switch_closes_the_door_for_everyone(web):
    from app import db
    _, tokens = _connected(web)
    db.set_setting("mcp_oauth_enabled", "0")
    assert _register(web).status_code == 403
    assert _mcp(web, tokens["access_token"]).status_code == 401


def test_the_admin_sees_connected_assistants_and_can_withdraw_them(web, formular):
    cid, tokens = _connected(web)
    _login(web)
    page = web.get("/admin/settings/assistants")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Claude" in body and "claude.ai" in body
    r = formular("/admin/settings/assistants", {"action": "revoke_client", "client_id": cid})
    assert r.status_code == 302
    assert _mcp(web, tokens["access_token"]).status_code == 401
    assert cid not in web.get("/admin/settings/assistants").get_data(as_text=True)


def test_the_admin_can_withdraw_everything_at_once(web, formular):
    _, first = _connected(web)
    _, second = _connected(web)
    _login(web)
    r = formular("/admin/settings/assistants", {"action": "revoke_all"})
    assert r.status_code == 302
    assert _mcp(web, first["access_token"]).status_code == 401
    assert _mcp(web, second["access_token"]).status_code == 401
