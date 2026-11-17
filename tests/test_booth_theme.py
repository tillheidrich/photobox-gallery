"""Aussehen der Box: Farben und Form kommen aus dem Profil.

Die Box setzt daraus CSS-Variablen. Wenn Galerie und Box hier auseinanderlaufen,
sieht der Betreiber im Formular etwas anderes als der Gast an der Box.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "booth" / "boothcore" / "web" / "static" / "app.js"


def _admin(client):
    client.get("/healthz")                      # Schema anlegen
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _token(client, dev_id) -> str:
    """Erst die Seite holen — sonst gibt es kein CSRF-Token zum Mitsenden."""
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        return s.get("csrf_token") or ""


def test_gallery_and_booth_ship_the_same_theme_block():
    from app import ecosystem
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore import config
    assert config.DEFAULT_PROFILE["theme"] == ecosystem.DEFAULT_PROFILE["theme"]


def test_form_saves_colors_and_shape(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Themebox")["id"]
    token = _token(client, dev_id)

    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on",
        "theme_accent": "#2f9e44", "theme_on_accent": "#ffffff", "theme_bg": "#fdfaf3",
        "theme_panel": "#ffffff", "theme_text": "#1b2b20", "theme_muted": "#5f6f63",
        "theme_radius": "round", "theme_size": "l", "theme_weight": "700",
        "theme_uppercase": "on", "theme_secondary": "outline", "theme_font": "rounded",
        "theme_mode": "eigene",
    }, follow_redirects=True)
    assert r.status_code == 200

    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["theme"] == {"mode": "eigene",
                          "accent": "#2f9e44", "on_accent": "#ffffff", "bg": "#fdfaf3",
                          "panel": "#ffffff", "text": "#1b2b20", "muted": "#5f6f63",
                          "radius": "round", "size": "l", "weight": "700",
                          "uppercase": True, "secondary": "outline", "font": "rounded",
                          "font_file": ""}
    # Ältere Boxen lesen noch das alte Feld — es muss mitlaufen.
    assert p["accent"] == "#2f9e44"


def test_form_refuses_nonsense(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Themebox 2")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on",
        "theme_accent": "rot bitte", "theme_radius": "wolke", "theme_size": "xxl",
        "theme_weight": "900", "theme_secondary": "schweben", "theme_font": "fraktur",
    }, follow_redirects=True)
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))["theme"]
    # The fallback is the shipped theme (since 15.11.2026; before, an older red)
    assert p["accent"] == ecosystem.DEFAULT_PROFILE["theme"]["accent"] and p["radius"] == "pill" and p["size"] == "m"
    assert p["weight"] == "600" and p["secondary"] == "solid" and p["font"] == "system"


def test_old_profiles_keep_their_accent():
    """Vor dem Abschnitt „Aussehen“ gab es nur accent — die Farbe darf nicht verschwinden."""
    from app import ecosystem
    p = ecosystem.device_profile({"profile_json": json.dumps({"accent": "#00aaff"})})
    assert p["theme"]["accent"] == "#00aaff"
    assert p["theme"]["radius"] == "pill"       # der Rest kommt aus dem Standard


def test_presets_only_use_values_the_booth_knows():
    from app.blueprints import admin_booth as ab
    js = APP_JS.read_text(encoding="utf-8")
    radii = set(re.findall(r"(\w+): \"[\d.]+px\"", js[js.index("const RADII"):js.index("const SIZES")]))
    fonts = set(re.findall(r"^\s{4}(\w+):", js[js.index("const FONTS"):js.index("const hex")], re.M))
    assert ab.THEME_RADII_IDS == radii, "Ecken-Auswahl und Box laufen auseinander"
    assert ab.THEME_FONT_IDS == fonts, "Schriftart-Auswahl und Box laufen auseinander"
    for name, preset in ab.THEME_PRESETS:
        assert preset["radius"] in ab.THEME_RADII_IDS, name
        assert preset["size"] in ab.THEME_SIZE_IDS, name
        assert preset["weight"] in ab.THEME_WEIGHT_IDS, name
        assert preset["secondary"] in ab.THEME_SECONDARY_IDS, name
        assert preset["font"] in ab.THEME_FONT_IDS, name
        for key in ("accent", "on_accent", "bg", "panel", "text", "muted"):
            assert re.fullmatch(r"#[0-9a-f]{6}", preset[key]), f"{name}: {key}"


def test_an_unknown_font_file_is_ignored(client):
    """Nur wirklich hochgeladene Schriften dürfen ins Profil — sonst zeigt die Box nichts."""
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Themebox 4")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "theme_font_file": "../../etc/passwd",
    }, follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["theme"]["font_file"] == ""


def test_preview_renders_with_the_form(client):
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Themebox 3")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'id="themePreview"' in body
    assert 'name="theme_accent"' in body and 'data-theme="radius"' in body
    assert "Kita &amp; Schule" in body                 # Vorlagen sind da


def test_the_preview_uses_the_same_font_as_the_booth():
    """Sonst sieht der Betreiber etwas anderes als der Gast."""
    from pathlib import Path
    tpl = Path(ROOT / "app" / "templates" / "admin" / "booth_profile.html").read_text(encoding="utf-8")
    js = Path(ROOT / "booth" / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    for schrift in ('"Inter"', '"Archivo"'):
        assert schrift in tpl and schrift in js, schrift


# ── Dunkel, hell, eigene ────────────────────────────────────────────────
def test_a_scheme_can_be_chosen_and_keeps_the_own_colors(client):
    """Ein Schema überschreibt die Farbfelder — es löscht sie aber nicht.

    Wer von „Eigene Farben" auf „Hell" stellt und es sich anders überlegt,
    findet seine Farben wieder vor.
    """
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Schemabox")["id"]
    token = _token(client, dev_id)
    felder = {"csrf_token": token, "mode_photo": "on",
              "theme_accent": "#2f9e44", "theme_on_accent": "#ffffff", "theme_bg": "#fdfaf3",
              "theme_panel": "#ffffff", "theme_text": "#1b2b20", "theme_muted": "#5f6f63"}
    client.post(f"/admin/devices/{dev_id}/booth", data={**felder, "theme_mode": "hell"},
                follow_redirects=True)
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["theme"]["mode"] == "hell"
    assert p["theme"]["accent"] == "#2f9e44", "Die eigenen Farben bleiben stehen"


def test_nonsense_falls_back_to_dark(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Schemabox 2")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth",
                data={"csrf_token": token, "mode_photo": "on", "theme_mode": "neon"},
                follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["theme"]["mode"] == "dunkel"


# ── Bildschirm als Blitz ────────────────────────────────────────────────
def test_the_screen_flash_is_saved(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Blitzbox")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "sf_enabled": "on",
        "sf_brightness": "80", "sf_lead": "300", "sf_hold": "350", "sf_color": "#fff1dd",
    }, follow_redirects=True)
    sf = ecosystem.device_profile(db.get_device_by_id(dev_id))["screen_flash"]
    assert sf == {"enabled": True, "brightness": 80, "lead_ms": 300,
                  "hold_ms": 350, "color": "#fff1dd"}


def test_the_screen_flash_refuses_nonsense(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Blitzbox 2")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "sf_enabled": "on",
        "sf_brightness": "1000", "sf_lead": "-5", "sf_color": "leuchtend",
    }, follow_redirects=True)
    sf = ecosystem.device_profile(db.get_device_by_id(dev_id))["screen_flash"]
    assert sf["brightness"] == 100 and sf["lead_ms"] == 0 and sf["color"] == "#ffffff"


def test_gallery_and_booth_ship_the_same_screen_flash():
    from app import ecosystem
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore import config
    assert config.DEFAULT_PROFILE["screen_flash"] == ecosystem.DEFAULT_PROFILE["screen_flash"]
