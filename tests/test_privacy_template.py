"""The privacy template says what the software does, per purpose.

Findings of the privacy review of 11.11.2026 on app/legal.py (no legal
advice, a template the operator completes): one blanket "lit. b, f" for
everything; a like id called "ohne Personenbezug" although it is a
pseudonym; deletion promised instead of the event's real period; no
recipients, no logs, no Art. 21, no supervisory authority, nothing on
cookies or local storage, no choice between controller and processor.
The periods quoted here are read from the code; if one changes, this test
says which sentence of the template has to change with it.
"""
import json
import secrets

import pytest

from app import access, db, ecosystem, gallery_settings as gsm, protokoll, retention, sharelinks

KEYS = ("legal_operator", "legal_contact_email", "legal_role", "legal_principal", "legal_hoster",
        "legal_external_storage", "legal_mail_provider", "legal_authority")


@pytest.fixture(autouse=True)
def schema(client, legal_facts):
    # Since 15.11.2026 a page with placeholders is only shown to the operator,
    # so every test starts from complete facts and clears what it wants empty.
    with client.application.app_context():
        ecosystem.init_schema()
        for k in KEYS:
            if k not in legal_facts:
                db.set_setting(k, "")
    yield


def _page(client, query=""):
    r = client.get("/datenschutz" + query)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_each_purpose_has_its_own_legal_basis(client):
    page = _page(client)
    assert "lit. b, f" not in page
    assert "Art. 6 Abs. 1 lit. f DSGVO" in page and "Erinnerungen" in page   # guests, named interest
    assert "Art. 6 Abs. 1 lit. b DSGVO" in page                               # the client's contract
    assert "Art. 6 Abs. 1 lit. a DSGVO" in page                               # notification mails


def test_the_like_id_is_called_a_pseudonym(client):
    page = _page(client)
    assert "ohne Personenbezug" not in page
    assert "pseudonym" in page


def test_the_period_is_the_events_own(client):
    k = secrets.token_hex(3)
    eid = db.create_event(name="Hochzeit <b>A</b>", slug=f"ds-{k}", upload_token=f"t-{k}", retention_days=14)
    db.update_event(eid, {"date": "2026-11-20", "published": 1})
    page = _page(client, f"?event=ds-{k}")
    assert "14 Tage" in page and "04.12.2026" in page
    assert "<b>A</b>" not in page, "the event name is text, not markup"


def test_without_an_event_the_default_period_is_named(client):
    page = _page(client)
    assert f"{db.DEFAULT_RETENTION_DAYS} Tage" in page


def test_the_gallery_footer_links_to_its_own_period(client):
    k = secrets.token_hex(3)
    eid = db.create_event(name="Fuss", slug=f"fuss-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean({}))})
    page = client.get(f"/gallery/fuss-{k}").get_data(as_text=True)
    assert f"/datenschutz?event=fuss-{k}" in page


def test_recipients_and_places_are_listed_as_configured(client):
    db.set_setting("legal_hoster", "Hetzner Online GmbH, Gunzenhausen")
    db.set_setting("legal_mail_provider", "Postfach bei Beispiel-Mail GmbH")
    page = _page(client)
    assert "Hetzner Online GmbH" in page and "Auftragsverarbeiter" in page
    assert "Beispiel-Mail GmbH" in page
    assert "andere Gäste" in page.lower() or "Andere Gäste" in page
    assert "NAS" not in page, "no external storage configured, none mentioned"
    db.set_setting("legal_external_storage", "eigenes NAS des Betreibers in Musterstadt")
    assert "eigenes NAS des Betreibers" in _page(client)


def _as_admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()


def test_unset_fields_show_placeholders_to_the_operator_only(client):
    db.set_setting("legal_hoster", "")
    db.set_setting("legal_authority", "")
    guest = _page(client)
    assert "[Hosting-Anbieter" not in guest and "noch nicht ausgefüllt" in guest
    _as_admin(client)
    page = _page(client)
    assert "[Hosting-Anbieter" in page and "[zuständige Aufsichtsbehörde" in page
    assert "Nur für dich sichtbar" in page


def test_the_logs_are_named_with_their_periods_from_the_code(client):
    page = _page(client)
    assert retention.LOG_RETENTION_DAYS == 1095 and "drei Jahre" in page
    assert protokoll.AUFBEWAHRUNG_TAGE == 365 and "365 Tage" in page and "gekürzt" in page
    assert "90 Tage" in page and "Freigabe" in page            # share_views: DELETE … -90 days
    assert access.KEEP_DAYS == 90
    assert sharelinks._KEEP_SECONDS == 86400 and "24 Stunden" in page
    assert ecosystem.LOG_RETENTION_DAYS == 30


def test_objection_under_art_21_is_set_apart(client):
    page = _page(client)
    assert "Art. 21" in page and "<strong>Widerspruch" in page


def test_authority_cookies_voluntariness_no_automated_decision(client):
    db.set_setting("legal_authority", "Die Landesbeauftragte für den Datenschutz Niedersachsen")
    page = _page(client)
    assert "Landesbeauftragte für den Datenschutz Niedersachsen" in page
    assert "Cookie" in page and "lokalen Speicher" in page and "Banner" in page
    assert "freiwillig" in page
    assert "automatisierte Entscheidung" in page


def test_the_operator_can_be_processor_for_a_client(client):
    db.set_setting("legal_operator", "Till Heidrich – Content Creation")
    db.set_setting("legal_role", "processor")
    db.set_setting("legal_principal", "Brautpaar Muster, Musterweg 1, 12345 Musterstadt")
    page = _page(client)
    assert "im Auftrag" in page and "Brautpaar Muster" in page and "Art. 28" in page
    db.set_setting("legal_role", "controller")
    page = _page(client)
    assert "Brautpaar Muster" not in page and "Verantwortlich" in page


def test_the_admin_form_saves_the_fields(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "x"
    data = {"csrf_token": "x", "impressum_text": "", "datenschutz_text": "",
            "impressum_text_en": "", "datenschutz_text_en": "",
            "legal_operator": "Betreiber X", "legal_contact_email": "a@b.de",
            "legal_role": "processor", "legal_principal": "Kunde Y",
            "legal_hoster": "Hoster Z", "legal_external_storage": "",
            "legal_mail_provider": "", "legal_authority": "LfD"}
    r = client.post("/admin/settings/legal", data=data)
    assert r.status_code in (200, 302)
    assert db.get_setting("legal_role") == "processor"
    assert db.get_setting("legal_principal") == "Kunde Y"
    assert db.get_setting("legal_hoster") == "Hoster Z"
    form = client.get("/admin/settings/legal").get_data(as_text=True)
    assert 'name="legal_authority"' in form and "LfD" in form


def test_a_nonsense_role_counts_as_controller(client):
    db.set_setting("legal_role", "irgendwas")
    db.set_setting("legal_principal", "Kunde Q")
    assert "Kunde Q" not in _page(client)


def test_the_assistant_paragraph_names_what_each_extra_right_opens(client):
    # Since the MCP extra rights (15.11.2026) "no photos, no guest addresses"
    # holds only for the base rights. The paragraph must say what each extra
    # right makes reachable, using the names the admin ticks (oauth.scope.*).
    from app import oauth
    assert set(oauth.EXTRA_SCOPES) == {"control", "photos", "secure", "updates"}, \
        "a new extra right: say in app/legal.py what it makes reachable"
    db.set_setting("legal_assistant", "Assistent X, Anbieter Y")
    try:
        page = _page(client)
    finally:
        db.set_setting("legal_assistant", "")
    assert "Assistent X, Anbieter Y" in page
    assert "Ohne Zusatzrechte" in page
    for label in ("„Gästefotos“", "„Fernsteuern“", "„Sicherheit“", "„Updates“"):
        assert label in page, label
    assert "E-Mail-Adressen der Gäste abrufen" in page
    assert "Livebilds der Kamera" in page
    # Paket D: base right lists access requests (address shortened), secure changes the legal texts,
    # photos shows the source of a picture.
    assert "Zugriffsanfragen" in page and "nur gekürzt" in page
    assert "ändert Impressum und Datenschutzerklärung" in page
    assert "von einem Gast, vom Betreiber oder von einer" in page
    from app.mcp import TOOLS
    import app.mcp_more  # noqa: F401 — registers the tools
    assert TOOLS["list_access_requests"]["scope"] == "manage"
    assert TOOLS["decide_access_request"]["scope"] == "manage"
    # Final review A1–A3: deciding a request, publishing and the password need
    # "Gästefotos"; a booth's event needs "Sicherheit". The paragraph says so.
    assert TOOLS["decide_access_request"]["also"] == ("photos",)
    assert TOOLS["assign_booth_event"]["also"] == ("secure",)
    assert "die er freigeben oder ablehnen kann" not in page
    assert "entscheiden kann er sie nur mit „Gästefotos“" in page
    assert "entscheidet Zugriffsanfragen" in page and "veröffentlicht Galerien" in page
    assert "welchem Event eine Fotobox ihre Bilder gibt" in page
    assert TOOLS["set_legal_texts"]["scope"] == "secure"
    assert TOOLS["list_media"]["scope"] == "photos"
    # Paket A: "Fernsteuern" also restarts or shuts down the booth PC and sets the kiosk language.
    assert "startet das Programm oder den PC der Fotobox neu, fährt ihn herunter" in page
    assert "stellt die Sprache" in page
    for name in ("booth_restart_pc", "booth_shutdown_pc", "booth_set_language"):
        assert TOOLS[name]["scope"] == "control", name
    assert "Fotos, E-Mail-Adressen von Gästen und Zugriffsanfragen gibt die Schnittstelle" not in page
    # Second review: clean-up needs "Gästefotos" (R2-N1), restart/lock/test photo
    # are "Fernsteuern" (R2-U2), seeing where data goes is "Sicherheit" (R2-N2/N3),
    # and the page says honestly what "no AI" means next to the assistant.
    assert "das Aufräumen der Fotobox einstellen" in page
    assert "sperrt den Bildschirm, macht ein Testfoto" in page
    assert "sieht und ändert er, wohin" in page and "Betreiber-Warnungen" in page
    assert "wertet Fotos nicht mit" in page and "erkennt keine Gesichter" in page
    from app import mcp as mcp_mod
    assert "aufraeumen" in mcp_mod.PROFILE_NEEDS_PHOTOS
    assert {"restart_app", "lock", "test_capture"} <= mcp_mod.NEEDS_CONTROL


def test_no_assistant_no_paragraph(client):
    db.set_setting("legal_assistant", "")
    assert "KI-Assistent für die Verwaltung" not in _page(client)
