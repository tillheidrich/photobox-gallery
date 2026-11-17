"""Branding pro Event: Akzentfarbe, eigenes Logo, Kontrast-Automatik.

Kernversprechen: Der Betreiber kann sich nicht in einen unlesbaren Zustand
konfigurieren — die Textfarbe auf dem Akzent wird gerechnet, nicht gewählt.
"""
import secrets

import pytest

from app import branding


# ── reine Logik ──────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("#c98a2e", "#c98a2e"),
    ("c98a2e", "#c98a2e"),
    ("#ABC", "#aabbcc"),
    ("  #FFF  ", "#ffffff"),
    ("", ""),
    (None, ""),
    ("rot", ""),
    ("#12345", ""),
    ("#gggggg", ""),
    ("javascript:alert(1)", ""),      # nichts Fremdes darf ins CSS
])
def test_normalize_hex(raw, expected):
    assert branding.normalize_hex(raw) == expected


def test_on_color_picks_readable_text():
    assert branding.on_color("#ffffff") == "#1a1206"   # helle Farbe -> dunkler Text
    assert branding.on_color("#000000") == "#ffffff"   # dunkle Farbe -> heller Text
    assert branding.on_color("#1d4ed8") == "#ffffff"   # sattes Blau -> heller Text
    assert branding.on_color("#fde047") == "#1a1206"   # helles Gelb -> dunkler Text


def test_on_color_always_meets_readable_contrast():
    """Für jede Farbe muss die gewählte Textfarbe mindestens 4.5:1 erreichen."""
    for hexv in ["#c98a2e", "#1d4ed8", "#16a34a", "#dc2626", "#fde047",
                 "#000000", "#ffffff", "#808080", "#7c3aed"]:
        on = branding.on_color(hexv)
        assert branding.contrast_ratio(hexv, on) >= 4.5, f"{hexv} -> {on}"


def test_parse_design_survives_garbage():
    assert branding.parse_design(None) == {}
    assert branding.parse_design("") == {}
    assert branding.parse_design("kein json") == {}
    assert branding.parse_design("[1,2,3]") == {}      # Liste ist kein Dict
    assert branding.parse_design('{"accent":"#fff"}') == {"accent": "#fff"}


def test_event_branding_defaults_to_empty():
    b = branding.event_branding({"design_json": "{}"})
    assert b["accent"] == "" and b["logo_url"] == "" and b["has_custom"] is False


def test_event_branding_reads_values():
    b = branding.event_branding({
        "design_json": '{"accent": "#1d4ed8", "logo_path": "assets/events/x/logo.png"}'
    })
    assert b["accent"] == "#1d4ed8"
    assert b["accent_on"] == "#ffffff"
    assert b["logo_url"] == "/data/assets/events/x/logo.png"
    assert b["has_custom"] is True


# ── Integration ──────────────────────────────────────────────

def _event(app, design="{}", slug=None):
    from app import db
    slug = slug or ("br-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        eid = db.create_event(slug=slug, name="Branding",
                              upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1, design_json=? WHERE id=?", (design, eid))
        con.commit(); con.close()
        return slug, eid


def test_gallery_injects_accent(app, client):
    slug, _ = _event(app, '{"accent": "#1d4ed8"}')
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert "--color-primary: #1d4ed8" in html
    assert "--color-on-primary: #ffffff" in html


def test_gallery_without_branding_has_no_override(app, client):
    slug, _ = _event(app, "{}")
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert "--color-primary:" not in html


def test_broken_design_json_does_not_break_gallery(app, client):
    slug, _ = _event(app, "das ist kaputt {{{")
    r = client.get(f"/gallery/{slug}")
    assert r.status_code == 200          # Galerie muss trotzdem laden


def test_event_logo_overrides_global(app, client):
    from app import db
    slug, _ = _event(app, '{"logo_path": "assets/events/demo/logo.png"}')
    with app.app_context():
        db.set_setting("branding_logo_path", "assets/branding/logo.png")
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert "/data/assets/events/demo/logo.png" in html
    assert 'src="/branding/logo"' not in html
    with app.app_context():
        db.set_setting("branding_logo_path", "")


def test_slideshow_inherits_accent(app, client):
    slug, _ = _event(app, '{"accent": "#16a34a"}')
    html = client.get(f"/slideshow/{slug}").get_data(as_text=True)
    assert "--color-primary: #16a34a" in html


def test_admin_saves_and_clears_accent(app, client):
    from app import db
    slug, eid = _event(app, "{}")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"

    client.post(f"/admin/events/{eid}/edit", data={
        "csrf_token": "x", "name": "Branding", "slug": slug,
        "brand_accent": "#7c3aed",
    }, follow_redirects=True)
    with app.app_context():
        ev = db.get_event_by_id(eid)
        assert branding.event_branding(ev)["accent"] == "#7c3aed"

    # Leeres Feld entfernt die Farbe wieder
    client.post(f"/admin/events/{eid}/edit", data={
        "csrf_token": "x", "name": "Branding", "slug": slug,
        "brand_accent": "",
    }, follow_redirects=True)
    with app.app_context():
        assert branding.event_branding(db.get_event_by_id(eid))["accent"] == ""


def test_admin_rejects_invalid_accent(app, client):
    from app import db
    slug, eid = _event(app, "{}")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/events/{eid}/edit", data={
        "csrf_token": "x", "name": "Branding", "slug": slug,
        "brand_accent": "'; background:url(evil)",
    }, follow_redirects=True)
    with app.app_context():
        assert branding.event_branding(db.get_event_by_id(eid))["accent"] == ""
