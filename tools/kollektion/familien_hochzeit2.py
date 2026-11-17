"""Wedding families 7–10: film strip, night blue and gold, rosé watercolour, winter.

Same geometry, margins and rules as ``familien``: names and date only (the
optional line stays empty), every slot landscape, nothing important inside
the 4 mm the printer may cut.
"""
from __future__ import annotations

from functools import partial
from typing import Callable, Dict, List, Tuple

from . import kunst
from .bau import (SCHRIFT_KAPITAELCHEN, SCHRIFT_KAPITAELCHEN_LEICHT, SCHRIFT_SCHREIBMASCHINE,
                  SCHRIFT_SCRIPT_KLAR, SCHRIFT_SERIF_KURSIV, Bau, Zeile, stapel, umriss)
from .familien import ART, GEOMETRIE, huelle


def _bau(fam: Dict, lid: str) -> Tuple[Bau, Dict, str]:
    g = GEOMETRIE[lid]
    b = Bau(f"{fam['id']}-{lid}", f"{fam['name']} · {g['name']}", g["canvas"], g["paper"], fam["grund"])
    return b, g, ART[lid]


def _streifen(b: Bau, g: Dict, macher: Callable[[int, Tuple], List[Dict]]):
    for n, region in enumerate(g["text"]):
        b.text([dict(t, id=f"{t['id']}-{n}") for t in macher(n, region)])


def _block(fam: Dict, zeilen: Callable[[float], List[Zeile]], region, gross: float, breite: float = 0.86):
    x, y, w, h = region
    return stapel(zeilen(gross), x + w / 2, y, h, w * breite)


# ════════════════════════════════════════════════════════════════════════
# 7 · Filmstreifen — black film stock, perforations, typewriter type
# ════════════════════════════════════════════════════════════════════════
FILM = {"id": "filmstreifen", "nummer": 7, "name": "Filmstreifen", "kategorie": "Hochzeit",
        "grund": "#141414", "tinte": "#f1eee8", "leise": "#b3aea6", "loch": "#e9e5dd"}


def _film_zeilen(fam):
    return lambda g: [Zeile("{event_name}", SCHRIFT_SCHREIBMASCHINE, int(78 * g), fam["tinte"],
                            luft=int(30 * g)),
                      Zeile("{date}", SCHRIFT_SCHREIBMASCHINE, int(36 * g), fam["leise"], hoehe_wie="18.11.2026")]


def filmstreifen(lid: str) -> Dict:
    fam = FILM
    b, g, art = _bau(fam, lid)
    cw, ch = b.canvas
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot)
    if art == "streifen":
        for c in (0, 600):
            for x in (c + 12, c + 552):
                b.bild("kol-film-perforation-senkrecht.png", x, 0, 36, ch,
                       partial(kunst.perforation, 36, ch, farbe=fam["loch"], loch=26, abstand=48, senkrecht=True),
                       oben=False)
        _streifen(b, g, lambda n, r: _block(fam, _film_zeilen(fam), (r[0], r[1], r[2], r[3] - 40), 0.8))
        return b.vorlage(familie="filmstreifen")
    if cw > ch:
        # Landscape: the perforations run along the top and bottom edge.
        datei = f"kol-film-perforation-{cw}.png"
        for y in (22, ch - 66):
            b.bild(datei, 0, y, cw, 44, partial(kunst.perforation, cw, 44, farbe=fam["loch"]), oben=False)
    else:
        # Portrait: down both sides, like a strip held upright.
        for x in (18, cw - 58):
            b.bild("kol-film-perforation-senkrecht-hoch.png", x, 0, 40, ch,
                   partial(kunst.perforation, 40, ch, farbe=fam["loch"], loch=28, abstand=50, senkrecht=True),
                   oben=False)
    x, y, w, h = g["text"][0]
    gross = {"quer-1": 0.9, "quer-3": 1.0, "hoch-4": 1.2}[art]
    texte = _block(fam, _film_zeilen(fam), (x, y - (6 if art == "quer-1" else 0), w, h - 30), gross, 0.8)
    if art != "quer-1":
        oben, _ = umriss(texte)
        b.linie(x + w / 2 - 30, oben - 46, 60, fam["leise"], 3)
    b.text(texte)
    return b.vorlage(familie="filmstreifen")


# ════════════════════════════════════════════════════════════════════════
# 8 · Nachtblau & Gold — deep navy, fine gold rules, serif small caps
# ════════════════════════════════════════════════════════════════════════
# Dye-sub check: rules 4 px (0.34 mm), text at least 36 px; gold #d9b874 on
# #172338 has a contrast of about 7:1, the cream ink about 12:1.
NACHT = {"id": "nachtblau", "nummer": 8, "name": "Nachtblau & Gold", "kategorie": "Hochzeit",
         "grund": "#172338", "tinte": "#f4eee2", "leise": "#cfc6b4", "gold": "#d9b874"}


def _nacht_zeilen(fam):
    return lambda g: [Zeile("{event_name}", SCHRIFT_KAPITAELCHEN_LEICHT, int(90 * g), fam["tinte"],
                            luft=int(20 * g)),
                      Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(40 * g), fam["gold"], hoehe_wie="18.11.2026")]


def _nacht_rahmen(b: Bau, fam, x, y, w, h):
    b.form_unten("rect", x, y, w, h, stroke=fam["gold"], stroke_width=4)
    b.form_unten("rect", x + 14, y + 14, w - 28, h - 28, stroke=fam["gold"], stroke_width=4)


def _raute(b: Bau, fam, mx, my, r=12):
    b.form_oben("rect", mx - r, my - r, 2 * r, 2 * r, fill=fam["gold"])
    b.oben[-1][1]["rotation"] = 45


def nachtblau(lid: str) -> Dict:
    fam = NACHT
    b, g, art = _bau(fam, lid)
    cw, ch = b.canvas
    if art == "streifen":
        for c in (0, 600):
            _nacht_rahmen(b, fam, c + 28, 28, 544, ch - 56)
    else:
        _nacht_rahmen(b, fam, 46, 46, cw - 92, ch - 92)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot)
    if art == "streifen":
        def block(n, r):
            texte = _block(fam, _nacht_zeilen(fam), (r[0], r[1], r[2], r[3] - 60), 0.68)
            oben, unten = umriss(texte)
            _raute(b, fam, r[0] + r[2] / 2, oben - 50, 9)
            return texte
        _streifen(b, g, block)
        return b.vorlage(familie="nachtblau")
    x, y, w, h = g["text"][0]
    gross = {"quer-1": 0.8, "quer-3": 0.82, "hoch-4": 1.0}[art]
    texte = _block(fam, _nacht_zeilen(fam), (x, y, w, h), gross, 0.78)
    oben, unten = umriss(texte)
    mitte_y = (oben + unten) / 2
    if art == "quer-1":
        # Rules out to both sides of the block, a small diamond at their ends.
        links = x + w / 2 - 470
        b.linie(x + 70, mitte_y, links - x - 110, fam["gold"], 4)
        b.linie(x + w / 2 + 470 + 40, mitte_y, links - x - 110, fam["gold"], 4)
        _raute(b, fam, x + 56, mitte_y)
        _raute(b, fam, x + w - 56, mitte_y)
    else:
        b.linie(x + w / 2 - 90, oben - 44, 180, fam["gold"], 4)
        _raute(b, fam, x + w / 2, unten + 50, 10)
    b.text(texte)
    return b.vorlage(familie="nachtblau")


# ════════════════════════════════════════════════════════════════════════
# 9 · Rosé Aquarell — blush washes, a script with a plain ampersand
# ════════════════════════════════════════════════════════════════════════
ROSE = {"id": "rose", "nummer": 9, "name": "Rosé Aquarell", "kategorie": "Hochzeit",
        "grund": "#fdf8f5", "tinte": "#7c3f48", "leise": "#6e5559"}


def _rose_zeilen(fam):
    return lambda g: [Zeile("{event_name}", SCHRIFT_SCRIPT_KLAR, int(124 * g), fam["tinte"], luft=int(18 * g)),
                      Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(40 * g), fam["leise"], hoehe_wie="18.11.2026")]


def rose(lid: str) -> Dict:
    fam = ROSE
    b, g, art = _bau(fam, lid)
    cw, ch = b.canvas
    wash = lambda w_, h_, seed=21, n=4: partial(kunst.aquarell, w_, h_, seed=seed, flecken=n, staerke=70,
                                                farben=("#f1c3bd", "#ecb1ad", "#f6d6cc"))
    # One soft wash behind each name block — the colour sits where the eye
    # goes after the photos, instead of staining a corner.
    for n, (x, y, w, h) in enumerate(g["text"]):
        bw, bh = int(min(w, 900)), int(min(h + 140, 520))
        b.bild(f"kol-rose-wash-{bw}x{bh}.png", x + (w - bw) / 2, y + (h - bh) / 2, bw, bh,
               wash(bw, bh), oben=False)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot, matte=("#ffffff", 12))
    if art == "streifen":
        def block(n, r):
            return _block(fam, _rose_zeilen(fam), (r[0], r[1], r[2], r[3] - 80), 0.78)
        _streifen(b, g, block)
    else:
        x, y, w, h = g["text"][0]
        gross = {"quer-1": 0.86, "quer-3": 0.95, "hoch-4": 1.15}[art]
        b.text(_block(fam, _rose_zeilen(fam), (x, y, w, h), gross))
    return b.vorlage(familie="rose")


# ════════════════════════════════════════════════════════════════════════
# 10 · Winter — a November wedding: evergreen twigs, deep green, warm white
# ════════════════════════════════════════════════════════════════════════
WINTER = {"id": "winterhochzeit", "nummer": 10, "name": "Winter", "kategorie": "Hochzeit",
          "grund": "#f8f4ec", "tinte": "#1f3a2e", "leise": "#4c5f54", "linie": "#9db0a2"}


def _winter_zeilen(fam):
    return lambda g: [Zeile("{event_name}", SCHRIFT_SERIF_KURSIV, int(92 * g), fam["tinte"], luft=int(28 * g)),
                      Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(40 * g), fam["leise"], hoehe_wie="18.11.2026")]


def winterhochzeit(lid: str) -> Dict:
    fam = WINTER
    b, g, art = _bau(fam, lid)
    tanne = lambda w_, h_, sp=False, seed=31, ast=True: partial(kunst.tannenzweig, w_, h_, spiegeln=sp,
                                                                 seed=seed, ast=ast)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot)
    if art == "quer-1":
        x, y, w, h = g["text"][0]
        b.bild("kol-winter-tanne-300.png", x - 40, y - 60, 300, 300, tanne(300, 300))
        b.bild("kol-winter-tanne-300-sp.png", x + w - 260, y - 60, 300, 300, tanne(300, 300, True, 33))
        b.text(_block(fam, _winter_zeilen(fam), (x, y, w, h), 0.86))
    elif art == "quer-3":
        x, y, w, h = g["text"][0]
        b.bild("kol-winter-tanne-280.png", x - 30, y + h - 260, 280, 280, tanne(280, 280))
        b.bild("kol-winter-tanne-280-sp.png", x + w - 250, y + h - 260, 280, 280, tanne(280, 280, True, 33))
        b.text(_block(fam, _winter_zeilen(fam), (x + 160, y, w - 320, h), 0.92))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        texte = _block(fam, _winter_zeilen(fam), (x, y, w, h - 140), 1.12, 0.8)
        oben, unten = umriss(texte)
        b.bild("kol-winter-tanne-360.png", 600 - 360 + 20, unten + 20, 360, 240, tanne(360, 240, ast=False))
        b.bild("kol-winter-tanne-360-sp.png", 600 - 20, unten + 20, 360, 240, tanne(360, 240, True, 33, False))
        b.linie(600 - 40, oben - 46, 80, fam["linie"], 3)
        b.text(texte)
    else:
        def block(n, r):
            x, y, w, h = r
            texte = _block(fam, _winter_zeilen(fam), (x, y, w, h - 190), 0.78)
            _, unten = umriss(texte)
            b.bild("kol-winter-tanne-200x140.png", x + w / 2 - 205, unten + 30, 200, 140,
                   tanne(200, 140, ast=False))
            b.bild("kol-winter-tanne-200x140-sp.png", x + w / 2 + 5, unten + 30, 200, 140,
                   tanne(200, 140, True, 33, False))
            return texte
        _streifen(b, g, block)
    return b.vorlage(familie="winterhochzeit")


FAMILIEN_HOCHZEIT2: List[Tuple[Dict, Callable[[str], Dict]]] = [
    (FILM, filmstreifen), (NACHT, nachtblau), (ROSE, rose), (WINTER, winterhochzeit),
]
