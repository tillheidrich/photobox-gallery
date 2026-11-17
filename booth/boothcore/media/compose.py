"""Bildkomposition: Framing, Collagen, Druckkarte, Web-Varianten.

Eine Aufnahme, mehrere Ausgabeprofile. Die Kamera liefert quer (3:2); der Gast
hat vorher sein Framing gewählt (4:5, 9:16, 3:2), und genau dieser Ausschnitt
wird aus dem Vollbild geschnitten — kein Nachbearbeiten in der Galerie nötig.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import qrcode
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

from . import aufnahmezeit


# The template renderer (style, fonts, photo fitting, render_template) lives in
# vorlagen_render.py, shared word for word with the gallery's preview.
from .vorlagen_render import (  # noqa: F401 — compose is the booth's door to them
    ASPECTS, BEISPIEL_QR, BEISPIEL_TEXTE, DEFAULT_STYLE, EINPASSEN, FOKUS_Y_HOCH_IN_QUER, FONT_DIR, PRINT_CANVAS, STYLE_KEYS,
    STYLE_PRESETS, _FONT_FILES, _SERIF_FALLBACK, _STYLE_TOKENS, _SYSTEM_FALLBACK, _SafeDict, _background,
    _bild, _deckkraft, _farbe, _form, _foto, _lage, _lege, _mitte, _rahmen, _text, _text_mitte, _text_zeilen,
    _zeilenabstand, crop_to_aspect, drehung, druckbogen, load_font, parse_aspect, passt_ins_feld, qr_image,
    beispielbilder, qr_gedruckt, render_template, sicherer_rand, style, testdruck_marke, textfeld, textsatz,
    textwarnungen, werte,
)


# ── Rahmen und Textzeilen auf jedem Bild ──────────────────
# dslrBooth nennt das "Overlay + Text". Bei uns steht alles in Prozent, damit
# dieselbe Einstellung für 4:5, 9:16 und die Druckkarte gilt — der Betreiber
# stellt es einmal ein und muss keine Vorlage schreiben.

QR_CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")


def qr_box(canvas: Tuple[int, int], size: int, position: str = "bottom-right", margin: int = 40) -> Tuple[int, int]:
    """Linke obere Ecke für den QR-Code in der gewünschten Bildecke."""
    w, h = canvas
    size = max(60, min(int(size), min(w, h) // 2))
    margin = max(0, int(margin))
    right, bottom = w - size - margin, h - size - margin
    return {
        "bottom-right": (right, bottom), "bottom-left": (margin, bottom),
        "top-right": (right, margin), "top-left": (margin, margin),
    }.get(position, (right, bottom))


def draw_lines(img: Image.Image, lines: List[Dict], values: Optional[Dict[str, str]] = None) -> Image.Image:
    """Textzeilen mit Prozentkoordinaten auf ein fertiges Bild schreiben.

    Eine Zeile: ``{"value": "{event_name}", "x": 50, "y": 92, "size": 4,
    "color": "#ffffff", "align": "center", "bold": true, "shadow": true}``.
    ``size`` ist die Schrifthöhe in Prozent der kürzeren Bildseite, damit die
    Zeile auf dem Handyformat genauso sitzt wie auf dem Druck.
    """
    if not lines:
        return img
    out = img.convert("RGB")
    draw = ImageDraw.Draw(out)
    short = min(out.size)
    vals = values or {}
    for line in lines:
        value = str(line.get("value", "")).format_map(_SafeDict(vals))
        if not value.strip():
            continue
        size = max(10, int(short * float(line.get("size", 4)) / 100))
        font = load_font(size, bool(line.get("bold", True)))
        x = int(out.width * float(line.get("x", 50)) / 100)
        y = int(out.height * float(line.get("y", 92)) / 100)
        align = line.get("align", "center")
        anchor = {"left": "la", "center": "ma", "right": "ra"}.get(align, "ma")
        if line.get("shadow", True):          # hält die Zeile auf hellen wie dunklen Bildern lesbar
            draw.text((x + max(2, size // 20), y + max(2, size // 20)), value, font=font,
                      fill="#00000080", anchor=anchor)
        draw.text((x, y), value, font=font, fill=line.get("color", "#ffffff"), anchor=anchor)
    return out


def apply_frame(img: Image.Image, *, overlay: Optional[Path] = None, lines: Optional[List[Dict]] = None,
                values: Optional[Dict[str, str]] = None) -> Image.Image:
    """Rahmen-PNG über das Bild legen und die Textzeilen schreiben.

    Das Overlay wird auf die Bildgröße gezogen; ein Rahmen im falschen
    Seitenverhältnis verzerrt also, statt das Bild zu beschneiden — beschnitten
    wird nie, das Foto ist wichtiger als der Rahmen.
    """
    out = img.convert("RGB")
    if overlay is not None and Path(overlay).exists():
        try:
            with Image.open(overlay) as ov:
                ov = ov.convert("RGBA").resize(out.size, Image.Resampling.LANCZOS)
            out = Image.alpha_composite(out.convert("RGBA"), ov).convert("RGB")
        except (OSError, ValueError):
            pass                              # kaputter Rahmen darf keine Aufnahme kosten
    return draw_lines(out, [l for l in (lines or [])], values)


def single_print_template(aspect: str, with_qr: bool, qr: Optional[Dict] = None) -> Dict:
    """Druckkarte für ein Einzelfoto — hochkant oder quer, je nach Framing.

    Hochkant (4:5, 9:16, 2:3, 1:1): Foto oben, Leiste unten mit Name, Datum, QR.
    Quer (3:2, 16:9): Karte quer, Foto links, schmale Spalte rechts.
    Der Drucker dreht die Karte beim Druck selbst passend aufs Papier.
    """
    ratio = parse_aspect(aspect)
    margin = 40
    if ratio > 1.05:
        W, H = PRINT_CANVAS[1], PRINT_CANVAS[0]
        col = 330 if with_qr else 280
        area_w, area_h = W - 2 * margin - col, H - 2 * margin
        pw, ph = (int(area_h * ratio), area_h) if area_w / area_h > ratio else (area_w, int(area_w / ratio))
        py = (H - ph) // 2
        cx = margin + pw + 30
        # The column beside the photo is narrow (280 px, 2.4 cm): a couple's
        # two first names do not fit it in one line at 46 px. The name gets
        # the column's whole height above the date, so it can go to two lines
        # (vorlagen_render.textsatz) instead of running off the sheet; the
        # date stands at the photo's lower edge, or above the QR code.
        oben, datum_y = py, py + ph - 38
        if with_qr:
            size = int((qr or {}).get("size") or 260)
            x, y = qr_box((W, H), size, str((qr or {}).get("position") or "bottom-right"),
                          int((qr or {}).get("margin", margin)))
            qr_pos = {"x": x, "y": y, "size": size}
            if x >= cx and y >= H // 2:        # the code below in the column: the date above it
                datum_y = y - 60
            elif x >= cx:                      # the code on top of the column: the name below it
                oben = max(py, y + size + 30)
        spalte = W - margin - cx
        tpl = {
            "id": "single-landscape", "canvas": [W, H], "background": "#ffffff", "orientation": "landscape",
            "slots": [{"x": margin, "y": py, "w": pw, "h": ph}],
            "texts": [
                {"x": cx, "y": oben, "size": 46, "value": "{headline}", "color": "#111111",
                 "w": spalte, "h": max(60, datum_y - 24 - oben)},
                {"x": cx, "y": datum_y, "size": 32, "bold": False, "value": "{subline}", "color": "#555555",
                 "w": spalte},
            ],
        }
        if with_qr:
            tpl["qr"] = qr_pos
        return tpl
    W, H = PRINT_CANVAS
    strip = 250 if with_qr else 170
    area_w, area_h = W - 2 * margin, H - 2 * margin - strip
    if area_w / area_h > ratio:
        pw, ph = int(area_h * ratio), area_h
    else:
        pw, ph = area_w, int(area_w / ratio)
    px = (W - pw) // 2
    tpl = {
        "id": "single", "canvas": [W, H], "background": "#ffffff", "orientation": "portrait",
        "slots": [{"x": px, "y": margin, "w": pw, "h": ph}],
        "texts": [
            {"x": margin + 10, "y": H - margin - strip + 70, "size": 54, "value": "{headline}", "color": "#111111"},
            {"x": margin + 10, "y": H - margin - strip + 140, "size": 36, "bold": False, "value": "{subline}", "color": "#555555"},
        ],
    }
    if with_qr:
        size = int((qr or {}).get("size") or 210)
        x, y = qr_box((W, H), size, str((qr or {}).get("position") or "bottom-right"),
                      int((qr or {}).get("margin", margin)))
        tpl["qr"] = {"x": x, "y": y, "size": size}
    return tpl


def template_info(t: Dict) -> Dict:
    """Kurzinfo für die Oberfläche: Ausrichtung und Seitenverhältnis der Aufnahme-Slots."""
    cw, ch = t.get("canvas", PRINT_CANVAS)
    slots = t.get("slots") or [{"w": cw, "h": ch}]
    first = slots[0]
    return {
        "id": t["id"], "name": t.get("name", t["id"]),
        "shots": int(t.get("shots") or len({s.get("shot", i) for i, s in enumerate(slots)})),
        "orientation": t.get("orientation") or ("landscape" if cw > ch else "portrait"),
        "canvas": [cw, ch], "slot_aspect": round(first["w"] / first["h"], 4),
        # Was am Ende aus dem Drucker kommt — die Oberfläche sagt es dem Gast,
        # und der Betreiber sieht beim Einrichten, wofür er Papier braucht.
        "paper": t.get("paper") or ("10x15-quer" if cw > ch else "10x15"),
        # Every slot with the shot it shows and how the shot is fitted, so the
        # kiosk's live preview can show for each shot exactly what its slot
        # keeps (crop or the whole picture, see passt_ins_feld). A slot hidden
        # in the editor is not printed, so it is not previewed either.
        "slots": [{"w": int(s["w"]), "h": int(s["h"]), "shot": int(s.get("shot", i)), "fit": str(s.get("fit") or "")}
                  for i, s in enumerate(slots) if not s.get("hidden")],
        # Where each of those slots sits ([x, y, w, h], same order), so the
        # set-up screen can draw the sheet's outline while its picture
        # (layoutbilder) is not there yet.
        "slot_boxes": [[int(s.get("x", 0)), int(s.get("y", 0)), int(s["w"]), int(s["h"])]
                       for s in slots if not s.get("hidden")],
    }


# ── Der mitgelieferte Vorlagenkatalog ─────────────────────
# Gebaut nach dem, was im Fotoboxbetrieb tatsächlich gedruckt wird: die
# Postkarte 10×15 (1200×1800 bei 300 dpi), dieselbe quer, der klassische
# 2×6-Zoll-Streifen — der wird doppelt auf ein 10×15-Blatt gesetzt und nach
# dem Druck einmal geschnitten, so arbeiten DNP, Citizen und Mitsubishi — und
# das quadratische Blatt. Jede Vorlage sagt, wie viele Aufnahmen sie braucht;
# die Box zählt danach ab.
#
# Das Aussehen steht nicht hier, sondern im Stil (siehe oben): @hintergrund,
# @schrift, @gedaempft, @akzent, @rund und die Zeilen {headline}/{subline}.
# Eine Anordnung, sechs Anlässe.

RAND, LUFT = 60, 20


def _fusszeile(canvas: Sequence[int], y: int, *, qr: Optional[int] = None,
               align: str = "left") -> Dict:
    """Die Zeilen unter den Bildern — überall gleich gesetzt."""
    breite = int(canvas[0])
    x = RAND if align == "left" else breite // 2
    texts = [
        {"x": x, "y": y, "size": 58, "value": "{headline}", "color": "@schrift", "align": align},
        {"x": x, "y": y + 78, "size": 36, "bold": False, "value": "{subline}",
         "color": "@gedaempft", "align": align},
    ]
    block = {"texts": texts}
    if qr:
        block["qr"] = {"x": breite - RAND - qr, "y": y - 10, "size": qr}
    return block


def _karte(tid: str, name: str, canvas: Sequence[int], slots: List[Dict], *,
           paper: str = "10x15", fuss: Optional[Dict] = None, shots: Optional[int] = None) -> Dict:
    t = {"id": tid, "name": name, "canvas": [int(canvas[0]), int(canvas[1])],
         "paper": paper, "background": "@hintergrund",
         "slots": [dict(s, radius=s.get("radius", "@rund")) for s in slots]}
    if shots:
        t["shots"] = int(shots)
    if fuss:
        t.update(fuss)
    return t


def _raster(spalten: int, zeilen: int, canvas: Sequence[int], *, oben: int = RAND,
            unten: int = 0, shots: Optional[List[int]] = None) -> List[Dict]:
    """Gleichmäßiges Raster, das den Platz zwischen den Rändern ausfüllt."""
    breite = (int(canvas[0]) - 2 * RAND - (spalten - 1) * LUFT) // spalten
    hoehe = (int(canvas[1]) - oben - unten - (zeilen - 1) * LUFT) // zeilen
    aus = []
    for r in range(zeilen):
        for c in range(spalten):
            i = r * spalten + c
            aus.append({"x": RAND + c * (breite + LUFT), "y": oben + r * (hoehe + LUFT),
                        "w": breite, "h": hoehe, "shot": (shots[i] if shots else i)})
    return aus


def _streifen(anzahl: int, canvas: Sequence[int] = PRINT_CANVAS) -> List[Dict]:
    """Der klassische Streifen, zweimal nebeneinander auf einem Blatt.

    Beide Spalten zeigen dieselben Aufnahmen: Nach dem Schnitt hat jeder der
    beiden einen vollständigen Streifen in der Hand.
    """
    breite, hoehe = int(canvas[0]), int(canvas[1])
    spalte = (breite - 3 * 50) // 2
    platz = hoehe - 80 - 230                     # oben Rand, unten die Zeilen
    bild = (platz - (anzahl - 1) * 16) // anzahl
    aus = []
    for spalte_i, x in enumerate((50, 50 + spalte + 50)):
        for i in range(anzahl):
            aus.append({"x": x, "y": 50 + i * (bild + 16), "w": spalte, "h": bild, "shot": i})
    return aus


def _streifen_fuss(anzahl: int, canvas: Sequence[int] = PRINT_CANVAS) -> Dict:
    breite, hoehe = int(canvas[0]), int(canvas[1])
    spalte = (breite - 3 * 50) // 2
    platz = hoehe - 80 - 230
    bild = (platz - (anzahl - 1) * 16) // anzahl
    y = 50 + anzahl * (bild + 16) + 24
    texts = []
    for x in (50 + spalte // 2, 50 + spalte + 50 + spalte // 2):
        texts.append({"x": x, "y": y, "size": 44, "value": "{headline}", "color": "@schrift", "align": "center"})
        texts.append({"x": x, "y": y + 62, "size": 30, "bold": False, "value": "{subline}",
                      "color": "@gedaempft", "align": "center"})
    return {"texts": texts}


_HOCH, _QUER, _QUADRAT = PRINT_CANVAS, (1800, 1200), (1200, 1200)
#: 13×18 quer bei 300 dpi — das größere Blatt, das DNP, Citizen und Mitsubishi
#: fahren. Für Gruppen und Panoramen ist 10×15 schlicht zu klein.
_GROSS_QUER = (2126, 1535)

BUILTIN_TEMPLATES: List[Dict] = [
    # ── eine Aufnahme ──
    _karte("solo", "Einzelbild · 1 Foto", _HOCH,
           [{"x": RAND, "y": RAND, "w": 1080, "h": 1380}],
           fuss=_fusszeile(_HOCH, 1500, qr=200), shots=1),
    _karte("solo-quer", "Einzelbild quer · 1 Foto", _QUER,
           [{"x": RAND, "y": RAND, "w": 1680, "h": 900}],
           paper="10x15-quer", fuss=_fusszeile(_QUER, 1000, qr=170), shots=1),
    _karte("quadrat1", "Quadrat · 1 Foto", _QUADRAT,
           [{"x": RAND, "y": RAND, "w": 1080, "h": 880}],
           paper="quadrat", fuss=_fusszeile(_QUADRAT, 990, qr=150), shots=1),
    # Edge to edge: no margin, no rounded corners, no text, no QR. A frame is
    # then the operator's own overlay (frame.print_overlay) or the editor.
    _karte("vollbild", "Vollbild · 1 Foto, ohne Rand", _HOCH,
           [{"x": 0, "y": 0, "w": _HOCH[0], "h": _HOCH[1], "radius": 0}], shots=1),
    _karte("vollbild-quer", "Vollbild quer · 1 Foto, ohne Rand", _QUER,
           [{"x": 0, "y": 0, "w": _QUER[0], "h": _QUER[1], "radius": 0}],
           paper="10x15-quer", shots=1),

    # ── zwei ──
    {"id": "duo", "name": "Duo · 2 Fotos", "shots": 2, "canvas": [1200, 1800], "paper": "10x15",
     "background": "@hintergrund",
     "slots": [{"x": 60, "y": 60, "w": 1080, "h": 720, "radius": "@rund"},
               {"x": 60, "y": 810, "w": 1080, "h": 720, "radius": "@rund"}],
     **_fusszeile(_HOCH, 1580, qr=190)},
    _karte("duo-quer", "Duo quer · 2 Fotos", _QUER,
           [{"x": RAND, "y": RAND, "w": 830, "h": 940}, {"x": 910, "y": RAND, "w": 830, "h": 940}],
           paper="10x15-quer", fuss=_fusszeile(_QUER, 1040, qr=140), shots=2),

    # Zwei breite Bilder übereinander: das Querformat für Gruppen, bei dem
    # niemand seitlich abgeschnitten wird.
    _karte("duo-quer-gestapelt", "Duo quer gestapelt · 2 Fotos", _QUER,
           _raster(1, 2, _QUER, unten=220), shots=2,
           paper="10x15-quer", fuss=_fusszeile(_QUER, 1010, qr=150)),

    # ── drei ──
    _karte("trio", "Dreier · 3 Fotos", _HOCH, _raster(1, 3, _HOCH, unten=300), shots=3,
           fuss=_fusszeile(_HOCH, 1560, qr=190)),
    {"id": "strips3", "name": "Fotostreifen · 3 Fotos", "shots": 3, "canvas": [1200, 1800],
     "paper": "streifen", "background": "@hintergrund",
     "slots": [dict(s, radius="@rund") for s in _streifen(3)], **_streifen_fuss(3)},
    _karte("trio-quer", "Panorama · 3 Fotos", _QUER, _raster(3, 1, _QUER, unten=220), shots=3,
           paper="10x15-quer", fuss=_fusszeile(_QUER, 1020, qr=140)),

    # ── vier ──
    _karte("grid4", "Raster · 4 Fotos", _HOCH, _raster(2, 2, _HOCH, unten=300), shots=4,
           fuss=_fusszeile(_HOCH, 1560, qr=190)),
    _karte("grid4-quer", "Raster quer · 4 Fotos", _QUER, _raster(2, 2, _QUER, unten=220), shots=4,
           paper="10x15-quer", fuss=_fusszeile(_QUER, 1010, qr=150)),
    _karte("quadrat4", "Quadrat · 4 Fotos", _QUADRAT, _raster(2, 2, _QUADRAT, unten=220), shots=4,
           paper="quadrat", fuss=_fusszeile(_QUADRAT, 1010, qr=140)),
    {"id": "streifen4", "name": "Fotostreifen · 4 Fotos", "shots": 4, "canvas": [1200, 1800],
     "paper": "streifen", "background": "@hintergrund",
     "slots": [dict(s, radius="@rund") for s in _streifen(4)], **_streifen_fuss(4)},

    # ── fünf und sechs ──
    _karte("fuenf", "Fünfer · 1 groß, 4 klein", _HOCH,
           [{"x": RAND, "y": RAND, "w": 1080, "h": 700, "shot": 0},
            {"x": RAND, "y": 790, "w": 530, "h": 350, "shot": 1},
            {"x": 610, "y": 790, "w": 530, "h": 350, "shot": 2},
            {"x": RAND, "y": 1160, "w": 530, "h": 350, "shot": 3},
            {"x": 610, "y": 1160, "w": 530, "h": 350, "shot": 4}],
           fuss=_fusszeile(_HOCH, 1580, qr=170), shots=5),
    _karte("fuenf-quer", "Fünfer quer · 1 groß, 4 klein", _QUER,
           [{"x": RAND, "y": RAND, "w": 1000, "h": 900, "shot": 0},
            {"x": 1080, "y": RAND, "w": 320, "h": 440, "shot": 1},
            {"x": 1420, "y": RAND, "w": 320, "h": 440, "shot": 2},
            {"x": 1080, "y": 520, "w": 320, "h": 440, "shot": 3},
            {"x": 1420, "y": 520, "w": 320, "h": 440, "shot": 4}],
           paper="10x15-quer", fuss=_fusszeile(_QUER, 1010, qr=150), shots=5),
    _karte("sechs", "Sechser · 2 × 3", _HOCH, _raster(2, 3, _HOCH, unten=300), shots=6,
           fuss=_fusszeile(_HOCH, 1560, qr=190)),
    _karte("sechs-quer", "Sechser quer · 3 × 2", _QUER, _raster(3, 2, _QUER, unten=220), shots=6,
           paper="10x15-quer", fuss=_fusszeile(_QUER, 1010, qr=150)),

    # ── quer auf dem großen Blatt ──
    # 13×18 quer ist das Blatt für Gruppenbilder: ein DS620, ein Citizen und
    # ein Mitsubishi können es, und darauf wird ein Panorama erst eines.
    _karte("panorama-gross", "Panorama 13×18 · 3 Fotos", _GROSS_QUER,
           _raster(3, 1, _GROSS_QUER, unten=260), shots=3,
           paper="13x18-quer", fuss=_fusszeile(_GROSS_QUER, 1290, qr=170)),
    _karte("grid4-gross", "Raster 13×18 quer · 4 Fotos", _GROSS_QUER,
           _raster(2, 2, _GROSS_QUER, unten=260), shots=4,
           paper="13x18-quer", fuss=_fusszeile(_GROSS_QUER, 1290, qr=170)),
]


# ── Schlicht / Plain (15.11.2026) ──
# Owner: "es muss auch ein paar Basic Layouts ohne Text oder so geben".
# Photos only, on white or black paper, no line of text, no rounded corners,
# no frame line from the style (``plain``, see vorlagen_render._rahmen).
# The QR code is there but hidden; the profile switch ``collage_plain_qr``
# (off by default) shows it (Booth.templates). All on 10×15, the SELPHY
# CP1300 postcard, with the margin at 60 px (5 mm, above the 4 mm safe
# margin of PAPIERE). Offered only where ticked: an empty selection keeps
# offering the catalogue above, as before.
_PLAIN_RAND, _PLAIN_LUFT = 60, 30
PLAIN_TONES = (("weiss", "weiß", "#ffffff"), ("schwarz", "schwarz", "#000000"))


def _plain_grid(spalten: int, zeilen: int, canvas: Sequence[int]) -> List[Dict]:
    w, h = int(canvas[0]), int(canvas[1])
    bw = (w - 2 * _PLAIN_RAND - (spalten - 1) * _PLAIN_LUFT) // spalten
    bh = (h - 2 * _PLAIN_RAND - (zeilen - 1) * _PLAIN_LUFT) // zeilen
    return [{"x": _PLAIN_RAND + c * (bw + _PLAIN_LUFT), "y": _PLAIN_RAND + r * (bh + _PLAIN_LUFT),
             "w": bw, "h": bh, "shot": r * spalten + c, "radius": 0}
            for r in range(zeilen) for c in range(spalten)]


def _plain_strip(anzahl: int) -> List[Dict]:
    """Two equal strips side by side (cut in the middle), the same shots in each."""
    w, h = PRINT_CANVAS
    spalte = (w - 3 * _PLAIN_RAND) // 2
    luft = 20
    bild = (h - 2 * _PLAIN_RAND - (anzahl - 1) * luft) // anzahl
    return [{"x": x, "y": _PLAIN_RAND + i * (bild + luft), "w": spalte, "h": bild, "shot": i, "radius": 0}
            for x in (_PLAIN_RAND, 2 * _PLAIN_RAND + spalte) for i in range(anzahl)]


def _plain_family() -> List[Dict]:
    formen = [
        ("1", "1 Foto", _HOCH, "10x15", _plain_grid(1, 1, _HOCH)),
        ("1-quer", "1 Foto quer", _QUER, "10x15-quer", _plain_grid(1, 1, _QUER)),
        ("2", "2 Fotos", _HOCH, "10x15", _plain_grid(1, 2, _HOCH)),
        ("2-quer", "2 Fotos quer", _QUER, "10x15-quer", _plain_grid(2, 1, _QUER)),
        ("3", "3 Fotos", _HOCH, "10x15", _plain_grid(1, 3, _HOCH)),
        ("3-quer", "3 Fotos quer", _QUER, "10x15-quer", _plain_grid(3, 1, _QUER)),
        ("4", "4 Fotos (Raster)", _HOCH, "10x15", _plain_grid(2, 2, _HOCH)),
        ("4-quer", "4 Fotos quer (Raster)", _QUER, "10x15-quer", _plain_grid(2, 2, _QUER)),
        ("streifen3", "Streifen · 3 Fotos", _HOCH, "streifen", _plain_strip(3)),
        ("streifen4", "Streifen · 4 Fotos", _HOCH, "streifen", _plain_strip(4)),
    ]
    aus = []
    for kern, name, canvas, paper, slots in formen:
        for ton, ton_name, farbe in PLAIN_TONES:
            w, h = int(canvas[0]), int(canvas[1])
            aus.append({
                "id": f"schlicht-{kern}-{ton}", "name": f"Schlicht · {name} · {ton_name}",
                "shots": len({s["shot"] for s in slots}), "canvas": [w, h], "paper": paper,
                "background": farbe, "plain": True, "slots": [dict(s) for s in slots],
                "qr": {"x": w - _PLAIN_RAND - 170, "y": h - _PLAIN_RAND - 170, "size": 150, "hidden": True},
            })
    return aus


BUILTIN_TEMPLATES.extend(_plain_family())


#: The built-in sheet for a GIF run, by number of pictures: (portrait, landscape).
#: All on 10×15. Six is the most a 10×15 sheet holds at a size that still
#: shows faces.
_GIF_SHEETS = {2: ("duo", "duo-quer"), 3: ("trio", "trio-quer"), 4: ("grid4", "grid4-quer"),
               5: ("fuenf", "fuenf-quer"), 6: ("sechs", "sechs-quer")}


def gif_print_template(aspect: str, count: int) -> Tuple[Dict, List[int]]:
    """The sheet a GIF run is printed on, and which of its pictures go on it.

    Portrait or square pictures (4:5, 9:16, 1:1) go on the portrait sheet,
    landscape ones (3:2, 16:9) on the landscape sheet. Up to six pictures all
    go on; of more, six spread evenly from the first to the last.
    """
    count = max(1, int(count))
    n = max(2, min(count, 6))
    hoch, quer = _GIF_SHEETS[n]
    tid = quer if parse_aspect(aspect) > 1.05 else hoch
    tpl = next(t for t in BUILTIN_TEMPLATES if t["id"] == tid)
    return tpl, gif_auswahl(count, n)


def gif_auswahl(count: int, n: int) -> List[int]:
    """Which of ``count`` pictures go into ``n`` slots: all in order, repeated
    when there are fewer, spread evenly from the first to the last when there
    are more."""
    count, n = max(1, int(count)), max(1, int(n))
    if count <= n:
        return list(range(count)) if count >= n else [i % count for i in range(n)]
    if n == 1:
        return [0]
    return [round(i * (count - 1) / (n - 1)) for i in range(n)]


def load_template(path: Path) -> Dict:
    """A template file through the same gate as every other way in (vorlagen_kern.annehmen).

    Raises ``ValueError`` (``VorlageAbgelehnt``, with the reasons) for a file
    that is not a template of this format, so the caller skips it; never the
    raw file — a file put into the media folder by hand or synced from the
    gallery is checked here, whatever it says.
    """
    from . import vorlagen_kern
    return vorlagen_kern.lade(Path(path))


def save_jpeg(img: Image.Image, path: Path, quality: int = 92, max_px: Optional[int] = None,
              progressive: bool = True, taken: Optional[float] = None) -> Path:
    """Progressive suits screens and the web. A file for a printer is written
    with ``progressive=False``: a SELPHY cannot read the other kind.

    ``taken`` (epoch seconds) goes into the EXIF dates: the PC's time of the
    run, because the camera's clock cannot be trusted."""
    if max_px:
        img = img.copy()
        img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = {"exif": aufnahmezeit.exif_for(taken)} if taken else {}
    img.convert("RGB").save(path, "JPEG", quality=quality, optimize=True, progressive=progressive, **extra)
    return path
