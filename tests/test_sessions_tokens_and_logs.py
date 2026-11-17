"""Security review of 11.11.2026, findings S10, S18 and S16.

* S10: a password change left every other admin session logged in — a
  stolen session survived the remedy.
* S18: the link key that replaces a gallery password was compared with
  ``==``; the like endpoint took likes for any picture of any event,
  protected, unpublished or silent.
* S16: the first start wrote the generated password *and* the permanent
  recovery code into the server log; the booth's photo mail wrote part of
  the guest's address into the device log.
"""
import json
import logging
import re
import secrets

import pytest

from app import accounts, db, ecosystem, gallery_settings as gsm

PASSWORT = "ein-langes-start-passwort"


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("admin_username", "admin")
        db.set_setting("admin_totp_enabled", "")
        db.set_setting("silent_mode", "0")
    yield


def _login(client, password=PASSWORT):
    with client.session_transaction() as s:
        s["csrf_token"] = "t"
    r = client.post("/admin/login", data={"csrf_token": "t", "username": "admin", "password": password})
    with client.session_transaction() as s:
        assert s.get("admin_logged_in"), r.data[:300]


# ── S10 ───────────────────────────────────────────────────────────────

def test_a_password_change_logs_out_every_other_session(app):
    accounts.set_password(PASSWORT)
    stolen, mine = app.test_client(), app.test_client()
    _login(stolen)
    _login(mine)
    assert stolen.get("/admin/dashboard").status_code == 200
    r = mine.post("/admin/settings/account", data={"csrf_token": "t", "admin_username": "admin",
                                                   "new_password": "ein-ganz-neues-passwort-42",
                                                   "confirm_password": "ein-ganz-neues-passwort-42",
                                                   "current_password": PASSWORT})
    assert r.status_code in (302, 303)
    assert mine.get("/admin/dashboard").status_code == 200, "the session that changed it stays in"
    r = stolen.get("/admin/dashboard")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def test_a_session_from_before_the_change_is_logged_out_too(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True                       # no epoch: older than this rule
    accounts.set_password(PASSWORT)
    assert client.get("/admin/dashboard").status_code == 302


# ── S18 ───────────────────────────────────────────────────────────────

def _event(passwort="", published=1, **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Like {k}", slug=f"like-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": published, "gallery_json": json.dumps(gsm.clean(gs))})
    if passwort:
        db.set_event_password(eid, passwort)
    mid = db.add_media(event_id=eid, kind="photo", filename="a.jpg", path_original=f"storage/like-{k}/orig/a.jpg",
                       path_webp=None, path_thumb=None, size_bytes=1, sha256=k, share_token=k)
    return db.get_event_by_id(eid), mid


def test_the_link_key_is_compared_in_constant_time(client, monkeypatch):
    import hmac as hmac_mod
    from app.blueprints import public
    calls = []
    real = hmac_mod.compare_digest
    monkeypatch.setattr(public.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    event, _ = _event(passwort="geheim-123")
    token = public._access_token(event)
    client.get(f"/gallery/{event['slug']}?access_token={token}")
    assert calls


def test_likes_need_access_to_the_gallery(client):
    open_event, open_photo = _event()
    assert client.post(f"/api/photo/{open_photo}/like").status_code == 200
    closed, closed_photo = _event(passwort="geheim-123")
    assert client.post(f"/api/photo/{closed_photo}/like").status_code == 403
    with client.session_transaction() as s:
        s[f"gallery_access_{closed['id']}"] = True
    assert client.post(f"/api/photo/{closed_photo}/like").status_code == 200
    hidden, hidden_photo = _event(published=0)
    assert client.post(f"/api/photo/{hidden_photo}/like").status_code == 404
    silent, silent_photo = _event(silent=True)
    assert client.post(f"/api/photo/{silent_photo}/like").status_code == 404


# ── S16 ───────────────────────────────────────────────────────────────

def test_the_first_start_logs_the_temporary_password_but_not_a_recovery_code(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with caplog.at_level(logging.WARNING, logger="photobox"):
        db.init_db()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "Startpasswort" in text
    assert not re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", text)
    assert not accounts.has_recovery_code(), "the permanent code is never written to a log"
    assert accounts.password_is_temporary()


def test_the_recovery_code_appears_once_after_the_first_password_change(client):
    accounts.set_password(PASSWORT)
    db.set_setting(accounts.TEMP_KEY, "1")
    db.set_setting(accounts.HASH_KEY, "")
    _login(client)
    client.post("/admin/settings/account", data={"csrf_token": "t", "admin_username": "admin",
                                                 "new_password": "ein-ganz-neues-passwort-42",
                                                 "confirm_password": "ein-ganz-neues-passwort-42",
                                                   "current_password": PASSWORT})
    assert accounts.has_recovery_code()
    with client.session_transaction() as s:
        assert s.get("fresh_recovery"), "shown once on the next page"


def test_the_photo_mail_log_does_not_name_the_guest(client, monkeypatch):
    from app import emailer
    monkeypatch.setattr(emailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(emailer, "send_text_mail", lambda **kw: None)
    _made = db.create_device(name="Mailbox")
    did = _made["id"]
    box = {**db.get_device_by_id(did), "api_key": _made["api_key"]}
    r = client.post("/api/v2/booth/mail", json={"to": "erika.mustermann@example.org", "session_uid": "logtest01", "attach": "link"},
                    headers={"X-API-Key": box["api_key"]})
    assert r.status_code == 200, r.data
    logs = json.dumps(ecosystem.device_logs(did) if hasattr(ecosystem, "device_logs")
                      else [dict(x) for x in db.get_db().execute(
                          "SELECT * FROM device_logs WHERE device_id=?", (did,))], default=str)
    assert "erika" not in logs and "mustermann" not in logs
