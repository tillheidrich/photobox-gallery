"""Security review G3, finding S-9 (15.11.2026): logging out ends the session on the server.

The admin session lives in a signed cookie. Logging out only removed the
flag from the browser's copy; a copy taken before (a shared computer, a
proxy log, malware) stayed valid until the password changed. Each login now
gets a session id whose hash the gallery keeps; logging out revokes it, and a
cookie with a revoked id is no login any more.
"""


def _account():
    from app import accounts, db
    db.set_setting("admin_username", "chef")
    db.set_setting("admin_totp_enabled", "")
    accounts.set_password("ein-langes-start-passwort")


def _real_login(client):
    with client.session_transaction() as s:
        s["csrf_token"] = "t"
    r = client.post("/admin/login", data={"csrf_token": "t", "username": "chef",
                                          "password": "ein-langes-start-passwort"})
    assert r.status_code == 302


def test_a_copied_cookie_is_dead_after_logout(app):
    a = app.test_client()
    a.get("/healthz")
    _account()
    _real_login(a)
    assert a.get("/admin/dashboard").status_code == 200
    stolen = a.get_cookie("session").value

    a.get("/admin/logout")
    assert a.get("/admin/dashboard").status_code == 302

    thief = app.test_client()
    thief.set_cookie("session", stolen)
    r = thief.get("/admin/dashboard")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def test_logging_out_one_browser_keeps_the_other_logged_in(app):
    a, b = app.test_client(), app.test_client()
    a.get("/healthz")
    _account()
    _real_login(a)
    _real_login(b)
    a.get("/admin/logout")
    assert b.get("/admin/dashboard").status_code == 200
