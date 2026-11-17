"""Families 11–15: black and gold, balloons, business, Christmas, party.

The retro film look is wedding family 7 (familien_hochzeit2); the draft
film family below is kept out of the collection.

Same geometry and building blocks as the wedding families (``familien``);
see there for margins and the rules every template keeps.
"""
from __future__ import annotations

from functools import partial
from typing import Callable, Dict, List, Tuple

from . import kunst
from .bau import (SCHRIFT_KAPITAELCHEN, SCHRIFT_KAPITAELCHEN_LEICHT, SCHRIFT_SANS, SCHRIFT_SCRIPT,
                  SCHRIFT_SERIF, SCHRIFT_SERIF_KURSIV, Bau, Zeile, stapel, umriss)
from .familien import ART, GEOMETRIE, ZEILE, huelle

DISPLAY = "display"          # Archivo, shipped with the program


def _bau(fam: Dict, lid: str) -> Tuple[Bau, Dict, str]:
    g = GEOMETRIE[lid]
    b = Bau(f"{fam['id']}-{lid}", f"{fam['name']} · {g['name']}", g["canvas"], g["paper"], fam["grund"])
    return b, g, ART[lid]


def _je_streifen(b: Bau, g: Dict, macher: Callable[[int, Tuple[int, int, int, int]], List[Dict]]):
    """Strip sheets: the same block in both columns, ids kept apart (three texts per strip at most)."""
    for n, region in enumerate(g["text"]):
        b.text([dict(t, id=f"{t['id']}-{n}") for t in macher(n, region)])


def _links(texte: List[Dict], x: int) -> List[Dict]:
    return [dict(t, x=int(x), align="left") for t in texte]


# ════════════════════════════════════════════════════════════════════════
# 7 · Retro Film — dark film stock, perforations, warm amber
# ════════════════════════════════════════════════════════════════════════
FILM = {"id": "film", "nummer": 7, "name": "Retro Film", "kategorie": "Party & Retro",
        "grund": "#1b1a18", "tinte": "#f3ecdf", "leise": "#bdb4a5", "akzent": "#e39b3a", "loch": "#efe6d6"}


def _film_text(fam, region, gross=1.0, zeile=True) -> List[Dict]:
    x, y, w, h = region
    z = [Zeile("{event_name}", DISPLAY, int(84 * gross), fam["tinte"], luft=int(18 * gross))]
    z.append(Zeile("{date}", SCHRIFT_SANS, int(34 * gross), fam["akzent"], luft=int(10 * gross),
                   hoehe_wie="18.11.2026"))
    if zeile and ZEILE:
        z.append(Zeile(ZEILE, SCHRIFT_SANS, int(30 * gross), fam["leise"]))
    return stapel(z, x + w / 2, y, h, w * 0.9)


def film(lid: str) -> Dict:
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
        _je_streifen(b, g, lambda n, r: _film_text(fam, (r[0], r[1], r[2], r[3] - 60), 0.78, zeile=False)
                     + [])
    else:
        name = f"kol-film-perforation-{cw}.png"
        b.bild(name, 0, 22, cw, 44, partial(kunst.perforation, cw, 44, farbe=fam["loch"]), oben=False)
        b.bild(name, 0, ch - 66, cw, 44, partial(kunst.perforation, cw, 44, farbe=fam["loch"]), oben=False)
        x, y, w, h = g["text"][0]
        if art == "quer-1":
            b.text(_film_text(fam, (x, y - 4, w, h - 30), 0.86, zeile=False))
        elif art == "quer-3":
            b.text(_film_text(fam, (x, y, w, h - 30), 0.95))
        else:
            texte = _film_text(fam, (x, y, w, h - 120), 1.25)
            oben, unten = umriss(texte)
            b.linie(cw / 2 - 40, oben - 50, 80, fam["akzent"], 4)
            b.text(texte)
    return b.vorlage(familie="film")


# ════════════════════════════════════════════════════════════════════════
# 8 · Schwarz & Gold — a milestone birthday: a big number, a double frame
# ════════════════════════════════════════════════════════════════════════
GOLD = {"id": "gold", "nummer": 11, "name": "Schwarz & Gold", "kategorie": "Geburtstag",
        "grund": "#121212", "tinte": "#f4eee2", "leise": "#bfb6a6", "gold": "#c9a45c"}
ZAHL = "30"


def _gold_rahmen(b: Bau, fam, x, y, w, h):
    b.form_unten("rect", x, y, w, h, stroke=fam["gold"], stroke_width=3)
    b.form_unten("rect", x + 12, y + 12, w - 24, h - 24, stroke=fam["gold"], stroke_width=3)


def _gold_text(fam, region, gross=1.0, zahl=True) -> List[Dict]:
    x, y, w, h = region
    z = []
    if zahl:
        z.append(Zeile(ZAHL, SCHRIFT_SERIF, int(220 * gross), fam["gold"], luft=int(34 * gross), tid="zahl"))
    z.append(Zeile("{event_name}", SCHRIFT_KAPITAELCHEN_LEICHT, int(66 * gross), fam["tinte"], luft=int(14 * gross)))
    z.append(Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(36 * gross), fam["leise"], hoehe_wie="18.11.2026"))
    return stapel(z, x + w / 2, y, h, w * 0.86)


def gold(lid: str) -> Dict:
    fam = GOLD
    b, g, art = _bau(fam, lid)
    cw, ch = b.canvas
    if art == "streifen":
        for c in (0, 600):
            _gold_rahmen(b, fam, c + 26, 26, 548, ch - 52)
    else:
        _gold_rahmen(b, fam, 44, 44, cw - 88, ch - 88)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot)
    funken = lambda w_, h_, seed=8: partial(kunst.funken, w_, h_, seed=seed, anzahl=14)
    if art == "quer-1":
        x, y, w, h = g["text"][0]
        zahl = stapel([Zeile(ZAHL, SCHRIFT_SERIF, 150, fam["gold"], tid="zahl")], 0, y - 6, h, 400)
        b.text([dict(zahl[0], x=x + 10, align="left")])
        b.text(_gold_text(fam, (x + 300, y, w - 600, h), 0.9, zahl=False))
        b.bild("kol-gold-funken-300x150.png", x + w - 300, y + 20, 300, 150, funken(300, 150))
    elif art == "quer-3":
        x, y, w, h = g["text"][0]
        b.text(_gold_text(fam, (x, y + 4, w, h - 4), 0.72))
        b.bild("kol-gold-funken-260x260.png", x + 20, y + 30, 260, 260, funken(260, 260))
        b.bild("kol-gold-funken-260x260-b.png", x + w - 280, y + 30, 260, 260, funken(260, 260, 18))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        b.text(_gold_text(fam, (x, y, w, h - 40), 1.3 if lid == "hoch-1" else 1.25))
        b.bild("kol-gold-funken-300x300.png", x + 10, y + 40, 300, 300, funken(300, 300))
        b.bild("kol-gold-funken-300x300-b.png", x + w - 310, y + h - 360, 300, 300, funken(300, 300, 18))
    else:
        _je_streifen(b, g, lambda n, r: _gold_text(fam, (r[0], r[1], r[2], r[3] - 50), 0.72))
    return b.vorlage(familie="gold")


# ════════════════════════════════════════════════════════════════════════
# 9 · Luftballons & Konfetti — a light, friendly birthday
# ════════════════════════════════════════════════════════════════════════
BALLON = {"id": "ballon", "nummer": 12, "name": "Luftballons & Konfetti", "kategorie": "Geburtstag",
          "grund": "#fffaf4", "tinte": "#34324a", "leise": "#625e72", "koralle": "#d65f50"}


def _ballon_text(fam, region, gross=1.0, zeile=True) -> List[Dict]:
    x, y, w, h = region
    z = [Zeile("Happy Birthday", SCHRIFT_SCRIPT, int(120 * gross), fam["koralle"], luft=int(16 * gross))]
    z.append(Zeile("{event_name}", SCHRIFT_SERIF, int(62 * gross), fam["tinte"], luft=int(12 * gross)))
    if zeile:
        z.append(Zeile("{date}", SCHRIFT_SANS, int(32 * gross), fam["leise"], hoehe_wie="18.11.2026"))
    return stapel(z, x + w / 2, y, h, w * 0.84)


def ballon(lid: str) -> Dict:
    fam = BALLON
    b, g, art = _bau(fam, lid)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot, radius=16)
    ballons = lambda w_, h_, seed=9, n=3: partial(kunst.ballons, w_, h_, seed=seed, anzahl=n)
    konfetti = lambda w_, h_, seed=12: partial(kunst.konfetti, w_, h_, seed=seed, anzahl=18,
                                              farben=("#f4b6b0", "#f7d6a3", "#bcd7e8", "#d8c7ec", "#e88f84"))
    if art == "quer-1":
        x, y, w, h = g["text"][0]
        b.bild("kol-ballon-ballons-200x260.png", x + 30, y - 76, 200, 260, ballons(200, 260))
        b.bild("kol-ballon-ballons-200x260-b.png", x + w - 230, y - 76, 200, 260, ballons(200, 260, 19))
        b.text(_ballon_text(fam, (x + 250, y, w - 500, h), 0.72))
    elif art == "quer-3":
        x, y, w, h = g["text"][0]
        b.bild("kol-ballon-konfetti-1000x317.png", x, y, w, h, konfetti(1000, 317), oben=False)
        b.bild("kol-ballon-ballons-200x300.png", x + w - 210, y + 10, 200, 300, ballons(200, 300))
        b.text(_ballon_text(fam, (x + 60, y + 10, w - 300, h - 10), 0.72))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        b.bild("kol-ballon-konfetti-1008x400.png", x, y, w, 400, konfetti(1008, 400, 13), oben=False)
        z = 420 if h > 600 else 300
        b.bild(f"kol-ballon-ballons-200x{z}.png", x, y + h - z - 10, 200, z, ballons(200, z))
        b.bild(f"kol-ballon-ballons-200x{z}-b.png", x + w - 200, y + h - z - 10, 200, z, ballons(200, z, 19))
        b.text(_ballon_text(fam, (x + 200, y, w - 400, h - 120), 1.0 if h > 600 else 0.82))
    else:
        def block(n, r):
            x, y, w, h = r
            b.bild("kol-ballon-ballons-200x240.png", x + w / 2 - 100, y + h - 250, 200, 240, ballons(200, 240))
            return _ballon_text(fam, (x, y, w, h - 240), 0.66)
        _je_streifen(b, g, block)
    return b.vorlage(familie="ballon")


# ════════════════════════════════════════════════════════════════════════
# 10 · Business klar — neutral, left-aligned, one accent bar
# ════════════════════════════════════════════════════════════════════════
BUSINESS = {"id": "business", "nummer": 13, "name": "Business klar", "kategorie": "Firma",
            "grund": "#ffffff", "tinte": "#1b2430", "leise": "#55606e", "akzent": "#1f6f8b",
            "flaeche": "#f2f4f6"}


def _business_text(fam, region, gross=1.0, zeile=True) -> List[Dict]:
    x, y, w, h = region
    z = [Zeile("{event_name}", DISPLAY, int(72 * gross), fam["tinte"], luft=int(16 * gross), bold=True)]
    z.append(Zeile("{date}", SCHRIFT_SANS, int(34 * gross), fam["akzent"], luft=int(10 * gross),
                   hoehe_wie="18.11.2026", bold=True))
    if zeile and ZEILE:
        z.append(Zeile(ZEILE, SCHRIFT_SANS, int(32 * gross), fam["leise"]))
    return _links(stapel(z, x + w / 2, y, h, w * 0.96), x)


def business(lid: str) -> Dict:
    fam = BUSINESS
    b, g, art = _bau(fam, lid)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot)
    if art == "streifen":
        def block(n, r):
            x, y, w, h = r
            b.form_unten("rect", x, y + 30, w, h - 60, fill=fam["flaeche"])
            texte = _business_text(fam, (x + 40, y + 30, w - 60, h - 60), 0.7, zeile=False)
            oben, unten = umriss(texte)
            b.form_oben("rect", x + 20, oben, 8, unten - oben, fill=fam["akzent"])
            return texte
        _je_streifen(b, g, block)
    else:
        x, y, w, h = g["text"][0]
        gross = {"quer-1": 0.8, "quer-3": 0.95, "hoch-4": 1.15}[art]
        zeile = art != "quer-1"
        b.form_unten("rect", x, y + (0 if art != "quer-1" else 0), w, h, fill=fam["flaeche"])
        texte = _business_text(fam, (x + 64, y, w - 100, h), gross, zeile)
        oben, unten = umriss(texte)
        b.form_oben("rect", x + 32, oben, 8, unten - oben, fill=fam["akzent"])
        b.text(texte)
    return b.vorlage(familie="business")


# ════════════════════════════════════════════════════════════════════════
# 11 · Winterabend — night blue, cream mats, line snowflakes
# ════════════════════════════════════════════════════════════════════════
WINTER = {"id": "winter", "nummer": 14, "name": "Winterabend", "kategorie": "Weihnachten & Winter",
          "grund": "#1f2d47", "tinte": "#f6f1e6", "leise": "#cfd7e4", "matte": "#f6f1e6", "flocke": "#dfe6f0"}


def _winter_text(fam, region, gross=1.0, zeile=True) -> List[Dict]:
    x, y, w, h = region
    z = [Zeile("Frohe Weihnachten", SCHRIFT_SCRIPT, int(110 * gross), fam["tinte"], luft=int(18 * gross))]
    z.append(Zeile("{event_name}", SCHRIFT_KAPITAELCHEN_LEICHT, int(50 * gross), fam["tinte"],
                   luft=int(10 * gross)))
    if zeile:
        z.append(Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(34 * gross), fam["leise"], hoehe_wie="18.11.2026"))
    return stapel(z, x + w / 2, y, h, w * 0.82)


def winter(lid: str) -> Dict:
    fam = WINTER
    b, g, art = _bau(fam, lid)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot, matte=(fam["matte"], 12))
    flocken = lambda w_, h_, seed=11, n=5: partial(kunst.schneeflocken, w_, h_, farbe=fam["flocke"], seed=seed,
                                                    anzahl=n, strich=3)
    if art == "quer-1":
        x, y, w, h = g["text"][0]
        b.bild("kol-winter-flocken-260x184.png", x, y, 260, 184, flocken(260, 184, n=3), opacity=80)
        b.bild("kol-winter-flocken-260x184-b.png", x + w - 260, y, 260, 184, flocken(260, 184, 21, 3), opacity=80)
        b.text(_winter_text(fam, (x + 260, y, w - 520, h), 0.6))
    elif art == "quer-3":
        x, y, w, h = g["text"][0]
        for seite, sx in ((0, x), (1, x + w - 160)):
            b.bild(f"kol-winter-flocken-160x317-{seite}.png", sx, y, 160, h, flocken(160, 317, 51 + seite, 3),
                   oben=False, opacity=60)
        b.text(_winter_text(fam, (x + 80, y + 10, w - 160, h - 10), 0.74))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        for seite, sx in ((0, x), (1, x + w - 200)):
            b.bild(f"kol-winter-flocken-200x{h}-{seite}.png", sx, y, 200, h, flocken(200, h, 41 + seite, 4),
                   oben=False, opacity=60)
        b.text(_winter_text(fam, (x, y, w, h - 40), 1.05))
    else:
        def block(n, r):
            x, y, w, h = r
            b.bild("kol-winter-flocken-480x200.png", x, y + h - 210, w, 200, flocken(480, 200, n=4),
                   oben=False, opacity=60)
            return _winter_text(fam, (x, y, w, h - 210), 0.6)
        _je_streifen(b, g, block)
    return b.vorlage(familie="winter")


# ════════════════════════════════════════════════════════════════════════
# 12 · Bunte Party — deep indigo, a tilted colour block, confetti
# ════════════════════════════════════════════════════════════════════════
PARTY = {"id": "party", "nummer": 15, "name": "Bunte Party", "kategorie": "Party",
         "grund": "#232056", "tinte": "#ffffff", "gelb": "#ffd23f", "pink": "#ff4f79"}


def _party_text(fam, region, gross=1.0, zeile=True) -> List[Dict]:
    x, y, w, h = region
    z = [Zeile("{event_name}", DISPLAY, int(88 * gross), fam["tinte"], luft=int(14 * gross), bold=True)]
    # Indigo on pink: about 6:1 (yellow on pink was 1.6:1).
    z.append(Zeile("{date}", DISPLAY, int(38 * gross), fam["grund"], hoehe_wie="18.11.2026", bold=False))
    return stapel(z, x + w / 2, y, h, w * 0.8)


def party(lid: str) -> Dict:
    fam = PARTY
    b, g, art = _bau(fam, lid)
    cw, ch = b.canvas
    konfetti = lambda w_, h_, seed=12, n=30: partial(kunst.konfetti, w_, h_, seed=seed, anzahl=n,
                                                     farben=("#ff4f79", "#ffd23f", "#2ec4b6", "#7b6cff"))
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot, radius=14)
    if art == "streifen":
        def block(n, r):
            x, y, w, h = r
            b.form_unten("rect", x + 10, y + 40, w - 20, 230, fill=fam["pink"], radius=14, opacity=100)
            b.bild("kol-party-konfetti-480x150.png", x, y + h - 150, w, 140, konfetti(480, 140, n=10))
            return _party_text(fam, (x + 10, y + 40, w - 20, 230), 0.72)
        _je_streifen(b, g, block)
    else:
        x, y, w, h = g["text"][0]
        if art == "quer-1":
            block = (x + w / 2 - 520, y + 4, 1040, h - 8)
        elif art == "quer-3":
            block = (x + 70, y + 40, w - 140, h - 80)
        else:
            block = (x + 60, y + 90, w - 120, 300)
        bx, by, bw, bh = block
        # Confetti first, the colour block over it: nothing lands on the names.
        b.bild(f"kol-party-konfetti-{cw}x{ch}.png", 0, 0, cw, ch, konfetti(cw, ch, n=46), oben=False)
        b.form_unten("rect", bx, by, bw, bh, fill=fam["pink"], radius=18)
        b.unten[-1][1]["rotation"] = -2
        gross = {"quer-1": 0.8, "quer-3": 0.9, "hoch-4": 1.25}[art]
        b.text(_party_text(fam, block, gross))
    return b.vorlage(familie="party")


FAMILIEN_FEIER: List[Tuple[Dict, Callable[[str], Dict]]] = [
    (GOLD, gold), (BALLON, ballon), (BUSINESS, business), (WINTER, winter), (PARTY, party),
]
