"""Unfinished legal pages never reach a guest unnoticed.

Audit of 15.11.2026: a live instance showed the raw Impressum template —
"[Name des Betreibers]", "[Straße und Hausnummer]" — to every visitor, and
nothing told the operator. Now a page with a placeholder shows guests one
neutral line, the operator the full text with what is missing, and the
dashboard names the unfinished page. The Impressum is built from the facts
in Einstellungen → Rechtliches, without the old boilerplate.
"""
import secrets

import pytest

from app import db, legal
from conftest import LEGAL_FACTS


@pytest.fixture()
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
    yield


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "x"


def _clear_facts():
    for k in legal.LEGAL_FIELDS + ("impressum_text", "datenschutz_text",
                                   "impressum_text_en", "datenschutz_text_en"):
        db.set_setting(k, "")


# ── what counts as a placeholder ──────────────────────────────

def test_placeholders_are_found_and_links_are_not():
    md = ("**[Name des Betreibers]**\n[OFFEN: Rechenzentrum] [PRÜFEN: Rolle]\n"
          "siehe [Impressum](/impressum), Liste [x], Fußnote [1], [Ref][2]\n[Name des Betreibers]")
    assert legal.placeholders(md) == ["[Name des Betreibers]", "[OFFEN: Rechenzentrum]",
                                      "[PRÜFEN: Rolle]"]
    assert legal.placeholders("Alles ausgefüllt, [Datenschutzerklärung](/datenschutz).") == []


# ── guests and the operator ───────────────────────────────────

def test_an_unfilled_impressum_shows_guests_a_neutral_line(client, schema):
    _clear_facts()
    body = client.get("/impressum").get_data(as_text=True)
    assert "Der Betreiber hat diese Seite noch nicht ausgefüllt." in body
    assert "[Name des Betreibers]" not in body and "[Straße" not in body
    assert "Hinweis für den Betreiber" not in body


def test_the_operator_sees_the_template_and_what_is_missing(client, schema):
    _clear_facts()
    _admin(client)
    body = client.get("/impressum").get_data(as_text=True)
    assert "Nur für dich sichtbar" in body
    assert "[Name des Betreibers]" in body and "[E-Mail-Adresse]" in body


def test_an_own_text_with_an_open_point_is_held_back_too(client, schema, legal_facts):
    db.set_setting("impressum_text", "## Impressum\n\n[OFFEN: Wirtschafts-IdNr.]\n\nMuster Fotobox")
    body = client.get("/impressum").get_data(as_text=True)
    assert "noch nicht ausgefüllt" in body and "OFFEN" not in body
    db.set_setting("impressum_text", "## Impressum\n\nMuster Fotobox, siehe [Datenschutz](/datenschutz)")
    body = client.get("/impressum").get_data(as_text=True)
    assert "Muster Fotobox" in body and "noch nicht ausgefüllt" not in body


def test_an_english_visitor_gets_the_neutral_line_in_english(client, schema, legal_facts):
    db.set_setting("site_languages", "both")
    db.set_setting("datenschutz_text_en", "## Privacy\n\n[Name of the operator]")
    client.post("/lang/en")
    body = client.get("/datenschutz").get_data(as_text=True)
    assert "The operator has not completed this page yet." in body
    assert "[Name of the operator]" not in body


def test_the_complete_privacy_page_has_no_operator_note(client, schema, legal_facts):
    body = client.get("/datenschutz").get_data(as_text=True)
    assert "noch nicht ausgefüllt" not in body
    assert "Hinweis für den Betreiber" not in body and "Nur für dich sichtbar" not in body
    assert "<table>" in body and "pb-theme" in body          # the cookie table renders
    assert "Musterweg 1" in body and "kontakt@example.org" in body
    assert "§ 38 BDSG" in body


# ── the Impressum itself ──────────────────────────────────────

def test_the_impressum_is_built_from_the_facts_without_boilerplate(client, schema, legal_facts):
    body = client.get("/impressum").get_data(as_text=True)
    assert "§ 5 Digitale-Dienste-Gesetz (DDG)" in body
    assert "Muster Fotobox" in body and "Musterweg 1<br" in body and "12345 Musterstadt" in body
    assert "kontakt@example.org" in body
    assert "größter Sorgfalt" not in body, "filler the review of 11.11.2026 asked to drop"
    assert "MStV" not in body, "a photo gallery is not journalistic-editorial content"
    assert "ec.europa.eu/consumers/odr" not in body, "the ODR platform is switched off"
    assert "Telefon" not in body, "no phone given, no phone line"


def test_optional_impressum_lines_appear_when_filled(client, schema, legal_facts):
    db.set_setting("legal_phone", "0123 456789")
    db.set_setting("legal_tax", "Kleinunternehmer nach § 19 UStG, keine USt-IdNr.")
    db.set_setting("legal_editorial", "Max Muster, Anschrift wie oben")
    db.set_setting("legal_dispute", "Wir nehmen nicht an Streitbeilegungsverfahren teil.")
    body = client.get("/impressum").get_data(as_text=True)
    assert "Telefon: 0123 456789" in body
    assert "§ 19 UStG" in body and "§ 18 Abs. 2" in body and "Streitbeilegung" in body


# ── the dashboard and the form ────────────────────────────────

def test_the_dashboard_warns_while_a_page_is_unfinished(client, schema):
    _clear_facts()
    _admin(client)
    body = client.get("/admin/dashboard").get_data(as_text=True)
    assert "legal-unfinished" in body and "Impressum" in body
    for k, v in LEGAL_FACTS.items():
        db.set_setting(k, v)
    body = client.get("/admin/dashboard").get_data(as_text=True)
    assert "legal-unfinished" not in body


def test_status_checks_the_english_text_only_when_there_is_one(client, schema, legal_facts):
    assert legal.status() == {}
    db.set_setting("impressum_text_en", "## Legal notice\n\n[Street and number]")
    assert legal.status() == {"impressum": ["[Street and number]"]}


def test_the_form_saves_the_new_impressum_fields(client, schema):
    _admin(client)
    data = {"csrf_token": "x", "impressum_text": "", "datenschutz_text": "",
            "impressum_text_en": "", "datenschutz_text_en": "", "legal_role": "controller",
            "legal_address": "Musterweg 2, 12345 Musterstadt", "legal_phone": "0123",
            "legal_tax": "Kleinunternehmer", "legal_server_logs": "keine"}
    assert client.post("/admin/settings/legal", data=data).status_code in (200, 302)
    assert db.get_setting("legal_address") == "Musterweg 2, 12345 Musterstadt"
    assert db.get_setting("legal_server_logs") == "keine"
    form = client.get("/admin/settings/legal").get_data(as_text=True)
    assert 'name="legal_address"' in form and 'name="legal_assistant"' in form
    assert "Leer lassen um die Seite auszublenden" not in form, "it never hid the page"
    assert "§ 5 TMG" not in form


# ── analytics never sees a key ────────────────────────────────

def test_the_measuring_script_stays_off_pages_with_a_key_in_the_path(client, schema):
    db.set_setting("umami_src", "https://umami.example.com/script.js")
    db.set_setting("umami_website_id", "wid")
    home = client.get("/").get_data(as_text=True)
    assert 'data-website-id="wid"' in home and 'data-exclude-search="true"' in home
    for path in ("/p/abc123", "/notify/confirm/" + secrets.token_urlsafe(8), "/s/xyz", "/g/ABC"):
        body = client.get(path).get_data(as_text=True)
        assert 'data-website-id="wid"' not in body, path


# ── double opt-in: the 7 days the mail promises ───────────────

def test_an_unconfirmed_signup_expires_and_is_purged(client, schema):
    k = secrets.token_hex(3)
    eid = db.create_event(name="Optin", slug=f"optin-{k}", upload_token=f"t-{k}")
    old_token, new_token = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    db.add_subscription(eid, f"alt-{k}@example.org", old_token)
    db.add_subscription(eid, f"neu-{k}@example.org", new_token)
    con = db.get_db()
    con.execute("UPDATE notify_subscriptions SET created_at=datetime('now','-8 days') WHERE token=?",
                (old_token,))
    con.commit()
    con.close()
    assert db.confirm_subscription(old_token) is None, "the link is older than 7 days"
    assert db.confirm_subscription(new_token) is not None
    assert db.purge_unconfirmed_subscriptions() >= 1
    con = db.get_db()
    left = [r[0] for r in con.execute("SELECT token FROM notify_subscriptions WHERE event_id=?", (eid,))]
    con.close()
    assert left == [new_token]
