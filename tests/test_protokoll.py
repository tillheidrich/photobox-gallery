"""Änderungsprotokoll: wer hat wann was umgestellt.

Geprüft wird vor allem die Haltung dahinter: Das Protokoll hängt nicht an
einzelnen Formularen, sondern an jeder ändernden Anfrage — sonst fehlt genau
die Umstellung, an die niemand gedacht hat. Kennwörter und Schlüssel stehen
nie im Klartext darin, und eine kaputte Aufzeichnung darf niemals eine
Anfrage kippen.
"""
import pytest

from app import protokoll


@pytest.fixture(autouse=True)
def sauber(client):
    client.get("/healthz")
    from app import db, ecosystem
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        con = db.get_db()
        con.execute("DELETE FROM admin_aenderungen")
        con.execute("DELETE FROM admin_vorgaenge")
        con.commit()
        con.close()
    yield


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


# ── Was überhaupt aufgezeichnet wird ──────────────────────────────────
@pytest.mark.parametrize("methode, pfad, erwartet", [
    ("POST", "/admin/settings", True),
    ("POST", "/mcp", True),
    ("GET", "/admin/settings", False),          # Lesen stellt nichts um
    ("POST", "/api/v2/booth/sync", False),      # die Box lädt hoch, sie verstellt nichts
    ("POST", "/admin/protokoll", False),        # das Protokoll protokolliert sich nicht selbst
])
def test_only_changing_admin_requests_are_recorded(methode, pfad, erwartet):
    assert protokoll.soll_protokollieren(methode, pfad) is erwartet


# ── Geheimnisse ───────────────────────────────────────────────────────
@pytest.mark.parametrize("feld", ["admin_password_hash", "smtp_password", "api_key",
                                  "mcp_token", "device_secret", "kennwort"])
def test_secrets_are_recognised_as_secrets(feld):
    assert protokoll.ist_geheim(feld)


def test_a_changed_secret_is_visible_but_never_readable():
    aenderungen = protokoll.vergleiche({("Einstellungen", "smtp_password"): "altes-geheimnis"},
                                       {("Einstellungen", "smtp_password"): "neues-geheimnis"})
    assert len(aenderungen) == 1
    eintrag = aenderungen[0]
    assert "geheimnis" not in str(eintrag["vorher"]) + str(eintrag["nachher"])
    assert protokoll.MASKE in str(eintrag["nachher"])


def test_an_ordinary_value_keeps_its_before_and_after():
    aenderungen = protokoll.vergleiche({("Einstellungen", "gallery_title"): "Alt"},
                                       {("Einstellungen", "gallery_title"): "Neu"})
    assert aenderungen == [{"gegenstand": "Einstellungen", "feld": "gallery_title",
                            "vorher": "Alt", "nachher": "Neu"}]


def test_a_new_subject_is_one_line_not_thirty():
    vorher = {("Einstellungen", "a"): "1"}
    nachher = {("Einstellungen", "a"): "1",
               ("Event „Hochzeit“ (#7)", "name"): "Hochzeit",
               ("Event „Hochzeit“ (#7)", "slug"): "hochzeit",
               ("Event „Hochzeit“ (#7)", "kind"): "wedding"}
    aenderungen = protokoll.vergleiche(vorher, nachher)
    assert aenderungen == [{"gegenstand": "Event „Hochzeit“ (#7)", "feld": "",
                            "vorher": None, "nachher": "angelegt"}]


def test_a_removed_subject_says_so():
    aenderungen = protokoll.vergleiche({("Gerät „Box 1“ (#2)", "name"): "Box 1"}, {})
    assert aenderungen[0]["nachher"] == "entfernt"


# ── Die IP ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("roh, erwartet", [
    ("192.168.10.37", "192.168.10.0"),
    ("2a01:db8:1234:5678::1", "2a01:db8:1234::"),
    ("", ""),
])
def test_the_address_is_shortened_before_it_is_stored(roh, erwartet):
    assert protokoll.kuerze_ip(roh) == erwartet


# ── Im Betrieb ────────────────────────────────────────────────────────
def test_a_settings_change_shows_up_with_before_and_after(admin):
    """Kein Aufruf im Formular, keine Zeile in der Route — und es steht trotzdem drin."""
    from app import db
    with admin.application.app_context():
        db.set_setting("umami_src", "https://vorher.example/script.js")
    admin.post("/admin/settings/tracking",
               data={"csrf_token": "test-token", "umami_src": "https://nachher.example/script.js",
                     "umami_website_id": ""})
    werte = [(a["feld"], a["vorher"], a["nachher"])
             for v in protokoll.liste(limit=5) for a in v["aenderungen"]]
    assert ("umami_src", "https://vorher.example/script.js",
            "https://nachher.example/script.js") in werte


def test_a_request_that_changes_nothing_is_still_on_the_record(admin):
    admin.post("/admin/deletions/sweep", data={"csrf_token": "test-token"})
    vorgaenge = protokoll.liste(limit=5)
    assert any(v["pfad"] == "/admin/deletions/sweep" for v in vorgaenge)


def test_reading_the_log_does_not_write_to_it(admin):
    admin.post("/admin/deletions/sweep", data={"csrf_token": "test-token"})
    vorher = protokoll.anzahl()
    assert admin.get("/admin/protokoll").status_code == 200
    assert admin.get("/admin/protokoll.csv").status_code == 200
    assert protokoll.anzahl() == vorher


@pytest.mark.parametrize("pfad, erwartet", [
    ("/admin/events/7/edit", (7, None)),
    ("/admin/devices/3/booth", (None, 3)),
    ("/admin/settings/tracking", (None, None)),
])
def test_only_what_the_path_names_gets_read_in_full(pfad, erwartet):
    """Vierhundert Events zweimal je Klick zu lesen wäre Aufwand für nichts."""
    assert protokoll.bereich(pfad) == erwartet


def test_a_new_event_is_noticed_even_without_an_id_in_the_path(admin):
    from app import db
    with admin.application.app_context():
        vorher = protokoll.schnappschuss()
        db.create_event(name="Protokolltest", slug="protokolltest-xyz",
                        upload_token="protokoll-test-token")
        nachher = protokoll.schnappschuss()
    neue = [a for a in protokoll.vergleiche(vorher, nachher) if a["nachher"] == "angelegt"]
    assert any("Protokolltest" in a["gegenstand"] for a in neue)


def test_the_page_needs_a_login(client):
    antwort = client.get("/admin/protokoll")
    assert antwort.status_code in (302, 401, 403)


def test_a_broken_log_never_takes_down_a_request(admin, monkeypatch):
    monkeypatch.setattr(protokoll, "schnappschuss",
                        lambda: (_ for _ in ()).throw(RuntimeError("Platte voll")))
    antwort = admin.post("/admin/deletions/sweep", data={"csrf_token": "test-token"})
    assert antwort.status_code < 500
