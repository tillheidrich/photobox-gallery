"""The login pages say whose gallery this is, not what to type.

Review of 15.11.2026: the headline read "Admin" and the user-name field
suggested "admin" — half of the credentials of every installation that kept
the default. Now the headline is the gallery's name (setup / home page
headline) or "Anmelden", the placeholder says "Benutzername", and neither the
configured user name nor the word "admin" appears in visible text or
placeholders of the login, the second-factor step or the recovery page.
"""
import re

import pytest

from app import accounts, db


def _visible(html: str) -> str:
    """Text a visitor sees plus placeholders and the title — no attributes, URLs or scripts."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    shown = re.findall(r'placeholder="([^"]*)"', html)
    title = re.findall(r"<title>(.*?)</title>", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", html)
    return " ".join([text] + shown + title)


@pytest.fixture()
def gallery(client):
    client.get("/healthz")
    db.set_setting("admin_username", "fotochef")
    db.set_setting("admin_totp_enabled", "")
    accounts.set_password("ein-langes-start-passwort")
    db.set_setting("homepage_headline", "Fotobox Heidrich")
    with client.session_transaction() as s:
        s["csrf_token"] = "t"
    yield client
    from app import sharelinks
    sharelinks.forget("login:127.0.0.1")


def test_the_login_page_names_the_gallery(gallery):
    html = gallery.get("/admin/login").get_data(as_text=True)
    assert re.search(r"<h1>\s*Fotobox Heidrich\s*</h1>", html)
    assert "<title>Anmelden – Fotobox Heidrich</title>" in html
    assert 'placeholder="Benutzername"' in html
    assert 'autocomplete="username"' in html and 'autocomplete="current-password"' in html
    shown = _visible(html)
    assert "fotochef" not in shown
    assert not re.search(r"\badmin\b", shown, re.I), shown


def test_without_a_name_the_headline_is_anmelden(gallery):
    db.set_setting("homepage_headline", "")
    html = gallery.get("/admin/login").get_data(as_text=True)
    assert re.search(r"<h1>\s*Anmelden\s*</h1>", html)
    assert "<title>Anmelden</title>" in html
    assert not re.search(r"\badmin\b", _visible(html), re.I)


def test_the_second_factor_step_and_the_recovery_page_say_no_admin(gallery, monkeypatch):
    shown = _visible(gallery.get("/admin/recover").get_data(as_text=True))
    assert not re.search(r"\badmin\b", shown, re.I) and "fotochef" not in shown
    db.set_setting("admin_totp_enabled", "1")
    db.set_setting("admin_totp_secret", "JBSWY3DPEHPK3PXP")
    r = gallery.post("/admin/login", data={"csrf_token": "t", "username": "fotochef",
                                           "password": "ein-langes-start-passwort"})
    html = r.get_data(as_text=True)
    assert 'name="code"' in html
    shown = _visible(html)
    assert not re.search(r"\badmin\b", shown, re.I) and "fotochef" not in shown
    db.set_setting("admin_totp_enabled", "")
