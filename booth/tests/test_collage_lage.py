"""Hochkant montierte Kamera, quer entworfene Vorlage — und trotzdem ein Abzug.

Der Fall aus der Praxis: Dem Kunden fällt am Vorabend ein, dass alle Fotos
hochkant sein müssen. Die Vorlagen sind abgenommen und haben liegende
Bildfelder. Bis zum 20.09.2026 hat die Box das stumm gelöst — ``ImageOps.fit``
schneidet mittig zu, bei 3:2 bleiben 44 % übrig, und zwar die mittleren:
Köpfe ab, Füße ab. Niemand erfährt es, bis der Abzug herauskommt.

Gemessen wird hier an einem Foto mit Kennfarben oben und unten. Ein Test, der
nur die Bildgröße prüft, würde das alte Verhalten für in Ordnung halten.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boothcore.media import compose                        # noqa: E402

KOPF = (0, 0, 255)
FUSS = (0, 255, 0)


def _person(breite: int = 400, hoehe: int = 600) -> Image.Image:
    """Ein Foto mit Kopf oben und Füßen unten — daran sieht man, was fehlt."""
    bild = Image.new("RGB", (breite, hoehe), (225, 225, 225))
    d = ImageDraw.Draw(bild)
    rand = max(1, min(breite, hoehe) // 6)
    d.rectangle((0, 0, breite, rand), fill=KOPF)
    d.rectangle((0, hoehe - rand, breite, hoehe), fill=FUSS)
    return bild


def _enthaelt(bild: Image.Image, farbe) -> bool:
    return any(abs(a - b) < 40 for px in [bild.getcolors(1 << 20) or []] for _, c in px
               for a, b in [(0, 0)]) if False else any(
        all(abs(c[i] - farbe[i]) < 60 for i in range(3)) for _, c in (bild.getcolors(1 << 20) or []))


# ── der eigentliche Fall ──────────────────────────────────────────────

def test_beschneiden_frisst_die_koepfe_das_ist_das_alte_verhalten():
    """Festgehalten, damit klar ist, wogegen „auto“ antritt."""
    aus = compose.passt_ins_feld(_person(), 600, 400, "beschneiden", "#ffffff")
    assert aus.size == (600, 400)
    assert not _enthaelt(aus, FUSS), "Der Zuschnitt lässt die Füße stehen — Test misst nichts"


def test_auto_zeigt_bei_falscher_lage_das_ganze_foto():
    aus = compose.passt_ins_feld(_person(), 600, 400, "auto", "#ffffff")
    assert aus.size == (600, 400)
    assert _enthaelt(aus, KOPF) and _enthaelt(aus, FUSS), \
        "Hochformat in liegendem Feld verliert immer noch Bildteile"


def test_auto_beschneidet_weiter_wenn_die_lagen_passen():
    """Ein stehendes Foto in einem stehenden Feld soll formatfüllend bleiben."""
    aus = compose.passt_ins_feld(_person(), 400, 500, "auto", "#ff00ff")
    assert aus.size == (400, 500)
    assert not _enthaelt(aus, (255, 0, 255)), "Da ist ein Rand, wo keiner hingehört"


def test_einpassen_laesst_nie_etwas_weg():
    for foto, feld in [(_person(400, 600), (600, 400)), (_person(600, 400), (400, 600)),
                       (_person(500, 500), (300, 900))]:
        aus = compose.passt_ins_feld(foto, *feld, "einpassen", "#ffffff")
        assert aus.size == feld
        assert _enthaelt(aus, KOPF) and _enthaelt(aus, FUSS)


def test_beim_beschneiden_ueber_kreuz_wandert_der_ausschnitt_nach_oben():
    """Menschen stehen im oberen Drittel. Die Mitte trifft den Bauch."""
    mitte = compose.crop_to_aspect(_person(), "600:400", 0.5, 0.5)
    oben = compose.passt_ins_feld(_person(), 600, 400, "beschneiden", "#ffffff")
    assert not _enthaelt(mitte, KOPF), "Der Vergleich taugt nichts, wenn die Mitte den Kopf trifft"
    assert _enthaelt(oben, KOPF), "Der Ausschnitt sitzt immer noch mittig"


def test_verzerrt_wird_nie():
    """Lieber ein Rand als ein gestauchter Mensch."""
    quadrat = Image.new("RGB", (500, 500))
    d = ImageDraw.Draw(quadrat)
    d.ellipse((100, 100, 400, 400), fill=(255, 0, 0))
    aus = compose.passt_ins_feld(quadrat, 900, 300, "einpassen", "#ffffff")
    # Ein Kreis bleibt ein Kreis: Höhe und Breite des roten Bereichs gleich.
    rot = aus.convert("RGB").point(lambda v: v).getbbox()
    treffer = [(x, y) for x in range(0, 900, 3) for y in range(0, 300, 3)
               if aus.getpixel((x, y))[0] > 180 and aus.getpixel((x, y))[1] < 80]
    assert treffer
    breite = max(x for x, _ in treffer) - min(x for x, _ in treffer)
    hoehe = max(y for _, y in treffer) - min(y for _, y in treffer)
    assert abs(breite - hoehe) <= 6, f"Der Kreis ist ein Ei geworden ({breite}x{hoehe})"


# ── durch die ganze Vorlage ───────────────────────────────────────────

def _vorlage(feld):
    w, h = feld
    return {"id": "test", "name": "Test", "paper": "10x15", "canvas": [1200, 1800], "shots": 1,
            "background": "@hintergrund",
            "slots": [{"x": 60, "y": 60, "w": w, "h": h, "shot": 0, "radius": 0}],
            "texts": []}


def test_die_vorlage_reicht_den_modus_durch():
    hoch = _person(800, 1200)
    quer_feld = _vorlage((1080, 720))
    alt = compose.render_template(quer_feld, [hoch], einpassen="beschneiden")
    neu = compose.render_template(quer_feld, [hoch], einpassen="auto")
    ausschnitt_alt = alt.crop((60, 60, 1140, 780))
    ausschnitt_neu = neu.crop((60, 60, 1140, 780))
    assert not _enthaelt(ausschnitt_alt, FUSS)
    assert _enthaelt(ausschnitt_neu, KOPF) and _enthaelt(ausschnitt_neu, FUSS)


def test_ein_einzelnes_bildfeld_darf_widersprechen():
    """Ein Feld, das als Anschnitt gedacht ist, bleibt Anschnitt."""
    vorlage = _vorlage((1080, 720))
    vorlage["slots"][0]["fit"] = "beschneiden"
    aus = compose.render_template(vorlage, [_person(800, 1200)], einpassen="auto")
    assert not _enthaelt(aus.crop((60, 60, 1140, 780)), FUSS)


def test_unbekannter_modus_faellt_auf_auto_zurueck():
    aus = compose.passt_ins_feld(_person(), 600, 400, "irgendwas", "#ffffff")
    assert _enthaelt(aus, KOPF) and _enthaelt(aus, FUSS)


# ── Profil ────────────────────────────────────────────────────────────

def test_der_schalter_steht_in_beiden_profilfassungen():
    wurzel = Path(__file__).resolve().parents[2]
    for datei in (wurzel / "booth" / "boothcore" / "config.py", wurzel / "app" / "ecosystem.py"):
        text = datei.read_text(encoding="utf-8")
        assert '"collage_fit": "auto"' in text, f"{datei.name} kennt den Schalter nicht"


def test_die_box_reicht_den_schalter_an_die_vorlage_weiter():
    quelle = (Path(__file__).resolve().parents[1] / "boothcore" / "app.py").read_text(encoding="utf-8")
    # Three places render a guest's sheet, the fourth a template test print
    # (since 12.11.2026) — it must fit photos the way the evening will — and
    # the fifth a boomerang's sheet (boomerang.print, since 15.11.2026).
    assert quelle.count("einpassen=self.profile.get(\"collage_fit\", \"auto\")") == 5, \
        "Eine der fünf Renderstellen bekommt den Schalter nicht"
