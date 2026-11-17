"""The second target's SFTP server key on the gallery's sync page (17.11.2026, A-3c).

The booth pins the server's key on first use and refuses a different one
(sync/photobox_sync/targets.py). The gallery shows the fingerprints the
heartbeat carries — only fingerprints — and can accept the new key as a
separate, explicit action, only while the booth allows remote changes and
only for exactly the fingerprint the booth reported. Saving the form never
accepts a key. Plain FTP is labelled as unencrypted.
"""
import base64
import hashlib
import json

import pytest

from app import db, ecosystem

ALT = "SHA256:" + "A" * 43
NEU = "SHA256:" + "B" * 43


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
        s["admin_epoch"] = accounts.session_epoch()
        s["lang"] = "de"


def _report(allowed=True, **extra):
    from nacl.public import PrivateKey
    pub = bytes(PrivateKey.generate().public_key)
    rep = {"v": 1, "allowed": allowed, "configured": True, "enabled": True, "type": "sftp",
           "host": "nas.example.org", "port": 2222, "user": "demo", "path": "", "flat": True, "originals": True,
           "print_sheets": False, "password_set": True,
           "host_key": {"sha256": ALT, "type": "ssh-ed25519", "at": 1763400000},
           "host_key_seen": {"sha256": NEU, "type": "ssh-ed25519", "at": 1763400100}}
    if allowed:
        rep.update({"public_key": base64.b64encode(pub).decode(), "key_id": hashlib.sha256(pub).hexdigest()[:12]})
    rep.update(extra)
    return rep


def _booth(status):
    dev = db.create_device(name="Demobox")
    from app import booth
    con = db.get_db()
    con.execute("UPDATE devices SET last_heartbeat_at=datetime('now'), status_json=? WHERE id=?",
                (json.dumps(booth.sanitize_payload(status)), dev["id"]))
    con.commit()
    con.close()
    return dev


def _jobs(dev_id):
    con = db.get_db()
    rows = [dict(r) for r in con.execute("SELECT * FROM device_jobs WHERE device_id=? AND type=?",
                                         (dev_id, "accept_second_target_host_key"))]
    con.close()
    return rows


def test_the_heartbeat_keeps_fingerprints_and_nothing_else():
    from app import booth
    rep = _report(host_key={"sha256": ALT, "key": "AAAAC3NzaC1lZDI1NTE5AAAA", "type": "ssh-ed25519"},
                  host_key_seen={"sha256": "-----BEGIN OPENSSH PRIVATE KEY-----"})
    clean = booth.sanitize_payload({"second_target": rep})["second_target"]
    assert clean["host_key"] == {"sha256": ALT, "type": "ssh-ed25519", "at": 0}
    assert "host_key_seen" not in clean
    assert "AAAAC3" not in json.dumps(clean)


def test_the_card_shows_both_fingerprints_and_a_separate_accept_form(client):
    _admin(client)
    dev = _booth({"second_target": _report()})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert ALT in html and NEU in html
    assert "data-host-key-form" in html and 'value="accept_host_key"' in html
    # The main form's save button is not the accept button.
    haupt = html.split('class="zt-form" autocomplete="off"')[1].split("</form>")[0]
    assert "accept_host_key" not in haupt and NEU not in haupt


def test_accepting_queues_exactly_the_reported_fingerprint(client):
    _admin(client)
    dev = _booth({"second_target": _report()})
    url = f"/admin/devices/{dev['id']}/sync/second-target"
    client.post(url, data={"csrf_token": "t", "action": "accept_host_key", "fingerprint": ALT})
    assert not _jobs(dev["id"])
    client.post(url, data={"csrf_token": "t", "action": "save", "type": "sftp", "host": "nas.example.org",
                           "user": "demo", "enabled": "1", "fingerprint": NEU})
    assert not _jobs(dev["id"]), "saving the form never accepts a key"
    client.post(url, data={"csrf_token": "t", "action": "accept_host_key", "fingerprint": NEU})
    (job,) = _jobs(dev["id"])
    assert json.loads(job["payload_json"]) == {"v": 1, "fingerprint": NEU}


def test_not_from_the_gallery_to_an_older_booth_with_its_switch_off(client):
    _admin(client)
    dev = _booth({"second_target": _report(allowed=False)})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert "data-host-key-form" not in html
    client.post(f"/admin/devices/{dev['id']}/sync/second-target",
                data={"csrf_token": "t", "action": "accept_host_key", "fingerprint": NEU})
    assert not _jobs(dev["id"])


def test_the_job_is_on_both_lists_and_not_for_assistants():
    from photobox_sync.agent import ALLOWED_JOBS
    from app import mcp
    assert "accept_second_target_host_key" in ecosystem.JOB_TYPES
    assert "accept_second_target_host_key" in ALLOWED_JOBS
    assert "accept_second_target_host_key" not in mcp.ASSISTANT_JOBS


def test_plain_ftp_is_labelled_unencrypted_in_both_languages(client):
    from app.i18n import de, en
    assert "ohne Verschlüsselung" in de.STRINGS["sync.second_target.opt_ftp"]
    assert "lesbar" in de.STRINGS["sync.second_target.opt_ftp"]
    assert "readable" in en.STRINGS["sync.second_target.opt_ftp"]
    _admin(client)
    dev = _booth({"second_target": _report(type="ftp", host_key={}, host_key_seen={})})
    html = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert "data-ftp-warn" in html
