"""Ein Passwort, das nichts taugt, soll gar nicht erst durchkommen.

Geprüft wird nach dem, was heute empfohlen wird: Länge zählt, Zeichensalat
nicht. „Sommer2024!" erfüllt jede klassische Regel und steht in jeder Liste;
„mein fahrrad steht im keller" erfüllt keine und ist deutlich besser.

Wichtig ist der zweite Teil: Die Prüfung muss an **jeder** Stelle greifen, an
der ein Passwort gesetzt wird — Einrichtung, Einstellungen und beide Wege
zurück. Eine Lücke genügt, und das schwache Passwort ist im System.
"""
import pytest

from app import passwords


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    from app import accounts
    with client.session_transaction() as s:
        # A real login stores the password generation; the account form
        # tests below set a new password, which ends sessions without it.
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
    return client


# ── Die Regel selbst ──────────────────────────────────────
@pytest.mark.parametrize("pw", [
    "", "kurz", "photobox", "passwort123", "P4ssw0rt!", "Sommer2024!",
    "aaaaaaaaaaaaaa", "1234567890123", "qwertzuiopü",
])
def test_these_never_pass(pw):
    ok, meldung = passwords.pruefe(pw)
    assert not ok and meldung


@pytest.mark.parametrize("pw", [
    "mein fahrrad steht im keller",
    "Lina-Jan-2026-Fotos",
    "Xq7!vbnm-Zert9",
    "lila elefant tanzt mittwochs",
])
def test_these_do(pw):
    ok, meldung = passwords.pruefe(pw)
    assert ok, meldung


def test_the_message_says_what_to_change():
    """„Ungültig" hilft niemandem — es muss dastehen, was fehlt."""
    _, meldung = passwords.pruefe("kurz")
    assert "10 Zeichen" in meldung
    _, meldung = passwords.pruefe("Sommer2024!")
    assert "bekanntes Wort" in meldung


def test_what_is_written_on_the_wall_is_not_a_secret():
    """Galeriename, Benutzername, Domain — alles, was ohnehin jeder sieht."""
    ok, _ = passwords.pruefe("hochzeitlina-galerie", ["hochzeitlina"])
    assert not ok


# ── Und überall, wo ein Passwort gesetzt wird ─────────────
def test_the_settings_refuse_a_weak_one(admin, client):
    from app import db
    from app import accounts
    accounts.set_password("das-aktuelle-passwort-7")
    with admin.session_transaction() as s:
        s["admin_epoch"] = accounts.session_epoch()
    vorher = db.get_setting("admin_password_hash")
    antwort = admin.post("/admin/settings/account", data={
        "csrf_token": "test-token", "admin_username": "admin", "current_password": "das-aktuelle-passwort-7",
        "new_password": "Sommer2024!", "confirm_password": "Sommer2024!"}, follow_redirects=True)
    assert antwort.status_code == 200
    assert db.get_setting("admin_password_hash") == vorher


def test_and_accept_a_good_one(admin, client):
    from app import db
    from app import accounts
    accounts.set_password("das-aktuelle-passwort-7")
    with admin.session_transaction() as s:
        s["admin_epoch"] = accounts.session_epoch()
    vorher = db.get_setting("admin_password_hash")
    admin.post("/admin/settings/account", data={
        "csrf_token": "test-token", "admin_username": "admin", "current_password": "das-aktuelle-passwort-7",
        "new_password": "lila elefant tanzt mittwochs",
        "confirm_password": "lila elefant tanzt mittwochs"}, follow_redirects=True)
    assert db.get_setting("admin_password_hash") != vorher


def test_the_way_back_with_the_recovery_code_checks_too(client):
    from app import accounts, db
    client.get("/healthz")
    db.set_setting("admin_username", "admin")
    code = accounts.new_recovery_code()
    ok, _neu, meldung = accounts.redeem_recovery("admin", code, "Sommer2024!")
    assert not ok and "bekanntes Wort" in meldung
    # der Code ist dabei nicht verbraucht worden
    ok, neu, _ = accounts.redeem_recovery("admin", code, "lila elefant tanzt mittwochs")
    assert ok and neu


def test_the_mail_link_checks_too(client):
    from app import accounts, db
    client.get("/healthz")
    db.set_setting(accounts.TOKEN_KEY, "")
    from werkzeug.security import generate_password_hash
    import time
    db.set_setting(accounts.TOKEN_KEY, generate_password_hash("tok123"))
    db.set_setting(accounts.TOKEN_UNTIL, str(int(time.time()) + 600))
    ok, meldung = accounts.redeem_reset_token("tok123", "passwort123")
    assert not ok and "bekanntes Wort" in meldung


def test_the_setup_refuses_a_weak_one(client):
    from app import db
    client.get("/healthz")
    db.set_setting("setup_completed", "")
    vorher = db.get_setting("admin_password_hash")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True      # the wizard is admin-only
    client.post("/setup", data={"admin_username": "admin", "admin_password": "photobox",
                                "base_url": "https://x.de", "language": "de",
                                "gallery_name": "Test"}, follow_redirects=True)
    assert db.get_setting("admin_password_hash") == vorher
    assert db.get_setting("setup_completed") != "1"


def test_the_meter_is_on_the_pages_that_ask_for_a_password(admin, client):
    for pfad in ("/admin/settings", "/admin/recover"):
        seite = admin.get(pfad).get_data(as_text=True)
        assert "passwortstaerke.js" in seite, pfad
