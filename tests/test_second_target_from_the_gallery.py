"""The booth's second upload target, set from the gallery's sync page (17.11.2026).

The card "Zweites Ziel (Picdrop, FTP, SFTP)" on /admin/devices/<id>/sync is a
form only when the booth allows it and reports a key; the password is sealed
in the request handler and only ciphertext reaches the database, inside a
job that is emptied once the booth answers or the job expires. Old booths
and booths with the switch off get the hint where to set it. MCP sets the
fields and triggers the check with the right ``secure``, never the password.
"""
import base64
import json
import re
import secrets
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from app import db, ecosystem
from app.i18n import de, en

DUMMY = "Dummy-Passwort-7f3a"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


@pytest.fixture()
def keypair():
    from nacl.public import PrivateKey
    return PrivateKey.generate()


def _admin(client, lang="de"):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
        s["admin_epoch"] = accounts.session_epoch()
        s["lang"] = lang


def _report(keypair, allowed=True, **target):
    from app import second_target
    pub = bytes(keypair.public_key)
    rep = {"v": 1, "allowed": allowed, "configured": bool(target)}
    if allowed:
        rep.update({"public_key": base64.b64encode(pub).decode(), "key_id": second_target.__dict__.get("x", "")})
        import hashlib
        rep["key_id"] = hashlib.sha256(pub).hexdigest()[:12]
    if target:
        rep.update({"enabled": True, "type": "ftps", "host": "ftp.example.org", "port": 0, "user": "demo@example.org",
                    "path": "", "flat": True, "originals": True, "print_sheets": False, "password_set": True,
                    "check": {"ok": False, "message": "Der Server antwortet nicht.", "at": 1763400000.0},
                    **target})
    return rep


def _booth(status=None):
    dev = db.create_device(name="Demobox")
    from app import booth
    clean = booth.sanitize_payload(status or {})
    con = db.get_db()
    con.execute("UPDATE devices SET last_heartbeat_at=datetime('now'), status_json=? WHERE id=?",
                (json.dumps(clean), dev["id"]))
    con.commit()
    con.close()
    return dev


def _jobs(dev_id, jtype="set_second_target"):
    con = db.get_db()
    rows = [dict(r) for r in con.execute("SELECT * FROM device_jobs WHERE device_id=? AND type=? ORDER BY id",
                                         (dev_id, jtype))]
    con.close()
    return rows


def _db_bytes(client):
    path = Path(client.application.config.get("DATABASE") or db.DB_PATH if hasattr(db, "DB_PATH") else "")
    con = db.get_db()
    files = [r[2] for r in con.execute("PRAGMA database_list") if r[2]]
    con.close()
    out = b""
    for f in files:
        for suffix in ("", "-wal", "-journal"):
            p = Path(f + suffix)
            if p.is_file():
                out += p.read_bytes()
    return out


def _form_fields(html):
    """The form as rendered: every input with its value, checkboxes only when checked, selects at the selected option."""
    form = html[html.index('<form method="post" action="/admin/devices/'):]
    form = form[:form.index("</form>")]
    data = {}
    for tag in re.findall(r"<input[^>]*>", form):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        kind = (re.search(r'type="([^"]+)"', tag) or [None, "text"])[1]
        value = (re.search(r'value="([^"]*)"', tag) or [None, ""])[1]
        if kind == "checkbox" and "checked" not in tag:
            continue
        data[name.group(1)] = value
    for name, body in re.findall(r'<select name="([^"]+)">(.*?)</select>', form, re.S):
        data[name] = re.search(r'<option value="([^"]+)" selected', body).group(1)
    return data, form


# ── Which card ─────────────────────────────────────────────

@pytest.mark.parametrize("lang,s", [("de", de.STRINGS), ("en", en.STRINGS)])
def test_an_old_booth_gets_the_hint_and_no_form(client, lang, s):
    _admin(client, lang)
    dev = _booth({"booth_version": "photobox-booth 0.2.0"})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    card = html[html.index('id="zweites-ziel"'):]
    card = card[:card.index("<script")]
    assert 'data-second-target="at-the-booth"' in card and s["sync.second_target.too_old"] in card
    for key in ("title", "where"):
        assert s[f"sync.second_target.{key}"] in card
    assert "<form" not in card and 'type="password"' not in card


def test_no_opt_in_any_booth_that_reports_its_key_gets_the_working_form(client, keypair):
    """The owner's decision of 17.11.2026: no "Noch nicht freigegeben", no switch at the booth."""
    _admin(client)
    rep = _report(keypair, host="ftp.example.org")
    rep.pop("allowed")                                         # a new booth may say nothing about a switch
    dev = _booth({"second_target": rep})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    card = html[html.index('id="zweites-ziel"'):]
    card = card[:card.index("<script")]
    assert 'data-second-target="form"' in card
    assert "Noch nicht freigegeben" not in html and "einstellen lassen" not in html
    assert "<fieldset" not in card and "disabled" not in card and 'value="check"' in card
    assert 'value="backfill"' in card and de.STRINGS["sync.second_target.rule_b2"] in card
    r = client.post(f"/admin/devices/{dev['id']}/sync/second-target",
                    data={"csrf_token": "t", "action": "save", "type": "sftp", "host": "nas.example.org",
                          "password": DUMMY, "enabled": "1"})
    assert r.status_code == 302 and len(_jobs(dev["id"])) == 1


def test_the_removed_states_and_texts_are_gone():
    for strings in (de.STRINGS, en.STRINGS):
        for key in ("sync.second_target.off_steps", "sync.second_target.off_at_booth", "booth.job.second_off"):
            assert key not in strings
        assert not any("Noch nicht freigegeben" in v or "einstellen lassen" in v for v in strings.values())
    card = (ROOT / "app" / "templates" / "admin" / "_second_target_card.html").read_text(encoding="utf-8")
    assert "data-locked" not in card and "off-at-booth" not in card


def test_the_check_button_on_the_booth_page_works_again(client, keypair):
    _admin(client)
    on = _booth({"second_target": _report(keypair)})
    body = client.get(f"/admin/devices/{on['id']}/booth").get_data(as_text=True)
    assert re.search(r'<button class="btn btn-secondary btn-sm">Zweites Ziel prüfen</button>', body)
    assert "data-second-off" not in body
    client.post(f"/admin/devices/{on['id']}/job", data={"csrf_token": "t", "type": "check_second_target"})
    assert len(_jobs(on["id"], "check_second_target")) == 1


def test_an_older_booth_with_its_switch_off_gets_a_plain_hint_and_no_form(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, allowed=False, host="ftp.example.org")})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    card = html[html.index('id="zweites-ziel"'):]
    card = card[:card.index("<script")]
    assert de.STRINGS["sync.second_target.old_booth"] in card and "<form" not in card


# ── Rule B2 and B4 in the gallery ──────────────────────────

def test_changing_the_server_without_a_password_warns_and_the_booth_will_wait(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    r = client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={
        "csrf_token": "t", "action": "save", "type": "ftps", "host": "other.example.net", "user": "demo@example.org",
        "enabled": "1", "flat": "1", "originals": "1"})
    assert r.status_code == 302
    payload = json.loads(_jobs(dev["id"])[-1]["payload_json"])
    assert "password_sealed" not in payload and payload["target"]["host"] == "other.example.net"
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert de.STRINGS["sync.second_target.sent_needs_password"] in html


def test_a_waiting_booth_says_so_and_asks_for_the_password(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="other.example.net", password_set=False,
                                           awaiting_password=True)})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert "data-awaiting-password" in html and de.STRINGS["sync.second_target.awaiting"] in html
    assert de.STRINGS["sync.second_target.ph_awaiting"] in html


def test_every_change_is_in_the_change_log_with_fields_and_shown_in_the_card(client, keypair):
    from app import protokoll
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={
        "csrf_token": "t", "action": "save", "type": "ftps", "host": "ftp.example.org", "user": "anders",
        "path": "/live/{event}", "password": DUMMY, "enabled": "1", "flat": "1", "originals": "1"})
    rows = [r for r in protokoll.liste(limit=20) if r["was"].startswith(f"Zweites Ziel · Box #{dev['id']} ·")]
    assert rows, protokoll.liste(limit=5)
    assert "Benutzer" in rows[0]["was"] and "Ordner" in rows[0]["was"] and "neues Passwort" in rows[0]["was"]
    assert "Server" not in rows[0]["was"].split(":", 1)[1].split(";")[0]
    everything = json.dumps(protokoll.liste(limit=50), default=str)
    assert DUMMY not in everything
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert "data-second-changes" in html and "Benutzer, Ordner" in html


def test_the_folder_rule_and_a_live_example_under_the_field(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org", path="/fotobox/{jahr}/{event}")})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert de.STRINGS["sync.second_target.path_rule"] in html and de.STRINGS["sync.second_target.layout_words"] in html
    assert "/fotobox/2026/sommerfest-2026/" in html
    client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={
        "csrf_token": "t", "action": "save", "type": "ftps", "host": "ftp.example.org", "path": "a/{wer}"})
    assert not _jobs(dev["id"])                                   # unknown placeholder: refused before queueing
    client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={
        "csrf_token": "t", "action": "save", "type": "ftps", "host": "ftp.example.org", "path": ""})
    assert json.loads(_jobs(dev["id"])[-1]["payload_json"])["target"]["path"] == ""


def test_the_form_never_shows_the_password_and_shows_the_state(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair), "upload_targets": {"gallery": {"done": 40, "pending": 2, "failed": 0},
                                                                         "picdrop": {"done": 38, "pending": 3, "failed": 1}}})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    data, form = _form_fields(html)
    assert 'name="password" value=""' in form and de.STRINGS["sync.second_target.ph_none"] in form
    assert data["host"] == "" and data["type"] == "ftps"
    assert "38 gesendet · 3 offen · 1 fehlgeschlagen" in html and "Zweites Ziel" in html
    dev2 = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    html2 = client.get(f"/admin/devices/{dev2['id']}/sync").get_data(as_text=True)
    assert de.STRINGS["sync.second_target.ph_stored"] in html2 and 'data-password-set="1"' in html2
    assert "ftp.example.org" in html2 and "demo@example.org" in html2 and "Der Server antwortet nicht." in html2


# ── Save: sealed, narrow, emptied ─────────────────────────

def test_saving_seals_the_password_and_the_database_never_holds_it(client, keypair):
    from nacl.public import SealedBox
    _admin(client)
    dev = _booth({"second_target": _report(keypair)})
    r = client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={
        "csrf_token": "t", "action": "save", "type": "ftps", "host": "ftp.example.org", "port": "",
        "user": "demo", "password": DUMMY, "path": "/galerie", "flat": "1", "enabled": "1", "originals": "1"})
    assert r.status_code == 302
    job = _jobs(dev["id"])[-1]
    payload = json.loads(job["payload_json"])
    assert set(payload) == {"v", "action", "target", "password_sealed", "key_id"}
    assert payload["target"] == {"type": "ftps", "host": "ftp.example.org", "user": "demo", "path": "/galerie",
                                 "flat": True, "enabled": True, "originals": True, "print_sheets": False}
    assert SealedBox(keypair).decrypt(base64.b64decode(payload["password_sealed"])).decode() == DUMMY
    assert DUMMY.encode() not in _db_bytes(client)
    # What a page or an assistant sees of the job: dots, not even the ciphertext.
    seen = json.dumps(ecosystem.recent_jobs(dev["id"], 10))
    assert payload["password_sealed"] not in seen and DUMMY not in seen
    # The booth answers: the payload is gone.
    ecosystem.finish_jobs(dev["id"], [{"id": job["id"], "ok": True, "result": "Zweites Ziel übernommen"}])
    assert _jobs(dev["id"])[-1]["payload_json"] == "{}"


def test_saving_the_form_as_rendered_keeps_the_stored_password(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    data, _ = _form_fields(html)
    assert data["password"] == "" and data["host"] == "ftp.example.org" and data["user"] == "demo@example.org"
    r = client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={**data, "action": "save"})
    assert r.status_code == 302
    payload = json.loads(_jobs(dev["id"])[-1]["payload_json"])
    assert "password_sealed" not in payload                       # empty keeps the stored one
    assert payload["target"]["user"] == "demo@example.org"         # not masked on the way back


def test_remove_access_clears_the_target_and_an_expired_job_is_emptied(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={
        "csrf_token": "t", "action": "save", "type": "ftps", "host": "ftp.example.org", "password": DUMMY})
    first = _jobs(dev["id"])[-1]
    client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={"csrf_token": "t", "action": "clear"})
    jobs = _jobs(dev["id"])
    assert json.loads(jobs[-1]["payload_json"]) == {"v": 1, "action": "clear"}
    # The older, unanswered one was replaced and emptied.
    old = next(j for j in jobs if j["id"] == first["id"])
    assert old["payload_json"] == "{}" and old["expired"] == 1
    con = db.get_db()
    con.execute("UPDATE device_jobs SET created_at=datetime('now','-11 minutes') WHERE device_id=?", (dev["id"],))
    con.commit()
    con.close()
    ecosystem.expire_jobs(dev["id"])
    assert all(j["payload_json"] == "{}" for j in _jobs(dev["id"]))


@pytest.mark.parametrize("field,value", [("host", "ftp://ftp.example.org"), ("host", "ftp.example.org/x"),
                                         ("port", "70000"), ("port", "abc"), ("path", "../x"), ("user", "a\x01b"),
                                         ("type", "http")])
def test_bad_values_are_refused_before_anything_is_queued(client, keypair, field, value):
    _admin(client)
    dev = _booth({"second_target": _report(keypair)})
    data = {"csrf_token": "t", "action": "save", "type": "ftps", "host": "ftp.example.org", "password": DUMMY}
    data[field] = value
    client.post(f"/admin/devices/{dev['id']}/sync/second-target", data=data)
    assert not _jobs(dev["id"])


def test_admin_and_csrf_are_needed(client, keypair):
    dev = _booth({"second_target": _report(keypair)})
    r = client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={"action": "check"})
    assert r.status_code in (302, 400) and not _jobs(dev["id"], "check_second_target")
    _admin(client)
    r = client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={"action": "check", "csrf_token": "falsch"})
    assert r.status_code == 400 and not _jobs(dev["id"], "check_second_target")
    r = client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={"action": "check", "csrf_token": "t"})
    assert r.status_code == 302 and _jobs(dev["id"], "check_second_target")
    client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={"action": "backfill", "csrf_token": "t"})
    assert _jobs(dev["id"], "backfill_second_target")


def test_the_generic_job_routes_cannot_carry_it(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair)})
    r = client.post(f"/admin/devices/{dev['id']}/sync/job", json={"type": "set_second_target", "payload": {"x": 1}},
                    headers={"X-CSRF-Token": "t"})
    assert r.status_code == 400
    client.post(f"/admin/devices/{dev['id']}/job", data={"csrf_token": "t", "type": "set_second_target"})
    assert not _jobs(dev["id"])


# ── What the booth reports ─────────────────────────────────

def test_the_heartbeat_keeps_no_password_and_no_unknown_key(keypair):
    from app import booth
    rep = _report(keypair, host="ftp.example.org", password=DUMMY, private_key="geheim")
    clean = booth.sanitize_payload({"second_target": rep, "upload_targets": {"picdrop": {"done": 1, "x": 5}, "b@d": {}}})
    assert DUMMY not in json.dumps(clean) and "geheim" not in json.dumps(clean)
    assert "password" not in clean["second_target"] and "private_key" not in clean["second_target"]
    assert clean["upload_targets"]["picdrop"] == {"done": 1, "pending": 0, "failed": 0}


def test_a_new_booth_with_an_old_gallery_report_is_just_ignored():
    # Old gallery = no "second_target" in ALLOWED_FIELDS: nothing breaks; here the reverse — a report without
    # no key yet: nothing can be sealed, so no form.
    from app import second_target
    st = second_target.state({"second_target": {"v": 1}})
    assert st["supported"] and not st["can_set"]


def test_booth_and_gallery_check_the_same_lists():
    import ast
    from app import second_target as here
    tree = ast.parse((ROOT / "booth" / "boothcore" / "second_target.py").read_text(encoding="utf-8"))
    there = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
             if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
             and n.targets[0].id in ("TYPES", "FIELDS", "LIMITS", "NAMES")}
    assert there == {"TYPES": here.TYPES, "FIELDS": here.FIELDS, "LIMITS": here.LIMITS, "NAMES": here.NAMES}


def test_the_gallery_still_cannot_set_targets_through_local_settings():
    assert not any("target" in k or "host" in k or "password" in k for k in ecosystem.REMOTE_LOCAL_KEYS)


def test_redaction_covers_the_new_fields():
    from app import redact
    out = redact.text('{"password_sealed": "QUJDREVGR0hJSktMTU5PUA==", "private_key": "abc"}')
    assert "QUJDREVGR0hJSktMTU5PUA" not in out and "abc" not in out


# ── MCP ────────────────────────────────────────────────────

@pytest.fixture()
def mcp(client):
    client.environ_base["REMOTE_ADDR"] = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"
    db.set_setting("admin_password_hash", generate_password_hash("Richtig-Langes-Passwort-42"))

    def token(scope):
        from app import accounts, oauth
        cid = "pbc_" + secrets.token_hex(6)
        con = db.get_db()
        con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                    "VALUES (?,?,?,datetime('now'))", (cid, "Test", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
        answer = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp", accounts.session_epoch())
        con.commit()
        con.close()
        return answer["access_token"]

    def tool(tok, name, **arguments):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments}},
                        headers={"Authorization": f"Bearer {tok}"})
        return r.get_json()
    return token, tool


def test_mcp_sets_fields_and_checks_with_secure_but_never_a_password(mcp, keypair):
    token, tool = mcp
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    plain = token("read manage")
    answer = tool(plain, "set_second_target", booth_id=dev["id"], type="ftps", host="ftp.example.org")
    assert "error" in answer or answer.get("result", {}).get("isError")
    assert not _jobs(dev["id"])
    sec = token("read manage secure")
    answer = tool(sec, "set_second_target", booth_id=dev["id"], type="ftps", host="ftp.example.org",
                  password=DUMMY)
    assert answer["result"].get("isError") and not _jobs(dev["id"])
    answer = tool(sec, "set_second_target", booth_id=dev["id"], type="sftp", host="nas.example.org", user="u")
    assert not answer["result"].get("isError"), answer
    payload = json.loads(_jobs(dev["id"])[-1]["payload_json"])
    assert payload["target"]["host"] == "nas.example.org" and "password_sealed" not in payload
    shown = tool(sec, "get_second_target", booth_id=dev["id"])["result"]
    text = json.dumps(shown)
    assert '\\"password_set\\": true' in text and DUMMY not in text and "public_key" not in text
    assert not tool(sec, "check_second_target", booth_id=dev["id"])["result"].get("isError")
    assert _jobs(dev["id"], "check_second_target")


def test_send_booth_job_does_not_offer_the_second_target():
    from app import mcp
    assert not set(mcp.ASSISTANT_JOBS) & {"set_second_target", "check_second_target", "backfill_second_target"}


def test_mcp_changes_only_the_named_fields_and_a_new_server_drops_the_password(mcp, keypair):
    from app import protokoll
    token, tool = mcp
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org", path="/alt")})
    sec = token("read manage secure")
    answer = tool(sec, "set_second_target", booth_id=dev["id"], path="/live/{event}")
    assert not answer["result"].get("isError"), answer
    target = json.loads(_jobs(dev["id"])[-1]["payload_json"])["target"]
    assert target["host"] == "ftp.example.org" and target["user"] == "demo@example.org"   # untouched
    assert target["path"] == "/live/{event}"
    assert "unverändert" in json.dumps(answer, ensure_ascii=False)
    answer = tool(sec, "set_second_target", booth_id=dev["id"], host="other.example.net")
    text = json.dumps(answer, ensure_ascii=False)
    assert "gelöscht" in text and "Besitzer" in text, text
    rows = [r for r in protokoll.liste(limit=20) if r["was"].startswith(f"Zweites Ziel · Box #{dev['id']} · MCP")]
    assert len(rows) == 2 and "Server" in rows[0]["was"] and "Passwort verworfen" in rows[0]["was"]
    assert "Ordner" in rows[1]["was"] and rows[0]["wer"].startswith("Assistent")


def test_mcp_can_never_set_a_password_or_accept_a_server_key(mcp, keypair):
    from app import mcp as mcp_mod
    token, tool = mcp
    dev = _booth({"second_target": _report(keypair, host="nas.example.org", type="sftp",
                                           host_key_seen={"sha256": "SHA256:" + "B" * 43, "type": "ssh-ed25519", "at": 1})})
    sec = token("read manage secure control")
    for extra in ({"password": DUMMY}, {"password_sealed": "x"}, {"fingerprint": "SHA256:" + "B" * 43},
                  {"accept_host_key": True}):
        answer = tool(sec, "set_second_target", booth_id=dev["id"], **extra)
        assert answer.get("error") or answer["result"].get("isError"), extra
    answer = tool(sec, "send_booth_job", booth_id=dev["id"], job="accept_second_target_host_key")
    assert answer.get("error") or answer["result"].get("isError")
    assert "accept_second_target_host_key" not in mcp_mod.ASSISTANT_JOBS
    assert not any("host_key" in name for name in mcp_mod.TOOLS)
    assert not _jobs(dev["id"]) and not _jobs(dev["id"], "accept_second_target_host_key")
    desc = mcp_mod.TOOLS["set_second_target"]["description"]
    assert "NIE setzen" in desc and "Schlüssel" in desc and "Passwort" in desc
