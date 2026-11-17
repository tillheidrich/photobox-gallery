"""Branding pro Event: Hintergrund, Textfarbe, Banner, Schrift.

Drei Regeln, die hier festgenagelt werden:
1. Event → Installation → Standard-Theme (Rückfall in dieser Reihenfolge).
2. Die Textfarbe wird gerechnet, wenn sie fehlt — nichts wird unlesbar.
3. Schlechter Kontrast warnt, blockiert aber nicht.
"""
import secrets

from app import branding


def _event(app, design="{}", slug=None):
    from app import db, ecosystem
    slug = slug or ("bre-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        eid = db.create_event(slug=slug, name="Branding", upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1, design_json=? WHERE id=?", (design, eid))
        con.commit(); con.close()
        return slug, eid


def _global(app, **settings):
    from app import db
    with app.app_context():
        db.init_db()
        for k, v in settings.items():
            db.set_setting(k, v)


def _reset_global(app):
    _global(app, branding_accent="", branding_bg="", branding_text="",
            branding_font="", branding_banner_path="")


# ── reine Logik ──────────────────────────────────────────────

def test_fonts_are_a_small_curated_set():
    assert branding.FONT_IDS == ["system", "serif", "geometric"]
    for stack in branding.FONTS.values():
        assert stack and "fonts.googleapis" not in stack and "http" not in stack


def test_font_id_rejects_anything_else():
    assert branding.font_id("serif") == "serif"
    assert branding.font_id("Comic") == ""
    assert branding.font_id(None) == ""


def test_text_colour_is_derived_from_the_background():
    b = branding.event_branding({"design_json": '{"bg": "#101010"}'})
    assert b["text"] == "#ffffff"
    b = branding.event_branding({"design_json": '{"bg": "#fefefe"}'})
    assert b["text"] == "#1a1206"


def test_explicit_text_colour_wins():
    b = branding.event_branding({"design_json": '{"bg": "#101010", "text": "#ffcc00"}'})
    assert b["text"] == "#ffcc00"


def test_css_vars_only_contain_what_is_set():
    assert branding.css_vars({}) == {}
    vars_ = branding.css_vars(branding.event_branding(
        {"design_json": '{"accent": "#1d4ed8", "bg": "#ffffff", "font": "serif"}'}))
    assert vars_["--color-primary"] == "#1d4ed8"
    assert vars_["--color-bg"] == "#ffffff"
    assert "Georgia" in vars_["--font"]


def test_contrast_warning_fires_below_aa():
    warn = branding.contrast_warnings({"bg": "#ffffff", "text": "#f0f0f0"})
    assert warn and warn[0]["kind"] == "text_on_bg" and warn[0]["need"] == 4.5


def test_no_contrast_warning_for_a_readable_pair():
    assert branding.contrast_warnings({"bg": "#ffffff", "text": "#222222"}) == []


# ── Rückfall auf die Installation ────────────────────────────

def test_event_overrides_the_global_setting(app):
    _global(app, branding_accent="#111111", branding_font="serif")
    b = branding.event_branding({"design_json": '{"accent": "#1d4ed8", "font": "geometric"}'})
    assert b["accent"] == "#1d4ed8" and b["font"] == "geometric"
    _reset_global(app)


def test_global_setting_applies_without_event_branding(app):
    _global(app, branding_accent="#16a34a", branding_bg="#101014", branding_font="serif")
    b = branding.event_branding({"design_json": "{}"})
    assert b["accent"] == "#16a34a"
    assert b["bg"] == "#101014"
    assert b["text"] == "#ffffff"              # gerechnet
    assert b["font"] == "serif"
    _reset_global(app)


def test_default_theme_when_nothing_is_set(app):
    _reset_global(app)
    b = branding.event_branding({"design_json": "{}"})
    assert b["accent"] == "" and b["bg"] == "" and b["font"] == ""
    assert b["has_custom"] is False


# ── Anwendung auf den Gastseiten ─────────────────────────────

def test_gallery_applies_background_text_and_font(app, client):
    _reset_global(app)
    slug, _ = _event(app, '{"bg": "#101014", "font": "serif"}')
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert "--color-bg: #101014" in html
    assert "--color-text: #ffffff" in html
    assert "Georgia" in html


def test_banner_is_rendered_in_the_gallery(app, client):
    _reset_global(app)
    slug, _ = _event(app, '{"banner_path": "assets/events/demo/banner.jpg"}')
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert "/data/assets/events/demo/banner.jpg" in html
    assert "gallery-banner" in html


def test_session_page_inherits_the_branding(app, client):
    """Die Durchgangs-Seite holt das Branding selbst — ohne Zutun ihrer Route."""
    from app import db, ecosystem
    _reset_global(app)
    slug, eid = _event(app, '{"accent": "#7c3aed"}')
    with app.app_context():
        key = ecosystem.ensure_share_key(db.get_event_by_id(eid))
        code = f"absentuid-{ecosystem.signature(key, 'absentuid')}"
    html = client.get(f"/p/{code}").get_data(as_text=True)
    assert "--color-primary: #7c3aed" in html


def test_kiosk_and_slideshow_inherit_the_branding(app, client):
    _reset_global(app)
    slug, _ = _event(app, '{"accent": "#7c3aed", "bg": "#101014"}')
    for path in (f"/kiosk/{slug}", f"/slideshow/{slug}"):
        html = client.get(path).get_data(as_text=True)
        assert "--color-primary: #7c3aed" in html, path
        assert "--color-bg: #101014" in html, path


def test_share_link_page_inherits_the_branding(app, client):
    from app import sharelinks
    _reset_global(app)
    slug, eid = _event(app, '{"accent": "#7c3aed"}')
    with app.app_context():
        link = sharelinks.create(eid, media_ids=[], title="Test", note="", days="30",
                                 allow_download=True, password="")
    html = client.get(f"/l/{link['slug']}").get_data(as_text=True)
    assert "--color-primary: #7c3aed" in html


# ── Admin ────────────────────────────────────────────────────

def test_admin_saves_colours_font_and_warns_without_blocking(app, client):
    from app import db
    _reset_global(app)
    slug, eid = _event(app)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    r = client.post(f"/admin/events/{eid}/edit", data={
        "csrf_token": "x", "name": "Branding", "slug": slug,
        "brand_bg": "#ffffff", "brand_text": "#f2f2f2",      # schlechter Kontrast
        "brand_font": "geometric",
    }, follow_redirects=True)
    html = r.get_data(as_text=True)
    with app.app_context():
        b = branding.event_branding(db.get_event_by_id(eid))
    assert b["bg"] == "#ffffff" and b["text"] == "#f2f2f2"   # trotzdem gespeichert
    assert b["font"] == "geometric"
    assert "Kontrast" in html                                 # aber mit Warnung


def test_admin_ignores_a_broken_colour(app, client):
    from app import db
    _reset_global(app)
    slug, eid = _event(app)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/events/{eid}/edit", data={
        "csrf_token": "x", "name": "Branding", "slug": slug,
        "brand_bg": "url(evil)", "brand_font": "ComicSans",
    }, follow_redirects=True)
    with app.app_context():
        b = branding.event_branding(db.get_event_by_id(eid))
    assert b["bg"] == "" and b["font"] == ""


def test_global_branding_form_saves(app, client):
    from app import db
    _reset_global(app)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post("/admin/settings/branding", data={
        "csrf_token": "x", "branding_accent": "#1d4ed8",
        "branding_bg": "#0b0b0e", "branding_font": "serif",
    }, follow_redirects=True)
    with app.app_context():
        assert db.get_setting("branding_accent") == "#1d4ed8"
        assert db.get_setting("branding_bg") == "#0b0b0e"
        assert db.get_setting("branding_font") == "serif"
    _reset_global(app)
