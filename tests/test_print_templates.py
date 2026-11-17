"""Der Vorlagenkatalog: was gedruckt wird, und wie es aussieht.

Gebaut nach dem, was im Betrieb tatsächlich aufs Papier kommt: die Postkarte
10×15, dieselbe quer, der klassische 2×6-Zoll-Streifen (doppelt auf ein Blatt,
einmal geschnitten) und das Quadrat. Eine Vorlage beschreibt die Anordnung,
der Stil das Aussehen — deshalb wird aus demselben Raster die Hochzeits- oder
die Firmenkarte, ohne eine zweite Vorlage.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL / "booth"))

from app import ecosystem                      # noqa: E402
from boothcore.media import compose            # noqa: E402


# ── Katalog ───────────────────────────────────────────────

def test_the_catalogue_covers_one_to_six_photos():
    """Wer vier Bilder machen will, soll nicht bei drei aufhören müssen."""
    anzahlen = {compose.template_info(t)["shots"] for t in compose.BUILTIN_TEMPLATES}
    assert {1, 2, 3, 4, 5, 6} <= anzahlen


def test_every_common_paper_format_has_a_template():
    papiere = {compose.template_info(t)["paper"] for t in compose.BUILTIN_TEMPLATES}
    assert {"10x15", "10x15-quer", "streifen", "quadrat", "13x18-quer"} <= papiere


def test_landscape_is_not_an_afterthought():
    """Eine Gruppe steht nebeneinander, nicht übereinander.

    Wer eine Collage quer will, darf nicht auf eine Anordnung beschränkt sein,
    nur weil hochkant zuerst gebaut wurde.
    """
    quer = [compose.template_info(t) for t in compose.BUILTIN_TEMPLATES
            if t["canvas"][0] > t["canvas"][1]]
    assert {v["shots"] for v in quer} >= {1, 2, 3, 4, 5, 6}, \
        "Für jede Bildanzahl muss es auch eine quere Anordnung geben"
    zweier = [v for v in quer if v["shots"] == 2]
    assert len(zweier) >= 2, "Nebeneinander und übereinander sind zwei verschiedene Bilder"


def test_the_bigger_sheet_is_used_where_it_helps():
    """Ein Panorama aus drei Bildern wird auf 10×15 zum Briefmarkenband."""
    gross = [t for t in compose.BUILTIN_TEMPLATES if t["paper"] == "13x18-quer"]
    assert gross, "Für Gruppen gibt es das größere Blatt"
    for t in gross:
        assert t["canvas"] == [2126, 1535]


def test_the_classic_strip_is_printed_twice_on_one_sheet():
    """2×6 Zoll gibt es als Papier fast nie — gedruckt wird 10×15 und geschnitten."""
    for tid, fotos in (("strips3", 3), ("streifen4", 4)):
        t = next(x for x in compose.BUILTIN_TEMPLATES if x["id"] == tid)
        assert t["canvas"] == [1200, 1800]
        assert len(t["slots"]) == 2 * fotos, "beide Streifen zeigen dieselben Aufnahmen"
        assert compose.template_info(t)["shots"] == fotos


def test_every_template_says_how_many_photos_it_needs():
    for t in compose.BUILTIN_TEMPLATES:
        info = compose.template_info(t)
        assert info["shots"] >= 1
        # Die Box zählt danach ab: mehr Slots als Aufnahmen ist erlaubt
        # (Streifen), weniger wäre ein Fehler in der Vorlage.
        benutzte = {int(s.get("shot", i)) for i, s in enumerate(t["slots"])}
        assert max(benutzte) + 1 == info["shots"]


def test_nothing_sticks_out_of_the_sheet():
    for t in compose.BUILTIN_TEMPLATES:
        breite, hoehe = t["canvas"]
        for s in t["slots"]:
            assert s["x"] >= 0 and s["y"] >= 0
            assert s["x"] + s["w"] <= breite and s["y"] + s["h"] <= hoehe, t["id"]
        for text in t.get("texts", []):
            assert 0 <= text["y"] <= hoehe - 40, t["id"]


def test_both_sides_know_the_same_catalogue():
    assert [c["id"] for c in ecosystem.BUILTIN_COLLAGES] == [t["id"] for t in compose.BUILTIN_TEMPLATES]
    for eintrag in ecosystem.BUILTIN_COLLAGES:
        info = compose.template_info(next(t for t in compose.BUILTIN_TEMPLATES if t["id"] == eintrag["id"]))
        assert (eintrag["shots"], eintrag["orientation"], eintrag["paper"]) == \
               (info["shots"], info["orientation"], info["paper"])


def test_the_admin_preview_file_is_current():
    """Die Verwaltung zeichnet aus dieser Datei. Läuft der Test rot:
    ``python tools/build_collage_catalog.py``."""
    ziel = WURZEL / "app" / "static" / "data" / "collage_templates.json"
    erzeugt = subprocess.run([sys.executable, str(WURZEL / "tools" / "build_collage_catalog.py")],
                             capture_output=True, text=True)
    assert erzeugt.returncode == 0, erzeugt.stderr
    daten = json.loads(ziel.read_text(encoding="utf-8"))
    assert [t["id"] for t in daten["templates"]] == [t["id"] for t in compose.BUILTIN_TEMPLATES]
    assert set(daten["presets"]) == set(compose.STYLE_PRESETS)


# ── Stil ──────────────────────────────────────────────────

def test_the_occasion_is_a_style_not_a_second_catalogue():
    assert {"clean", "hell", "geburtstag", "hochzeit", "feier", "firma"} == set(compose.STYLE_PRESETS)
    for name in compose.STYLE_PRESETS:
        st = compose.style({"preset": name})
        assert st["background"].startswith("#") and st["ink"].startswith("#")


def test_own_values_beat_the_occasion():
    st = compose.style({"preset": "hochzeit", "accent": "#123456", "radius": 40})
    assert st["accent"] == "#123456" and st["radius"] == 40
    assert st["font"] == "serif", "alles andere bleibt beim Anlass"


def test_nonsense_in_the_style_does_not_reach_the_print():
    st = compose.style({"preset": "gibtsnicht", "radius": "viele"})
    assert st["preset"] == "clean" and isinstance(st["radius"], int)


def test_both_sides_know_the_same_occasions():
    assert compose.STYLE_PRESETS == ecosystem.COLLAGE_STYLE_PRESETS
    assert tuple(compose.STYLE_KEYS) == tuple(ecosystem.COLLAGE_STYLE_KEYS)


def test_the_form_keeps_only_what_makes_sense():
    sauber = ecosystem.clean_collage_style({"preset": "firma", "accent": "nicht-hex", "font": "comic",
                                            "headline": "Frohes Fest", "radius": "999"})
    assert sauber["preset"] == "firma" and sauber["accent"] == "" and sauber["font"] == ""
    assert sauber["headline"] == "Frohes Fest" and sauber["radius"] == "80"


# ── Gerendert ─────────────────────────────────────────────

def _shots(n=6):
    from PIL import Image
    return [Image.new("RGB", (1200, 900), (40 + i * 30, 90, 140)) for i in range(n)]


@pytest.mark.parametrize("preset", sorted(compose.STYLE_PRESETS))
def test_every_occasion_renders_every_template(preset):
    shots = _shots()
    for t in compose.BUILTIN_TEMPLATES:
        img = compose.render_template(t, shots, texts={"event_name": "Mia & Tom", "date": "20.09.2026"},
                                      qr_url="https://x.test/p/abc", style_cfg={"preset": preset})
        assert img.size == tuple(t["canvas"])


def test_the_style_actually_changes_the_sheet():
    shots = _shots(4)
    t = next(x for x in compose.BUILTIN_TEMPLATES if x["id"] == "grid4")
    hell = compose.render_template(t, shots, style_cfg={"preset": "hell"}, texts={"event_name": "X", "date": "1."})
    dunkel = compose.render_template(t, shots, style_cfg={"preset": "clean"}, texts={"event_name": "X", "date": "1."})
    assert hell.getpixel((5, 5)) != dunkel.getpixel((5, 5))


def test_the_headline_of_the_occasion_reaches_the_print():
    """Beim Geburtstag steht „Happy Birthday" auf der Karte, ohne eigene Vorlage."""
    t = next(x for x in compose.BUILTIN_TEMPLATES if x["id"] == "solo")
    ohne = compose.render_template(t, _shots(1), style_cfg={"preset": "clean"},
                                   texts={"event_name": "Mia & Tom", "date": "20.09.2026"})
    mit = compose.render_template(t, _shots(1), style_cfg={"preset": "geburtstag"},
                                  texts={"event_name": "Mia & Tom", "date": "20.09.2026"})
    assert ohne.tobytes() != mit.tobytes()


def test_the_brand_fonts_travel_with_the_booth():
    """Ein Abzug darf nicht davon abhängen, was auf dem Windows-Rechner installiert ist."""
    assert (compose.FONT_DIR / "Inter-SemiBold.ttf").is_file()
    assert (compose.FONT_DIR / "Archivo-Bold.ttf").is_file()
    assert compose.load_font(40, True, "display").getname()[0].startswith("Archivo")
