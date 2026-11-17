"""Download-Größen als Auswahl-Blatt: 640 / 1024 / 2048 px plus Original.

Gäste laden am Handy. Wer ein Bild für WhatsApp will, darf kein 8-MB-Original
bekommen — und der Veranstalter darf einzelne Größen abschalten.
"""
import io
import json
import secrets

from PIL import Image

from app import gallery_settings as gsm, media


def _event_with_photo(app, w=3000, h=2000, slug=None, gallery=None):
    from app import db, ecosystem
    slug = slug or ("do-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("download_sizes_enabled", "1")
        eid = db.create_event(slug=slug, name="Größen",
                              upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1, gallery_json=? WHERE id=?",
                    (json.dumps(gsm.clean(gallery or {})), eid))
        con.commit(); con.close()

        rel = f"orig/{slug}.jpg"
        full = media.data_dir() / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (w, h), (90, 110, 60)).save(full, "JPEG", quality=90)
        mid = db.add_media(event_id=eid, kind="photo", filename="foto_1.jpg",
                           path_original=rel, path_webp=rel, path_thumb=rel,
                           size_bytes=full.stat().st_size,
                           sha256=secrets.token_hex(8),
                           share_token=secrets.token_hex(6))
        con = db.get_db()
        con.execute("UPDATE media SET width=?, height=? WHERE id=?", (w, h, mid))
        con.commit(); con.close()
        return slug, eid, mid


def _longest_edge(data: bytes) -> int:
    with Image.open(io.BytesIO(data)) as im:
        return max(im.size)


# ── reine Logik ──────────────────────────────────────────────

def test_offered_sizes_are_640_1024_2048():
    assert [media.DOWNLOAD_SIZES[s] for s in media.DOWNLOAD_SIZE_ORDER] == [640, 1024, 2048]


def test_scaled_size_keeps_ratio_and_never_upscales():
    assert media.scaled_size(3000, 2000, 640) == (640, 427)
    assert media.scaled_size(400, 300, 1024) == (400, 300)


def test_estimate_grows_with_edge_length():
    small = media.estimate_download_bytes(3000, 2000, 640)
    large = media.estimate_download_bytes(3000, 2000, 2048)
    assert 0 < small < large


def test_allowed_sizes_follow_the_event_settings():
    cfg = gsm.clean({})
    assert media.allowed_download_sizes(cfg) == ["small", "web", "large", "original"]

    cfg = gsm.clean({"download_640": False, "download_original": False})
    assert media.allowed_download_sizes(cfg) == ["web", "large"]


def test_download_original_stays_authoritative_for_the_original():
    cfg = gsm.clean({"download_original": False})
    assert "original" not in media.allowed_download_sizes(cfg)


def test_all_sizes_off_still_leaves_the_original():
    cfg = gsm.clean({"download_640": False, "download_1024": False,
                     "download_2048": False, "download_original": False})
    assert media.allowed_download_sizes(cfg) == ["original"]


def test_download_options_carry_estimates():
    rows = [{"width": 3000, "height": 2000, "size_bytes": 7_000_000}]
    opts = media.download_options(gsm.clean({}), rows)
    assert [o["id"] for o in opts] == ["small", "web", "large", "original"]
    assert all(o["size_h"] for o in opts)
    assert opts[-1]["bytes"] == 7_000_000        # Original: echte Größe aus der DB


def test_download_options_without_dimensions_stay_quiet():
    opts = media.download_options(gsm.clean({}), [{"width": None, "height": None}])
    assert [o["size_h"] for o in opts[:3]] == ["", "", ""]


# ── Route ────────────────────────────────────────────────────

def test_small_size_is_scaled_to_640(app, client):
    slug, _, mid = _event_with_photo(app)
    r = client.get(f"/gallery/{slug}/photo/{mid}/download?size=small")
    assert r.status_code == 200
    assert _longest_edge(r.get_data()) == 640


def test_disabled_size_falls_back_to_the_largest_allowed(app, client):
    slug, _, mid = _event_with_photo(app, gallery={"download_2048": False})
    r = client.get(f"/gallery/{slug}/photo/{mid}/download?size=large")
    assert r.status_code == 200
    assert _longest_edge(r.get_data()) == 1024


def test_sheet_only_offers_allowed_sizes(app, client):
    slug, _, _ = _event_with_photo(app, gallery={"download_640": False,
                                                 "download_original": False})
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert 'data-size="web"' in html
    assert 'data-size="large"' in html
    assert 'data-size="small"' not in html
    assert 'data-size="original"' not in html


def test_sheet_shows_size_estimate(app, client):
    slug, _, _ = _event_with_photo(app)
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert "640 px" in html
    assert "ca. " in html                 # geschätzte Dateigröße


def test_selection_zip_honours_small_size(app, client):
    import zipfile
    slug, _, mid = _event_with_photo(app)
    r = client.get(f"/gallery/{slug}/selection.zip?ids={mid}&size=small")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.get_data()))
    assert _longest_edge(zf.read(zf.namelist()[0])) == 640
