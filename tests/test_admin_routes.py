"""Regression: every admin page must render (guards against missing templates).

This suite exists because the admin/settings/* and admin/events/* templates were
once missing from the repo, so those pages 500'd in production.
"""
import pytest


@pytest.fixture()
def admin_client(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    return client


def _seed_event(app):
    import secrets
    with app.app_context():
        from app import db
        db.init_db()
        return db.create_event(slug="t-" + secrets.token_hex(3), name="T",
                               upload_token=secrets.token_hex(6))


ADMIN_GET_ROUTES = [
    "/admin/dashboard", "/admin/events", "/admin/events/new", "/admin/devices",
    "/admin/analytics", "/admin/settings", "/admin/settings/homepage",
    "/admin/settings/tracking",
    "/admin/settings/notifications", "/admin/settings/legal", "/admin/settings/faq",
]


@pytest.mark.parametrize("route", ADMIN_GET_ROUTES)
def test_admin_pages_render(admin_client, route):
    r = admin_client.get(route)
    assert r.status_code == 200, f"{route} -> {r.status_code}"


def test_settings_pages_share_tab_bar(admin_client):
    # Jede Settings-Seite trägt die gemeinsame Tab-Leiste inkl. Tracking-Tab.
    for route in ("/admin/settings", "/admin/settings/homepage",
                  "/admin/settings/tracking", "/admin/settings/legal"):
        html = admin_client.get(route).get_data(as_text=True)
        assert "/admin/settings/tracking" in html
        assert 'class="settings-tab' in html


def test_homepage_settings_persist_download_toggles(app, admin_client):
    from app import db
    with app.app_context():
        db.init_db()
    # „Meine Auswahl" aus, kompletter ZIP an, admin-only aus, Gäste-Upload an
    admin_client.post("/admin/settings/homepage", data={
        "csrf_token": "x",
        "homepage_headline": "H",
        "zip_download_enabled": "1",
        "guest_upload_enabled": "1",
        # selection_download_enabled + zip_download_admin_only bewusst NICHT gesetzt
    }, follow_redirects=True)
    with app.app_context():
        assert db.get_setting("selection_download_enabled") == "0"
        assert db.get_setting("zip_download_admin_only") == "0"
        assert db.get_setting("zip_download_enabled") == "1"
        assert db.get_setting("guest_upload_enabled") == "1"
        # Defaults wiederherstellen (conftest teilt EIN DATA_DIR über alle Tests)
        for k, v in (("selection_download_enabled", "1"), ("zip_download_admin_only", "1"),
                     ("zip_download_enabled", "0"), ("guest_upload_enabled", "0")):
            db.set_setting(k, v)


def test_tracking_settings_persist(app, admin_client):
    from app import db
    with app.app_context():
        db.init_db()
    admin_client.post("/admin/settings/tracking", data={
        "csrf_token": "x",
        "umami_src": "https://a.example.com/script.js",
        "umami_website_id": "wid-123",
    }, follow_redirects=True)
    with app.app_context():
        assert db.get_setting("umami_src") == "https://a.example.com/script.js"
        assert db.get_setting("umami_website_id") == "wid-123"
        # Wieder leeren, sonst leakt aktives Tracking in andere Tests (shared DATA_DIR)
        db.set_setting("umami_src", "")
        db.set_setting("umami_website_id", "")


def test_event_edit_renders(app, admin_client):
    eid = _seed_event(app)
    r = admin_client.get(f"/admin/events/{eid}/edit")
    assert r.status_code == 200


def test_admin_requires_login(client):
    # logged-out admin hits should redirect, never 200 the page
    r = client.get("/admin/settings")
    assert r.status_code in (301, 302)


def test_the_schema_flag_knows_which_database_it_applied_to(tmp_path, monkeypatch):
    """Ein Merker ohne Pfad richtet die zweite Datenbank nicht mehr ein.

    Gefunden am 19.09.2026: ``ecosystem.init_schema`` merkte sich in einem
    einzelnen Ja/Nein, dass es gelaufen war. Zeigte ``DATA_DIR`` danach
    woanders hin, übersprang es stumm — und die Tabellen fehlten dort. Im
    Betrieb gibt es nur eine Datenbank; im Testlauf fiel es in umgekehrter
    Reihenfolge als 317 Fehlschläge auf.
    """
    from app import db, ecosystem

    ecosystem.init_schema()                       # die geteilte Datenbank
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "zweite"))
    db.init_db()
    ecosystem.init_schema()
    con = db.get_db()
    try:
        con.execute("SELECT 1 FROM booth_assets LIMIT 1")
        con.execute("SELECT 1 FROM event_domains LIMIT 1")
    finally:
        con.close()
