"""Zwei Sprachen — oder eine, wenn der Betreiber das so will.

Zwei getrennte Dinge: Die freien Texte (Überschrift, Impressum, Datenschutz)
gibt es auf Deutsch und Englisch, und der Betreiber kann Englisch für seine
Seiten ganz abschalten. Fehlt eine englische Fassung, erscheint die deutsche —
eine leere Seite wäre schlimmer als eine in der falschen Sprache.
"""
import pytest


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    from app import db
    # Alles zurückstellen, was dieser Test anfasst — sonst färbt er die
    # Einstellungen aller anderen Tests ein.
    beruehrt = ("site_languages", "homepage_headline", "homepage_headline_en",
                "homepage_subtext", "homepage_subtext_en", "impressum_text",
                "impressum_text_en", "datenschutz_text", "datenschutz_text_en",
                # Das Formular schreibt beim Speichern seine ganze Seite —
                # auch die Haken, die dieser Test gar nicht anfasst.
                "homepage_show_galleries", "homepage_show_empty_events",
                "footer_show_powered_by", "guest_upload_enabled",
                "zip_download_enabled", "zip_download_admin_only",
                "selection_download_enabled", "download_sizes_enabled")
    vorher = {k: db.get_setting(k) for k in beruehrt}
    yield client
    for k, v in vorher.items():
        db.set_setting(k, v if v is not None else "")


def test_the_english_headline_is_used_when_set(admin, client):
    from app import db
    db.set_setting("homepage_headline", "Willkommen bei unserer Fotobox!")
    db.set_setting("homepage_headline_en", "Welcome to our photo booth!")
    assert db.text_setting("homepage_headline", "de") == "Willkommen bei unserer Fotobox!"
    assert db.text_setting("homepage_headline", "en") == "Welcome to our photo booth!"


def test_without_an_english_version_the_german_one_appears(admin, client):
    from app import db
    client.get("/healthz")
    db.set_setting("homepage_headline", "Nur deutsch")
    db.set_setting("homepage_headline_en", "")
    assert db.text_setting("homepage_headline", "en") == "Nur deutsch"


def test_the_start_page_switches_with_the_language(admin, client):
    from app import db
    db.set_setting("homepage_headline", "Deutsche Überschrift")
    db.set_setting("homepage_headline_en", "English headline")
    assert "Deutsche Überschrift" in client.get("/").get_data(as_text=True)
    client.post("/lang/en")
    assert "English headline" in client.get("/").get_data(as_text=True)


def test_turning_english_off_removes_the_switch(admin, client):
    from app import db
    db.set_setting("site_languages", "de")
    seite = client.get("/").get_data(as_text=True)
    assert "/lang/en" not in seite


def test_and_nobody_can_switch_past_it(admin, client):
    """Auch nicht mit einem Aufruf von Hand — sonst steht der Gast doch auf Englisch."""
    from app import db
    db.set_setting("homepage_headline", "Deutsche Überschrift")
    db.set_setting("homepage_headline_en", "English headline")
    db.set_setting("site_languages", "de")
    client.post("/lang/en")
    assert "Deutsche Überschrift" in client.get("/").get_data(as_text=True)


def test_english_only_is_possible_too(admin, client):
    from app import db
    db.set_setting("homepage_headline", "Deutsche Überschrift")
    db.set_setting("homepage_headline_en", "English headline")
    db.set_setting("site_languages", "en")
    seite = client.get("/").get_data(as_text=True)
    assert "English headline" in seite and "/lang/de" not in seite


def test_the_legal_pages_are_bilingual_too(admin, client):
    from app import db
    db.set_setting("impressum_text", "Angaben nach § 5 TMG")
    db.set_setting("impressum_text_en", "Legal notice")
    client.post("/lang/en")
    assert "Legal notice" in client.get("/impressum").get_data(as_text=True)
    client.post("/lang/de")
    assert "§ 5 TMG" in client.get("/impressum").get_data(as_text=True)


def test_the_form_saves_all_of_it(admin, client):
    antwort = admin.post("/admin/settings/homepage", data={
        "csrf_token": "test-token", "site_languages": "en",
        "homepage_headline": "Deutsch", "homepage_headline_en": "English",
        "homepage_subtext": "", "homepage_subtext_en": "Sub"})
    assert antwort.status_code in (200, 302)
    from app import db
    assert db.languages() == "en"
    assert db.get_setting("homepage_headline_en") == "English"


def test_a_nonsense_language_setting_falls_back_to_both(admin, client):
    from app import db
    db.set_setting("site_languages", "klingonisch")
    assert db.languages() == "both"
