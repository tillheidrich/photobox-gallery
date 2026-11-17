"""Die Bitte um eine Bewertung.

Geprüft wird vor allem, was hier bewusst nicht passiert: keine Vorsortierung
nach Sternen, keine Zeile je Person, kein Link, der irgendwohin führt. Und die
Vererbung: Was am Event leer bleibt, kommt von der Instanz.
"""
import pytest

from app import bewertung


@pytest.fixture(autouse=True)
def sauber(client):
    client.get("/healthz")
    from app import db, ecosystem
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        con = db.get_db()
        con.execute("DELETE FROM bewertung_tage")
        con.commit()
        con.close()
        db.set_setting("review_enabled", "0")
        db.set_setting("review_url", "")
        db.set_setting("review_text", "")
    yield


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


# ── Was als Adresse durchgeht ─────────────────────────────────────────
@pytest.mark.parametrize("roh, erwartet", [
    ("https://g.page/r/ABC", "https://g.page/r/ABC"),
    ("g.page/r/ABC", "https://g.page/r/ABC"),      # kopiert ohne Schema
    ("  https://provenexpert.com/x/  ", "https://provenexpert.com/x/"),
    ("javascript:alert(1)", ""),
    ("data:text/html,<script>", ""),
    ("ftp://irgendwo/", ""),
    ("", ""),
])
def test_only_web_addresses_survive(roh, erwartet):
    assert bewertung.gueltige_url(roh) == erwartet


# ── Vererbung ─────────────────────────────────────────────────────────
def test_nothing_is_shown_until_somebody_switches_it_on(client):
    assert bewertung.fuer_event({"slug": "x"}, {})["an"] is False


def test_the_instance_default_carries_the_event(client):
    from app import db
    with client.application.app_context():
        db.set_setting("review_enabled", "1")
        db.set_setting("review_url", "https://g.page/r/ABC")
        cfg = bewertung.fuer_event({"slug": "x"}, {})
    assert cfg["an"] and cfg["url"] == "https://g.page/r/ABC"
    assert cfg["text"] == bewertung.STANDARDTEXT


def test_an_event_may_replace_only_the_text(client):
    from app import db
    with client.application.app_context():
        db.set_setting("review_enabled", "1")
        db.set_setting("review_url", "https://g.page/r/ABC")
        cfg = bewertung.fuer_event({"slug": "x"}, {"review_text": "War's schön?"})
    assert cfg["url"] == "https://g.page/r/ABC"
    assert cfg["text"] == "War's schön?"


def test_an_event_may_switch_it_off_for_itself(client):
    from app import db
    with client.application.app_context():
        db.set_setting("review_enabled", "1")
        db.set_setting("review_url", "https://g.page/r/ABC")
        cfg = bewertung.fuer_event({"slug": "x"}, {"review_mode": "aus"})
    assert cfg["an"] is False


def test_an_event_may_switch_it_on_alone(client):
    cfg = bewertung.fuer_event({"slug": "x"},
                               {"review_mode": "an", "review_url": "https://eigene.example/bewerten"})
    assert cfg["an"] and cfg["url"] == "https://eigene.example/bewerten"


def test_switched_on_without_an_address_shows_nothing(client):
    assert bewertung.fuer_event({"slug": "x"}, {"review_mode": "an"})["an"] is False


# ── Die Einstellungen halten die Adresse sauber ───────────────────────
def test_a_junk_address_never_reaches_the_settings():
    from app import gallery_settings as gsm
    sauber = gsm.clean({"review_url": "javascript:alert(1)"})
    assert sauber["review_url"] == ""


def test_a_long_address_is_not_cut_at_a_hundred_and_twenty():
    from app import gallery_settings as gsm
    lang = "https://g.page/r/" + "A" * 200
    assert gsm.clean({"review_url": lang})["review_url"] == lang


# ── Im Betrieb ────────────────────────────────────────────────────────
def _event(client, slug):
    from app import db, gallery_settings as gsm
    import json
    with client.application.app_context():
        eid = db.create_event(name="Bewertungstest", slug=slug, upload_token=f"tok-{slug}")
        db.update_event(eid, {"published": 1,
                              "gallery_json": json.dumps(gsm.clean({
                                  "review_mode": "an",
                                  "review_url": "https://g.page/r/ABC"}))})
    return eid


def test_the_link_counts_and_forwards(client):
    eid = _event(client, "bewertung-weiter")
    antwort = client.get("/bewertung/bewertung-weiter")
    assert antwort.status_code == 302
    assert antwort.headers["Location"] == "https://g.page/r/ABC"
    assert bewertung.zahlen(eid)["gesamt"] == 1
    client.get("/bewertung/bewertung-weiter")
    assert bewertung.zahlen(eid)["gesamt"] == 2


def test_a_gallery_without_a_review_has_no_such_link(client):
    from app import db
    with client.application.app_context():
        eid = db.create_event(name="Ohne", slug="ohne-bewertung", upload_token="tok-ohne")
        db.update_event(eid, {"published": 1})
    assert client.get("/bewertung/ohne-bewertung").status_code == 404


def _foto(client, event_id, nummer=0):
    from app import db
    with client.application.app_context():
        con = db.get_db()
        con.execute("INSERT INTO media (event_id, filename, path_original, kind, share_token, "
                    "created_at) VALUES (?,?,?,?,?,datetime('now'))",
                    (event_id, f"f{nummer}.jpg", f"x/f{nummer}.jpg", "photo",
                     f"bew-{event_id}-{nummer}"))
        con.commit()
        con.close()


def test_the_ask_appears_under_the_photos(client):
    eid = _event(client, "bewertung-galerie")
    _foto(client, eid)
    seite = client.get("/gallery/bewertung-galerie").get_data(as_text=True)
    assert "/bewertung/bewertung-galerie" in seite
    assert bewertung.STANDARDTEXT in seite


def test_an_empty_gallery_asks_nobody_whether_it_was_nice(client):
    """„Hat es euch gefallen?" unter null Fotos wäre eine Frage ohne Anlass."""
    _event(client, "bewertung-leer")
    seite = client.get("/gallery/bewertung-leer").get_data(as_text=True)
    assert "/bewertung/bewertung-leer" not in seite


def test_the_admin_page_opens_and_saves(admin):
    assert admin.get("/admin/settings/bewertungen").status_code == 200
    admin.post("/admin/settings/bewertungen",
               data={"csrf_token": "test-token", "review_enabled": "on",
                     "review_url": "g.page/r/XYZ", "review_text": "Kurz was schreiben?"})
    from app import db
    with admin.application.app_context():
        assert db.get_setting("review_url") == "https://g.page/r/XYZ"
        assert db.get_setting("review_enabled") == "1"


def test_a_junk_address_in_the_admin_is_refused_not_stored(admin):
    admin.post("/admin/settings/bewertungen",
               data={"csrf_token": "test-token", "review_enabled": "on",
                     "review_url": "javascript:alert(1)"})
    from app import db
    with admin.application.app_context():
        assert db.get_setting("review_url") == ""
