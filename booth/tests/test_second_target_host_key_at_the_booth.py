"""The SFTP server's key of the second target, as the booth handles it (17.11.2026, A-3c).

The pinning itself lives in the shared sync package (``HostKeys``,
sync/tests/test_sftp_host_key_is_pinned.py runs it against a real SSH
server). Here: the booth keeps the store in its data folder, reports
fingerprints only, lets the operator accept exactly the key the server
showed (with the PIN, or from the gallery only while the booth allows it),
and a changed host or port clears the old pin.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ALT = "SHA256:" + "A" * 43
NEU = "SHA256:" + "B" * 43


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-hostkey-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213",
                        "targets": [{"name": "picdrop", "type": "sftp", "host": "nas.example.org", "port": 2222,
                                     "user": "demo", "password": "Dummy-7f3a", "enabled": True}]})
    yield b
    b.camera.stop()
    b.mail.stop()


def _job(b, jtype, payload):
    b._test_job_id = getattr(b, "_test_job_id", 0) + 1
    b.agent._run_job({"id": b._test_job_id, "type": jtype, "payload": payload})
    ack = b.agent._acks[-1]
    return ack["ok"], ack["result"]


def test_the_store_lives_in_the_data_folder_and_the_agent_uses_it(booth):
    assert booth.host_keys.path == booth.data / "sftp_host_keys.json"
    assert booth.agent.host_keys is booth.host_keys
    (sftp,) = [t for t in booth.agent.targets() if t.name == "picdrop"]
    assert sftp.host_keys is booth.host_keys


def test_the_report_carries_fingerprints_only(booth):
    booth.host_keys.pin("nas.example.org", 2222, ALT, "ecdsa-sha2-nistp256")
    booth.host_keys.saw("nas.example.org", 2222, NEU, "ssh-ed25519")
    rep = booth.second_target.report()
    assert rep["host_key"]["sha256"] == ALT and rep["host_key_seen"]["sha256"] == NEU
    assert set(rep["host_key"]) == {"sha256", "type", "at"}


def test_the_gallery_can_accept_only_the_key_seen(booth):
    booth.host_keys.pin("nas.example.org", 2222, ALT)
    booth.host_keys.saw("nas.example.org", 2222, NEU)
    assert not _job(booth, "accept_second_target_host_key", {"v": 1, "fingerprint": ALT})[0]
    assert not _job(booth, "accept_second_target_host_key", {"v": 1, "fingerprint": NEU, "host": "x"})[0]
    assert booth.host_keys.get("nas.example.org", 2222)["sha256"] == ALT
    ok, _text = _job(booth, "accept_second_target_host_key", {"v": 1, "fingerprint": NEU})
    assert ok and booth.host_keys.get("nas.example.org", 2222)["sha256"] == NEU


def test_at_the_booth_with_the_pin(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    assert c.get("/api/admin/targets/host-key").status_code in (401, 403)
    token = c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]
    c.headers.update({"X-Admin-Token": token})
    booth.host_keys.pin("nas.example.org", 2222, ALT)
    booth.host_keys.saw("nas.example.org", 2222, NEU)
    assert c.get("/api/admin/targets/host-key").json()["host_key_seen"]["sha256"] == NEU
    assert c.post("/api/admin/targets/host-key", json={"fingerprint": ALT}).status_code == 400
    r = c.post("/api/admin/targets/host-key", json={"fingerprint": NEU})
    assert r.status_code == 200 and r.json()["host_key"]["sha256"] == NEU
    # Saving the form never accepts or keeps a key for a new server: another port, a first use again.
    booth.host_keys.saw("nas.example.org", 2222, ALT)
    alle = booth.store.local["targets"]
    c.post("/api/admin/config", json={"targets": [dict(alle[0], port=22, password="••••")]})
    assert booth.host_keys.get("nas.example.org", 2222) == {}


def test_a_changed_host_from_the_gallery_clears_the_old_pin(booth):
    booth.store.save_local({"remote_targets": {"allowed": True}})
    booth.host_keys.pin("nas.example.org", 2222, ALT)
    ok, _ = _job(booth, "set_second_target", {"v": 1, "action": "set", "target": {
        "type": "sftp", "host": "nas2.example.org", "port": 2222, "user": "demo", "path": "", "flat": True,
        "enabled": True, "originals": True, "print_sheets": False}})
    assert ok
    assert booth.host_keys.get("nas.example.org", 2222) == {}
    assert "host_key" not in booth.second_target.report()


def test_the_admin_page_shows_the_key_and_labels_plain_ftp():
    web = Path(__file__).resolve().parents[1] / "boothcore" / "web"
    html = (web / "admin.html").read_text(encoding="utf-8")
    js = (web / "static" / "admin-page.js").read_text(encoding="utf-8")
    assert "FTP ohne Verschlüsselung – Passwort und Bilder gehen lesbar durchs Netz" in html
    assert 'id="zt_ftp_warn"' in html and 'id="zt_hostkey_accept"' in html
    assert "/api/admin/targets/host-key" in js


@pytest.mark.parametrize("alias", ["ssh", "scp", "SFTP"])
def test_an_ssh_or_scp_typed_target_reports_and_accepts_its_key_like_sftp(booth, alias):
    """build_extra_targets treats ssh/scp as SFTP and pins their key; the booth must report and accept it too."""
    booth.store.save_local({"targets": [{"name": "picdrop", "type": alias, "host": "nas.example.org", "port": 2222,
                                          "user": "demo", "password": "Dummy-7f3a", "enabled": True}]})
    booth.host_keys.pin("nas.example.org", 2222, ALT)
    booth.host_keys.saw("nas.example.org", 2222, NEU)
    rep = booth.second_target.report()
    assert rep["host_key_seen"]["sha256"] == NEU
    assert booth.second_target.accept_host_key(NEU)["host_key"]["sha256"] == NEU


def test_the_booth_form_saves_ssh_and_scp_as_sftp(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    c.headers.update({"X-Admin-Token": c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]})
    t = {"name": "picdrop", "type": "scp", "host": "nas.example.org", "port": 2222, "user": "demo", "password": "x"}
    assert c.post("/api/admin/config", json={"targets": [t]}).status_code == 200
    assert booth.store.local["targets"][0]["type"] == "sftp"
