"""Security review G3, finding S-16 (15.11.2026): the login brake counts per account too.

The brake counted failures per address only. Someone with many addresses
(a botnet, IPv6 privacy addresses) could guess the one admin password
without ever being slowed down. Failures now also count for the account;
above ACCOUNT_LIMIT in the window every address waits.
"""


def _try(client, ip, password="falsch"):
    with client.session_transaction() as s:
        s["csrf_token"] = "t"
    return client.post("/admin/login", data={"csrf_token": "t", "username": "chef", "password": password},
                       environ_base={"REMOTE_ADDR": ip})


def test_many_addresses_do_not_get_around_the_brake(client, monkeypatch):
    from app import accounts, db, sharelinks
    from app.blueprints import admin
    client.get("/healthz")
    db.set_setting("admin_username", "chef")
    db.set_setting("admin_totp_enabled", "")
    accounts.set_password("ein-langes-start-passwort")
    monkeypatch.setattr(admin.time, "sleep", lambda s: None)
    monkeypatch.setattr(admin, "ACCOUNT_LIMIT", 6)
    sharelinks.forget("login-account")
    for i in range(6):
        assert _try(client, f"198.51.100.{i}").status_code == 200       # wrong, but not braked
    r = _try(client, "203.0.113.77", "ein-langes-start-passwort")
    assert r.status_code == 429, "a fresh address is braked once the account saw too many failures"
    sharelinks.forget("login-account")
    assert _try(client, "203.0.113.78", "ein-langes-start-passwort").status_code == 302
