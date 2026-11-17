"""OAuth 2.1 for the MCP door, so an assistant like Claude connects without a pasted key.

Why it exists: a remote MCP connector in Claude (claude.ai "custom connector")
signs in with OAuth. The static key in ``app/mcp.py`` stays for scripts, but a
key copied into a chat window is the wrong way to hand someone the whole
administration. With OAuth the operator logs in (password and, if set up, the
second factor), sees who asks and where the answer goes, and decides.

Which standards, and which parts of them (MCP authorization, revision
2026-07-28, the subset Claude uses):

* Protected Resource Metadata (RFC 9728) at ``/.well-known/oauth-protected-resource``
  and the path-suffixed variant; a 401 from ``/mcp`` points there.
* Authorization Server Metadata (RFC 8414) at ``/.well-known/oauth-authorization-server``.
* Dynamic Client Registration (RFC 7591), public clients only. Client ID
  Metadata Documents are deliberately not supported: they make the gallery
  fetch URLs a stranger names (SSRF), and Claude falls back to registration.
* Authorization code with PKCE, S256 only, no exceptions.
* Resource indicators (RFC 8707): every token is bound to this gallery's
  ``/mcp`` address and refused anywhere else.
* ``iss`` in the authorization response (RFC 9207).
* Token revocation (RFC 7009) at ``/oauth/revoke``.

The rules this file keeps (and the tests in ``tests/test_mcp_oauth.py`` hold):

* Redirect URIs are compared exactly. Only loopback redirects may change the
  port (RFC 8252, 7.3) — Claude Code listens on a random one.
* An unknown client or redirect gets an error page, never a redirect.
* Consent is a POST with the admin form token, from a page that refuses to
  sit in a frame.
* A code lives 60 seconds and works once. A second use withdraws everything
  the first one gave.
* Access tokens live an hour. Refresh tokens rotate; presenting a rotated one
  withdraws the whole family (everything that came from one consent).
* Only hashes are stored. Codes and tokens never appear in a log or a URL
  this gallery builds (the code travels in the redirect, as OAuth requires).
* A new admin password ends every grant: tokens carry the session epoch of
  ``app/accounts.py`` they were issued under.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, Response, abort, g, jsonify, redirect, render_template, request, session, url_for

from app import db

bp = Blueprint("oauth", __name__)

#: ``read``: look (status, numbers, diagnostics, profiles, settings).
#: ``manage``: change things, send jobs to booths, create events, media, templates.
#: ``manage`` includes ``read``: a token that may change a profile may look at it.
#: The four after them are extra rights (15.11.2026). Each one must be ticked
#: by the admin, with the password typed again, and none includes another:
#: ``control`` drives a booth live, ``photos`` sees and deletes guest pictures,
#: ``secure`` changes what leads to guests or holds secrets (QR target, mail,
#: webhooks, PIN, keys, pairing), ``updates`` installs booth software.
SCOPES = ("read", "manage", "control", "photos", "secure", "updates")
BASE_SCOPES = ("read", "manage")
EXTRA_SCOPES = ("control", "photos", "secure", "updates")

ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
CODE_TTL = 60

#: An open registration endpoint is a table anyone can write to. These keep it
#: small: few registrations per address, a ceiling for all of them, and
#: clients nobody ever approved go after a day.
MAX_CLIENTS = 100
MAX_REDIRECT_URIS = 5
STALE_CLIENT_HOURS = 24
REGISTER_LIMIT = (10, 3600)
AUTHORIZE_LIMIT = (60, 900)
TOKEN_LIMIT = (60, 900)

#: "0" closes the OAuth door: no registration, no consent, no token works.
ENABLED_KEY = "mcp_oauth_enabled"

_LOOPBACK = ("localhost", "127.0.0.1", "::1")
_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")


# ── Schema ────────────────────────────────────────────────────

def init_schema(cur) -> None:
    """Called from ``ecosystem.init_schema``. Additive only."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS oauth_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            redirect_uris TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            consented_at TEXT,
            last_used_at TEXT
        );""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS oauth_codes (
            code_hash TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            challenge TEXT NOT NULL,
            scope TEXT NOT NULL,
            resource TEXT NOT NULL,
            family TEXT NOT NULL,
            epoch TEXT NOT NULL DEFAULT '',
            expires_at REAL NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        );""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            client_id TEXT NOT NULL,
            family TEXT NOT NULL,
            scope TEXT NOT NULL,
            resource TEXT NOT NULL,
            epoch TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at REAL NOT NULL,
            rotated_at REAL,
            revoked_at TEXT,
            revoked_reason TEXT
        );""")
    cur.execute("CREATE INDEX IF NOT EXISTS oauth_tokens_family ON oauth_tokens(family);")
    cur.execute("CREATE INDEX IF NOT EXISTS oauth_tokens_client ON oauth_tokens(client_id);")


# ── Small helpers ─────────────────────────────────────────────

def enabled() -> bool:
    return (db.get_setting(ENABLED_KEY) or "1") != "0"


def _hash(value: str) -> str:
    # Codes and tokens carry 256 bits of randomness; a plain SHA-256 is enough
    # to make a stolen database useless without making lookups slow.
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _epoch() -> str:
    from app import accounts
    return accounts.session_epoch()


def _canonical(url: str) -> str:
    """Lower-case scheme and host, no trailing slash, no fragment."""
    parts = urlsplit(str(url or "").strip())
    if not parts.scheme or not parts.netloc:
        return ""
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def issuer() -> str:
    """This gallery as authorization server: its public address, no path.

    The configured address wins over the one the request came in on, for the
    same reason QR codes use it: a gallery reachable under two names must
    hand out tokens for one of them.
    """
    return _canonical(db.base_url() or request.host_url)


def resource_uri() -> str:
    return issuer() + "/mcp"


def resource_metadata_url() -> str:
    return issuer() + "/.well-known/oauth-protected-resource/mcp"


def challenge_header(error: str = "", scope: str = "read manage", description: str = "") -> str:
    """The ``WWW-Authenticate`` value for 401 and 403 answers of ``/mcp``."""
    parts = [f'resource_metadata="{resource_metadata_url()}"', f'scope="{scope}"']
    if error:
        parts.append(f'error="{error}"')
    if description:
        parts.append(f'error_description="{description}"')
    return "Bearer " + ", ".join(parts)


def _client_ip() -> str:
    return request.remote_addr or "unknown"


def _too_often(door: str, limit: Tuple[int, int]) -> bool:
    from app import sharelinks
    return sharelinks.too_often(f"oauth-{door}:{_client_ip()}", limit[0], limit[1])


def _clean_text(value: Any, limit: int) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isprintable())
    return text.strip()[:limit]


def parse_scope(raw: Any) -> Set[str]:
    return {s for s in str(raw or "").split() if s}


def scope_text(scopes: Iterable[str]) -> str:
    return " ".join(s for s in SCOPES if s in set(scopes))


# ── Redirect URIs ─────────────────────────────────────────────

def valid_redirect_uri(uri: Any) -> bool:
    """https anywhere, plain http only to this computer (RFC 8252 loopback)."""
    if not isinstance(uri, str) or not uri or len(uri) > 500:
        return False
    if any(ch in uri for ch in "#\\ \t\r\n") or not uri.isprintable():
        return False
    try:
        parts = urlsplit(uri)
        parts.port                                # raises on a port that is not a number
    except ValueError:
        return False
    if parts.username or parts.password or not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in _LOOPBACK


def redirect_matches(registered: Iterable[str], sent: str) -> bool:
    """Exact string comparison; for loopback redirects the port may differ."""
    if not isinstance(sent, str) or not sent:
        return False
    for uri in registered:
        if hmac.compare_digest(uri, sent):
            return True
        try:
            a, b = urlsplit(uri), urlsplit(sent)
            if not (a.scheme == b.scheme == "http" and a.hostname in _LOOPBACK):
                continue
            b.port                                # a broken port is no match
        except ValueError:
            continue
        if (a.hostname == b.hostname and a.path == b.path and a.query == b.query
                and not b.fragment and not b.username and not b.password):
            return True
    return False


def _origin_of(uri: str) -> str:
    parts = urlsplit(uri)
    return f"{parts.scheme}://{parts.netloc}"


# ── Clients ───────────────────────────────────────────────────

def get_client(client_id: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(client_id, str) or not client_id or len(client_id) > 100:
        return None
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    client = dict(row)
    try:
        client["redirect_uris"] = [u for u in json.loads(client["redirect_uris"] or "[]") if isinstance(u, str)]
    except ValueError:
        client["redirect_uris"] = []
    return client


def _purge_stale_clients(con) -> None:
    # Registered but never approved: nothing hangs on them, nobody misses them.
    con.execute("DELETE FROM oauth_clients WHERE consented_at IS NULL AND created_at < datetime('now', ?)",
                (f"-{STALE_CLIENT_HOURS} hours",))
    con.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (time.time() - 86400,))
    con.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (time.time() - 7 * 86400,))


def list_clients() -> List[Dict[str, Any]]:
    """For the admin page: every client with what it may do and when it was last seen."""
    epoch, now = _epoch(), time.time()
    con = db.get_db()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM oauth_clients ORDER BY id DESC")]
        for client in rows:
            try:
                uris = json.loads(client["redirect_uris"] or "[]")
            except ValueError:
                uris = []
            client["redirect_uris"] = uris
            client["redirect_hosts"] = sorted({urlsplit(u).netloc for u in uris})
            live = con.execute(
                "SELECT scope FROM oauth_tokens WHERE client_id=? AND kind='refresh' AND revoked_at IS NULL "
                "AND rotated_at IS NULL AND expires_at > ? AND epoch=?",
                (client["client_id"], now, epoch)).fetchall()
            granted: Set[str] = set()
            for r in live:
                granted |= parse_scope(r["scope"])
            client["scopes"] = scope_text(granted)
            client["active"] = bool(live)
    finally:
        con.close()
    return rows


def revoke_family(family: str, reason: str, con=None) -> None:
    own = con is None
    con = con or db.get_db()
    try:
        con.execute("UPDATE oauth_tokens SET revoked_at=datetime('now'), revoked_reason=? "
                    "WHERE family=? AND revoked_at IS NULL", (reason[:80], family))
        con.execute("UPDATE oauth_codes SET used=1 WHERE family=?", (family,))
        if own:
            con.commit()
    finally:
        if own:
            con.close()


def revoke_client(client_id: str) -> bool:
    """Withdraw every token of a client and forget the client itself."""
    con = db.get_db()
    try:
        con.execute("UPDATE oauth_tokens SET revoked_at=datetime('now'), revoked_reason='withdrawn by admin' "
                    "WHERE client_id=? AND revoked_at IS NULL", (client_id,))
        con.execute("DELETE FROM oauth_codes WHERE client_id=?", (client_id,))
        cur = con.execute("DELETE FROM oauth_clients WHERE client_id=?", (client_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def withdraw_extras(client_id: str, scopes: Optional[Iterable[str]] = None) -> int:
    """Take extra rights back from one client without ending its access.

    ``scopes`` names the rights to take (default: all four). Access and
    refresh tokens keep working with what is left; a refresh can never ask
    for more than its token carries, so the right does not come back.
    """
    take = set(scopes or EXTRA_SCOPES) & set(EXTRA_SCOPES)
    con = db.get_db()
    try:
        rows = con.execute("SELECT token_hash, scope FROM oauth_tokens WHERE client_id=? AND revoked_at IS NULL",
                           (client_id,)).fetchall()
        n = 0
        for r in rows:
            before = parse_scope(r["scope"])
            after = before - take
            if after != before:
                con.execute("UPDATE oauth_tokens SET scope=? WHERE token_hash=?", (scope_text(after), r["token_hash"]))
                n += 1
        con.commit()
        return n
    finally:
        con.close()


def revoke_all() -> int:
    con = db.get_db()
    try:
        cur = con.execute("UPDATE oauth_tokens SET revoked_at=datetime('now'), revoked_reason='all withdrawn' "
                          "WHERE revoked_at IS NULL")
        con.execute("DELETE FROM oauth_codes")
        con.commit()
        return int(cur.rowcount or 0)
    finally:
        con.close()


# ── Checking an access token (used by app/mcp.py) ─────────────

def check_access_token(raw: str) -> Optional[Dict[str, Any]]:
    """Who stands behind a bearer token, or ``None``.

    Valid means: issued here, an access token, not withdrawn, not expired,
    issued under the current admin password, and for this very ``/mcp``.
    """
    if not raw or len(raw) > 200 or not enabled():
        return None
    con = db.get_db()
    try:
        row = con.execute(
            "SELECT t.*, c.name AS client_name, c.last_used_at AS client_seen FROM oauth_tokens t "
            "JOIN oauth_clients c ON c.client_id = t.client_id WHERE t.token_hash=? AND t.kind='access'",
            (_hash(raw),)).fetchone()
        if not row:
            return None
        if row["revoked_at"] or row["expires_at"] <= time.time():
            return None
        if not hmac.compare_digest(str(row["epoch"] or ""), _epoch()):
            return None
        if _canonical(row["resource"]) != resource_uri():
            return None
        # "Last seen" for the admin page; once a minute is plenty.
        con.execute("UPDATE oauth_clients SET last_used_at=datetime('now') WHERE client_id=? AND "
                    "(last_used_at IS NULL OR last_used_at < datetime('now', '-1 minute'))", (row["client_id"],))
        con.commit()
        return {"kind": "oauth", "client_id": row["client_id"], "client_name": row["client_name"],
                "scopes": parse_scope(row["scope"]), "family": row["family"]}
    finally:
        con.close()


# ── Issuing ───────────────────────────────────────────────────

def _issue(con, client_id: str, family: str, scope: str, resource: str, epoch: str) -> Dict[str, Any]:
    access, refresh = "pba_" + secrets.token_urlsafe(32), "pbr_" + secrets.token_urlsafe(32)
    now = time.time()
    for raw, kind, ttl in ((access, "access", ACCESS_TTL), (refresh, "refresh", REFRESH_TTL)):
        con.execute("INSERT INTO oauth_tokens (token_hash, kind, client_id, family, scope, resource, epoch, "
                    "expires_at) VALUES (?,?,?,?,?,?,?,?)",
                    (_hash(raw), kind, client_id, family, scope, resource, epoch, now + ttl))
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL,
            "refresh_token": refresh, "scope": scope}


def _token_answer(body: Dict[str, Any], status: int = 200) -> Response:
    resp = jsonify(body)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _token_error(error: str, description: str = "", status: int = 400) -> Response:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return _token_answer(body, status)


# ── Metadata ──────────────────────────────────────────────────

def _public_json(body: Dict[str, Any]) -> Response:
    # Public documents. A browser-based client (the MCP inspector) may read
    # them; nothing else on this blueprint answers cross-origin.
    resp = jsonify(body)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "max-age=300"
    return resp


@bp.get("/.well-known/oauth-protected-resource")
@bp.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata():
    return _public_json({
        "resource": resource_uri(),
        "authorization_servers": [issuer()],
        "scopes_supported": list(SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "Photobox Gallery",
    })


@bp.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    base = issuer()
    return _public_json({
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register",
        "revocation_endpoint": base + "/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(SCOPES),
        "authorization_response_iss_parameter_supported": True,
        "client_id_metadata_document_supported": False,
    })


# ── Registration (RFC 7591) ───────────────────────────────────

@bp.post("/oauth/register")
def register():
    if not enabled():
        return _token_error("access_denied", "Connections are switched off for this gallery.", 403)
    if _too_often("register", REGISTER_LIMIT):
        return _token_error("temporarily_unavailable", "Too many registrations, try again later.", 429)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _token_error("invalid_client_metadata", "A JSON object is expected.")
    uris = body.get("redirect_uris")
    if (not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECT_URIS
            or not all(valid_redirect_uri(u) for u in uris)):
        return _token_error("invalid_redirect_uri",
                            "Redirect URIs must use https, or http to localhost / 127.0.0.1.")
    grants = body.get("grant_types") or ["authorization_code", "refresh_token"]
    responses = body.get("response_types") or ["code"]
    if (not isinstance(grants, list) or not set(grants) <= {"authorization_code", "refresh_token"}
            or not isinstance(responses, list) or set(responses) != {"code"}):
        return _token_error("invalid_client_metadata",
                            "Only the authorization code flow with refresh tokens is offered.")
    # Public clients only: a secret a desktop app or a chat service keeps is
    # no secret here, PKCE does that job. Whatever was asked, "none" is given.
    name = _clean_text(body.get("client_name"), 80) or "Unnamed assistant"
    client_id = "pbc_" + secrets.token_urlsafe(18)
    con = db.get_db()
    try:
        _purge_stale_clients(con)
        count = con.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0]
        if count >= MAX_CLIENTS:
            con.commit()
            return _token_error("temporarily_unavailable", "Too many registered clients.", 429)
        con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris) VALUES (?,?,?)",
                    (client_id, name, json.dumps(list(uris))))
        con.commit()
    finally:
        con.close()
    return _token_answer({
        "client_id": client_id, "client_id_issued_at": int(time.time()), "client_name": name,
        "redirect_uris": list(uris), "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none",
    }, 201)


# ── Authorization ─────────────────────────────────────────────

@bp.before_request
def _language():
    g.lang = session.get("lang", "de")


def _error_page(message_key: str, status: int = 400) -> Response:
    resp = Response(render_template("oauth/error.html", message_key=message_key), status=status)
    return _harden(resp)


def _harden(resp: Response, form_target: str = "") -> Response:
    """No framing, no caching, and a form that may only end up where it should."""
    from app import security_headers
    policy = security_headers.policy("'none'")
    if form_target:
        policy = policy.replace("form-action 'self'", f"form-action 'self' {form_target}")
    resp.headers["Content-Security-Policy"] = policy
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _back(redirect_uri: str, **params: str) -> Response:
    """Send the browser back to the client, with ``iss`` (RFC 9207)."""
    params = {k: v for k, v in params.items() if v}
    params["iss"] = issuer()
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    resp = redirect(target, 302)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@bp.get("/oauth/authorize")
def authorize():
    if _too_often("authorize", AUTHORIZE_LIMIT):
        return _error_page("oauth.error.too_often", 429)
    if not enabled():
        return _error_page("oauth.error.disabled", 403)
    args = request.args
    client = get_client(args.get("client_id"))
    redirect_uri = args.get("redirect_uri") or ""
    # Until client and redirect are known to belong together, nothing is
    # sent anywhere: an error page here is the whole point (no open redirect).
    if not client or not redirect_matches(client["redirect_uris"], redirect_uri):
        return _error_page("oauth.error.unknown_client", 400)
    state = str(args.get("state") or "")[:500]
    if args.get("response_type") != "code":
        return _back(redirect_uri, error="unsupported_response_type", state=state)
    challenge = str(args.get("code_challenge") or "")
    if args.get("code_challenge_method") != "S256" or not _CHALLENGE.match(challenge):
        return _back(redirect_uri, error="invalid_request",
                     error_description="PKCE with S256 is required", state=state)
    asked = parse_scope(args.get("scope"))
    scopes = (asked & set(SCOPES)) if asked else set(SCOPES)
    if not scopes:
        return _back(redirect_uri, error="invalid_scope", state=state)
    resource = args.get("resource")
    if resource and _canonical(resource) != resource_uri():
        return _back(redirect_uri, error="invalid_target", state=state)

    if not session.get("admin_logged_in"):
        # The admin login does the asking (password, second factor, brakes)
        # and comes back here afterwards; see ``admin.login``.
        session["after_login"] = request.full_path
        return redirect(url_for("admin.login"))

    request_id = secrets.token_urlsafe(16)
    pending = dict(session.get("oauth_pending") or {})
    while len(pending) >= 3:                       # an abandoned tab must not grow the cookie
        pending.pop(next(iter(pending)))
    pending[request_id] = {"client_id": client["client_id"], "redirect_uri": redirect_uri,
                           "challenge": challenge, "scope": scope_text(scopes), "state": state,
                           "resource": resource_uri(), "at": int(time.time())}
    session["oauth_pending"] = pending
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(24)
    return _consent_page(client, request_id, redirect_uri, scopes)


def _consent_page(client: Dict[str, Any], request_id: str, redirect_uri: str, scopes: Set[str],
                  error: str = "", ticked: Iterable[str] = ()) -> Response:
    """The consent form. The extra rights are always offered and never ticked in advance.

    A client usually asks for ``read manage`` (that is what the 401 challenge
    names); the owner may still give it more — RFC 6749 3.3 lets the server
    grant another scope than asked, and the token answer says which.
    """
    target = urlsplit(redirect_uri)
    page = render_template("oauth/consent.html", client=client, request_id=request_id,
                           redirect_host=target.netloc, loopback=target.hostname in _LOOPBACK,
                           scopes=scopes, extras=EXTRA_SCOPES, ticked=set(ticked), error=error,
                           csrf_token=session["csrf_token"])
    return _harden(Response(page), form_target=_origin_of(redirect_uri))


def extras_from_form(form) -> Set[str]:
    return {s for s in EXTRA_SCOPES if form.get("scope_" + s)}


def reauthenticate(password: str, door: str = "reauth") -> Tuple[bool, str]:
    """The admin password once more, before an extra right is given.

    Same check as the login, with its own brake (5 failures in 15 minutes),
    so a stolen browser session cannot simply try passwords here.
    Returns (ok, message for the page).
    """
    from app import sharelinks
    from app.blueprints.admin import _check_password
    key = f"{door}:{_client_ip()}"
    wait = sharelinks.retry_after(key, 5, 900)
    if wait:
        return False, sharelinks.wait_message(wait)
    stored = db.get_setting("admin_password_hash") or ""
    if password and stored and _check_password(str(password), stored):
        sharelinks.forget(key)
        return True, ""
    sharelinks.record_failure(key, 900)
    return False, "Das Passwort stimmt nicht. Zusatzrechte gibt es nur mit dem Admin-Passwort."


def note_grant(who_gets: str, extras: Iterable[str], action: str = "Zusatzrechte erteilt") -> None:
    """One line in the admin change log: who gave which extra right to whom."""
    from app import auth, protokoll
    try:
        user = auth.admin_user()
    except Exception:                               # noqa: BLE001
        user = "admin"
    protokoll.notiere(wer=user or "admin", ip=request.remote_addr or "", methode=request.method,
                      pfad=request.path, was=f"{action}: {who_gets}"[:200],
                      ergebnis=", ".join(sorted(extras)) or "keine",
                      aenderungen=[{"gegenstand": f"Zugang {who_gets}"[:200], "feld": "Rechte",
                                    "vorher": None, "nachher": " ".join(sorted(extras))}])


@bp.post("/oauth/authorize")
def authorize_decide():
    if _too_often("authorize", AUTHORIZE_LIMIT):
        return _error_page("oauth.error.too_often", 429)
    sent = str(request.form.get("csrf_token") or "")
    expected = str(session.get("csrf_token") or "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400)
    if not session.get("admin_logged_in"):
        return _error_page("oauth.error.expired", 403)
    pending = dict(session.get("oauth_pending") or {})
    req = pending.pop(str(request.form.get("request_id") or ""), None)
    session["oauth_pending"] = pending
    if not req or req["at"] < time.time() - 600 or not enabled():
        return _error_page("oauth.error.expired", 400)
    client = get_client(req["client_id"])
    if not client or not redirect_matches(client["redirect_uris"], req["redirect_uri"]):
        return _error_page("oauth.error.unknown_client", 400)
    if request.form.get("decision") != "allow":
        return _back(req["redirect_uri"], error="access_denied", state=req["state"])
    granted = {"read"}
    if request.form.get("manage") and "manage" in parse_scope(req["scope"]):
        granted.add("manage")
    extras = extras_from_form(request.form)
    if extras:
        ok, message = reauthenticate(str(request.form.get("password") or ""))
        if not ok:
            # Nothing granted; the same request stays open for a second try.
            pending[str(request.form.get("request_id") or "")] = req
            session["oauth_pending"] = pending
            return _consent_page(client, str(request.form.get("request_id") or ""), req["redirect_uri"],
                                 parse_scope(req["scope"]), error=message, ticked=extras)
        granted |= extras
        note_grant(f"Assistent „{client['name'] or client['client_id']}“", extras)
    code = secrets.token_urlsafe(32)
    con = db.get_db()
    try:
        con.execute("INSERT INTO oauth_codes (code_hash, client_id, redirect_uri, challenge, scope, resource, "
                    "family, epoch, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (_hash(code), client["client_id"], req["redirect_uri"], req["challenge"],
                     scope_text(granted), req["resource"], secrets.token_hex(12), _epoch(),
                     time.time() + CODE_TTL))
        con.execute("UPDATE oauth_clients SET consented_at=datetime('now') WHERE client_id=?",
                    (client["client_id"],))
        con.commit()
    finally:
        con.close()
    return _back(req["redirect_uri"], code=code, state=req["state"])


# ── Token endpoint ────────────────────────────────────────────

def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not _VERIFIER.match(verifier or ""):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, challenge or "")


@bp.post("/oauth/token")
def token():
    if _too_often("token", TOKEN_LIMIT):
        return _token_error("temporarily_unavailable", "Too many requests, try again later.", 429)
    if not enabled():
        return _token_error("invalid_grant", "Connections are switched off for this gallery.")
    form = request.form
    grant = form.get("grant_type")
    if grant in ("authorization_code", "refresh_token") and not get_client(form.get("client_id")):
        # Claude registers again when it reads invalid_client with 401 — the
        # way back after the admin removed it here.
        return _token_error("invalid_client", "Unknown client.", 401)
    if grant == "authorization_code":
        return _grant_code(form)
    if grant == "refresh_token":
        return _grant_refresh(form)
    return _token_error("unsupported_grant_type")


def _grant_code(form) -> Response:
    code = str(form.get("code") or "")
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM oauth_codes WHERE code_hash=?", (_hash(code),)).fetchone() if code else None
        if not row:
            return _token_error("invalid_grant")
        # Burn it before anything else: a second attempt — even after a wrong
        # verifier — is the sign that the code is in other hands.
        if con.execute("UPDATE oauth_codes SET used=1 WHERE code_hash=? AND used=0",
                       (row["code_hash"],)).rowcount == 0:
            revoke_family(row["family"], "authorization code reused", con)
            con.commit()
            return _token_error("invalid_grant")
        con.commit()
        if row["expires_at"] < time.time():
            return _token_error("invalid_grant", "The code has expired.")
        if not hmac.compare_digest(str(form.get("client_id") or ""), row["client_id"]):
            return _token_error("invalid_grant")
        if not hmac.compare_digest(str(form.get("redirect_uri") or ""), row["redirect_uri"]):
            return _token_error("invalid_grant")
        if not _pkce_ok(str(form.get("code_verifier") or ""), row["challenge"]):
            return _token_error("invalid_grant")
        resource = form.get("resource")
        if resource and _canonical(resource) != _canonical(row["resource"]):
            return _token_error("invalid_target")
        if not hmac.compare_digest(str(row["epoch"] or ""), _epoch()) or not get_client(row["client_id"]):
            return _token_error("invalid_grant")
        answer = _issue(con, row["client_id"], row["family"], row["scope"], row["resource"], row["epoch"])
        con.commit()
        return _token_answer(answer)
    finally:
        con.close()


def _grant_refresh(form) -> Response:
    raw = str(form.get("refresh_token") or "")
    con = db.get_db()
    try:
        row = con.execute("SELECT * FROM oauth_tokens WHERE token_hash=? AND kind='refresh'",
                          (_hash(raw),)).fetchone() if raw and len(raw) <= 200 else None
        if not row or row["revoked_at"]:
            return _token_error("invalid_grant")
        if not hmac.compare_digest(str(form.get("client_id") or ""), row["client_id"]):
            return _token_error("invalid_grant")
        if con.execute("UPDATE oauth_tokens SET rotated_at=? WHERE id=? AND rotated_at IS NULL",
                       (time.time(), row["id"])).rowcount == 0:
            # Rotated already, and here it is again: one of two holders is not
            # the client. Which one cannot be told, so both lose it.
            revoke_family(row["family"], "refresh token reused", con)
            con.commit()
            return _token_error("invalid_grant")
        expired = row["expires_at"] <= time.time()
        stale = not hmac.compare_digest(str(row["epoch"] or ""), _epoch())
        if expired or stale or not get_client(row["client_id"]):
            revoke_family(row["family"], "expired" if expired else "admin password changed", con)
            con.commit()
            return _token_error("invalid_grant")
        granted = parse_scope(row["scope"])
        asked = parse_scope(form.get("scope"))
        if asked and not asked <= granted:
            con.rollback()
            return _token_error("invalid_scope")
        resource = form.get("resource")
        if resource and _canonical(resource) != _canonical(row["resource"]):
            con.rollback()
            return _token_error("invalid_target")
        # The access tokens of the old pair go with it.
        con.execute("UPDATE oauth_tokens SET revoked_at=datetime('now'), revoked_reason='rotated' "
                    "WHERE family=? AND kind='access' AND revoked_at IS NULL", (row["family"],))
        answer = _issue(con, row["client_id"], row["family"], scope_text(asked or granted),
                        row["resource"], row["epoch"])
        con.commit()
        return _token_answer(answer)
    finally:
        con.close()


@bp.post("/oauth/revoke")
def revoke():
    """RFC 7009: the answer is the same whether the token was known or not."""
    if _too_often("token", TOKEN_LIMIT):
        return _token_error("temporarily_unavailable", status=429)
    raw = str(request.form.get("token") or "")
    if raw and len(raw) <= 200:
        con = db.get_db()
        try:
            row = con.execute("SELECT family, client_id FROM oauth_tokens WHERE token_hash=?",
                              (_hash(raw),)).fetchone()
            client_id = str(request.form.get("client_id") or "")
            if row and (not client_id or hmac.compare_digest(client_id, row["client_id"])):
                revoke_family(row["family"], "revoked by client", con)
                con.commit()
        finally:
            con.close()
    return _token_answer({})


__all__ = ["SCOPES", "bp", "challenge_header", "check_access_token", "enabled", "init_schema",
           "issuer", "list_clients", "parse_scope", "redirect_matches", "resource_metadata_url",
           "resource_uri", "revoke_all", "revoke_client", "revoke_family", "valid_redirect_uri"]
