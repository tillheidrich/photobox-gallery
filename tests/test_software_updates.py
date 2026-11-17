"""Updates: eine Datei hochladen, und die Boxen holen sie sich.

Kein App-Store, kein zweiter Dienst, kein Konto — jede Box spricht ohnehin mit
ihrer Galerie. Wichtig sind drei Dinge: Es wird nichts erzwungen (mitten im
Abend startet sich kein Rechner neu), zu jeder Datei gehört eine Prüfsumme,
und wenn etwas schiefgeht, läuft die alte Fassung weiter.
"""
import hashlib
import secrets

import pytest

from app import software


@pytest.fixture(autouse=True)
def leerer_regal(client):
    """Jeder Test fängt mit einem leeren Regal an.

    Die Tests teilen sich eine Datenbank; eine liegengebliebene Fassung würde
    im nächsten Test als Angebot auftauchen.
    """
    client.get("/healthz")
    _raeumen()
    yield
    _raeumen()


def _raeumen():
    software.init_schema()
    for eintrag in software.liste():
        software.loeschen(int(eintrag["id"]))


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    return client


def _ablegen(version="0.3.0", program="booth", released=True, daten=b"MZ-Programm"):
    return software.hinzufuegen(program=program, version=version, dateiname="PhotoboxBooth.exe",
                                daten=daten, notes="Neue Vorlagen", released=released)


# ── Ablegen und freigeben ─────────────────────────────────

def test_a_version_is_stored_with_its_checksum(client):
    client.get("/healthz")
    eintrag = _ablegen()
    assert eintrag["sha256"] == hashlib.sha256(b"MZ-Programm").hexdigest()
    assert (software.ordner() / eintrag["file"]).is_file()


def test_nonsense_is_refused_rather_than_stored(client):
    client.get("/healthz")
    with pytest.raises(ValueError):
        software.hinzufuegen(program="booth", version="neueste", dateiname="x.exe", daten=b"x")
    with pytest.raises(ValueError):
        software.hinzufuegen(program="booth", version="0.3.0", dateiname="x.docx", daten=b"x")
    with pytest.raises(ValueError):
        software.hinzufuegen(program="gibtsnicht", version="0.3.0", dateiname="x.exe", daten=b"x")


def test_the_same_version_replaces_instead_of_piling_up(client):
    client.get("/healthz")
    _ablegen(version="0.4.0", daten=b"eins")
    _ablegen(version="0.4.0", daten=b"zwei")
    passende = [f for f in software.liste("booth") if f["version"] == "0.4.0"]
    assert len(passende) == 1 and passende[0]["sha256"] == hashlib.sha256(b"zwei").hexdigest()


def test_versions_are_compared_as_numbers_not_as_text():
    assert software.neuer_als("0.10.0", "0.9.0") is True
    assert software.neuer_als("0.2.0", "0.2.0") is False
    assert software.neuer_als("1.0", "0.9.9") is True


# ── Angebot an die Box ────────────────────────────────────

def test_only_released_versions_are_offered(client):
    """Eine hochgeladene Fassung liegt erst einmal nur da."""
    client.get("/healthz")
    _ablegen(version="99.0.0", released=False)
    angebot = software.angebot("booth", "0.2.0", "https://g.test")
    assert angebot is None or angebot["version"] != "99.0.0"
    _ablegen(version="99.0.0", released=True)
    assert software.angebot("booth", "0.2.0", "https://g.test")["version"] == "99.0.0"


def test_a_box_that_is_current_gets_nothing(client):
    client.get("/healthz")
    _ablegen(version="9.1.0")
    assert software.angebot("booth", "9.1.0", "https://g.test") is None


def test_the_sync_carries_the_offer_and_the_checksum(client):
    from app import db
    client.get("/healthz")
    eintrag = _ablegen(version="9.2.0")
    dev = db.create_device(name="Update-Box " + secrets.token_hex(2))
    antwort = client.post("/api/v2/booth/sync",
                          json={"status": {"booth_version": "photobox-booth 0.2.0", "camera": {"state": "ready"}}},
                          headers={"X-API-Key": dev["api_key"]})
    angebot = antwort.get_json().get("update")
    assert angebot and angebot["version"] == "9.2.0"
    assert angebot["sha256"] == eintrag["sha256"]
    assert angebot["url"].endswith(eintrag["file"])


def test_the_file_needs_a_key_to_download(client):
    from app import db
    client.get("/healthz")
    eintrag = _ablegen(version="9.3.0")
    assert client.get(f"/api/v2/booth/software/{eintrag['file']}").status_code == 401
    dev = db.create_device(name="Lade-Box " + secrets.token_hex(2))
    antwort = client.get(f"/api/v2/booth/software/{eintrag['file']}",
                         headers={"X-API-Key": dev["api_key"]})
    assert antwort.status_code == 200 and antwort.data == b"MZ-Programm"


def test_nothing_outside_the_folder_can_be_fetched(client):
    from app import db
    client.get("/healthz")
    dev = db.create_device(name="Frech " + secrets.token_hex(2))
    assert client.get("/api/v2/booth/software/..%2f..%2fapp%2fdb.py",
                      headers={"X-API-Key": dev["api_key"]}).status_code in (400, 404)


# ── Verwaltung ────────────────────────────────────────────

def test_the_page_shows_what_runs_where(admin):
    from app import db
    _ablegen(version="9.4.0")
    db.create_device(name="Sichtbare Box " + secrets.token_hex(2))
    seite = admin.get("/admin/software").get_data(as_text=True)
    assert "9.4.0" in seite and "Sichtbare Box" in seite
    assert "docker compose pull" in seite, "wie die Galerie selbst aktualisiert wird, steht dabei"


def test_releasing_and_deleting_work_from_the_page(admin):
    eintrag = _ablegen(version="9.5.0", released=False)
    admin.post("/admin/software", data={"csrf_token": "test-token", "release": eintrag["id"], "on": "1"},
               follow_redirects=True)
    assert [f for f in software.liste("booth") if f["version"] == "9.5.0"][0]["released"] == 1
    admin.post("/admin/software", data={"csrf_token": "test-token", "delete": eintrag["id"]},
               follow_redirects=True)
    assert not [f for f in software.liste("booth") if f["version"] == "9.5.0"]
    assert not (software.ordner() / eintrag["file"]).exists()
