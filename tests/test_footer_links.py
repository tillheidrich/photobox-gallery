"""Die Fußzeile gehört dem Betreiber.

Impressum und Datenschutz stehen immer da — in Deutschland Pflicht. Die FAQ
nur, wenn es welche gibt: Ein Link auf eine leere Seite ist ein Fehler. Alles
Weitere bestimmt der Betreiber, so viele Links wie er braucht, zweisprachig.
"""
import pytest


@pytest.fixture()
def aufgeraeumt(client):
    client.get("/healthz")
    from app import db
    # Das Homepage-Formular speichert beim Abschicken *alle* seine Felder mit.
    # Ein Test, der nur die Fußzeile meint, würde sonst die Einstellungen aller
    # anderen Tests umlegen — deshalb sichern wir hier den ganzen Satz.
    keys = ("footer_links_json", "footer_custom_link_label", "footer_custom_link_url",
            "faq_intro_text", "site_languages",
            "homepage_headline", "homepage_subtext", "homepage_headline_en", "homepage_subtext_en",
            "homepage_show_galleries", "homepage_show_empty_events",
            "footer_show_powered_by", "guest_upload_enabled",
            "zip_download_enabled", "zip_download_admin_only",
            "selection_download_enabled", "download_sizes_enabled",
            "silent_mode", "storage_warn_percent", "gallery_texts_json")
    vorher = {k: db.get_setting(k) for k in keys}
    yield client
    for k, v in vorher.items():
        db.set_setting(k, v if v is not None else "")


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"


def test_pflichtseiten_stehen_immer_in_der_fusszeile(aufgeraeumt):
    seite = aufgeraeumt.get("/").get_data(as_text=True)
    assert "/impressum" in seite and "/datenschutz" in seite


def test_ohne_faq_kein_faq_link(aufgeraeumt):
    from app import db, footer
    db.set_setting("faq_intro_text", "")
    if db.get_faq_items():
        pytest.skip("Diese Installation hat FAQ-Einträge")
    assert all(l["url"] != "/faq" for l in footer.links())
    db.set_setting("faq_intro_text", "Fragen und Antworten")
    assert any(l["url"] == "/faq" for l in footer.links())


def test_eigene_links_erscheinen_in_der_reihenfolge_des_formulars(aufgeraeumt):
    from app import footer
    footer.save([{"label": "Mein Studio", "url": "https://studio.example", "new_tab": True},
                 {"label": "Preise", "url": "/preise", "new_tab": False}])
    seite = aufgeraeumt.get("/").get_data(as_text=True)
    assert seite.index("Mein Studio") < seite.index("Preise")
    assert 'href="https://studio.example" target="_blank"' in seite


def test_der_alte_einzelne_link_wandert_mit(aufgeraeumt):
    """Nach dem Update soll niemand eine leere Fußzeile haben."""
    from app import db, footer
    db.set_setting("footer_links_json", "")
    db.set_setting("footer_custom_link_label", "Altes Studio")
    db.set_setting("footer_custom_link_url", "https://alt.example")
    assert footer.custom()[0]["label"] == "Altes Studio"
    assert "Altes Studio" in aufgeraeumt.get("/").get_data(as_text=True)


def test_englische_beschriftung_gilt_nur_auf_englisch(aufgeraeumt):
    from app import db, footer
    db.set_setting("site_languages", "both")
    footer.save([{"label": "Preise", "label_en": "Pricing", "url": "/preise", "new_tab": False}])
    assert footer.links("de")[-1]["label"] == "Preise"
    assert footer.links("en")[-1]["label"] == "Pricing"
    footer.save([{"label": "Preise", "label_en": "", "url": "/preise", "new_tab": False}])
    assert footer.links("en")[-1]["label"] == "Preise"


def test_ein_gefaehrliches_ziel_wird_entschaerft(aufgeraeumt):
    from app import footer
    footer.save([{"label": "Böse", "url": "javascript:alert(1)", "new_tab": False}])
    assert footer.custom()[0]["url"].startswith("https://")


def test_das_formular_speichert_mehrere_zeilen(aufgeraeumt):
    _admin(aufgeraeumt)
    from app import footer
    from werkzeug.datastructures import MultiDict
    antwort = aufgeraeumt.post("/admin/settings/homepage", data=MultiDict([
        ("csrf_token", "test-token"),
        ("footer_label", "Studio"), ("footer_label_en", "Studio"), ("footer_url", "studio.example"),
        ("footer_new_tab", "0"),
        ("footer_label", "Buchen"), ("footer_label_en", ""), ("footer_url", "/buchen"),
        ("footer_label", ""), ("footer_label_en", ""), ("footer_url", "https://leer.example"),
    ]), follow_redirects=True)
    assert antwort.status_code == 200
    links = footer.custom()
    assert [l["label"] for l in links] == ["Studio", "Buchen"]
    assert links[0]["url"] == "https://studio.example"   # ohne Schema getippt
    assert links[0]["new_tab"] is True and links[1]["new_tab"] is False
