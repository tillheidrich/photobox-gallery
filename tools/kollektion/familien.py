"""The families of the collection: one design language each, several layouts.

Geometry is shared (``GEOMETRIE``), so photos sit at the same places in every
family and the gutters are the same; what makes a family is its colours,
type, photo treatment and ornament. Margins: 96 px (8 mm) around the photos
on postcards, 60 px (5 mm) on each side of a strip — both well outside the
3–4 mm a borderless printer may cut off.

Texts are the template's: ``{event_name}`` and ``{date}`` from the event,
a monogram and one optional line as plain default text the operator edits
in the editor.
"""
from __future__ import annotations

from functools import partial
from typing import Callable, Dict, List, Tuple

from . import kunst
from .bau import (HOCH, QUER, SCHRIFT_KAPITAELCHEN, SCHRIFT_KAPITAELCHEN_LEICHT, SCHRIFT_SANS,
                  SCHRIFT_SCRIPT, SCHRIFT_SERIF, SCHRIFT_SERIF_KURSIV, Bau, Zeile, stapel, umriss,
                  ET_SERIF, SCHRIFT_SCRIPT_KLAR, SCHRIFT_SCHREIBMASCHINE)

MONOGRAMM = "A & B"
# The optional third line is empty by default: the owner wants names and
# date only. Put a sentence here (or in the editor) to bring it back.
ZEILE = ""

Rechteck = Tuple[int, int, int, int]

#: Layout id -> (name, canvas, paper, photo rects with shot numbers, text regions)
GEOMETRIE: Dict[str, Dict] = {
    "quer-1": {"name": "quer · 1 Foto", "canvas": QUER, "paper": "10x15-quer",
               "fotos": [((96, 96, 1608, 800), 0)], "text": [(96, 920, 1608, 184)]},
    "quer-3": {"name": "quer · 3 Fotos", "canvas": QUER, "paper": "10x15-quer",
               "fotos": [((96, 96, 1000, 667), 0), ((1120, 96, 584, 492), 1), ((1120, 612, 584, 492), 2)],
               "text": [(96, 787, 1000, 317)]},
    "hoch-4": {"name": "hoch · 4 Fotos", "canvas": HOCH, "paper": "10x15",
               # Slots stay landscape (w > h): the camera shoots 3:2 landscape,
               # and a portrait slot would get the whole photo with bars.
               "fotos": [((96, 96, 492, 448), 0), ((612, 96, 492, 448), 1),
                         ((96, 568, 492, 448), 2), ((612, 568, 492, 448), 3)],
               "text": [(96, 1040, 1008, 664)]},
    "streifen-3": {"name": "Streifen · 3 Fotos", "canvas": HOCH, "paper": "streifen",
                   "fotos": [((c + 60, 72 + i * 380, 480, 360), i) for c in (0, 600) for i in range(3)],
                   "text": [(60, 1216, 480, 512), (660, 1216, 480, 512)]},
}

# The three further layouts reuse the design of their closest sibling:
# a 2x2 grid over a text band is laid out like the single landscape photo,
# the single portrait photo like the portrait grid, four strip photos like three.
GEOMETRIE["quer-4"] = {"name": "quer · 4 Fotos", "canvas": QUER, "paper": "10x15-quer",
                       "fotos": [((96 + c * 816, 96 + r * 412, 792, 388), r * 2 + c)
                                 for r in range(2) for c in range(2)],
                       "text": [(96, 920, 1608, 184)]}
GEOMETRIE["hoch-1"] = {"name": "hoch · 1 Foto", "canvas": HOCH, "paper": "10x15",
                       "fotos": [((96, 96, 1008, 840), 0)], "text": [(96, 960, 1008, 744)]}
GEOMETRIE["streifen-4"] = {"name": "Streifen · 4 Fotos", "canvas": HOCH, "paper": "streifen",
                           "fotos": [((c + 60, 72 + i * 316, 480, 300), i) for c in (0, 600) for i in range(4)],
                           "text": [(60, 1336, 480, 392), (660, 1336, 480, 392)]}

# Six pictures — a six-shot collage, or a GIF run printed as its stills:
# a 3×2 grid over the same text band as the single landscape photo, and a
# 2×3 grid over a shorter text field. Every slot stays landscape.
GEOMETRIE["quer-6"] = {"name": "quer · 6 Fotos", "canvas": QUER, "paper": "10x15-quer",
                       "fotos": [((96 + c * 544, 96 + r * 412, 520, 388), r * 3 + c)
                                 for r in range(2) for c in range(3)],
                       "text": [(96, 920, 1608, 184)]}
GEOMETRIE["hoch-6"] = {"name": "hoch · 6 Fotos", "canvas": HOCH, "paper": "10x15",
                       "fotos": [((96 + c * 516, 96 + r * 384, 492, 360), r * 2 + c)
                                 for r in range(3) for c in range(2)],
                       "text": [(96, 1248, 1008, 456)]}

ART = {"quer-1": "quer-1", "quer-4": "quer-1", "quer-6": "quer-1", "quer-3": "quer-3", "hoch-4": "hoch-4",
       "hoch-1": "hoch-4", "hoch-6": "hoch-4", "streifen-3": "streifen", "streifen-4": "streifen"}
WEDDING_LAYOUTS = ("quer-1", "hoch-1", "quer-3", "hoch-4", "quer-6", "hoch-6", "streifen-3")
ALLE_LAYOUTS = ("quer-1", "hoch-1", "quer-3", "quer-4", "hoch-4", "quer-6", "hoch-6", "streifen-3", "streifen-4")


def huelle(g: Dict) -> Rechteck:
    """The box around all photos of a layout."""
    xs = [f[0][0] for f in g["fotos"]] + [f[0][0] + f[0][2] for f in g["fotos"]]
    ys = [f[0][1] for f in g["fotos"]] + [f[0][1] + f[0][3] for f in g["fotos"]]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _bau(fam: Dict, lid: str) -> Tuple[Bau, Dict]:
    g = GEOMETRIE[lid]
    b = Bau(f"{fam['id']}-{lid}", f"{fam['name']} · {g['name']}", g["canvas"], g["paper"], fam["grund"])
    return b, g


# ════════════════════════════════════════════════════════════════════════
# 1 · Grün & Gold — watercolour greenery, gold lines
# ════════════════════════════════════════════════════════════════════════
GRUEN = {"id": "gruen", "nummer": 1, "name": "Grün & Gold", "kategorie": "Hochzeit",
         "grund": "#faf8f3", "tinte": "#2f4235", "leise": "#55665a", "gold": "#b08d4f"}


def _gruen_text(fam, region, *, mit_zeile=True, gross=1.0) -> List[Dict]:
    x, y, w, h = region
    zeilen = [Zeile("{event_name}", SCHRIFT_SCRIPT, int(118 * gross), fam["tinte"], luft=int(26 * gross), et=ET_SERIF)]
    zeilen.append(Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(40 * gross), fam["leise"],
                        luft=int(16 * gross), hoehe_wie="18.11.2026"))
    if mit_zeile and ZEILE:
        zeilen.append(Zeile(ZEILE, SCHRIFT_KAPITAELCHEN_LEICHT, int(34 * gross), fam["leise"]))
    return stapel(zeilen, x + w / 2, y, h, w * 0.86)


def gruen(lid: str) -> Dict:
    fam = GRUEN
    b, g = _bau(fam, lid)
    art = ART[lid]
    zweig_a = partial(kunst.zweig, seed=11, gold=fam["gold"])
    zweig_b = partial(kunst.zweig, seed=12, gold=fam["gold"], art="olive", spiegeln=True)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot)
    if art == "quer-1" and lid != "quer-1":
        # Grids: the sprigs stay in the text band, off the guests' photos.
        x, y, w, h = g["text"][0]
        b.bild("kol-gruen-zweig-a-240.png", x - 30, y - 40, 240, 240, partial(zweig_a, 240, 240))
        b.bild("kol-gruen-zweig-a-240-sp.png", x + w - 210, y - 40, 240, 240,
               partial(kunst.zweig, 240, 240, seed=11, gold=fam["gold"], spiegeln=True))
        b.text(_gruen_text(fam, g["text"][0], mit_zeile=False, gross=0.82))
    elif art == "quer-1":
        px, py, pw, ph = huelle(g)
        b.bild("kol-gruen-zweig-a-420.png", px - 70, py + ph - 330, 420, 420, partial(zweig_a, 420, 420))
        b.bild("kol-gruen-zweig-b-380.png", px + pw - 300, py - 70, 380, 380,
               partial(kunst.zweig, 380, 380, seed=12, gold=fam["gold"], art="olive", spiegeln=True),
               rotation=90)
        b.text(_gruen_text(fam, g["text"][0], mit_zeile=False, gross=0.82))
    elif art == "quer-3":
        (px, py, pw, ph), _ = g["fotos"][0]
        b.bild("kol-gruen-zweig-a-360.png", px - 60, py - 60, 360, 360, partial(zweig_a, 360, 360),
               rotation=90)
        # The second sprig crosses the far corner of the last photo — the
        # names below the big photo need the whole width of their block.
        b.bild("kol-gruen-zweig-b-300.png", 1800 - 36 - 300, 1104 - 230, 300, 300,
               partial(zweig_b, 300, 300))
        b.text(_gruen_text(fam, g["text"][0], gross=0.92))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        # The six-photo sheet leaves a shorter text field: smaller sprigs, smaller names.
        z = 360 if h > 600 else 270
        b.bild(f"kol-gruen-zweig-a-{z}.png", 40, y + h - z + 30, z, z, partial(zweig_a, z, z))
        b.bild(f"kol-gruen-zweig-b-{z}.png", 1200 - z - 40, y + h - z + 30, z, z,
               partial(kunst.zweig, z, z, seed=12, gold=fam["gold"], art="olive", spiegeln=True))
        texte = _gruen_text(fam, (x, y + 10, w, h - z + 130), gross=1.12 if h > 600 else 0.92)
        oben, _ = umriss(texte)
        b.linie(600 - 50, oben - 50, 100, fam["gold"], 3)
        b.text(texte)
    elif art == "streifen":
        for n, (x, y, w, h) in enumerate(g["text"]):
            b.bild("kol-gruen-zweig-a-210.png", x + w / 2 - 222, y + h - 230, 210, 210,
                   partial(zweig_a, 210, 210))
            b.bild("kol-gruen-zweig-a-210-sp.png", x + w / 2 + 12, y + h - 230, 210, 210,
                   partial(kunst.zweig, 210, 210, seed=11, gold=fam["gold"], spiegeln=True))
            b.text([dict(t, id=f"{t['id']}-{n}") for t in
                    _gruen_text(fam, (x, y, w, h - 200), gross=0.78)])
    return b.vorlage(familie="gruen")


# ════════════════════════════════════════════════════════════════════════
# 2 · Schrift — minimal modern, calligraphic names on white
# ════════════════════════════════════════════════════════════════════════
SCHRIFT = {"id": "schrift", "nummer": 2, "name": "Schrift auf Weiß", "kategorie": "Hochzeit",
           "grund": "#ffffff", "tinte": "#1d1d1b", "leise": "#62605c", "linie": "#bdb8b0"}


def _schrift_text(fam, region, *, mit_zeile=True, gross=1.0) -> List[Dict]:
    x, y, w, h = region
    zeilen = [Zeile("{event_name}", SCHRIFT_SCRIPT, int(132 * gross), fam["tinte"], luft=int(30 * gross), et=ET_SERIF)]
    zeilen.append(Zeile("{date}", SCHRIFT_SANS, int(32 * gross), fam["leise"], luft=int(14 * gross),
                        hoehe_wie="18.11.2026"))
    if mit_zeile and ZEILE:
        zeilen.append(Zeile(ZEILE, SCHRIFT_SANS, int(30 * gross), fam["leise"]))
    return stapel(zeilen, x + w / 2, y, h, w * 0.9)


def schrift(lid: str) -> Dict:
    fam = SCHRIFT
    b, g = _bau(fam, lid)
    art = ART[lid]
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot)
    if art == "quer-1":
        b.text(_schrift_text(fam, g["text"][0], mit_zeile=False, gross=0.78))
    elif art == "quer-3":
        x, y, w, h = g["text"][0]
        b.text(_schrift_text(fam, g["text"][0], gross=0.88))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        texte = _schrift_text(fam, (x, y, w, h), gross=1.3)
        oben, unten = umriss(texte)
        b.linie(600 - 40, oben - 70, 80, fam["linie"], 3)
        b.linie(600 - 40, unten + 70, 80, fam["linie"], 3)
        b.text(texte)
    elif art == "streifen":
        for n, (x, y, w, h) in enumerate(g["text"]):
            texte = _schrift_text(fam, (x, y, w, h), gross=0.85)
            oben, unten = umriss(texte)
            b.linie(x + w / 2 - 30, oben - 50, 60, fam["linie"], 3)
            b.linie(x + w / 2 - 30, unten + 50, 60, fam["linie"], 3)
            b.text([dict(t, id=f"{t['id']}-{n}") for t in texte])
    return b.vorlage(familie="schrift")


# ════════════════════════════════════════════════════════════════════════
# 3 · Editorial — black and white, small caps, a monogram
# ════════════════════════════════════════════════════════════════════════
EDITORIAL = {"id": "editorial", "nummer": 3, "name": "Editorial Schwarz-Weiß", "kategorie": "Hochzeit",
             "grund": "#ffffff", "tinte": "#141414", "leise": "#4d4d4d"}


def _monogramm(b: Bau, fam, mx, my, r, groesse, tid="mono"):
    b.form_oben("ellipse", mx - r, my - r, 2 * r, 2 * r, stroke=fam["tinte"], stroke_width=3)
    zeilen = stapel([Zeile(MONOGRAMM, SCHRIFT_KAPITAELCHEN_LEICHT, groesse, fam["tinte"], tid=tid)],
                    mx, my - r, 2 * r, 2 * r * 0.72)
    b.text(zeilen)


def _editorial_text(fam, region, *, mit_zeile=True, gross=1.0) -> List[Dict]:
    x, y, w, h = region
    zeilen = [Zeile("{event_name}", SCHRIFT_KAPITAELCHEN_LEICHT, int(92 * gross), fam["tinte"],
                    luft=int(22 * gross))]
    zeilen.append(Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(36 * gross), fam["leise"], luft=int(12 * gross),
                        hoehe_wie="18.11.2026"))
    if mit_zeile and ZEILE:
        zeilen.append(Zeile(ZEILE, SCHRIFT_SERIF_KURSIV, int(32 * gross), fam["leise"]))
    return stapel(zeilen, x + w / 2, y, h, w * 0.9)


def editorial(lid: str) -> Dict:
    fam = EDITORIAL
    b, g = _bau(fam, lid)
    art = ART[lid]
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot, kontur=(fam["tinte"], 4))
    if art == "quer-1":
        # A triptych band: monogram — names — date, on one axis.
        x, y, w, h = g["text"][0]
        _monogramm(b, fam, x + 64, y + h / 2, 60, 38)
        namen = stapel([Zeile("{event_name}", SCHRIFT_KAPITAELCHEN_LEICHT, 84, fam["tinte"])],
                       x + w / 2, y, h, w - 2 * 330)
        b.text(namen)
        datum = stapel([Zeile("{date}", SCHRIFT_KAPITAELCHEN, 38, fam["leise"], hoehe_wie="18.11.2026")],
                       0, y, h, 300)
        b.text([dict(datum[0], id="datum", x=x + w, align="right")])
        b.linie(x + 150, y + h / 2, 110, fam["tinte"], 3)
        b.linie(x + w - 260 - 110, y + h / 2, 110, fam["tinte"], 3)
    elif art == "quer-3":
        x, y, w, h = g["text"][0]
        _monogramm(b, fam, x + w / 2, y + 74, 52, 34)
        b.linie(x + 90, y + 74, w / 2 - 90 - 80, fam["tinte"], 3)
        b.linie(x + w / 2 + 80, y + 74, w / 2 - 90 - 80, fam["tinte"], 3)
        b.text(_editorial_text(fam, (x, y + 130, w, h - 130), gross=0.82))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        _monogramm(b, fam, 600, y + 120, 70, 46)
        b.linie(x + 60, y + 120, 600 - 70 - 40 - (x + 60), fam["tinte"], 3)
        b.linie(600 + 70 + 40, y + 120, x + w - 60 - (600 + 110), fam["tinte"], 3)
        b.text(_editorial_text(fam, (x, y + 210, w, h - 250), gross=0.95))
    elif art == "streifen":
        for n, (x, y, w, h) in enumerate(g["text"]):
            # Three texts per strip at most: the sheet holds six.
            _monogramm(b, fam, x + w / 2, y + 120, 58, 36, tid=f"mono-{n}")
            b.text([dict(t, id=f"{t['id']}-{n}") for t in
                    _editorial_text(fam, (x, y + 200, w, h - 240), mit_zeile=False, gross=0.78)])
    return b.vorlage(familie="editorial")


# ════════════════════════════════════════════════════════════════════════
# 4 · Gartenfest — soft stripes, white mats, a framed card for the names
# ════════════════════════════════════════════════════════════════════════
STREIFEN = {"id": "gartenfest", "nummer": 4, "name": "Gartenfest Streifen", "kategorie": "Hochzeit",
            "grund": "#ffffff", "streifen": "#e3ebdf", "tinte": "#2f4a37", "leise": "#4f6455",
            "rahmen": "#8fa691", "matte": "#ffffff"}


def _streifen_grund(b: Bau, fam):
    w, h = b.canvas
    datei = f"kol-gartenfest-streifen-{w}x{h}.png"
    b.bild(datei, 0, 0, w, h, partial(kunst.streifen, w, h, fam["grund"], fam["streifen"], 50), oben=False)


def _karte(b: Bau, fam, x, y, w, h, linie=16):
    """A white card on the stripes with a fine sage rule just inside its edge."""
    b.form_unten("rect", x, y, w, h, fill=fam["matte"])
    b.form_unten("rect", x + linie, y + linie, w - 2 * linie, h - 2 * linie,
                 stroke=fam["rahmen"], stroke_width=3)


def _garten_text(fam, region, *, mit_zeile=True, gross=1.0) -> List[Dict]:
    x, y, w, h = region
    zeilen = [Zeile("{event_name}", SCHRIFT_SCRIPT, int(118 * gross), fam["tinte"], luft=int(22 * gross), et=ET_SERIF)]
    zeilen.append(Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(38 * gross), fam["leise"], luft=int(12 * gross),
                        hoehe_wie="18.11.2026"))
    if mit_zeile and ZEILE:
        zeilen.append(Zeile(ZEILE, SCHRIFT_KAPITAELCHEN_LEICHT, int(32 * gross), fam["leise"]))
    return stapel(zeilen, x + w / 2, y, h, w * 0.82)


def gartenfest(lid: str) -> Dict:
    fam = STREIFEN
    b, g = _bau(fam, lid)
    art = ART[lid]
    # Stripes only as a border; everything sits on one white card.
    _streifen_grund(b, fam)
    cw, ch = b.canvas
    if art == "streifen":
        for c in (0, 600):
            _karte(b, fam, c + 24, 24, 552, ch - 48, linie=12)
    else:
        _karte(b, fam, 48, 48, cw - 96, ch - 96)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot)
    if art == "quer-1":
        b.text(_garten_text(fam, g["text"][0], mit_zeile=False, gross=0.8))
    elif art == "quer-3":
        b.text(_garten_text(fam, g["text"][0], gross=0.92))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        texte = _garten_text(fam, (x, y + 130, w, h - 170), gross=1.15)
        oben, _ = umriss(texte)
        b.bild("kol-gartenfest-blume-170.png", 600 - 85, oben - 200, 170, 170,
               partial(kunst.linienblume, 170, 170, farbe=fam["rahmen"], strich=3, seed=4))
        b.text(texte)
    elif art == "streifen":
        for n, (x, y, w, h) in enumerate(g["text"]):
            texte = _garten_text(fam, (x, y + 110, w, h - 170), gross=0.8)
            oben, _ = umriss(texte)
            b.bild("kol-gartenfest-blume-130.png", x + w / 2 - 65, oben - 160, 130, 130,
                   partial(kunst.linienblume, 130, 130, farbe=fam["rahmen"], strich=3, seed=4))
            b.text([dict(t, id=f"{t['id']}-{n}") for t in texte])
    return b.vorlage(familie="gartenfest")


# ════════════════════════════════════════════════════════════════════════
# 5 · Salbei — a sage colour block, cream mats, fine line-art flowers
# ════════════════════════════════════════════════════════════════════════
SALBEI = {"id": "salbei", "nummer": 5, "name": "Salbei & feine Zweige", "kategorie": "Hochzeit",
          "grund": "#5f7259", "tinte": "#fbf7ee", "leise": "#e6e0d2", "matte": "#f6f1e6", "blume": "#ece5d5"}


def _salbei_text(fam, region, *, mit_zeile=True, gross=1.0) -> List[Dict]:
    x, y, w, h = region
    zeilen = [Zeile("{event_name}", SCHRIFT_SCRIPT, int(122 * gross), fam["tinte"], luft=int(24 * gross), et=ET_SERIF)]
    zeilen.append(Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(40 * gross), fam["leise"], luft=int(12 * gross),
                        hoehe_wie="18.11.2026"))
    if mit_zeile and ZEILE:
        zeilen.append(Zeile(ZEILE, SCHRIFT_KAPITAELCHEN_LEICHT, int(34 * gross), fam["leise"]))
    return stapel(zeilen, x + w / 2, y, h, w * 0.78)


def salbei(lid: str) -> Dict:
    fam = SALBEI
    b, g = _bau(fam, lid)
    art = ART[lid]
    # Redrawn 15.11.2026: a slender line sprig instead of the open flower,
    # which read naive on paper.
    blume = lambda n, s, sp=False, seed=5: partial(kunst.zweig_linie, n, n, farbe=fam["blume"], strich=3,
                                                   spiegeln=sp, seed=seed)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot, matte=(fam["matte"], 12))
    if art == "quer-1":
        x, y, w, h = g["text"][0]
        b.bild("kol-salbei-zweig-220.png", x + 10, y - 20, 220, 220, blume(220, 3))
        b.bild("kol-salbei-zweig-220-sp.png", x + w - 230, y - 20, 220, 220, blume(220, 3, True))
        b.text(_salbei_text(fam, g["text"][0], mit_zeile=False, gross=0.88))
    elif art == "quer-3":
        x, y, w, h = g["text"][0]
        b.bild("kol-salbei-zweig-260.png", x - 10, y + h - 250, 260, 260, blume(260, 4))
        b.bild("kol-salbei-zweig-260-sp.png", x + w - 250, y + h - 250, 260, 260, blume(260, 4, True))
        b.text(_salbei_text(fam, (x + 200, y + 10, w - 400, h - 10), gross=0.98))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        z = 340 if h > 600 else 250
        b.bild(f"kol-salbei-zweig-{z}.png", x - 20, y + h - z + 10, z, z, blume(z, 4))
        b.bild(f"kol-salbei-zweig-{z}-sp.png", x + w - z + 20, y + h - z + 10, z, z, blume(z, 4, True))
        b.text(_salbei_text(fam, (x, y, w, h - z + 80), gross=1.28 if h > 600 else 1.0))
    elif art == "streifen":
        for n, (x, y, w, h) in enumerate(g["text"]):
            b.bild("kol-salbei-zweig-200.png", x + w / 2 - 100, y + h - 210, 200, 200, blume(200, 3))
            b.text([dict(t, id=f"{t['id']}-{n}") for t in
                    _salbei_text(fam, (x, y, w, h - 200), gross=0.82)])
    return b.vorlage(familie="salbei")


# ════════════════════════════════════════════════════════════════════════
# 6 · Boho — sand and terracotta, arches, dried grasses
# ════════════════════════════════════════════════════════════════════════
BOHO = {"id": "boho", "nummer": 6, "name": "Boho Terrakotta", "kategorie": "Hochzeit",
        "grund": "#f2e8dc", "tinte": "#8f4a2f", "leise": "#6b4a3a", "terra": "#b5603f", "sand": "#e4d3bf"}


def _boho_text(fam, region, *, mit_zeile=True, gross=1.0) -> List[Dict]:
    x, y, w, h = region
    zeilen = [Zeile("{event_name}", SCHRIFT_SERIF_KURSIV, int(96 * gross), fam["tinte"], luft=int(20 * gross))]
    zeilen.append(Zeile("{date}", SCHRIFT_KAPITAELCHEN, int(40 * gross), fam["leise"], luft=int(12 * gross),
                        hoehe_wie="18.11.2026"))
    if mit_zeile and ZEILE:
        zeilen.append(Zeile(ZEILE, SCHRIFT_KAPITAELCHEN_LEICHT, int(34 * gross), fam["leise"]))
    return stapel(zeilen, x + w / 2, y, h, w * 0.8)


def boho(lid: str) -> Dict:
    fam = BOHO
    b, g = _bau(fam, lid)
    art = ART[lid]
    gras = lambda n, sp=False, seed=5: partial(kunst.graeser, n, n, spiegeln=sp, seed=seed)
    for (x, y, w, h), shot in g["fotos"]:
        b.foto(x, y, w, h, shot, radius=18)
    if art == "quer-1":
        x, y, w, h = g["text"][0]
        b.bild("kol-boho-sonne-150.png", x + w / 2 - 75, y - 4, 150, 79, partial(kunst.sonne, 150, fam["terra"], 9, 3))
        b.bild("kol-boho-gras-300.png", x - 40, y + h - 300 + 70, 300, 300, gras(300))
        b.bild("kol-boho-gras-300-sp.png", x + w - 260, y + h - 300 + 70, 300, 300, gras(300, True, 6))
        b.text(_boho_text(fam, (x, y + 70, w, h - 70), mit_zeile=False, gross=0.8))
    elif art == "quer-3":
        x, y, w, h = g["text"][0]
        b.form_unten("rect", x, y + 24, w, h - 24, fill=fam["sand"], radius=18)
        b.bild("kol-boho-sonne-180.png", x + w / 2 - 90, y + 48, 180, 94, partial(kunst.sonne, 180, fam["terra"], 9, 3))
        b.bild("kol-boho-gras-260.png", x + 10, y + h - 270, 260, 260, gras(260))
        b.bild("kol-boho-gras-260-sp.png", x + w - 270, y + h - 270, 260, 260, gras(260, True, 6))
        b.text(_boho_text(fam, (x + 200, y + 140, w - 400, h - 150), gross=0.84))
    elif art == "hoch-4":
        x, y, w, h = g["text"][0]
        eng = h < 600
        bogen_w, bogen_h = 640, h - 20
        b.bild(f"kol-boho-bogen-640x{bogen_h}.png", 600 - bogen_w / 2, y + 20, bogen_w, bogen_h,
               partial(kunst.bogen, bogen_w, bogen_h, fam["sand"]), oben=False)
        b.bild("kol-boho-sonne-200.png", 600 - 100, y + (80 if eng else 150), 200, 104,
               partial(kunst.sonne, 200, fam["terra"], 11, 3))
        b.bild("kol-boho-gras-320.png", x - 50, y + h - 300, 320, 320, gras(320))
        b.bild("kol-boho-gras-320-sp.png", x + w - 270, y + h - 300, 320, 320, gras(320, True, 6))
        b.text(_boho_text(fam, (600 - 290, y + (190 if eng else 270), 580, h - (220 if eng else 300)),
                          gross=0.95 if eng else 1.05))
    elif art == "streifen":
        for n, (x, y, w, h) in enumerate(g["text"]):
            b.bild("kol-boho-sonne-140.png", x + w / 2 - 70, y + 40, 140, 74, partial(kunst.sonne, 140, fam["terra"], 9, 3))
            b.bild("kol-boho-gras-220.png", x + w / 2 - 110, y + h - 230, 220, 220, gras(220))
            b.text([dict(t, id=f"{t['id']}-{n}") for t in
                    _boho_text(fam, (x, y + 120, w, h - 350), gross=0.8)])
    return b.vorlage(familie="boho")


from .familien_hochzeit2 import FAMILIEN_HOCHZEIT2  # noqa: E402 — needs the helpers above

FAMILIEN: List[Tuple[Dict, Callable[[str], Dict]]] = [
    (GRUEN, gruen), (SCHRIFT, schrift), (EDITORIAL, editorial),
    (STREIFEN, gartenfest), (SALBEI, salbei), (BOHO, boho),
] + FAMILIEN_HOCHZEIT2
