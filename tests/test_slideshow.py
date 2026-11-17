"""Slideshow (Beamer/Screen): Route, JSON-Feed, Zugriffsschutz.

Wichtig: Die Slideshow läuft am Event auf einem Screen — sie muss ohne Login
funktionieren, darf aber unveröffentlichte oder passwortgeschützte Galerien
nicht preisgeben.
"""
import secrets


def _event_with_photos(app, n=3, slug=None, published=True, password=None):
    from app import db, media
    slug = slug or ("ss-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        eid = db.create_event(slug=slug, name="Slideshow-Event",
                              upload_token=secrets.token_hex(8),
                              password=password)
        # Events sind per Spalten-Default veröffentlicht -> für den
        # Negativtest explizit zurücksetzen.
        con = db.get_db()
        con.execute("UPDATE events SET published=? WHERE id=?",
                    (1 if published else 0, eid))
        con.commit(); con.close()
        data_dir = media.data_dir()
        ids = []
        for i in range(n):
            rel = f"webp/{slug}-{i}.webp"
            full = data_dir / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(b"RIFFfakewebp")
            mid = db.add_media(event_id=eid, kind="photo",
                               filename=f"foto_{i}.jpg", path_original=rel,
                               path_webp=rel, path_thumb=rel,
                               size_bytes=12, sha256=secrets.token_hex(8),
                               share_token=secrets.token_hex(6))
            ids.append(mid)
        return slug, eid, ids


def test_slideshow_renders_for_guest(app, client):
    slug, _, _ = _event_with_photos(app, 3)
    r = client.get(f"/slideshow/{slug}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="ss-stage"' in html
    assert "slideshow.json" in html          # Polling verdrahtet
    assert 'id="ssCorner"' in html           # QR-Ecke da


def test_slideshow_feed_returns_photos(app, client):
    slug, _, ids = _event_with_photos(app, 3)
    r = client.get(f"/gallery/{slug}/slideshow.json")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert len(data["photos"]) == 3
    got = {p["id"] for p in data["photos"]}
    assert got == set(ids)
    assert all(p["url"].startswith("/data/") for p in data["photos"])


def test_slideshow_hidden_for_unpublished(app, client):
    slug, _, _ = _event_with_photos(app, 2, published=False)
    assert client.get(f"/slideshow/{slug}").status_code == 404
    assert client.get(f"/gallery/{slug}/slideshow.json").status_code == 404


def test_slideshow_respects_password_gate(app, client):
    slug, _, _ = _event_with_photos(app, 2, password="geheim123")
    assert client.get(f"/slideshow/{slug}").status_code == 403
    # Der JSON-Feed darf die Fotos ebenfalls nicht durchreichen
    assert client.get(f"/gallery/{slug}/slideshow.json").status_code == 403


def test_slideshow_empty_state(app, client):
    slug, _, _ = _event_with_photos(app, 0)
    html = client.get(f"/slideshow/{slug}").get_data(as_text=True)
    assert 'id="ssEmpty"' in html
    # Ausgerechnet die Zusage „der leere Zustand ist sichtbar" stand hier hinter
    # einem ``or True`` und war damit unbelegt. Jetzt zählt sie.
    nach_dem_id = html.split('id="ssEmpty"')[1]
    bis_zum_ende_des_tags = nach_dem_id.split(">", 1)[0]
    assert "hidden" not in bis_zum_ende_des_tags, "Der leere Zustand ist versteckt"
    assert client.get(f"/gallery/{slug}/slideshow.json").get_json()["photos"] == []
