"""Download in wählbarer Größe (Web / Groß / Original).

Wichtig: Gäste sollen nicht gezwungen sein, ein mehrere MB großes Original
aufs Handy zu laden. Die skalierten Fassungen werden serverseitig erzeugt
und gecacht.
"""
import io
import secrets
import zipfile

from PIL import Image


def _event_with_photo(app, w=3000, h=2000, slug=None, password=None):
    from app import db, media
    slug = slug or ("dl-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        db.set_setting("download_sizes_enabled", "1")
        eid = db.create_event(slug=slug, name="Download",
                              upload_token=secrets.token_hex(8),
                              password=password)
        con = db.get_db()
        con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
        con.commit(); con.close()

        rel = f"orig/{slug}.jpg"
        full = media.data_dir() / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (w, h), (120, 80, 40)).save(full, "JPEG", quality=90)
        mid = db.add_media(event_id=eid, kind="photo", filename="foto_1.jpg",
                           path_original=rel, path_webp=rel, path_thumb=rel,
                           size_bytes=full.stat().st_size,
                           sha256=secrets.token_hex(8),
                           share_token=secrets.token_hex(6))
        return slug, eid, mid


def _longest_edge(data: bytes) -> int:
    with Image.open(io.BytesIO(data)) as im:
        return max(im.size)


def test_web_size_is_scaled_down(app, client):
    slug, _, mid = _event_with_photo(app)
    r = client.get(f"/gallery/{slug}/photo/{mid}/download?size=web")
    assert r.status_code == 200
    assert _longest_edge(r.get_data()) == 1024


def test_large_size_is_scaled_down(app, client):
    slug, _, mid = _event_with_photo(app)
    r = client.get(f"/gallery/{slug}/photo/{mid}/download?size=large")
    assert r.status_code == 200
    assert _longest_edge(r.get_data()) == 2048


def test_original_keeps_full_resolution(app, client):
    slug, _, mid = _event_with_photo(app, w=3000, h=2000)
    r = client.get(f"/gallery/{slug}/photo/{mid}/download?size=original")
    assert r.status_code == 200
    assert _longest_edge(r.get_data()) == 3000


def test_web_is_smaller_than_original(app, client):
    slug, _, mid = _event_with_photo(app)
    web = client.get(f"/gallery/{slug}/photo/{mid}/download?size=web").get_data()
    orig = client.get(f"/gallery/{slug}/photo/{mid}/download?size=original").get_data()
    assert len(web) < len(orig)


def test_unknown_size_falls_back_to_default(app, client):
    slug, _, mid = _event_with_photo(app)
    r = client.get(f"/gallery/{slug}/photo/{mid}/download?size=quatsch")
    assert r.status_code == 200
    assert _longest_edge(r.get_data()) == 2048   # DEFAULT_DOWNLOAD_SIZE


def test_setting_off_forces_original(app, client):
    slug, _, mid = _event_with_photo(app, w=3000, h=2000)
    with app.app_context():
        from app import db
        db.set_setting("download_sizes_enabled", "0")
    r = client.get(f"/gallery/{slug}/photo/{mid}/download?size=web")
    assert r.status_code == 200
    assert _longest_edge(r.get_data()) == 3000   # trotz size=web
    with app.app_context():
        from app import db
        db.set_setting("download_sizes_enabled", "1")


def test_foreign_media_id_is_rejected(app, client):
    """Ein Foto aus Event A darf nicht über den Slug von Event B laufen."""
    slug_a, _, mid_a = _event_with_photo(app, slug="dl-a-" + secrets.token_hex(2))
    slug_b, _, _ = _event_with_photo(app, slug="dl-b-" + secrets.token_hex(2))
    r = client.get(f"/gallery/{slug_b}/photo/{mid_a}/download?size=web")
    assert r.status_code == 404


def test_password_gate_applies(app, client):
    slug, _, mid = _event_with_photo(app, password="geheim123")
    r = client.get(f"/gallery/{slug}/photo/{mid}/download?size=web")
    assert r.status_code == 403


def test_selection_zip_honours_size(app, client):
    slug, _, mid = _event_with_photo(app)
    r = client.get(f"/gallery/{slug}/selection.zip?ids={mid}&size=web")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.get_data()))
    names = zf.namelist()
    assert len(names) == 1
    assert _longest_edge(zf.read(names[0])) == 1024


def test_cache_is_reused(app, client):
    """Zweiter Abruf liefert dieselbe Datei aus dem Cache."""
    from app import media
    slug, _, mid = _event_with_photo(app)
    client.get(f"/gallery/{slug}/photo/{mid}/download?size=web")
    with app.app_context():
        cached = media.download_cache_dir() / f"{mid}_web.jpg"
        assert cached.exists()
        stamp = cached.stat().st_mtime
    client.get(f"/gallery/{slug}/photo/{mid}/download?size=web")
    with app.app_context():
        assert (media.download_cache_dir() / f"{mid}_web.jpg").stat().st_mtime == stamp


def test_size_picker_rendered_in_gallery(app, client):
    slug, _, _ = _event_with_photo(app)
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert 'id="dlSheet"' in html
    assert 'data-size="web"' in html
    assert 'data-size="original"' in html
