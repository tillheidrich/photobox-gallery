"""A saved template as a test print at a chosen booth (job ``test_print``).

The editor's button and the MCP tool call the same function. Only templates
the gallery lists can be sent (own ones by file name, built-in ones by id),
an own template travels with its saved content, and the editor polls the job
result like the remote-maintenance page.
"""
import json
import secrets

import pytest


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    return client


def _device():
    from app import db
    return db.create_device(name="Box " + secrets.token_hex(2))["id"]


def _saved(admin, name="Probe Testdruck"):
    r = admin.post("/admin/vorlagen/speichern", headers={"X-CSRF-Token": "test-token"},
                   json={"name": name, "slots": [{"x": 60, "y": 60, "w": 1000, "h": 1200}]})
    return r.get_json()["asset"]["file"]


def _post(admin, body):
    return admin.post("/admin/vorlagen/testdruck", json=body, headers={"X-CSRF-Token": "test-token"})


def _payload(job_id):
    from app import db
    con = db.get_db()
    row = con.execute("SELECT type, payload_json FROM device_jobs WHERE id=?", (job_id,)).fetchone()
    con.close()
    return row["type"], json.loads(row["payload_json"])


def test_the_editor_sends_the_saved_template_with_its_content(admin):
    did, datei = _device(), _saved(admin)
    r = _post(admin, {"device_id": did, "datei": datei})
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    typ, payload = _payload(d["job_id"])
    assert typ == "test_print"
    assert payload["template"] == datei and payload["mark"] is True
    assert payload["content"]["name"] == "Probe Testdruck"
    assert d["result_url"].endswith(f"/sync/job/{d['job_id']}")
    assert admin.get(d["result_url"]).get_json()["state"] == "issued"


def test_the_label_can_be_switched_off(admin):
    did, datei = _device(), _saved(admin)
    d = _post(admin, {"device_id": did, "datei": datei, "mark": False}).get_json()
    assert _payload(d["job_id"])[1]["mark"] is False


def test_a_built_in_template_goes_by_its_id_without_content(admin):
    d = _post(admin, {"device_id": _device(), "datei": "solo"}).get_json()
    assert _payload(d["job_id"])[1] == {"template": "solo", "mark": True}


@pytest.mark.parametrize("name", ["../../etc/passwd", "gibt-es-nicht.json", "/tmp/x.json", ""])
def test_only_templates_the_gallery_lists_are_sent(admin, name):
    from app import db
    did = _device()
    r = _post(admin, {"device_id": did, "datei": name})
    assert r.status_code == 400
    con = db.get_db()
    assert con.execute("SELECT COUNT(*) c FROM device_jobs WHERE device_id=?", (did,)).fetchone()["c"] == 0
    con.close()


def test_an_unknown_booth_is_refused(admin):
    assert _post(admin, {"device_id": 99999, "datei": "solo"}).status_code == 400


def test_the_editor_page_offers_the_booths(admin):
    _device()
    datei = _saved(admin)
    html = admin.get(f"/admin/vorlagen/editor?datei={datei}").get_data(as_text=True)
    assert 'id="pbTestdruck"' in html and "testdruckUrl" in html


def test_without_login_or_csrf_nothing_is_sent(client):
    from app import db, ecosystem
    client.get("/healthz")
    ecosystem.init_schema()
    did = _device()
    r = client.post("/admin/vorlagen/testdruck", json={"device_id": did, "datei": "solo"})
    assert r.status_code in (302, 400, 403)
    con = db.get_db()
    assert con.execute("SELECT COUNT(*) c FROM device_jobs WHERE device_id=?", (did,)).fetchone()["c"] == 0
    con.close()


def test_the_assistant_can_send_a_template_test_print(client):
    from app import ecosystem, mcp as mcp_mod
    client.get("/healthz")
    ecosystem.init_schema()
    key = mcp_mod.new_key()
    did = _device()
    r = client.post("/mcp", headers={"X-API-Key": key}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "send_booth_job",
                   "arguments": {"booth_id": did, "job": "test_print", "template": "solo", "mark": False}}})
    result = r.get_json()["result"]
    assert not result.get("isError"), result
    job = json.loads(result["content"][0]["text"])
    assert _payload(job["job_id"]) == ("test_print", {"template": "solo", "mark": False})


def test_the_assistant_cannot_attach_a_template_to_another_job(client):
    from app import ecosystem, mcp as mcp_mod
    client.get("/healthz")
    ecosystem.init_schema()
    key = mcp_mod.new_key()
    r = client.post("/mcp", headers={"X-API-Key": key}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "send_booth_job",
                   "arguments": {"booth_id": _device(), "job": "lock", "template": "solo"}}})
    assert r.get_json()["result"].get("isError")
