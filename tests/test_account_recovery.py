"""Zugang zur eigenen Galerie — ohne Umweg über den Server.

Das Startpasswort stand bisher in einer Umgebungsvariablen und hieß im
Zweifel „admin". Wer es später vergaß, brauchte eine Terminalsitzung auf dem
Server. Beides ist hier geprüft: kein schwaches Standardpasswort mehr, und
ein Wiederherstellungscode, der im Browser reicht.
"""
import pytest

from app import accounts, db


@pytest.fixture()
def admin_client(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        # A real login stores the password generation; without it the
        # session counts as one from before the last password change.
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "test-token"
    return client


@pytest.fixture(autouse=True)
def _clean(client):
    """Jeder Test startet mit bekannten Zugangsdaten."""
    client.get("/healthz")
    # Die Verwaltung prüft bei jeder schreibenden Anfrage das Formular-Zeichen.
    # Im Browser kommt es mit der Seite; hier wird es gesetzt.
    with client.session_transaction() as s:
        s["csrf_token"] = "test-token"
    db.set_setting("admin_username", "admin")
    accounts.set_password("start-passwort")
    db.set_setting(accounts.HASH_KEY, "")
    db.set_setting(accounts.MADE_KEY, "")
    yield


# ── Kein schwaches Standardpasswort ───────────────────────
def test_a_fresh_gallery_never_gets_the_password_admin(tmp_path, monkeypatch, caplog):
    """Ohne ADMIN_PASSWORD wird ein Zufallspasswort erzeugt — nicht „admin"."""
    import logging
    import sqlite3

    from werkzeug.security import check_password_hash

    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    probe = tmp_path / "probe.sqlite"

    def fresh():
        con = sqlite3.connect(probe)
        con.row_factory = sqlite3.Row
        return con

    monkeypatch.setattr(db, "get_db", fresh)
    with caplog.at_level(logging.WARNING, logger="photobox"):
        db.init_db()
    con = fresh()
    row = con.execute("SELECT value FROM settings WHERE key='admin_password_hash'").fetchone()
    assert row and not check_password_hash(row["value"], "admin")
    # Das erzeugte Passwort steht genau einmal im Protokoll — sonst käme niemand rein
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "Erste Anmeldung" in logged and "Wiederherstellungscode" in logged
    temp = con.execute("SELECT value FROM settings WHERE key='admin_password_temp'").fetchone()
    assert temp["value"] == "1"
    con.close()


def test_the_first_login_with_a_generated_password_asks_for_a_new_one(client):
    db.set_setting(accounts.TEMP_KEY, "1")
    r = client.post("/admin/login", data={"csrf_token": "test-token", "username": "admin", "password": "start-passwort"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/settings")


def test_setting_a_password_clears_the_starter_flag(admin_client):
    db.set_setting(accounts.TEMP_KEY, "1")
    admin_client.post("/admin/settings/account", data={
        "csrf_token": "test-token", "new_password": "ein-gutes-passwort", "confirm_password": "ein-gutes-passwort",
        "current_password": "start-passwort"})
    assert not accounts.password_is_temporary()
    r = admin_client.post("/admin/login", data={"csrf_token": "test-token", "username": "admin", "password": "ein-gutes-passwort"})
    assert r.headers["Location"].endswith("/admin/dashboard")


def test_a_typo_in_the_confirmation_locks_nobody_out(admin_client):
    """Das Formular fragt zweimal — bisher wurde die zweite Eingabe ignoriert."""
    admin_client.post("/admin/settings/account", data={
        "csrf_token": "test-token", "new_password": "neues-passwort", "confirm_password": "neues-pasword",
        "current_password": "start-passwort"})
    r = admin_client.post("/admin/login", data={"csrf_token": "test-token", "username": "admin", "password": "start-passwort"})
    assert r.status_code == 302        # das alte Passwort gilt weiter
    admin_client.post("/admin/settings/account", data={
        "csrf_token": "test-token", "new_password": "kurz", "confirm_password": "kurz",
        "current_password": "start-passwort"})
    assert client_login_works(admin_client, "start-passwort")


def client_login_works(c, password: str) -> bool:
    r = c.post("/admin/login", data={"csrf_token": "test-token", "username": "admin", "password": password})
    return r.status_code == 302


# ── Wiederherstellungscode ────────────────────────────────
def test_the_code_is_readable_and_unambiguous():
    code = accounts.make_recovery_code()
    assert len(code) == 19 and code.count("-") == 3
    for verwechselbar in "IO01":
        assert verwechselbar not in code.replace("-", "")


def test_the_code_is_stored_only_as_a_hash(admin_client):
    code = accounts.new_recovery_code()
    stored = db.get_setting(accounts.HASH_KEY)
    assert stored and accounts.normalize(code) not in stored
    assert accounts.check_recovery(code)
    assert accounts.check_recovery(code.lower().replace("-", " "))     # so, wie man es abtippt
    assert not accounts.check_recovery("ABCD-EFGH-JKLM-NPQR")


def test_the_whole_way_back_without_the_server(client):
    code = accounts.new_recovery_code()
    r = client.post("/admin/recover", data={"csrf_token": "test-token", "username": "admin", "code": code,
                                            "new_password": "endlich-wieder-drin"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "Passwort gesetzt" in body

    assert client_login_works(client, "endlich-wieder-drin")
    # Der alte Code ist verbraucht, ein neuer steht auf der Seite
    assert not accounts.check_recovery(code)
    import re
    neu = re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", body)
    assert neu and accounts.check_recovery(neu.group(0))


def test_the_code_alone_is_not_enough(client):
    code = accounts.new_recovery_code()
    r = client.post("/admin/recover", data={"csrf_token": "test-token", "username": "jemand-anders", "code": code,
                                            "new_password": "fremdes-passwort"})
    assert "stimmt nicht" in r.get_data(as_text=True)
    assert client_login_works(client, "start-passwort")
    assert accounts.check_recovery(code)         # unverbraucht


def test_a_short_new_password_is_refused(client):
    code = accounts.new_recovery_code()
    r = client.post("/admin/recover", data={"csrf_token": "test-token", "username": "admin", "code": code, "new_password": "kurz"})
    assert "acht Zeichen" in r.get_data(as_text=True)
    assert accounts.check_recovery(code)


def test_guessing_the_code_gets_you_locked_out(client):
    accounts.new_recovery_code()
    for _ in range(5):
        client.post("/admin/recover", data={"csrf_token": "test-token", "username": "admin", "code": "AAAA-BBBB-CCCC-DDDD",
                                            "new_password": "egal-egal-egal"})
    r = client.post("/admin/recover", data={"csrf_token": "test-token", "username": "admin", "code": "AAAA-BBBB-CCCC-DDDD",
                                            "new_password": "egal-egal-egal"})
    assert "Zu viele Versuche" in r.get_data(as_text=True)


def test_without_a_code_the_page_says_so_instead_of_pretending(client):
    r = client.get("/admin/recover")
    assert "kein Wiederherstellungscode hinterlegt" in r.get_data(as_text=True)


def test_the_settings_page_shows_a_new_code_exactly_once(admin_client):
    admin_client.post("/admin/settings/recovery", data={"csrf_token": "test-token"})
    first = admin_client.get("/admin/settings").get_data(as_text=True)
    import re
    found = re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", first)
    assert found, "Der frische Code muss einmal zu sehen sein"
    again = admin_client.get("/admin/settings").get_data(as_text=True)
    assert found.group(0) not in again          # danach nie wieder
    assert "Ein Code ist hinterlegt" in again


def test_the_login_page_points_the_way(client):
    assert "/admin/recover" in client.get("/admin/login").get_data(as_text=True)


# ── Zweiter Weg zurück: Link per E-Mail ───────────────────
class _FakeSMTP:
    """Ein Postausgang, der nichts verschickt, aber alles mitschreibt."""

    sent: list = []

    def __init__(self, *a, **kw):
        pass

    def __call__(self, *a, **kw):
        return self

    def ehlo(self, *a, **kw):
        return (250, b"ok")

    def starttls(self, **kw):
        return None

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)

    def quit(self):
        return None


@pytest.fixture()
def postausgang(monkeypatch):
    import smtplib

    from app import emailer

    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP())
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP())
    emailer.set_smtp_config({"host": "smtp.example.de", "port": 465, "username": "mailuser",
                             "password": "geheim", "from_email": "mail@photobox-gallery.com",
                             "use_ssl": True, "use_tls": False})
    db.set_setting("admin_email", "admin@example.de")
    yield _FakeSMTP
    emailer.set_smtp_config({"host": "", "from_email": ""})
    db.set_setting("admin_email", "")


def test_without_a_mailbox_the_page_offers_no_link(client):
    assert "Link per E-Mail anfordern" not in client.get("/admin/recover").get_data(as_text=True)


def test_the_link_arrives_and_works_exactly_once(client, postausgang):
    import re

    page = client.get("/admin/recover").get_data(as_text=True)
    assert "Link per E-Mail anfordern" in page

    r = client.post("/admin/recover", data={"csrf_token": "test-token", "send_link": "1"})
    assert "unterwegs" in r.get_data(as_text=True)
    assert len(postausgang.sent) == 1
    mail = postausgang.sent[0]
    assert mail["To"] == "admin@example.de" and mail["From"] == "mail@photobox-gallery.com"
    body = mail.get_content()
    link = re.search(r"https?://\S+/admin/recover\?token=(\S+)", body)
    assert link, body
    token = link.group(1)

    # Der Link zeigt das Formular, nicht schon das Ergebnis
    form = client.get(f"/admin/recover?token={token}").get_data(as_text=True)
    assert "Der Link ist gültig" in form

    done = client.post("/admin/recover", data={"csrf_token": "test-token", "token": token, "new_password": "frisch-gesetztes-pw"})
    assert "Passwort gesetzt" in done.get_data(as_text=True)
    assert client_login_works(client, "frisch-gesetztes-pw")

    # Zweiter Versuch mit demselben Link läuft ins Leere
    again = client.post("/admin/recover", data={"csrf_token": "test-token", "token": token, "new_password": "noch-ein-versuch"})
    assert "abgelaufen oder schon benutzt" in again.get_data(as_text=True)
    assert client_login_works(client, "frisch-gesetztes-pw")


def test_an_expired_link_is_no_key(client, postausgang):
    from app import accounts as acc

    client.post("/admin/recover", data={"csrf_token": "test-token", "send_link": "1"})
    db.set_setting(acc.TOKEN_UNTIL, "1")          # in der Vergangenheit
    assert not acc.check_reset_token("egal")
    r = client.post("/admin/recover", data={"csrf_token": "test-token", "token": "irgendwas", "new_password": "langes-passwort"})
    assert "abgelaufen" in r.get_data(as_text=True)


def test_the_answer_does_not_reveal_the_admin_address(client, postausgang):
    r = client.post("/admin/recover", data={"csrf_token": "test-token", "send_link": "1"}).get_data(as_text=True)
    assert "musterbox" not in r and "Wenn eine Adresse hinterlegt ist" in r


def test_the_test_mail_button_uses_the_configured_mailbox(admin_client, postausgang):
    r = admin_client.post("/admin/settings/mail/test", data={"csrf_token": "test-token"},
                          follow_redirects=True)
    assert "verschickt" in r.get_data(as_text=True)
    assert postausgang.sent[-1]["To"] == "admin@example.de"
    assert "Photobox Gallery" in postausgang.sent[-1]["Subject"]


def test_saving_mail_settings_keeps_the_password(admin_client, postausgang):
    from app import emailer

    admin_client.post("/admin/settings/mail", data={
        "csrf_token": "test-token", "host": "smtp.example.de", "port": "465",
        "username": "mailuser", "password": "", "security": "ssl",
        "from_email": "mail@photobox-gallery.com", "admin_email": "admin@example.de"})
    cfg = emailer.get_smtp_config()
    assert cfg["password"] == "geheim" and cfg["use_ssl"] and cfg["port"] == 465

    page = admin_client.get("/admin/settings/mail").get_data(as_text=True)
    assert "geheim" not in page          # das Kennwort steht nie wieder auf der Seite
    assert "smtp.example.de" in page


# ── Zweiter Faktor ────────────────────────────────────────
def test_the_totp_code_matches_what_an_authenticator_app_computes():
    """Gegenprobe mit dem Testvektor aus RFC 6238 (SHA1, 8 Ziffern gekürzt auf 6)."""
    import base64

    from app import totp

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(secret, 59) == "287082"           # RFC 6238, Tabelle
    assert totp.code_at(secret, 1111111109) == "081804"
    assert len(totp.new_secret()) >= 32


def test_a_code_works_once_and_not_twice(monkeypatch):
    from app import totp

    secret = totp.new_secret()
    now = 1_700_000_000
    code = totp.code_at(secret, now)
    ok, step = totp.verify(secret, code, when=now)
    assert ok
    again, _ = totp.verify(secret, code, when=now, last_step=step)
    assert not again                      # derselbe Schritt zählt nicht noch einmal
    assert not totp.verify(secret, "000000", when=now)[0]
    # Eine halbe Minute daneben wird noch akzeptiert, zwei Minuten nicht
    assert totp.verify(secret, totp.code_at(secret, now - 30), when=now)[0]
    assert not totp.verify(secret, totp.code_at(secret, now - 120), when=now)[0]


def test_the_provisioning_link_is_what_the_apps_expect():
    from app import totp

    uri = totp.provisioning_uri("ABCD", "admin@photobox-gallery.com")
    assert uri.startswith("otpauth://totp/Photobox%20Gallery%3Aadmin%40photobox-gallery.com?")
    assert "secret=ABCD" in uri and "period=30" in uri and "digits=6" in uri


def test_switching_it_on_needs_a_real_code_and_then_guards_the_login(admin_client):
    from app import totp

    admin_client.post("/admin/settings/totp/start", data={"csrf_token": "test-token"})
    with admin_client.session_transaction() as s:
        secret = s["totp_pending"]
    page = admin_client.get("/admin/settings").get_data(as_text=True)
    assert "totp/qr.png" in page and secret in page       # Schlüssel zum Abtippen
    assert admin_client.get("/admin/settings/totp/qr.png").status_code == 200

    admin_client.post("/admin/settings/totp/confirm", data={"csrf_token": "test-token", "code": "000000"})
    assert (db.get_setting("admin_totp_enabled") or "") != "1"      # falscher Code: bleibt aus

    admin_client.post("/admin/settings/totp/confirm",
                      data={"csrf_token": "test-token", "code": totp.code_at(secret)})
    assert db.get_setting("admin_totp_enabled") == "1"

    with admin_client.session_transaction() as s:
        s.clear()
    admin_client.get("/admin/login")        # wie ein Browser: Seite holen, Zeichen mitnehmen
    with admin_client.session_transaction() as s:
        s["csrf_token"] = "test-token"
    # Der Code vom Einschalten ist verbraucht — wie im Betrieb, wo zwischen
    # Einrichten und Anmelden mehr als eine halbe Minute liegt.
    db.set_setting("admin_totp_last_step", "-1")
    r = admin_client.post("/admin/login", data={"csrf_token": "test-token", "username": "admin", "password": "start-passwort"})
    assert r.status_code == 200 and "Authenticator" in r.get_data(as_text=True)
    with admin_client.session_transaction() as s:
        assert not s.get("admin_logged_in") and s.get("pending_admin")

    bad = admin_client.post("/admin/login/code", data={"csrf_token": "test-token", "code": "123456"})
    assert "stimmt nicht" in bad.get_data(as_text=True)
    good = admin_client.post("/admin/login/code", data={"csrf_token": "test-token", "code": totp.code_at(db.get_setting("admin_totp_secret"))})
    assert good.status_code == 302
    db.set_setting("admin_totp_enabled", ""); db.set_setting("admin_totp_secret", "")


def test_the_recovery_code_also_turns_the_second_factor_off(client):
    """„Telefon weg" muss lösbar sein — sonst sperrt der zweite Faktor endgültig aus."""
    from app import totp

    secret = totp.new_secret()
    db.set_setting("admin_totp_secret", secret)
    db.set_setting("admin_totp_enabled", "1")
    code = accounts.new_recovery_code()

    client.post("/admin/recover", data={"csrf_token": "test-token", "username": "admin", "code": code, "new_password": "wieder-drin-ohne-app"})
    assert (db.get_setting("admin_totp_enabled") or "") != "1"
    r = client.post("/admin/login", data={"csrf_token": "test-token", "username": "admin", "password": "wieder-drin-ohne-app"})
    assert r.status_code == 302 and "/admin/dashboard" in r.headers["Location"]


def test_the_mail_link_leaves_the_second_factor_standing(client, postausgang):
    """Ein übernommenes Postfach darf nicht den ganzen Zugang bedeuten."""
    import re

    from app import totp

    db.set_setting("admin_totp_secret", totp.new_secret())
    db.set_setting("admin_totp_enabled", "1")
    client.post("/admin/recover", data={"csrf_token": "test-token", "send_link": "1"})
    token = re.search(r"token=(\S+)", postausgang.sent[-1].get_content()).group(1)
    client.post("/admin/recover", data={"csrf_token": "test-token", "token": token, "new_password": "neues-passwort-x"})
    assert db.get_setting("admin_totp_enabled") == "1"
    r = client.post("/admin/login", data={"csrf_token": "test-token", "username": "admin", "password": "neues-passwort-x"})
    assert r.status_code == 200 and "Authenticator" in r.get_data(as_text=True)
    db.set_setting("admin_totp_enabled", ""); db.set_setting("admin_totp_secret", "")
