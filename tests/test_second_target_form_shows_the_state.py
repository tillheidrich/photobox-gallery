"""The second-target card says what is set and what happens next (18.11.2026).

The owner saved the form for real and got it back empty, with "Stand der Box:
Noch kein zweites Ziel eingerichtet" and the job "erteilt" — while the booth
took the setting seconds later. The page rendered the booth's last report,
which at that moment still predated the change, ignored the job it had just
queued and never looked again. Now the form is filled from the pending job
(non-secret fields only, marked as waiting) or from the booth's report, the
state names the last contact, the pending job and the last check, and a small
status endpoint lets the page follow the booth's answer without a reload.
The password is never echoed and never stored readable (rule B2 unchanged).
"""
import base64
import hashlib
import json
import re

import pytest

from app import db, ecosystem
from app.i18n import de, en

DUMMY = "Dummy-Passwort-9c1e"


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


def _report(keypair, **target):
    pub = bytes(keypair.public_key)
    rep = {"v": 2, "allowed": True, "configured": bool(target),
           "public_key": base64.b64encode(pub).decode(), "key_id": hashlib.sha256(pub).hexdigest()[:12]}
    if target:
        rep.update({"enabled": True, "type": "ftps", "host": "ftp.example.org", "port": 0, "user": "demo@example.org",
                    "path": "/", "flat": True, "originals": True, "print_sheets": False, "password_set": True,
                    **target})
    return rep


def _booth(status=None, minutes_ago=0):
    made = db.create_device(name="Demobox")
    from app import booth
    con = db.get_db()
    con.execute("UPDATE devices SET last_heartbeat_at=datetime('now', ?), status_json=? WHERE id=?",
                (f"-{int(minutes_ago)} minutes", json.dumps(booth.sanitize_payload(status or {})), made["id"]))
    con.commit()
    con.close()
    return {**db.get_device_by_id(made["id"]), "api_key": made["api_key"]}


def _card(html):
    card = html[html.index('id="zweites-ziel"'):]
    return card[:card.index("<script")]


def _form(html):
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


def _save(client, dev, **fields):
    data = {"csrf_token": "t", "action": "save", "type": "ftps", "host": "ftp.example.org", "user": "demo@example.org",
            "path": "/", "enabled": "1", "flat": "1", "originals": "1", **fields}
    return client.post(f"/admin/devices/{dev['id']}/sync/second-target", data=data)


def _state(client, dev):
    r = client.get(f"/admin/devices/{dev['id']}/sync/second-target/state")
    assert r.status_code == 200 and r.is_json
    return r.get_json()


# ── The form is always filled ─────────────────────────────

def test_the_form_is_filled_from_what_the_booth_reports(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org", path="/", port=2121)})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    data, form = _form(html)
    assert data["host"] == "ftp.example.org" and data["user"] == "demo@example.org" and data["path"] == "/"
    assert data["port"] == "2121" and data["type"] == "ftps" and data["flat"] == "1" and data["enabled"] == "1"
    assert data["password"] == "" and 'data-form-source="booth"' in form
    assert de.STRINGS["sync.second_target.pw_stored"] in form
    assert de.STRINGS["sync.second_target.not_configured"] not in html


def test_while_a_change_waits_the_form_shows_the_sent_values_marked_as_waiting(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair)})                    # nothing set at the booth yet
    r = _save(client, dev, host="upload.example.net", port="2121", user="fotobox", path="/live/{event}",
              password=DUMMY, print_sheets="1")
    assert r.status_code == 302
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    data, form = _form(html)
    assert data["host"] == "upload.example.net" and data["user"] == "fotobox" and data["path"] == "/live/{event}"
    assert data["port"] == "2121" and data["print_sheets"] == "1" and data["password"] == ""
    assert 'data-form-source="pending"' in form and "wartet auf die Box" in form
    assert de.STRINGS["sync.second_target.pw_sent"] in form
    card = _card(html)
    assert "data-pending-set" in card and de.STRINGS["sync.second_target.not_configured"] in card


def test_a_pending_change_without_password_says_the_booth_will_wait_for_one(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    _save(client, dev, host="other.example.net")
    _, form = _form(client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True))
    assert de.STRINGS["sync.second_target.pw_will_wait"] in form


def test_the_password_never_reaches_the_page_or_the_status_endpoint(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair)})
    _save(client, dev, password=DUMMY)
    sealed = json.loads(ecosystem.recent_jobs(dev["id"], 5)[0]["payload_json"]).get("password_sealed")
    con = db.get_db()
    raw = con.execute("SELECT payload_json FROM device_jobs WHERE device_id=? AND type='set_second_target'",
                      (dev["id"],)).fetchone()["payload_json"]
    con.close()
    ciphertext = json.loads(raw)["password_sealed"]
    assert sealed == "••••" and ciphertext and DUMMY not in raw            # sealing as before
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    text = json.dumps(_state(client, dev))
    for page in (html, text):
        assert DUMMY not in page and ciphertext not in page and "password_sealed" not in page
    assert 'name="password" value=""' in html


# ── Status endpoint ─────────────────────────────────────────

def test_the_status_endpoint_needs_the_admin_and_carries_no_secret(client, keypair):
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    r = client.get(f"/admin/devices/{dev['id']}/sync/second-target/state")
    assert r.status_code in (302, 401, 403) and not r.is_json
    _admin(client)
    d = _state(client, dev)
    assert d["ok"] is True and d["busy"] is False and d["form_source"] == "booth"
    assert d["form"]["host"] == "ftp.example.org" and "password" not in d["form"]
    assert "public_key" not in json.dumps(d)
    assert base64.b64encode(bytes(keypair.public_key)).decode() not in json.dumps(d)
    assert 'data-zt-live-body' in d["html"]
    # Read-only: GET without a token is fine, the route takes no POST.
    assert client.post(f"/admin/devices/{dev['id']}/sync/second-target/state",
                       data={"csrf_token": "t"}).status_code == 405
    assert client.get("/admin/devices/999999/sync/second-target/state").status_code == 404


def test_the_page_follows_the_booth_without_a_reload(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair)})
    _save(client, dev)
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert 'data-zt-busy="1"' in html and "/sync/second-target/state" in html
    assert re.search(r'<script src="/static/js/second-target-live\.js"', html)
    assert client.get("/static/js/second-target-live.js").status_code == 200
    assert _state(client, dev)["busy"] is True


# ── The owner's case, reproduced ────────────────────────────

def test_saving_then_the_booth_answering_shows_the_booths_state_not_an_empty_form(client, keypair):
    """Before: right after saving the card said "Noch kein zweites Ziel" and the form was
    empty although the change was on its way; once the booth answered, nothing on the
    page moved. Now the waiting values show at once and the booth's answer replaces them."""
    _admin(client)
    dev = _booth({"second_target": _report(keypair)})
    _save(client, dev, password=DUMMY)
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    data, _ = _form(html)
    assert data["host"] == "ftp.example.org"                              # was "": the empty form
    # The booth's next contact: it takes the job ...
    r = client.post("/api/v2/booth/sync", json={"status": {"second_target": _report(keypair)}},
                    headers={"X-API-Key": dev["api_key"]})
    jobs = r.get_json()["jobs"]
    assert [j["type"] for j in jobs] == ["set_second_target"]
    assert 'data-pending-set="delivered"' in _state(client, dev)["html"]
    # ... and answers with its new state in the same call that carries the result.
    answer = "Zweites Ziel gespeichert (ftps, ftp.example.org); Passwort gespeichert."
    client.post("/api/v2/booth/sync", json={
        "status": {"second_target": _report(keypair, host="ftp.example.org", path="/")},
        "job_results": [{"id": jobs[0]["id"], "ok": True, "result": answer}]},
        headers={"X-API-Key": dev["api_key"]})
    d = _state(client, dev)
    assert d["busy"] is False and d["form_source"] == "booth" and d["form"]["host"] == "ftp.example.org"
    assert de.STRINGS["sync.second_target.not_configured"] not in d["html"] and answer in d["html"]
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert 'data-zt-busy="0"' in html and de.STRINGS["sync.second_target.pw_stored"] in html


# ── Check, contact, job list ───────────────────────────────

def test_the_check_button_sends_the_check_and_shows_its_result(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert re.search(r'value="check"[^>]*>' + re.escape(de.STRINGS["sync.second_target.check_btn"]), html)
    assert de.STRINGS["sync.second_target.check_btn"] == "Verbindung prüfen"
    client.post(f"/admin/devices/{dev['id']}/sync/second-target", data={"csrf_token": "t", "action": "check"})
    d = _state(client, dev)
    assert d["busy"] is True and "data-pending-check" in d["html"]
    jid = ecosystem.recent_jobs(dev["id"], 5)[0]["id"]
    import time
    client.post("/api/v2/booth/sync", json={
        "status": {"second_target": _report(keypair, host="ftp.example.org",
                                            check={"ok": True, "message": "Anmeldung und Ordner in Ordnung.",
                                                   "at": time.time()})},
        "job_results": [{"id": jid, "ok": True, "result": "Anmeldung und Ordner in Ordnung."}]},
        headers={"X-API-Key": dev["api_key"]})
    d = _state(client, dev)
    assert d["busy"] is False and "data-pending-check" not in d["html"]
    assert de.STRINGS["sync.second_target.check_ok"] in d["html"] and "Anmeldung und Ordner in Ordnung." in d["html"]
    assert "vor " in d["html"] or "gerade eben" in d["html"]


def test_check_is_offered_while_the_first_setting_is_still_on_its_way(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair)})
    _save(client, dev)
    assert 'value="check"' in client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)


def test_a_silent_booth_is_named_with_its_last_contact(client, keypair):
    _admin(client)
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")}, minutes_ago=25)
    _save(client, dev, user="neu")
    card = _card(client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True))
    assert "data-contact-stale" in card and "25 min" in card
    fresh = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    card = _card(client.get(f"/admin/devices/{fresh['id']}/sync").get_data(as_text=True))
    assert "data-contact-stale" not in card and "data-contact" in card


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_job_list_says_the_booths_answer_in_plain_words(client, keypair, lang):
    _admin(client, lang)
    s = de.STRINGS if lang == "de" else en.STRINGS
    dev = _booth({"second_target": _report(keypair, host="ftp.example.org")})
    old = ecosystem.add_job(dev["id"], "set_second_target", {"v": 1, "action": "set"})
    ecosystem.finish_jobs(dev["id"], [{"id": old, "ok": False,
                                       "result": "An dieser Box ist nicht erlaubt, das zweite Ziel von außen zu ändern."}])
    _save(client, dev, user="erst")
    _save(client, dev, user="dann")                                       # replaces the first, still open
    card = _card(client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True))
    jobs = card[card.index("data-second-jobs"):]
    assert s["sync.second_target.job_replaced"] in jobs and s["sync.second_target.job_issued"] in jobs
    assert "An dieser Box ist nicht erlaubt" in jobs and s["sync.second_target.job_refused"] in jobs
    assert s["sync.second_target.job_kind_set"] in jobs


def test_new_texts_exist_in_both_languages_and_stay_short():
    keys = [k for k in de.STRINGS if k.startswith(("sync.second_target.", "events.booths."))]
    for k in keys:
        assert k in en.STRINGS, k
    for k in ("pw_stored", "pw_awaiting", "pw_none", "pw_sent", "pw_will_wait", "form_pending", "form_booth",
              "contact_stale", "pending_set", "pending_check", "sent", "check_sent"):
        assert len(de.STRINGS[f"sync.second_target.{k}"]) <= 160, k
    assert de.STRINGS["sync.second_target.pw_stored"] == "Passwort ist an der Box gespeichert."
    assert de.STRINGS["sync.second_target.pw_awaiting"] == "Box wartet auf ein Passwort."


def test_the_assistant_sees_the_change_on_its_way_like_the_page(client, keypair):
    """get_second_target and the page come from the same report; the waiting change is named in both."""
    from app import mcp_more
    _admin(client)
    dev = _booth({"second_target": _report(keypair)})
    _save(client, dev, host="upload.example.net", password=DUMMY)
    with client.application.test_request_context():
        shown = mcp_more._second_state(db.get_device_by_id(dev["id"]))
    assert shown["configured"] is False and shown["last_contact"]
    assert shown["pending_change"]["fields"]["host"] == "upload.example.net"
    assert shown["pending_change"]["with_password"] is True and DUMMY not in json.dumps(shown)
    assert "password_sealed" not in json.dumps(shown)
