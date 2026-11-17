"""A brake on every door of the gallery.

Before this, the admin login, the second factor, the recovery page and the
gallery password could be tried as fast as the network allowed. Each door now
counts failed attempts per client address and closes for a while once there
are too many, with one sentence that says when it opens again.

The rule that matters most to the operator: correct answers never count. An
operator who logs in thirty times on the evening of an event is never locked
out, and a correct answer wipes the failures before it.
"""
import re
import secrets

import pytest

from app import accounts, db, sharelinks, totp

PASSWORT = "start-passwort-lang"


@pytest.fixture()
def prepared(client):
    client.get("/healthz")
    db.set_setting("admin_username", "admin")
    accounts.set_password(PASSWORT)
    db.set_setting("admin_totp_enabled", "")
    with client.session_transaction() as s:
        s["csrf_token"] = "test-token"
    return client


def _age_attempts(seconds: float) -> None:
    """Pretend every recorded attempt happened ``seconds`` earlier."""
    con = db.get_db()
    con.execute("UPDATE auth_attempts SET at = at - ?", (seconds,))
    con.commit()
    con.close()


def _login(client, password):
    r = client.post("/admin/login", data={"csrf_token": "test-token", "username": "admin",
                                          "password": password})
    with client.session_transaction() as s:
        drin = bool(s.get("admin_logged_in"))
        s.pop("admin_logged_in", None)
        s["csrf_token"] = "test-token"
    return r, drin


def _minutes_named(text: str) -> bool:
    return bool(re.search(r"\d+\s*(Min|min)", text))


# ── Admin login: 10 failures per address in 15 minutes ───
def test_the_admin_login_closes_after_ten_failures(prepared):
    for _ in range(10):
        r, _ = _login(prepared, "falsch")
        assert r.status_code == 200
    r, drin = _login(prepared, "falsch")
    assert r.status_code == 429
    assert _minutes_named(r.get_data(as_text=True)), "says when it continues"
    # While closed, not even the right password opens it — otherwise the
    # brake would only slow down the wrong guesses.
    r, drin = _login(prepared, PASSWORT)
    assert r.status_code == 429 and not drin


def test_the_admin_login_opens_again_after_fifteen_minutes(prepared):
    for _ in range(10):
        _login(prepared, "falsch")
    _age_attempts(14 * 60)
    assert _login(prepared, PASSWORT)[0].status_code == 429
    _age_attempts(61)
    r, drin = _login(prepared, PASSWORT)
    assert r.status_code == 302 and drin


def test_a_correct_login_wipes_the_failures(prepared):
    for _ in range(9):
        _login(prepared, "falsch")
    assert _login(prepared, PASSWORT)[1]
    for _ in range(9):
        r, _ = _login(prepared, "falsch")
        assert r.status_code == 200
    assert _login(prepared, PASSWORT)[1]


def test_correct_logins_are_never_throttled(prepared):
    for _ in range(30):
        r, drin = _login(prepared, PASSWORT)
        assert r.status_code == 302 and drin


def test_the_count_is_per_address(prepared):
    for _ in range(10):
        _login(prepared, "falsch")
    r = prepared.post("/admin/login", data={"csrf_token": "test-token", "username": "admin",
                                            "password": PASSWORT},
                      environ_base={"REMOTE_ADDR": "10.9.8.7"})
    assert r.status_code == 302


# ── Second factor: 5 failures in 15 minutes ──────────────
@pytest.fixture()
def second_factor(prepared):
    secret = totp.new_secret()
    db.set_setting("admin_totp_secret", secret)
    db.set_setting("admin_totp_enabled", "1")
    db.set_setting("admin_totp_last_step", "-1")
    yield prepared, secret
    db.set_setting("admin_totp_enabled", "")
    db.set_setting("admin_totp_secret", "")


def _code(client, code):
    with client.session_transaction() as s:
        s["pending_admin"] = True
        s["admin_epoch"] = accounts.session_epoch()     # as the password step stores it
        s.pop("admin_logged_in", None)
        s["csrf_token"] = "test-token"
    r = client.post("/admin/login/code", data={"csrf_token": "test-token", "code": code})
    with client.session_transaction() as s:
        return r, bool(s.get("admin_logged_in"))


def _wrong(secret):
    right = totp.code_at(secret)
    return "000000" if right != "000000" else "111111"


def test_the_second_factor_closes_after_five_failures(second_factor):
    client, secret = second_factor
    for _ in range(5):
        assert _code(client, _wrong(secret))[0].status_code == 200
    r, drin = _code(client, totp.code_at(secret))
    assert r.status_code == 429 and not drin
    assert _minutes_named(r.get_data(as_text=True))


def test_a_correct_code_wipes_the_failures(second_factor):
    client, secret = second_factor
    for _ in range(4):
        _code(client, _wrong(secret))
    assert _code(client, totp.code_at(secret))[1]
    db.set_setting("admin_totp_last_step", "-1")           # the same code again, for the test
    for _ in range(4):
        assert _code(client, _wrong(secret))[0].status_code == 200
    assert _code(client, totp.code_at(secret))[1]


def test_correct_codes_are_never_throttled(second_factor):
    client, secret = second_factor
    for _ in range(12):
        db.set_setting("admin_totp_last_step", "-1")
        r, drin = _code(client, totp.code_at(secret))
        assert drin, r.status_code


# ── Recovery: 5 attempts per hour ────────────────────────
def _recover(client, code="AAAA-BBBB-CCCC-DDDD"):
    return client.post("/admin/recover", data={"csrf_token": "test-token", "username": "admin",
                                               "code": code, "new_password": "ein-neues-langes-passwort-7"})


def test_recovery_allows_five_attempts_an_hour(prepared):
    accounts.new_recovery_code()
    for _ in range(5):
        assert "Zu viele Versuche" not in _recover(prepared).get_data(as_text=True)
    r = _recover(prepared)
    assert r.status_code == 429 and _minutes_named(r.get_data(as_text=True))
    _age_attempts(30 * 60)
    assert _recover(prepared).status_code == 429, "an hour, not a quarter of it"
    _age_attempts(31 * 60)
    assert _recover(prepared).status_code == 200


def test_a_successful_recovery_wipes_the_attempts(prepared):
    for _ in range(4):
        _recover(prepared)
    code = accounts.new_recovery_code()
    r = _recover(prepared, code)
    assert "Passwort gesetzt" in r.get_data(as_text=True)
    accounts.new_recovery_code()
    for _ in range(5):
        assert _recover(prepared).status_code == 200


def test_the_recovery_count_survives_other_doors(prepared):
    """A door with a 15-minute window used to wipe every older row, an hour's included."""
    accounts.new_recovery_code()
    for _ in range(5):
        _recover(prepared)
    _age_attempts(20 * 60)
    _login(prepared, "falsch")                    # records with a 15-minute window
    assert _recover(prepared).status_code == 429


# ── Gallery password: 10 failures per event and address ──
@pytest.fixture()
def protected(prepared):
    def make():
        slug = "brems-" + secrets.token_hex(4)
        eid = db.create_event(name="Bremse", slug=slug, upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
        con.commit()
        con.close()
        db.set_event_password(eid, "gaeste-passwort")
        return slug, eid
    return prepared, make


def _gallery(client, slug, eid, password):
    client.get(f"/gallery/{slug}")
    with client.session_transaction() as s:
        token = s.get("csrf_token")
    r = client.post(f"/gallery/{slug}", data={"csrf_token": token, "password": password})
    with client.session_transaction() as s:
        drin = bool(s.pop(f"gallery_access_{eid}", None))
    return r, drin


def test_the_gallery_password_closes_after_ten_failures(protected):
    client, make = protected
    slug, eid = make()
    for _ in range(10):
        assert _gallery(client, slug, eid, "falsch")[0].status_code == 200
    r, drin = _gallery(client, slug, eid, "gaeste-passwort")
    assert r.status_code == 429 and not drin
    assert _minutes_named(r.get_data(as_text=True))
    # Another event is a different door.
    other, other_id = make()
    assert _gallery(client, other, other_id, "gaeste-passwort")[1]


def test_the_right_gallery_password_wipes_the_failures(protected):
    client, make = protected
    slug, eid = make()
    for _ in range(9):
        _gallery(client, slug, eid, "falsch")
    assert _gallery(client, slug, eid, "gaeste-passwort")[1]
    for _ in range(9):
        assert _gallery(client, slug, eid, "falsch")[0].status_code == 200
    assert _gallery(client, slug, eid, "gaeste-passwort")[1]


def test_guests_who_know_the_password_are_never_throttled(protected):
    client, make = protected
    slug, eid = make()
    for _ in range(25):
        r, drin = _gallery(client, slug, eid, "gaeste-passwort")
        assert drin, r.status_code


# ── The counter itself ───────────────────────────────────
def test_checking_costs_no_attempt():
    key = "probe:" + secrets.token_hex(4)
    for _ in range(50):
        assert sharelinks.retry_after(key, 3, 900) == 0
    for _ in range(3):
        sharelinks.record_failure(key)
    wait = sharelinks.retry_after(key, 3, 900)
    assert 0 < wait <= 900
    sharelinks.forget(key)
    assert sharelinks.retry_after(key, 3, 900) == 0
