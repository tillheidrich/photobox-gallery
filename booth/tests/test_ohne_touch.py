"""Die ganze Box mit zwei Knöpfen bedienen.

In vielen Gehäusen steckt kein Touchscreen, sondern ein Buzzer und vielleicht
ein zweiter Knopf. „Auslösen" allein reicht dann nicht: Auf dem
Vorbereitungsbildschirm gibt es ein Format, eine Vorlage, einen Hintergrund zu
wählen. Zwei Aufgaben schließen die Lücke — weiterschalten und auswählen.
"""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text()
CSS = (WEB / "static" / "app.css").read_text()


def test_there_is_a_way_to_move_and_to_choose():
    assert 'if (a === "next") return fokusWeiter(1);' in JS
    assert 'if (a === "prev") return fokusWeiter(-1);' in JS
    assert 'if (a === "select") {' in JS


def test_the_first_press_only_marks():
    """Sonst drückt ein Knopf etwas, das der Gast noch nicht gesehen hat."""
    assert "if (fokusIndex < 0) { fokusWeiter(0); return true; }" in JS


def test_only_what_is_really_on_the_screen_can_be_marked():
    """Ein Knopf hinter einem ausgeblendeten Kasten wäre eine Sackgasse."""
    assert 'el.closest("[hidden]")' in JS
    assert "r.width > 8 && r.height > 8" in JS


def test_the_mark_starts_fresh_on_every_screen():
    assert "fokusZuruecksetzen();          // jeder Bildschirm fängt bei null an" in JS


def test_the_mark_is_visible_from_two_metres():
    """Der Tastaturfokus ist für fünfzig Zentimeter gemacht, nicht für eine Box."""
    assert ".is-fokus{outline:6px solid var(--accent)" in CSS
    assert "prefers-reduced-motion" in CSS


def test_the_admin_offers_the_two_tasks():
    admin = (Path(__file__).resolve().parents[2] / "app" / "templates" / "admin"
             / "booth_profile.html").read_text()
    for wert in ("next", "prev", "select"):
        assert f'value="{wert}"' in admin, f"{wert} fehlt in der Auswahl"
    assert "Ganz ohne Touchscreen" in admin


def test_the_gallery_accepts_them():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.blueprints import admin_booth
    quelle = Path(admin_booth.__file__).read_text()
    assert '"next", "prev", "select"' in quelle


def test_one_button_still_works_without_any_of_this():
    """Wer nur einen Knopf hat, schaltet die Auswahlschritte ab — dann fragt die Box nie."""
    from boothcore.config import DEFAULT_PROFILE
    assert "guest_choice" in DEFAULT_PROFILE
    assert "start_mode" in DEFAULT_PROFILE["controls"]


# ── Funde der Durchsicht vom 19.09.2026 ───────────────────────────────

def test_the_buzzer_leaves_an_open_window_alone():
    """Wer seine Adresse tippt, soll nicht vom Buzzer nach Hause geschickt werden.

    ``triggerPrimary`` prüfte nur drei von sechs Fenstern; die Maus-Variante
    direkt daneben prüfte mit ``overlayOpen()`` alle.
    """
    js = (WEB / "static" / "app.js").read_text()
    stelle = js.index("function triggerPrimary(")
    block = js[stelle:stelle + 900]
    assert "overlayOpen()" in block, "Der Buzzer kennt nicht alle offenen Fenster"


def test_going_home_closes_every_window():
    js = (WEB / "static" / "app.js").read_text()
    stelle = js.index("function home(")
    block = js[stelle:js.index("function openSetup(", stelle)]
    for fenster in ("#qrOverlay", "#confirmOverlay", "#fullOverlay", "#mailOverlay", "#textOverlay"):
        assert fenster in block, f"{fenster} bleibt beim Zurückgehen stehen"


def test_the_back_key_closes_the_window_not_the_screen():
    js = (WEB / "static" / "app.js").read_text()
    stelle = js.index("back.includes(e.code) && overlayOpen()")
    assert "return zurueckTaste();" in js[stelle:stelle + 200]
    # one place for the back key (keys_back, and Backspace with arrow navigation)
    block = js[js.index("function zurueckTaste()"):][:700]
    assert "closeMail()" in block and "gastTextFertig(false)" in block


def test_two_taps_on_go_start_one_session():
    """Ohne Sperre legte ein Doppeltipp zwei Durchgänge an."""
    js = (WEB / "static" / "app.js").read_text()
    stelle = js.index('case "go":')
    block = js[stelle:stelle + 400]
    assert "lastTrigger" in block, "Der Touch-Weg hat keine Entprellung"


# ── Funde der Durchsicht vom 19.09.2026 ───────────────────────────────
HTML = (WEB / "index.html").read_text()


def test_every_window_is_one_and_carries_a_name():
    """Ein <div class="overlay"> ist fuer Hilfsmittel ein Kasten, kein Fenster."""
    import re
    fenster = re.findall(r'<div class="overlay[^"]*" id="(\w+)"([^>]*)>', HTML)
    assert len(fenster) >= 8, "Die Fenster werden nicht mehr gefunden"
    for name, rest in fenster:
        assert 'role="dialog"' in rest, f"#{name} ist kein Fenster"
        assert 'aria-modal="true"' in rest, f"#{name} laesst den Rest bedienbar"
        assert "aria-label" in rest, f"#{name} hat keinen Namen"


def test_the_focus_moves_into_the_window_and_comes_back():
    """Der Fokus blieb hinter dem Fenster stehen — bei allen zwölf Öffnungsstellen."""
    assert "MutationObserver" in JS, "Ohne eine Stelle driftet das beim nächsten Fenster wieder auseinander"
    stelle = JS.index("function fokusFuehrung()")
    block = JS[stelle:JS.index("function overlayOpen()", stelle)]
    assert 'attributeFilter: ["hidden"]' in block
    assert "ziel.focus(" in block, "Der Fokus wandert nicht hinein"
    assert "vorher.focus(" in block, "Der Fokus kommt nicht zurück"


def test_tab_stays_inside_the_open_window():
    assert 'aria-modal="true"' in HTML
    stelle = JS.index('if (e.key !== "Tab") return;')
    block = JS[stelle:stelle + 900]
    assert "letztes.focus()" in block and "erstes.focus()" in block
