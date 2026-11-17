"""The AI-assistant paragraph of the privacy template exists in German and English.

The paragraph says what each MCP extra right makes reachable. It existed in
German only; an English visitor read nothing about it. The English text has
the same content: the same rights in the same order, the share links that
lead to the pictures (they need "photos" since 15.11.2026), credentials never.
"""
import re

import pytest

from app import db, legal


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    db.set_setting("legal_assistant", "Assistent X, Anbieter Y")
    yield
    db.set_setting("legal_assistant", "")


def test_the_english_page_carries_the_assistant_paragraph(client):
    md = legal.page_markdown("datenschutz", "en")
    assert "AI assistant for the administration" in md
    assert "Assistent X, Anbieter Y" in md
    for label in ("“guest photos”", "“remote control”", "“security”", "“updates”"):
        assert label in md, label
    assert "share links" in md and "never hands out credentials" in md


def test_german_and_english_name_the_same_rights_in_the_same_order(client):
    de = legal.assistant_paragraph("X", "de")
    en = legal.assistant_paragraph("X", "en")
    order_de = [m for m in ("Gästefotos", "Fernsteuern", "Sicherheit", "Updates") if m in de]
    order_en = [m for m in ("guest photos", "remote control", "security", "updates") if m in en]
    assert len(order_de) == len(order_en) == 4
    assert [de.index(f"„{m}“") for m in order_de] == sorted(de.index(f"„{m}“") for m in order_de)
    assert [en.index(f"“{m}”") for m in order_en] == sorted(en.index(f"“{m}”") for m in order_en)
    assert "Freigabe-Links" in de and "share links" in en
    assert "Art. 6 Abs. 1 lit. f" in de and "Art. 6(1)(f)" in en


def test_the_german_page_has_no_english_block(client):
    md = legal.page_markdown("datenschutz", "de")
    assert "Summary in English" not in md
    assert len(re.findall("KI-Assistent für die Verwaltung", md)) == 1


def test_the_english_page_through_the_browser(client):
    # Logged in: the operator sees the page even while placeholders are open.
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["lang"] = "en"
    page = client.get("/datenschutz").get_data(as_text=True)
    assert 'lang="en"' in page
    assert "AI assistant for the administration" in page


def test_english_says_what_paket_d_and_paket_a_added_too(client):
    # After the merge of the three branches the English text must carry the
    # same facts as the German one, not only the share links.
    de = legal.assistant_paragraph("X", "de")
    en = legal.assistant_paragraph("X", "en")
    assert "Zugriffsanfragen" in de and "access requests" in en
    assert "nur gekürzt" in de and "only shortened" in en
    assert "vom Betreiber oder von einer Fotobox" in de and "the operator or a photo booth" in en
    assert "Impressum und Datenschutzerklärung" in de and "the legal notice and" in en
    assert "den PC der Fotobox neu" in de and "restarts the program or the photo booth's" in en
    assert "Sprache" in de and "switches the language" in en
    # Final review A1–A3, in both languages.
    assert "entscheidet Zugriffsanfragen" in de and "decides access requests" in en
    assert "veröffentlicht Galerien" in de and "publishes galleries" in en
    assert "welchem Event eine Fotobox ihre Bilder gibt" in de and "which event a photo booth gives its pictures to" in en
    assert "only with “guest photos”" in en
