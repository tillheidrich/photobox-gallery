"""Umami-Tracking: konfigurierbar, standardmäßig aus, Datenschutz-Hinweis."""
import secrets


def _pub_event(app, slug="trk"):
    with app.app_context():
        from app import db
        db.init_db()
        eid = db.create_event(slug=slug, name="T", upload_token=secrets.token_hex(6))
        con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (eid,)); con.commit(); con.close()
        return eid


def test_no_umami_when_unconfigured(app, client):
    _pub_event(app, "trk-off")
    html = client.get("/gallery/trk-off").get_data(as_text=True)
    assert "data-website-id" not in html
    assert "window.pbTrack" in html  # Shim ist trotzdem da (no-op)


def test_umami_injected_when_configured(app, client):
    from app import db
    _pub_event(app, "trk-on")
    with app.app_context():
        db.set_setting("umami_src", "https://umami.example.com/script.js")
        db.set_setting("umami_website_id", "abc-123-def")
    html = client.get("/gallery/trk-on").get_data(as_text=True)
    assert 'src="https://umami.example.com/script.js"' in html
    assert 'data-website-id="abc-123-def"' in html


def test_home_and_declarative_events(app, client):
    from app import db
    with app.app_context():
        db.init_db()
        db.set_setting("umami_src", "https://umami.example.com/script.js")
        db.set_setting("umami_website_id", "wid")
    html = client.get("/").get_data(as_text=True)
    assert 'data-website-id="wid"' in html
    assert 'data-umami-event="code-search"' in html
    assert 'data-umami-event="lang-switch"' in html


def test_datenschutz_mentions_umami_only_when_enabled(app, client, legal_facts):
    from app import db
    with app.app_context():
        db.init_db()
        db.set_setting("umami_src", "")
        db.set_setting("umami_website_id", "")
    # aus -> kein Umami-Absatz
    assert "Umami" not in client.get("/datenschutz").get_data(as_text=True)
    with app.app_context():
        db.set_setting("umami_src", "https://umami.example.com/script.js")
        db.set_setting("umami_website_id", "wid")
    assert "Umami" in client.get("/datenschutz").get_data(as_text=True)
