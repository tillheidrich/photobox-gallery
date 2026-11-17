"""Legal-Seiten immer erreichbar (mit Default) + Sharepic inline."""
import secrets, pytest


def test_impressum_default_renders(client, legal_facts):
    r = client.get("/impressum")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Impressum" in body and "DDG" in body  # Default-Vorlage


def test_datenschutz_default_renders(client, legal_facts):
    r = client.get("/datenschutz")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Datenschutz" in body and "DSGVO" in body


def test_footer_links_legal_always(client):
    r = client.get("/")
    body = r.get_data(as_text=True)
    assert '/impressum' in body and '/datenschutz' in body


def test_custom_legal_overrides_default(app, client):
    with app.app_context():
        from app import db
        db.init_db()
        db.set_setting("impressum_text", "## Mein eigenes Impressum XYZ")
    body = client.get("/impressum").get_data(as_text=True)
    assert "Mein eigenes Impressum XYZ" in body


def _mk_event(app):
    with app.app_context():
        from app import db
        db.init_db()
        return db.create_event(slug="share-t", name="Share T", upload_token=secrets.token_hex(6))


def test_sharepic_is_inline_png(app, client):
    _mk_event(app)
    r = client.get("/gallery/share-t/sharepic.png")
    assert r.status_code == 200
    assert r.mimetype == "image/png"
    # NICHT als Download erzwungen:
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" not in cd.lower()


def test_single_photo_share_wired(app, client):
    import io, secrets
    from PIL import Image
    from app import db, media
    with app.app_context():
        db.init_db()
        ev = db.create_event(slug="share-g", name="SG", upload_token=secrets.token_hex(6))
        con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (ev,)); con.commit(); con.close()
        b = io.BytesIO(); Image.new("RGB", (400, 500), (180, 150, 110)).save(b, "JPEG"); b.seek(0)
        class F:  # minimal FileStorage-Ersatz
            filename = "x.jpg"
            def __init__(self, s): self._s = s
            def save(self, p): open(p, "wb").write(self._s.getvalue())
            def read(self, *a): return self._s.read(*a)
            def seek(self, *a): return self._s.seek(*a)
        media.save_upload(ev, F(b))
        tok = db.list_media(ev)[0]["share_token"]
    html = client.get("/gallery/share-g").get_data(as_text=True)
    assert 'id="lbShare"' in html and "function sharePhoto" in html
    assert 'data-token=' in html
    # /s/<token> leitet auf die Galerie mit Foto-Anker
    r = client.get(f"/s/{tok}")
    assert r.status_code in (301, 302) and "#photo-" in r.headers["Location"]
