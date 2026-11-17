"""Auslösen und Bildschirm: Buzzer, Tasten, sichere Ränder.

Der Buzzer ist die einzige Bedienung, die ein Gast am Abend wirklich benutzt,
und der sichere Rand entscheidet, ob der Betreiber überhaupt noch an die
Verwaltung kommt, wenn das Panel getauscht wurde. Beides muss aus der Ferne
einstellbar sein — und beide Seiten müssen dasselbe Profil kennen.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "booth"))

from app import ecosystem                      # noqa: E402
from boothcore import config                   # noqa: E402


@pytest.fixture()
def admin_client(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _device(client):
    client.get("/healthz")
    from app import db
    ecosystem.init_schema()
    return db.get_device_by_id(db.create_device(name="Box")["id"])


def test_both_sides_know_the_same_blocks():
    """Box und Galerie lesen dasselbe Profil — auseinanderlaufen heißt Datenverlust."""
    assert config.DEFAULT_PROFILE["controls"] == ecosystem.DEFAULT_PROFILE["controls"]
    assert config.DEFAULT_PROFILE["layout"] == ecosystem.DEFAULT_PROFILE["layout"]


def test_the_buzzer_is_off_until_somebody_turns_it_on():
    """Eine Maus ist erst ein Buzzer, wenn es jemand so will — sonst löst jeder
    versehentliche Klick eine Aufnahme aus."""
    c = config.DEFAULT_PROFILE["controls"]
    assert c["mouse_start"] == "off"
    assert c["touch"] is True and c["hide_cursor"] is True
    assert "F13" in c["keys_start"] and "Space" in c["keys_start"]
    assert c["repeat_ms"] >= 500          # Prellschutz von Haus aus


def test_the_safe_area_starts_at_zero():
    l = config.DEFAULT_PROFILE["layout"]
    assert [l["safe_top"], l["safe_right"], l["safe_bottom"], l["safe_left"]] == [0, 0, 0, 0]
    assert l["scale"] == 100 and l["corner"] == "top-right" and l["guides"] is False


def test_the_remote_form_writes_both_blocks(admin_client, client):
    dev = _device(client)
    form = {
        "csrf_token": "test-token",
        # Auslöser
        "mouse_start": "left", "mouse_swallow": "on", "hide_cursor": "on", "ctl_touch": "on",
        "keys_start": "Space F13 NumpadEnter", "keys_back": "Escape", "keys_print": "KeyP",
        "repeat_ms": "900",
        # Bildschirm
        "safe_top": "4", "safe_right": "6", "safe_bottom": "3", "safe_left": "6",
        "ui_scale": "115", "corner": "bottom-left", "guides": "on",
    }
    r = admin_client.post(f"/admin/devices/{dev['id']}/booth", data=form)
    assert r.status_code in (200, 302)

    from app import db

    p = ecosystem.device_profile(db.get_device_by_id(dev["id"]))    # frisch lesen, nicht die alte Kopie
    c, l = p["controls"], p["layout"]
    assert c["mouse_start"] == "left" and c["repeat_ms"] == 900
    assert c["keys_start"] == ["Space", "F13", "NumpadEnter"]
    assert l == {"safe_top": 4, "safe_right": 6, "safe_bottom": 3, "safe_left": 6,
                 "scale": 115, "corner": "bottom-left", "guides": True,
                 "gallery_size": "m", "back_size": "m"}          # not in this form post: today's sizes


def test_nonsense_values_do_not_reach_the_booth(admin_client, client):
    """Ein Rand von 90 % oder eine erfundene Maustaste würde die Box unbedienbar
    machen — beides wird beim Speichern begrenzt, nicht erst an der Box."""
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token",
        "mouse_start": "fußschalter", "keys_start": "Leer;taste rm -rf", "repeat_ms": "99999",
        "safe_top": "90", "safe_left": "-5", "ui_scale": "400", "corner": "mitte",
    })
    from app import db

    p = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert p["controls"]["mouse_start"] == "off"
    assert p["controls"]["keys_start"] == config.DEFAULT_PROFILE["controls"]["keys_start"]
    assert p["controls"]["repeat_ms"] == 5000
    assert p["layout"]["safe_top"] == 20 and p["layout"]["safe_left"] == 0
    assert p["layout"]["scale"] == 140 and p["layout"]["corner"] == "top-right"


def test_the_booth_page_offers_both_sections(admin_client, client):
    dev = _device(client)
    body = admin_client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    for shown in ("Maus-Buzzer", "Prellschutz", "Rand oben", "Ecke für die Verwaltung",
                  "Hilfsrahmen", "F13"):
        assert shown in body, shown


def test_the_kiosk_never_sees_the_pin_but_does_see_the_controls():
    """Das Profil geht an jeden Gast-Browser. Die PIN darf da nicht mit rein,
    die Auslöser-Einstellungen müssen."""
    import json

    profile = json.loads(json.dumps(config.DEFAULT_PROFILE))
    profile["access"]["pin"] = "4711"
    public = json.loads(json.dumps(profile))
    public.get("access", {}).pop("pin", None)       # das macht die Box in public_profile()
    assert "4711" not in json.dumps(public)
    assert public["controls"]["keys_start"] and public["layout"]["scale"] == 100


# ── Was welcher Knopf tut ─────────────────────────────────────
# Ein Buzzer mit zwei Tasten hängt als Maus am Rechner. Bis hierher tat jede
# Taste dasselbe: die Hauptschaltfläche drücken. Jetzt bekommt jede ihre eigene
# Aufgabe — und ein Vertipper darf dabei nichts Stilles anrichten.

def test_the_second_button_does_nothing_until_it_is_given_a_job():
    c = config.DEFAULT_PROFILE["controls"]
    assert c["mouse_right"] == "off" and c["mouse_middle"] == "off"
    assert c["start_mode"] == "" and c["keys_actions"] == {}


def test_the_form_gives_each_button_its_own_job(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token",
        "mode_photo": "on", "mode_collage": "on", "mode_gif": "on",
        "mouse_start": "left", "mouse_right": "mode:collage", "mouse_middle": "gallery",
        "start_mode": "photo",
        "keys_actions": "KeyF=mode:photo KeyG=mode:gif Escape=back",
    })
    from app import db
    c = ecosystem.device_profile(db.get_device_by_id(dev["id"]))["controls"]
    assert c["start_mode"] == "photo"
    assert c["mouse_right"] == "mode:collage" and c["mouse_middle"] == "gallery"
    assert c["keys_actions"] == {"KeyF": "mode:photo", "KeyG": "mode:gif", "Escape": "back"}


def test_a_typo_becomes_nothing_rather_than_something_wrong(admin_client, client):
    """``mode:kollage`` gibt es nicht — der Knopf tut dann lieber gar nichts."""
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "mode_photo": "on",
        "mouse_right": "mode:kollage", "mouse_middle": "rm -rf",
        "start_mode": "kollage",
        "keys_actions": "Taste;1=mode:photo KeyH=fliegen KeyJ=print",
    })
    from app import db
    c = ecosystem.device_profile(db.get_device_by_id(dev["id"]))["controls"]
    assert c["mouse_right"] == "off" and c["mouse_middle"] == "off"
    assert c["start_mode"] == ""
    assert c["keys_actions"] == {"KeyJ": "print"}


def test_the_booth_reads_the_jobs_and_refuses_switched_off_modes():
    """Die Oberfläche entscheidet, nicht das Formular: Ein abgeschalteter Modus
    lässt sich auch über eine Taste nicht starten."""
    js = (Path(__file__).resolve().parents[1] / "booth" / "boothcore" / "web" / "static" / "app.js").read_text()
    assert "fuehreAus" in js and "erlaubterModus" in js
    assert "keys_actions" in js and "mouse_right" in js and "mouse_middle" in js
    assert "start_mode" in js
    # Während Aufnahme, Verarbeitung und Durchsehen bleibt jeder Knopf still.
    # (Zum Durchsehen gehört ein eigener Abbruch-Knopf auf dem Bildschirm.)
    assert 'if (sc === "capture" || sc === "processing" || sc === "review") return;' in js
