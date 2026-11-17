"""Client access: one code opens every gallery assigned to one client.

The rules (business_logic/current/models/galerie-sichtbarkeit-und-zugang.md,
"Kundenzugang"): the code replaces the gallery password of the assigned
galleries and nothing else; it is shown once and stored as a hash; guessing
is braked; the client's session is its own and ends on logout, inactivity,
blocking and a new code.
"""
import json
import re
import secrets
import time

import pytest
from PIL import Image

from app import clients, db, ecosystem, gallery_settings as gsm, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    db.set_setting("silent_mode", "0")
    yield


def _event(password="geheim1234", published=1, **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Kunde {k}", slug=f"kd-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": published, "gallery_json": json.dumps(gsm.clean(gs))})
    if password:
        db.set_event_password(eid, password)
    return db.get_event_by_id(eid)


def _photo(event):
    d = media.event_storage_dir(event["slug"])
    stem = secrets.token_hex(5)
    orig, webp, thumb = d / "orig" / f"{stem}.jpg", d / "webp" / f"{stem}.webp", d / "thumb" / f"{stem}.webp"
    Image.new("RGB", (40, 30)).save(orig, "JPEG")
    Image.new("RGB", (40, 30)).save(webp, "WEBP")
    Image.new("RGB", (20, 15)).save(thumb, "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
        " share_token) VALUES (?,?,?,?,?,?,?,?,?)",
        (event["id"], "photo", f"IMG_{stem}.jpg", rel(orig), rel(webp), rel(thumb), 1, stem, secrets.token_hex(8)))
    con.commit()
    con.close()
    return {"id": cur.lastrowid, "thumb": "/data/" + rel(thumb)}


def _client_with(*events, name="Familie Meier"):
    cid = clients.create(name)
    code = clients.new_code(cid)
    clients.set_events(cid, [e["id"] for e in events])
    return cid, code


def _csrf(client, path="/kunde"):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)


def _login(client, code, path="/kunde"):
    return client.post(path, data={"code": code, "csrf_token": _csrf(client, path)})


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    from app import accounts
    with client.session_transaction() as s:
        s["admin_epoch"] = accounts.session_epoch()


# ── The code ──────────────────────────────────────────────────

def test_the_code_is_stored_only_as_a_hash(client):
    cid, code = _client_with()
    row = db.get_db().execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    assert code.replace("-", "") not in json.dumps(dict(row))
    assert clients.find_by_code(code)["id"] == cid
    assert clients.find_by_code(code.upper().replace("-", " "))["id"] == cid


def test_the_admin_sees_a_new_code_once_and_never_again(client):
    _admin(client)
    cid = clients.create("Schule Musterstadt")
    answer = client.post(f"/admin/clients/{cid}/code", data={"csrf_token": "test-token"})
    assert answer.status_code == 200
    shown = re.search(r'id="neuerCode">([a-z0-9-]+)<', answer.get_data(as_text=True)).group(1)
    assert clients.find_by_code(shown)["id"] == cid
    assert shown not in client.get(f"/admin/clients/{cid}").get_data(as_text=True)
    with client.session_transaction() as s:
        assert shown not in json.dumps(dict(s), default=str)


def test_wrong_codes_are_braked(client):
    for _ in range(clients.BRAKE_LIMIT):
        assert _login(client, "aaaa-bbbb-cccc").status_code == 401
    assert _login(client, "aaaa-bbbb-cccc").status_code == 429


def test_a_right_code_does_not_count_against_the_brake(client):
    _, code = _client_with()
    for _ in range(clients.BRAKE_LIMIT + 3):
        assert _login(client, code).status_code == 302


# ── The overview ──────────────────────────────────────────────

def test_the_overview_lists_only_the_clients_published_galleries(client):
    mine, hidden, other = _event(), _event(published=0), _event()
    _photo(mine)
    _, code = _client_with(mine, hidden)
    assert _login(client, code).status_code == 302
    page = client.get("/kunde").get_data(as_text=True)
    assert mine["name"] in page
    assert hidden["name"] not in page and other["name"] not in page
    assert client.get("/client").status_code == 200


def test_the_overview_counts_what_is_new_since_the_last_visit(client):
    ev = _event()
    _photo(ev)
    _, code = _client_with(ev)
    _login(client, code)
    assert client.get(f"/gallery/{ev['slug']}").status_code == 200
    con = db.get_db()
    con.execute("UPDATE client_events SET last_seen_at=datetime('now','-1 hour') WHERE event_id=?", (ev["id"],))
    con.execute("UPDATE media SET created_at=datetime('now','-2 hours') WHERE event_id=?", (ev["id"],))
    con.commit(); con.close()
    _photo(ev)
    tiles = clients.overview(clients.find_by_code(code)["id"])
    assert tiles[0]["count"] == 2 and tiles[0]["new"] == 1


# ── The key and nothing more ──────────────────────────────────

def test_the_code_opens_assigned_galleries_without_their_password(client):
    mine, foreign = _event(), _event()
    p = _photo(mine)
    _, code = _client_with(mine)
    _login(client, code)
    assert client.get(f"/gallery/{mine['slug']}").status_code == 200
    assert client.get(p["thumb"]).status_code == 200
    assert 'name="password"' in client.get(f"/gallery/{foreign['slug']}").get_data(as_text=True)


def test_the_code_changes_nothing_about_expiry_or_silent_mode(client):
    expired = _event(expires_at="2000-01-01T00:00")
    silent = _event(silent=True)
    _, code = _client_with(expired, silent)
    _login(client, code)
    assert client.get(f"/gallery/{expired['slug']}").status_code == 410
    assert client.get(f"/gallery/{silent['slug']}").status_code == 404
    assert silent["name"] not in client.get("/kunde").get_data(as_text=True)


def test_the_code_does_not_open_the_zip_when_the_switch_is_off(client):
    ev = _event()
    _photo(ev)
    _, code = _client_with(ev)
    _login(client, code)
    assert client.get(f"/gallery/{ev['slug']}/download.zip").status_code == 403


# ── The session ───────────────────────────────────────────────

def test_logout_ends_the_client_session_and_leaves_the_admin_alone(client):
    ev = _event()
    _, code = _client_with(ev)
    _admin(client)
    _login(client, code)
    client.post("/kunde/abmelden", data={"csrf_token": _csrf(client)})
    with client.session_transaction() as s:
        assert "client" not in s and s.get("admin_logged_in") is True


def test_an_admin_login_is_no_client_login(client):
    _admin(client)
    assert 'name="code"' in client.get("/kunde").get_data(as_text=True)


def test_the_session_ends_after_inactivity(client):
    ev = _event()
    _, code = _client_with(ev)
    _login(client, code)
    with client.session_transaction() as s:
        s["client"] = {**s["client"], "t": int(time.time()) - clients.idle_seconds() - 5}
    assert 'name="password"' in client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)


def test_blocking_or_a_new_code_ends_existing_sessions(client):
    ev = _event()
    cid, code = _client_with(ev)
    _login(client, code)
    clients.new_code(cid)
    assert 'name="password"' in client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    code2 = clients.new_code(cid)
    _login(client, code2)
    clients.update(cid, active=False)
    assert 'name="password"' in client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert _login(client, code2).status_code == 401


def test_the_login_form_needs_the_form_token(client):
    _, code = _client_with()
    assert client.post("/kunde", data={"code": code}).status_code == 400


# ── Admin ─────────────────────────────────────────────────────

def test_the_admin_assigns_galleries_from_the_event_page(client, formular):
    ev = _event()
    _admin(client)
    cid = clients.create("Firma Nord")
    answer = client.post(f"/admin/events/{ev['id']}/clients",
                         data={"csrf_token": "test-token", "client_ids": [str(cid)]})
    assert answer.status_code == 302
    assert clients.event_ids(cid) == [ev["id"]]
    assert "Firma Nord" in client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)


def test_client_pages_need_the_admin(client):
    assert client.get("/admin/clients").status_code == 302
    assert client.post("/admin/clients", data={"name": "x"}).status_code == 302


# ── MCP ───────────────────────────────────────────────────────

def _mcp(client, name, arguments, scope="read manage"):
    from app import accounts, oauth
    cid = "pbc_" + secrets.token_hex(6)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    token = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp", accounts.session_epoch())
    con.commit()
    con.close()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": name, "arguments": arguments}},
                    headers={"Authorization": f"Bearer {token['access_token']}"})
    return r


def test_mcp_creates_a_client_but_never_hands_out_a_code(client):
    ev = _event()
    # With events the client's code opens galleries: "photos" too (final review A1).
    r = _mcp(client, "create_client", {"name": "Brautpaar K.", "event_ids": [ev["id"]]}, scope="read manage photos")
    data = json.loads(r.get_json()["result"]["content"][0]["text"])
    assert data["event_ids"] == [ev["id"]] and data["has_code"] is False
    cid = data["id"]
    code = clients.new_code(cid)
    listed = _mcp(client, "list_clients", {}, scope="read").get_data(as_text=True)
    assert "Brautpaar K." in listed and code not in listed and code.replace("-", "") not in listed


def test_mcp_assigning_events_needs_manage_and_photos(client):
    cid = clients.create("Firma Süd")
    ev = _event()
    assert _mcp(client, "assign_client_events", {"client_id": cid, "add": [ev["id"]]}, scope="read").status_code == 403
    assert _mcp(client, "assign_client_events", {"client_id": cid, "add": [ev["id"]]}).status_code == 403
    r = _mcp(client, "assign_client_events", {"client_id": cid, "add": [ev["id"]]}, scope="read manage photos")
    assert json.loads(r.get_json()["result"]["content"][0]["text"])["event_ids"] == [ev["id"]]
