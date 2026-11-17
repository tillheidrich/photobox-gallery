"""Kiosk-Dateien: Töne und Bildschirmschoner sind verdrahtet und übersetzt.

Die Oberfläche hat keinen Build-Schritt und keine JS-Tests — diese Prüfungen
halten wenigstens fest, dass die Teile vorhanden und in beiden Sprachen benannt
sind, damit ein Umbenennen im Profil nicht unbemerkt ins Leere läuft.
"""
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


@pytest.fixture(scope="module")
def files():
    return {
        "index": (WEB / "index.html").read_text(encoding="utf-8"),
        "app": (WEB / "static" / "app.js").read_text(encoding="utf-8"),
        "css": (WEB / "static" / "app.css").read_text(encoding="utf-8"),
        "i18n": (WEB / "static" / "i18n.js").read_text(encoding="utf-8"),
    }


@pytest.mark.parametrize("event", ["countdown", "shutter", "done", "print"])
def test_sound_event_is_played(event, files):
    assert f'playSound("{event}"' in files["app"]


def test_sounds_come_from_the_profile_assets(files):
    app = files["app"]
    assert "S.profile.sounds" in app
    assert '"/assets/" + encodeURIComponent(cfg[key])' in app
    assert "cloneNode" in app                      # überlappende Töne
    assert "soundCfg().enabled === false" in app   # abschaltbar
    assert "unlockSounds" in app                   # erste Berührung schaltet Ton frei


def test_attract_loop_is_wired(files):
    app, index, css = files["app"], files["index"], files["css"]
    assert "S.profile.attract" in app
    assert "/api/gallery?offset=0" in app          # letzte Aufnahmen als Quelle
    assert "dismissAttract" in app                 # jede Berührung beendet ihn
    assert 'id="attract"' in index and 'id="attractA"' in index and 'id="attractB"' in index
    assert ".attract-layer" in css


def test_attract_hint_exists_in_every_language(files):
    assert files["i18n"].count('"attract.hint"') == 2
    assert 'data-t="attract.hint"' in files["index"]


# ── Hochformat und Spiegelbox (20.09.2026) ────────────────────────────
# Gemessen wurde im Browser bei 1080×1920 und 1920×1080: Vorher ragte die
# Look-Auswahl auf dem Ergebnisschirm oben UND unten aus dem Bild, weil sie
# zentriert war und keinen Deckel hatte. Bei 1920×1080 — dem häufigsten
# Boxformat — waren „Fertig" und „Löschen" damit nicht erreichbar. Aufgefallen
# ist es erst beim Nachmessen fürs Hochformat; im Querformat war es genauso
# kaputt, nur hat nie jemand nachgesehen.

def test_the_look_chooser_can_never_push_the_buttons_off_screen():
    css = (WEB / "static" / "app.css").read_text()
    seite = css[css.index(".result-side{"):css.index("}", css.index(".result-side{"))]
    assert "max-height:100%" in seite, "Die Spalte darf nicht höher werden als der Schirm"
    assert "overflow:hidden" in seite

    looks = css[css.index(".looks{"):css.index("}", css.index(".looks{"))]
    # Since 11.11.2026 one row that scrolls sideways (test_look_strip.py).
    assert "overflow-x:auto" in looks, "Die Auswahl muss rollen statt herauszuragen"
    assert "min-height:0" in looks, "Ohne das schrumpft ein Grid-Kind im Flex nicht"


def test_what_matters_stays_put_while_the_looks_scroll():
    """Drucken, Löschen und Fertig dürfen nie das sein, was verschwindet."""
    css = (WEB / "static" / "app.css").read_text()
    for wahl in (".result-actions{", ".print{"):
        block = css[css.index(wahl):css.index("}", css.index(wahl))]
        assert "flex-shrink:0" in block, f"{wahl} kann zusammengedrückt werden"


# Derselbe Fehler, zweiter Fundort (20.09.2026): Mit zehn Collagen und zwölf
# Hintergründen wurde die Auswahlspalte des Vorbereitungsschirms bei 1920×1080
# 1983 px hoch — fast das Doppelte des Bildes. „Los geht's" lag außerhalb.
# Im Hochformat fiel es nicht auf, weil dort Platz ist; gemeldet hätte es
# niemand, weil der Gast nur denkt, die Box sei kaputt.

def test_the_setup_column_can_never_outgrow_the_screen():
    css = (WEB / "static" / "app.css").read_text()
    setup = css[css.index(".setup{"):css.index("}", css.index(".setup{"))]
    assert "grid-template-rows:minmax(0,1fr)" in setup, \
        "Eine auto-Zeile wächst mit dem Inhalt über das Raster hinaus"

    spalte = css[css.index(".setup-options{"):css.index("}", css.index(".setup-options{"))]
    assert "min-height:0" in spalte

    rollen = css[css.index(".setup-scroll{"):css.index("}", css.index(".setup-scroll{"))]
    assert "overflow-y:auto" in rollen, "Die Auswahl muss rollen statt herauszuragen"
    assert "min-height:0" in rollen
    assert "justify-content:safe center" in rollen, \
        "Zentriert ohne safe schneidet den Anfang ab, sobald es überläuft"

    los = css[css.index(".btn-go{"):css.index("}", css.index(".btn-go{"))]
    assert "flex-shrink:0" in los, "Der Startknopf darf nicht zusammengedrückt werden"


def test_the_setup_choices_sit_in_the_scrolling_box_and_the_button_does_not():
    """Sonst rollt „Los geht's" mit weg — genau der Fehler, der behoben wurde."""
    html = (WEB / "index.html").read_text()
    teil = html[html.index('<div class="setup-options">'):html.index("</section>", html.index('<div class="setup-options">'))]
    rollbereich = teil[teil.index('<div class="setup-scroll">'):teil.index("</div>\n        <button")]
    for kennung in ("groupFormat", "groupTemplate", "groupBackground", "groupChroma", "disclaimer"):
        assert kennung in rollbereich, f"{kennung} liegt nicht im rollenden Bereich"
    assert "btn-go" not in rollbereich, "Der Startknopf gehört außerhalb"
