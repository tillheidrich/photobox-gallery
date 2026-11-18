"""„Meine Aufnahmen" (/meine) — ohne Biometrie.

Die Liste entsteht allein aus den QR-Codes, die dieser Browser gescannt hat
(``session['p_uids']``). Kein Gesichtsmodell, keine Anmeldung, keine Liste auf
dem Server. Das muss auch so getestet sein.
"""
import json
import secrets

from PIL import Image

from app import gallery_settings as gsm, media


def _event(app, slug=None, gallery=None):
    from app import db, ecosystem
    slug = slug or ("mine-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        eid = db.create_event(slug=slug, name="Sommerfest",
                              upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1, share_key=?, gallery_json=? WHERE id=?",
                    (secrets.token_urlsafe(16), json.dumps(gsm.clean(gallery or {})), eid))
        con.commit(); con.close()
        return slug, eid


def _shot(app, event_id, slug, session_uid, taken="2026-07-01T20:00:00"):
    from app import db
    with app.app_context():
        name = f"{session_uid}_{secrets.token_hex(2)}"
        rel = f"storage/{slug}/webp/{name}.jpg"
        full = media.data_dir() / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 1000), (40, 90, 120)).save(full, "JPEG")
        mid = db.add_media(event_id=event_id, kind="photo", filename=f"{name}.jpg",
                           path_original=rel, path_webp=rel, path_thumb=rel,
                           size_bytes=full.stat().st_size,
                           sha256=secrets.token_hex(8),
                           share_token=secrets.token_hex(6))
        con = db.get_db()
        con.execute("UPDATE media SET session_uid=?, uid=?, variant='web', taken_at=? WHERE id=?",
                    (session_uid, name, taken, mid))
        con.commit(); con.close()
        return mid


def _scanned(client, *uids):
    with client.session_transaction() as s:
        s["p_uids"] = list(uids)


# ── Seite ────────────────────────────────────────────────────

def test_page_without_scans_explains_itself(app, client):
    _event(app)
    r = client.get("/meine")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Noch nichts gescannt" in html
    assert "Gesichtserkennung" in html          # der Punkt der ganzen Seite


def test_page_lists_the_scanned_session(app, client):
    slug, eid = _event(app)
    _shot(app, eid, slug, "sess1")
    _shot(app, eid, slug, "sess1")
    _scanned(client, "sess1")
    html = client.get("/meine").get_data(as_text=True)
    assert "Sommerfest" in html
    assert "2 Aufnahmen" in html
    assert "/p/sess1-" in html                  # signierter Link zum Durchgang
    assert "nur in diesem browser" in html.lower()


def test_download_and_share_actions_are_offered(app, client):
    slug, eid = _event(app)
    mid = _shot(app, eid, slug, "sess2")
    _scanned(client, "sess2")
    html = client.get("/meine").get_data(as_text=True)
    # One download, the best the event allows (18.11.2026): the original here.
    assert f"/gallery/{slug}/photo/{mid}/download?size=original" in html
    assert 'download?size=large&amp;code' not in html and "download?size=large&code" not in html
    # Sharing fetches at most 2048 px (iOS share sheet, 18.11.2026).
    assert "mineShare(" in html and "download?size=large\\u0026share=1" in html


def test_original_button_follows_the_event_setting(app, client):
    slug, eid = _event(app, gallery={"download_original": False})
    _shot(app, eid, slug, "sess3")
    _scanned(client, "sess3")
    html = client.get("/meine").get_data(as_text=True)
    assert "size=original" not in html
    assert "size=large" in html                 # the largest size the event allows


def test_sessions_are_grouped_newest_first(app, client):
    slug, eid = _event(app)
    _shot(app, eid, slug, "old1", taken="2026-06-01T18:00:00")
    _shot(app, eid, slug, "new1", taken="2026-08-01T18:00:00")
    _scanned(client, "old1", "new1")
    html = client.get("/meine").get_data(as_text=True)
    assert html.index("/p/new1-") < html.index("/p/old1-")


def test_expired_gallery_is_not_listed(app, client):
    slug, eid = _event(app, gallery={"expires_at": "2020-01-01T00:00"})
    _shot(app, eid, slug, "gone1")
    _scanned(client, "gone1")
    html = client.get("/meine").get_data(as_text=True)
    assert "Noch nichts gescannt" in html


def test_scheduled_gallery_is_not_listed_before_the_start(app, client):
    from app import db
    slug, eid = _event(app)
    _shot(app, eid, slug, "later1")
    with app.app_context():
        db.update_event(eid, scheduled_live_at="2099-01-01T10:00", published=0)
    _scanned(client, "later1")
    html = client.get("/meine").get_data(as_text=True)
    assert "Noch nichts gescannt" in html


def test_list_can_be_cleared_and_lives_only_in_the_session(app, client):
    slug, eid = _event(app)
    _shot(app, eid, slug, "sess4")
    _scanned(client, "sess4")
    assert "Sommerfest" in client.get("/meine").get_data(as_text=True)

    r = client.post("/meine/leeren")
    assert r.status_code == 302
    with client.session_transaction() as s:
        assert "p_uids" not in s
    assert "Noch nichts gescannt" in client.get("/meine").get_data(as_text=True)


def test_other_browser_sees_nothing(app, client):
    """Ohne Scan keine Liste — es gibt nichts, was sie serverseitig verknüpft."""
    slug, eid = _event(app)
    _shot(app, eid, slug, "sess5")
    html = client.get("/meine").get_data(as_text=True)
    assert "Sommerfest" not in html


# ── Einstieg aus der Galerie ─────────────────────────────────

def test_gallery_header_links_only_after_a_scan(app, client):
    slug, eid = _event(app)
    _shot(app, eid, slug, "sess6")
    assert 'href="/meine"' not in client.get(f"/gallery/{slug}").get_data(as_text=True)
    _scanned(client, "sess6")
    assert 'href="/meine"' in client.get(f"/gallery/{slug}").get_data(as_text=True)
