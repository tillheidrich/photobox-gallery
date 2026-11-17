"""Eigene Domains: die Galerie unter der Adresse des Kunden.

Wer die Galerie für jemanden betreibt, will dessen Gäste nicht auf einer
fremden Marke landen lassen. Geprüft wird hier vor allem die Haltung: Eine
Domain zählt erst, wenn sie tatsächlich hierher zeigt — bis dahin bleiben
alle Links auf der Hauptadresse. Ein QR-Code ins Leere wäre schlimmer als
einer auf die richtige, aber fremde Adresse.
"""
import pytest

from app import domains


@pytest.fixture()
def admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


@pytest.fixture(autouse=True)
def sauber(client):
    """Schema anlegen wie im Betrieb (über eine Anfrage) und Tabelle leeren."""
    client.get("/healthz")
    from app import db, ecosystem
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        con = db.get_db()
        con.execute("DELETE FROM event_domains")
        con.commit()
        con.close()
    yield


# ── Was als Domain durchgeht ───────────────────────────────────────────
@pytest.mark.parametrize("roh, erwartet", [
    ("https://Bilder.Kunde.de/gallery/x", "bilder.kunde.de"),
    ("  BILDER.kunde.de  ", "bilder.kunde.de"),
    ("bilder.kunde.de:8443", "bilder.kunde.de"),
    ("bilder.kunde.de.", "bilder.kunde.de"),
])
def test_what_a_person_types_becomes_a_hostname(roh, erwartet):
    assert domains.normalisiere(roh) == erwartet


@pytest.mark.parametrize("wert", ["kein host", "localhost", "kunde", "-start.de", "", "a..b"])
def test_what_is_not_a_hostname_is_refused(wert):
    assert not domains.gueltig(domains.normalisiere(wert))


def test_the_instruction_is_something_you_can_hand_over(client):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        a = domains.anleitung("bilder.kunde.de")
        assert a["typ"] == "CNAME" and a["name"] == "bilder"
        assert a["ziel"] == "galerie.example.de"
        # Eine Domain ohne Unterdomain kann nicht jeder Anbieter als CNAME.
        b = domains.anleitung("kunde.de")
        assert b["name"] == "@" and "ALIAS" in b["typ"]


# ── Eintragen und bestätigen ───────────────────────────────────────────
def test_an_unverified_domain_changes_nothing(client):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        event = db.get_event_by_id(db.create_event(name="Fest", slug="fest", upload_token="t-fest"))
        domains.anlegen("bilder.kunde.de", event["id"])
        assert domains.fuer_event(event) == "https://galerie.example.de", \
            "Solange die Domain nicht zeigt, bleibt es bei der Hauptadresse"


def test_a_verified_domain_carries_the_links(client, monkeypatch):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        event = db.get_event_by_id(db.create_event(name="Fest", slug="fest2", upload_token="t-fest2"))
        eintrag = domains.anlegen("bilder.kunde.de", event["id"])
        # DNS im Test: beide Namen zeigen auf dieselbe Adresse.
        monkeypatch.setattr(domains, "_adressen", lambda host: ["203.0.113.7"])
        assert domains.bestaetigen(eintrag["id"])["ok"] is True
        assert domains.fuer_event(event) == "https://bilder.kunde.de"


def test_a_domain_for_the_whole_instance_applies_to_every_event(client, monkeypatch):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        event = db.get_event_by_id(db.create_event(name="Fest", slug="fest3", upload_token="t-fest3"))
        eintrag = domains.anlegen("bilder.kunde.de")        # ohne Event
        monkeypatch.setattr(domains, "_adressen", lambda host: ["203.0.113.7"])
        domains.bestaetigen(eintrag["id"])
        assert domains.fuer_event(event) == "https://bilder.kunde.de"


def test_the_event_beats_the_instance(client, monkeypatch):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        event = db.get_event_by_id(db.create_event(name="Fest", slug="fest4", upload_token="t-fest4"))
        monkeypatch.setattr(domains, "_adressen", lambda host: ["203.0.113.7"])
        domains.bestaetigen(domains.anlegen("allgemein.example")["id"])
        domains.bestaetigen(domains.anlegen("bilder.kunde.de", event["id"])["id"])
        assert domains.fuer_event(event) == "https://bilder.kunde.de"


def test_a_domain_that_points_elsewhere_is_named_not_accepted(client, monkeypatch):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        eintrag = domains.anlegen("fremd.kunde.de")
        monkeypatch.setattr(domains, "_adressen",
                            lambda host: ["198.51.100.9"] if host.startswith("fremd") else ["203.0.113.7"])
        ergebnis = domains.bestaetigen(eintrag["id"])
        assert ergebnis["ok"] is False
        assert "198.51.100.9" in ergebnis["grund"] and "203.0.113.7" in ergebnis["grund"]


def test_a_domain_that_does_not_resolve_yet_says_so(client, monkeypatch):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        eintrag = domains.anlegen("neu.kunde.de")
        monkeypatch.setattr(domains, "_adressen",
                            lambda host: [] if host.startswith("neu") else ["203.0.113.7"])
        ergebnis = domains.bestaetigen(eintrag["id"])
        assert ergebnis["ok"] is False and "löst noch nicht auf" in ergebnis["grund"]


def test_the_own_address_cannot_be_claimed(client):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        with pytest.raises(ValueError):
            domains.anlegen("galerie.example.de")


def test_the_same_domain_twice_is_refused(client):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        domains.anlegen("bilder.kunde.de")
        with pytest.raises(ValueError):
            domains.anlegen("BILDER.kunde.de")


# ── Was die Box und die Gäste davon merken ─────────────────────────────
def test_the_booth_gets_the_customers_address_for_its_qr(client, monkeypatch):
    with client.application.app_context():
        from app import db, ecosystem
        db.set_setting("app_base_url", "https://galerie.example.de")
        event = db.get_event_by_id(db.create_event(name="Fest", slug="fest5", upload_token="t-fest5"))
        dev = db.create_device(name="Box")
        ecosystem.save_device_profile(dev["id"], {}, event["id"])
        monkeypatch.setattr(domains, "_adressen", lambda host: ["203.0.113.7"])
        domains.bestaetigen(domains.anlegen("bilder.kunde.de", event["id"])["id"])
        prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://galerie.example.de")
        assert prof["event"]["base_url"] == "https://bilder.kunde.de", \
            "Der QR an der Box trägt die Marke des Kunden oder gar nichts"


def test_the_bare_customer_domain_leads_to_their_gallery(client, monkeypatch):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
        event = db.get_event_by_id(db.create_event(name="Fest", slug="kundenfest", upload_token="t-kundenfest"))
        monkeypatch.setattr(domains, "_adressen", lambda host: ["203.0.113.7"])
        domains.bestaetigen(domains.anlegen("bilder.kunde.de", event["id"])["id"])
    r = client.get("/", headers={"Host": "bilder.kunde.de"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/gallery/kundenfest")


def test_the_main_address_still_shows_the_normal_start(client):
    with client.application.app_context():
        from app import db
        db.set_setting("app_base_url", "https://galerie.example.de")
    r = client.get("/")
    assert r.status_code in (200, 302)
    if r.status_code == 302:
        assert "/gallery/" not in r.headers.get("Location", "")


# ── Die Seite in der Verwaltung ────────────────────────────────────────
def test_the_admin_page_lists_and_adds(admin):
    r = admin.post("/admin/domains", data={"csrf_token": "test-token", "tat": "anlegen",
                                           "domain": "bilder.kunde.de"}, follow_redirects=True)
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert "bilder.kunde.de" in text
    assert "CNAME" in text, "Ohne den DNS-Eintrag kann der Kunde nichts tun"
    assert "Zertifikat" in text, "Der offene Schritt muss dastehen, nicht verschwiegen werden"


def test_nonsense_is_refused_with_a_sentence(admin):
    r = admin.post("/admin/domains", data={"csrf_token": "test-token", "tat": "anlegen",
                                           "domain": "kein host"}, follow_redirects=True)
    assert "kein Hostname" in r.get_data(as_text=True)
