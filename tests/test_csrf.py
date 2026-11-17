"""Das Formular-Zeichen — überall, wo etwas geändert wird.

Die Vorlagen rendern das Feld seit jeher. Ausgewertet wurde es bis zum
19.09.2026 in ``admin.py`` an zwei von fünfunddreißig Stellen; auf der
Passwortseite und im Einrichtungsassistenten war es sogar reine Dekoration —
das Zeichen entstand bei jedem Rendern neu und wurde nirgends abgelegt.
"""
import re
import secrets

import pytest

from app import db, ecosystem, gallery_settings as gsm


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "echtes-zeichen"
    return client


#: Bewusst keine Route, die beim Speichern andere Einstellungen zurücksetzt —
#: ``/admin/settings/homepage`` setzt jeden nicht mitgesendeten Schalter auf
#: aus und hätte die Downloadtests umgeworfen. Die Testdatenbank ist geteilt.
SCHREIBENDE_ROUTEN = [
    ("/admin/settings/tracking", {"umami_src": "https://zaehler.example/x.js"}),
    ("/admin/events/new", {"name": "CSRF-Probe"}),
    ("/admin/deletions/sweep", {}),
]


@pytest.mark.parametrize("pfad, daten", SCHREIBENDE_ROUTEN)
def test_without_the_token_nothing_is_written(admin, pfad, daten):
    antwort = admin.post(pfad, data=daten)
    assert antwort.status_code == 400, f"{pfad} nahm ein Formular ohne Zeichen an"


@pytest.mark.parametrize("pfad, daten", SCHREIBENDE_ROUTEN)
def test_with_the_token_the_route_works(admin, pfad, daten):
    """Die Gegenprobe — sonst prüfte oben nur, dass irgendetwas 400 sagt."""
    antwort = admin.post(pfad, data={**daten, "csrf_token": "echtes-zeichen"})
    assert antwort.status_code != 400


def test_a_wrong_token_is_as_good_as_none(admin):
    # Eigener Wert je Lauf: Die Testdatenbank ist über alle Tests geteilt, und
    # ein fester Wert wäre schon von einem anderen Test gesetzt worden.
    fremd = f"https://boese.example/{secrets.token_hex(4)}.js"
    antwort = admin.post("/admin/settings/tracking",
                         data={"csrf_token": "geraten", "umami_src": fremd})
    assert antwort.status_code == 400
    with admin.application.app_context():
        assert (db.get_setting("umami_src") or "") != fremd


# ── Die öffentlichen Formulare ────────────────────────────────────────
def test_the_password_page_token_is_the_one_in_the_session(client):
    """Es war ein Zufallswert, der nirgends stand — also nie prüfbar."""
    kennung = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name="Geschützt", slug=f"csrf-{kennung}",
                              upload_token=f"tok-{kennung}")
        db.update_event(eid, {"published": 1})
        db.set_event_password(eid, "geheim1234")

    seite = client.get(f"/gallery/csrf-{kennung}").get_data(as_text=True)
    treffer = re.search(r'name="csrf_token" value="([^"]*)"', seite)
    assert treffer, "Die Passwortseite rendert kein Zeichen"
    with client.session_transaction() as s:
        assert s.get("csrf_token") == treffer.group(1)

    ohne = client.post(f"/gallery/csrf-{kennung}", data={"password": "geheim1234"})
    assert ohne.status_code == 400

    mit = client.post(f"/gallery/csrf-{kennung}",
                      data={"password": "geheim1234", "csrf_token": treffer.group(1)})
    assert mit.status_code in (200, 302)


def test_the_setup_wizard_checks_its_own_token(client):
    with client.application.app_context():
        db.set_setting("setup_completed", "")
        db.set_setting("admin_username", "admin")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True      # the wizard is admin-only
    seite = client.get("/setup").get_data(as_text=True)
    treffer = re.search(r'name="csrf_token" value="([^"]*)"', seite)
    assert treffer

    ohne = client.post("/setup", data={"admin_username": "eve",
                                       "admin_password": "Ein-langes-Kennwort-42"})
    assert ohne.status_code == 400
    with client.application.app_context():
        assert db.get_setting("admin_username") == "admin"
