"""Der Bildschirmschoner muss vollständig über die Fernwartung steuerbar sein.

Er läuft dort, wo niemand zusieht — deshalb gehört jede Einstellung ins Profil
und nicht in den Kopf des Betreibers.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _token(client, dev_id) -> str:
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        return s.get("csrf_token") or ""


def test_gallery_and_booth_ship_the_same_attract_block():
    from app import ecosystem
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore import config
    assert config.DEFAULT_PROFILE["attract"] == ecosystem.DEFAULT_PROFILE["attract"]


def test_every_setting_survives_the_form(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Schonerbox")["id"]
    token = _token(client, dev_id)

    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on",
        "attract_enabled": "on", "attract_after": "45", "attract_source": "own",
        "attract_media": ["logo.mp4", "wand.jpg"], "attract_seconds": "9",
        "attract_show_hint": "on", "attract_hint_style": "center",
        "attract_color": "#101322", "attract_text_color": "#ffd166",
        "attract_dim": "40", "attract_fit": "contain",
    }, follow_redirects=True)
    assert r.status_code == 200

    a = ecosystem.device_profile(db.get_device_by_id(dev_id))["attract"]
    assert a["enabled"] and a["after_seconds"] == 45 and a["source"] == "own"
    assert a["media"] == ["logo.mp4", "wand.jpg"] and a["seconds_per_item"] == 9
    assert a["hint_style"] == "center" and a["dim"] == 40 and a["fit"] == "contain"
    assert a["color"] == "#101322" and a["text_color"] == "#ffd166"
    assert a["mute"] is False                      # Kästchen nicht angekreuzt heißt aus


def test_switching_it_off_is_one_checkbox(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Schonerbox 2")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "attract_source": "recent",
    }, follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["attract"]["enabled"] is False


def test_nonsense_falls_back_instead_of_breaking_the_screen(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Schonerbox 3")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "attract_enabled": "on",
        "attract_source": "disko", "attract_hint_style": "überall", "attract_fit": "quetschen",
        "attract_color": "knallrot", "attract_text_color": "", "attract_dim": "500",
    }, follow_redirects=True)
    a = ecosystem.device_profile(db.get_device_by_id(dev_id))["attract"]
    assert a["source"] == "recent" and a["hint_style"] == "bar" and a["fit"] == "auto"   # the default since 15.11.2026
    assert a["color"] == "#000000" and a["text_color"] == "#ffffff"
    assert a["dim"] == 80                          # auf das Erlaubte gestutzt, nicht abgelehnt


def test_the_two_lines_are_part_of_the_text_catalogue():
    """Titel und Hinweis gehören zu den Texten — sonst wären sie einsprachig."""
    from app import booth_texts
    assert "attract.title" in booth_texts.KEYS and "attract.hint" in booth_texts.KEYS
    assert booth_texts.shipped("en", "attract.title") == "Tap me"


def test_the_form_offers_every_source(client):
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Schonerbox 4")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    for value in ("recent", "media", "own", "text", "blank", "off"):
        assert f'value="{value}"' in body
    assert 'name="attract_dim"' in body and 'name="attract_color"' in body


def test_text_mode_can_sit_on_a_background(client):
    """„Tipp mich an" soll auch über einem Bild stehen dürfen, nicht nur auf einer Fläche."""
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Schonerbox 5")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "attract_enabled": "on",
        "attract_source": "text", "attract_media": ["wand.jpg"],
    }, follow_redirects=True)
    a = ecosystem.device_profile(db.get_device_by_id(dev_id))["attract"]
    assert a["source"] == "text" and a["media"] == ["wand.jpg"]


def test_the_booth_puts_the_text_over_the_media_when_there_are_any():
    """Ohne Medien eine Fläche, mit Medien die Schleife darunter — beides mit Text darüber."""
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'cfg.source === "text" && !(cfg.media || []).length' in js
    assert 'if (cfg.source === "own" || cfg.source === "text")' in js
    css = (ROOT / "booth" / "boothcore" / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert '.attract[data-plain="1"] .attract-dim{opacity:0}' in css
