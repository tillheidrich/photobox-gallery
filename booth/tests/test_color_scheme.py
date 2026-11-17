"""Dunkel und hell.

Dunkel bleibt die Voreinstellung — im abgedunkelten Raum blendet eine helle
Fläche, und das Foto soll das Hellste im Bild sein. Bei Tageslicht, im
Schaufenster oder in einer hellen Markenwelt ist hell besser lesbar.

Geprüft wird beides: dass die Oberfläche wirklich der gewählten Farbe folgt
(und nicht an fest eingebauten Grautönen hängt) und dass das helle Schema
lesbar ist — mit gerechneten Kontrasten, nicht nach Gefühl.
"""
import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text()
CSS = (WEB / "static" / "app.css").read_text()


def _schema(name: str) -> dict:
    roh = re.search(rf"{name}:\s*\{{(.*?)\}},", JS, re.S).group(1)
    return dict(re.findall(r'(\w+):\s*"(#[0-9a-f]{6})"', roh))


def _leuchte(hexfarbe: str) -> float:
    teile = [int(hexfarbe[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    teile = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in teile]
    return 0.2126 * teile[0] + 0.7152 * teile[1] + 0.0722 * teile[2]


def _kontrast(a: str, b: str) -> float:
    hell, dunkel = sorted((_leuchte(a), _leuchte(b)), reverse=True)
    return (hell + 0.05) / (dunkel + 0.05)


def test_the_profile_offers_both():
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["theme"]["mode"] == "dunkel"


def test_both_schemes_are_readable():
    for name in ("dunkel", "hell"):
        s = _schema(name)
        assert _kontrast(s["text"], s["bg"]) >= 7, f"{name}: Fließtext auf dem Grund"
        assert _kontrast(s["text"], s["panel"]) >= 7, f"{name}: Text auf Kacheln"
        assert _kontrast(s["muted"], s["bg"]) >= 4.5, f"{name}: Nebentext"
        assert _kontrast(s["muted"], s["panel"]) >= 4.5, f"{name}: Nebentext auf Kacheln"
        # Knöpfe sind an der Box groß und fett; 4,5:1 ist trotzdem der Anspruch.
        assert _kontrast(s["on_accent"], s["accent"]) >= 4.5, f"{name}: Schrift auf der Hauptfarbe"
        assert _kontrast(s["accent"], s["bg"]) >= 3, f"{name}: Hauptfarbe gegen den Grund"


def test_the_colors_come_from_the_brand_source():
    """Farben werden in der Farbquelle geändert, nicht in der Oberfläche.

    Die Box trägt die Tabelle doppelt: einmal als Quelle im Erscheinungsbild,
    einmal ausgeschrieben in app.js, weil sie ohne Netz und ohne Galerie
    startet. Auseinanderlaufen dürfen die beiden nicht.
    """
    tokens = json.loads((WEB / "static" / "css" / "brand" / "tokens.json").read_text())
    for name in ("dunkel", "hell"):
        quelle = {rolle: wert["hex"] for rolle, wert in tokens["kiosk"][name].items()}
        assert _schema(name) == quelle, f"{name}: app.js und tokens.json sind verschieden"


def test_the_gallery_preview_shows_the_same_thing():
    """Wer im Admin auf „Hell" klickt, muss sehen, was die Box später zeigt."""
    admin = (Path(__file__).resolve().parents[2] / "app" / "templates" / "admin"
             / "booth_profile.html").read_text()
    for name in ("dunkel", "hell"):
        for wert in _schema(name).values():
            assert wert in admin, f"{wert} fehlt in der Vorschau des Admins"


def test_both_token_files_agree():
    a = json.loads((WEB / "static" / "css" / "brand" / "tokens.json").read_text())["kiosk"]
    b = json.loads((Path(__file__).resolve().parents[2] / "app" / "static" / "css"
                    / "brand" / "tokens.json").read_text())["kiosk"]
    assert a == b


def test_the_surface_follows_the_chosen_colors():
    """Fest eingebaute Grautöne würden im hellen Schema stehen bleiben."""
    for grau in ("#18181b", "#26262b", "#232329", "#3f3f46", "#09090b"):
        assert grau not in CSS, f"{grau} hängt nicht am Schema"
    for var in ("--gefahr", "--schatten", "--schatten-tief", "--gefahr-flaeche"):
        assert var in CSS and var in JS, f"{var} fehlt auf einer Seite"


def test_light_or_dark_is_read_off_the_color_not_a_flag():
    # Wer eigene Farben setzt, soll trotzdem die passenden Schatten bekommen.
    assert "function istHell(" in JS and "app.dataset.scheme" in JS


def test_a_chosen_scheme_beats_the_single_color_fields():
    assert "const vor = SCHEMATA[th.mode] || null;" in JS


def _mix(a: str, b: str, t: float) -> str:
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t):02x}" for i in (1, 3, 5))


def test_muted_text_stays_readable_on_a_pressed_chip():
    # app.js: --panel-2 = mix(panel, text, 0.12); a pressed chip sits on it.
    s = _schema("dunkel")
    assert _kontrast(s["muted"], _mix(s["panel"], s["text"], 0.12)) >= 4.5


def test_hint_on_the_accent_tile_reaches_4_5_in_both_schemes():
    css = re.search(r"\.mode:first-child span\s*\{([^}]*)\}", CSS)
    assert css and "opacity:1" in css.group(1).replace(" ", ""), "hint on the accent tile must be fully opaque"
    for name in ("dunkel", "hell"):
        s = _schema(name)
        assert _kontrast(s["on_accent"], s["accent"]) >= 4.5
