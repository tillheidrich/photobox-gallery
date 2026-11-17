"""„Meine Auswahl": Gäste dürfen ihre Foto-Auswahl als ZIP laden.

Kernpunkte:
- funktioniert für Gäste (nicht admin-only), anders als /download.zip
- respektiert das Setting selection_download_enabled
- verlangt eine Auswahl (ids); „alles" ist hier nicht vorgesehen
- filtert exakt auf die gewählten ids
- deckelt die Anzahl (SELECTION_MAX)
"""
import io
import os
import secrets
import zipfile


def _make_event_with_photos(app, n=3, slug=None):
    from app import db, media
    slug = slug or ("sel-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        eid = db.create_event(slug=slug, name="Selection",
                              upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
        con.commit(); con.close()
        data_dir = media.data_dir()
        ids = []
        for i in range(n):
            rel = f"orig/{slug}-{i}.jpg"
            full = data_dir / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(b"\xff\xd8\xff\xe0JPEGDATA" + str(i).encode())
            mid = db.add_media(event_id=eid, kind="photo",
                               filename=f"foto_{i}.jpg", path_original=rel,
                               path_webp=None, path_thumb=None,
                               size_bytes=full.stat().st_size,
                               sha256=secrets.token_hex(8),
                               share_token=secrets.token_hex(6))
            ids.append(mid)
        return slug, ids


def test_selection_zip_works_for_guest(app, client):
    slug, ids = _make_event_with_photos(app, 3)
    picked = ids[:2]
    r = client.get(f"/gallery/{slug}/selection.zip?ids={picked[0]},{picked[1]}")
    assert r.status_code == 200, r.status_code
    assert r.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.get_data()))
    assert len(zf.namelist()) == 2  # nur die zwei gewählten


def test_selection_zip_requires_ids(app, client):
    slug, ids = _make_event_with_photos(app, 2)
    r = client.get(f"/gallery/{slug}/selection.zip")
    assert r.status_code == 400  # keine Auswahl -> kein „alles"


def test_selection_zip_filters_unknown_ids(app, client):
    slug, ids = _make_event_with_photos(app, 2)
    r = client.get(f"/gallery/{slug}/selection.zip?ids=999999")
    assert r.status_code == 404  # nichts Gültiges dabei


def test_selection_zip_respects_setting(app, client):
    slug, ids = _make_event_with_photos(app, 2)
    with app.app_context():
        from app import db
        db.set_setting("selection_download_enabled", "0")
    r = client.get(f"/gallery/{slug}/selection.zip?ids={ids[0]}")
    assert r.status_code == 403
    # wieder einschalten fuer nachfolgende Tests (shared DATA_DIR)
    with app.app_context():
        from app import db
        db.set_setting("selection_download_enabled", "1")


def test_selection_bar_rendered_on_photo_only_gallery(app, client):
    slug, ids = _make_event_with_photos(app, 2)
    with app.app_context():
        from app import db
        db.set_setting("selection_download_enabled", "1")
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert 'id="selectionBar"' in html          # Auswahl-Leiste da
    assert 'onclick="toggleSelectMode()"' in html
