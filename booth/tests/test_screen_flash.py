"""Der Bildschirm als Blitz.

Boxen ohne Blitzgerät — Webcam, altes Handy, Tablet — haben nur das Panel.
Damit das Licht etwas nützt, muss es stehen, *bevor* die Kamera belichtet, und
noch kurz danach. Geprüft wird die Reihenfolge, nicht das Aussehen: Ein Blitz,
der erst nach der Aufnahme angeht, ist ein Effekt und kein Licht.
"""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text()


def test_the_profile_carries_the_switch():
    from boothcore.config import DEFAULT_PROFILE
    sf = DEFAULT_PROFILE["screen_flash"]
    assert sf["enabled"] is False, "Niemand will ungefragt angestrahlt werden"
    assert sf["lead_ms"] > 0, "Ohne Vorlauf leuchtet es erst, wenn das Bild schon da ist"
    assert sf["hold_ms"] > 0
    assert 20 <= sf["brightness"] <= 100


def test_gallery_and_booth_ship_the_same_block():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import ecosystem
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["screen_flash"] == ecosystem.DEFAULT_PROFILE["screen_flash"]


def test_there_is_a_surface_to_light_with():
    html = (WEB / "index.html").read_text()
    css = (WEB / "static" / "app.css").read_text()
    assert 'id="screenFlash"' in html
    assert ".screen-flash{" in css and "inset:0" in css


def test_the_light_stands_before_the_shutter():
    vorher = JS.index("await schirmblitzHoch();")
    ausloesen = JS.index('api(`/api/session/${flow.uid}/shot`')
    danach = JS.index("await schirmblitzRunter(weissSeit);")
    assert vorher < ausloesen < danach, "Reihenfolge: Licht an, auslösen, Licht aus"


def test_a_failed_shot_does_not_leave_the_box_glaring():
    assert "} finally {" in JS and "await schirmblitzRunter(weissSeit);" in JS


def test_brightness_is_light_and_not_transparency():
    # Durchsichtig ließe die dunkle Oberfläche durchscheinen — es würde blasser
    # aussehen, aber nicht weniger leuchten.
    assert 'el.style.background = mix("#000000", hex(sf.color, "#ffffff"), anteil);' in JS


def test_the_old_shimmer_stays_out_of_the_way():
    assert "if (schirmblitzAn()) return;" in JS, \
        "Zwei Weiß übereinander sind eins zu viel"
