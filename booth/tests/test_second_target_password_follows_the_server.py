"""A stored password goes only to the server it was typed for (rule B2, 17.11.2026).

With the switch at the booth gone, the gallery (and an assistant through
MCP) can point the second target at another server. What keeps the stored
password from going along is decided here, at the booth, whatever the
gallery sends: when type, host, port or user change and no new password
comes with the change, the booth deletes the stored one, forgets the old
server's pinned SFTP key and holds the uploads until a password for the new
server is entered — in the gallery form or at the booth. A target whose
stored endpoint does not match ``password_for`` (a hand-edited local.json,
a bug elsewhere) connects nowhere at all.
"""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DUMMY = "Dummy-Passwort-b2b2"


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-b2-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213", "server": {"url": "https://galerie.example.org", "api_key": "k" * 8}})
    yield b
    b.camera.stop()
    b.mail.stop()


def _seal(b, secret=DUMMY):
    from nacl.public import PublicKey, SealedBox
    rep = b.second_target.report()
    box = SealedBox(PublicKey(base64.b64decode(rep["public_key"]))).encrypt(secret.encode())
    return {"password_sealed": base64.b64encode(box).decode(), "key_id": rep["key_id"]}


def _target(**over):
    t = {"type": "ftps", "host": "ftp.example.org", "port": 0, "user": "demo", "path": "",
         "flat": True, "enabled": True, "originals": True, "print_sheets": False}
    t.update(over)
    return t


def _job(b, jtype, payload):
    b._test_job_id = getattr(b, "_test_job_id", 0) + 1
    b.agent._run_job({"id": b._test_job_id, "type": jtype, "payload": payload})
    ack = b.agent._acks[-1]
    return ack["ok"], ack["result"]


def _entry(b):
    return next(t for t in b.store.local["targets"] if t["name"] == "picdrop")


def _second(b):
    return [t for t in b.agent.targets() if t.name == "picdrop"]


@pytest.mark.parametrize("change", [{"host": "other.example.net"}, {"port": 2121}, {"user": "jemand"},
                                    {"type": "ftp"}, {"type": "sftp"}])
def test_a_changed_endpoint_without_a_password_drops_the_stored_one_and_waits(booth, change):
    from photobox_sync.targets import AwaitingPasswordTarget, TemporaryError
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(), **_seal(booth)})[0]
    assert _entry(booth)["password"] == DUMMY
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(**change)})
    assert ok and "altes Passwort gelöscht" in text, text
    entry = _entry(booth)
    assert entry["password"] == "" and entry["awaiting_password"] is True and "password_for" not in entry
    rep = booth.second_target.report()
    assert rep["awaiting_password"] is True and rep["password_set"] is False
    (stand_in,) = _second(booth)
    assert isinstance(stand_in, AwaitingPasswordTarget) and "password" not in stand_in.cfg
    with pytest.raises(TemporaryError) as e:             # temporary: the files wait, nothing is given up
        stand_in.upload({"uid": "a", "variant": "web", "path": "x", "meta": {}})
    assert "Wartet auf das Passwort" in str(e.value)


def test_a_password_for_the_new_server_ends_the_wait_and_is_bound_to_it(booth):
    from photobox_sync.targets import FtpsTarget, endpoint_key
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(), **_seal(booth)})[0]
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(host="other.example.net")})[0]
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(host="other.example.net"),
                                                 **_seal(booth, "neu-fuer-den-neuen")})
    assert ok and "Passwort neu" in text
    entry = _entry(booth)
    assert entry["password"] == "neu-fuer-den-neuen" and not entry.get("awaiting_password")
    assert entry["password_for"] == endpoint_key(entry)
    (t,) = _second(booth)
    assert isinstance(t, FtpsTarget) and t.cfg["password"] == "neu-fuer-den-neuen"


def test_path_flat_enabled_and_the_upload_choices_keep_the_password(booth):
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(), **_seal(booth)})[0]
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target(path="/fotos/{jahr}/{event}", flat=False,
                                                                           enabled=False, originals=False,
                                                                           print_sheets=True)})
    assert ok and "unverändert" in text
    entry = _entry(booth)
    assert entry["password"] == DUMMY and not entry.get("awaiting_password")
    # The same host written differently is the same server.
    assert _job(booth, "set_second_target", {"v": 1, "target": _target(host="FTP.Example.org.", port=21)})[0]
    assert _entry(booth)["password"] == DUMMY


def test_a_new_target_from_the_gallery_without_a_password_waits_too(booth):
    ok, text = _job(booth, "set_second_target", {"v": 1, "target": _target()})
    assert ok and "wartet" in text
    assert _entry(booth)["awaiting_password"] is True


def test_a_changed_sftp_server_forgets_its_pinned_key(booth):
    booth.store.save_local({"targets": [{"name": "picdrop", "type": "sftp", "host": "nas.example.org", "port": 2222,
                                          "user": "demo", "password": DUMMY}]})
    booth.host_keys.pin("nas.example.org", 2222, "SHA256:" + "A" * 43)
    ok, _ = _job(booth, "set_second_target", {"v": 1, "target": _target(type="sftp", host="nas.example.org",
                                                                       port=2222, user="anders")})
    assert ok and booth.host_keys.get("nas.example.org", 2222) == {}
    assert _entry(booth)["password"] == ""


def test_a_hand_edited_server_gets_no_password_even_without_the_job(booth):
    """The binding holds where no job ran at all: local.json changed by hand, or by a bug."""
    from photobox_sync import targets as tg
    entry = {"name": "picdrop", **_target(), "password": DUMMY}
    entry["password_for"] = tg.endpoint_key(entry)
    booth.store.save_local({"targets": [{**entry, "host": "evil.example.net"}]})
    connected = []
    real = tg.FtpsTarget._connect
    tg.FtpsTarget._connect = lambda self: connected.append(dict(self.cfg)) or real(self)
    try:
        (t,) = _second(booth)
        assert isinstance(t, tg.AwaitingPasswordTarget)
        with pytest.raises(tg.TemporaryError):
            t.upload({"uid": "a", "variant": "web", "path": "x", "meta": {}})
    finally:
        tg.FtpsTarget._connect = real
    assert connected == [] and booth.second_target.report()["awaiting_password"] is True


def _admin(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    c.headers.update({"X-Admin-Token": c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]})
    return c


def test_the_booth_form_follows_the_same_rule(booth):
    c = _admin(booth)
    t = {"name": "picdrop", **_target(), "password": DUMMY}
    assert c.post("/api/admin/config", json={"targets": [t]}).status_code == 200
    assert _entry(booth)["password"] == DUMMY and _entry(booth)["password_for"]
    # Dots ("keep it") with the same server: kept.
    assert c.post("/api/admin/config", json={"targets": [{**t, "password": "••••", "path": "x"}]}).status_code == 200
    assert _entry(booth)["password"] == DUMMY
    # Dots with another server: dropped, waits.
    assert c.post("/api/admin/config", json={"targets": [{**t, "password": "••••", "host": "b.example.net"}]}).status_code == 200
    assert _entry(booth)["password"] == "" and _entry(booth)["awaiting_password"] is True
    # The form cannot clear the wait by sending the flag; a typed password can.
    assert c.post("/api/admin/config", json={"targets": [{**t, "host": "b.example.net", "password": "",
                                                          "awaiting_password": False}]}).status_code == 200
    assert _entry(booth)["awaiting_password"] is True
    assert c.post("/api/admin/config", json={"targets": [{**t, "host": "b.example.net",
                                                          "password": "getippt"}]}).status_code == 200
    assert _entry(booth)["password"] == "getippt" and not _entry(booth).get("awaiting_password")
    # A folder the rule refuses is refused here as well.
    r = c.post("/api/admin/config", json={"targets": [{**t, "password": "••••", "host": "b.example.net",
                                                       "path": "../etc"}]})
    assert r.status_code == 400


def test_the_booth_check_never_hands_the_stored_password_to_another_server(booth, monkeypatch):
    c = _admin(booth)
    t = {"name": "picdrop", **_target(), "password": DUMMY}
    c.post("/api/admin/config", json={"targets": [t]})
    import photobox_sync.targets as tg
    seen = []
    monkeypatch.setattr(tg, "build_extra_targets", lambda cfgs, keys=None: seen.extend(cfgs) or [])
    r = c.post("/api/admin/targets/check", json={"target": {**t, "password": "••••", "host": "evil.example.net"}})
    assert r.json()["ok"] is False and "Passwort" in r.json()["message"]
    assert not any(cfg.get("password") == DUMMY for cfg in seen)


def test_the_second_target_gets_the_event_name_for_its_folder_and_the_gallery_does_not(booth):
    import json as _json
    import time as _time
    booth.store.profile["event"] = {**(booth.store.profile.get("event") or {}), "name": "Sommer Fest", "slug": "sf"}
    booth.store.save_local({"targets": [{"name": "picdrop", **_target(path="{event_name}"), "password": "x"}]})
    booth.camera.start()
    for _ in range(50):
        if booth.camera.state == "ready":
            break
        _time.sleep(0.1)
    try:
        s = booth.start_session("photo", "4:5", None, None)
        booth.take_shot(s)
        booth.finalize(s, "original")
    finally:
        booth.camera.stop()
    metas = {(r["target"], r["variant"]): _json.loads(r["meta"] or "{}") for r in booth.outbox.due(500)
             if r["uid"] == s.uid}
    assert metas[("picdrop", "web")]["event_name"] == "Sommer Fest"
    assert "event_name" not in metas[("gallery", "web")]
