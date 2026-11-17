"""The admin account form (settings, "Admin-Account").

Review of 15.11.2026: a session alone could change the user name and the
password — whoever found an unlocked laptop owned the gallery for good. The
confirmation field was only compared when it was filled in. Now: the current
password is needed for either change, wrong answers count on the login brake,
the confirmation must match, and a new user name ends the other sessions like
a new password does. The forced change after the first login (temporary
password) keeps working: the temporary password is the current one.
"""
import pytest

from app import accounts, db
from app.blueprints import admin as admin_bp

AKTUELL = "das-aktuelle-passwort-7"
NEU = "lila elefant tanzt mittwochs"


@pytest.fixture()
def logged_in(client):
    client.get("/healthz")
    db.set_setting("admin_username", "admin")
    db.set_setting("admin_totp_enabled", "")
    accounts.set_password(AKTUELL)
    from app import sharelinks
    sharelinks.forget("login:127.0.0.1")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "t"
    yield client
    sharelinks.forget("login:127.0.0.1")


def _post(client, **form):
    return client.post("/admin/settings/account", data={"csrf_token": "t", "admin_username": "admin", **form})


def _logs_in(client, user, password):
    with client.session_transaction() as s:
        s["csrf_token"] = "t"
    r = client.post("/admin/login", data={"csrf_token": "t", "username": user, "password": password})
    return r.status_code == 302 and "/admin/login" not in r.headers.get("Location", "")


def test_a_new_password_needs_the_current_one(logged_in):
    vorher = db.get_setting("admin_password_hash")
    _post(logged_in, new_password=NEU, confirm_password=NEU)
    assert db.get_setting("admin_password_hash") == vorher
    _post(logged_in, new_password=NEU, confirm_password=NEU, current_password="falsch")
    assert db.get_setting("admin_password_hash") == vorher
    _post(logged_in, new_password=NEU, confirm_password=NEU, current_password=AKTUELL)
    assert db.get_setting("admin_password_hash") != vorher


def test_a_new_user_name_needs_the_current_password(logged_in):
    _post(logged_in, admin_username="chef")
    assert db.get_setting("admin_username") == "admin"
    _post(logged_in, admin_username="chef", current_password=AKTUELL)
    assert db.get_setting("admin_username") == "chef"


def test_the_confirmation_is_not_optional(logged_in):
    vorher = db.get_setting("admin_password_hash")
    _post(logged_in, new_password=NEU, confirm_password="", current_password=AKTUELL)
    assert db.get_setting("admin_password_hash") == vorher


def test_wrong_current_passwords_hit_the_login_brake(logged_in, monkeypatch):
    monkeypatch.setattr(admin_bp.time, "sleep", lambda s: None)
    for _ in range(admin_bp.LOGIN_LIMIT):
        _post(logged_in, admin_username="chef", current_password="geraten")
    # Now even the right one waits, here and at the login
    _post(logged_in, admin_username="chef", current_password=AKTUELL)
    assert db.get_setting("admin_username") == "admin"
    assert not _logs_in(logged_in.application.test_client(), "admin", AKTUELL)


def test_a_new_user_name_ends_the_other_sessions(app, logged_in):
    other = app.test_client()
    with other.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
    assert other.get("/admin/dashboard").status_code == 200
    _post(logged_in, admin_username="chef", current_password=AKTUELL)
    assert logged_in.get("/admin/dashboard").status_code == 200, "the session that changed it stays in"
    r = other.get("/admin/dashboard")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def test_nothing_is_written_when_the_password_is_refused(logged_in):
    """A rename together with a refused password changes neither."""
    _post(logged_in, admin_username="chef", new_password="kurz", confirm_password="kurz",
          current_password=AKTUELL)
    assert db.get_setting("admin_username") == "admin"


def test_the_forced_first_change_works_with_the_temporary_password(client):
    client.get("/healthz")
    db.set_setting("admin_username", "admin")
    db.set_setting("admin_totp_enabled", "")
    accounts.set_password("start-passwort-aus-dem-log")
    db.set_setting(accounts.TEMP_KEY, "1")
    with client.session_transaction() as s:
        s["csrf_token"] = "t"
    r = client.post("/admin/login", data={"csrf_token": "t", "username": "admin",
                                          "password": "start-passwort-aus-dem-log"})
    assert r.headers["Location"].endswith("/admin/settings")
    page = client.get("/admin/settings").get_data(as_text=True)
    assert 'name="current_password"' in page and "Startpasswort" in page
    _post(client, new_password=NEU, confirm_password=NEU, current_password="start-passwort-aus-dem-log")
    assert not accounts.password_is_temporary()
    assert _logs_in(client.application.test_client(), "admin", NEU)
