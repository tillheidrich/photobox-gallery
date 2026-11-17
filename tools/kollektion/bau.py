"""Building blocks for the collection: geometry, text stacks measured with the real fonts.

A template here is plain data in the editor's format (``vorlagen_kern.pruefe``
takes it unchanged). Everything the renderer would otherwise take from the
operator's style — background, colours, frame line — is set explicitly, so a
template looks as designed whatever style the booth has: the background is a
full-sheet rectangle at the bottom, and the style's frame layer ("overlay")
sits under it, where it cannot draw over the design.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageFont

WURZEL = Path(__file__).resolve().parents[2]
MEDIEN = WURZEL / "app" / "vorlagen_bibliothek" / "medien"

# The fonts the collection uses — one line each to swap a font.
SCHRIFT_SCRIPT = "PinyonScript-Regular.ttf"
SCHRIFT_KAPITAELCHEN = "CormorantSC-SemiBold.ttf"
SCHRIFT_KAPITAELCHEN_LEICHT = "CormorantSC-Medium.ttf"
SCHRIFT_SERIF = "DMSerifDisplay-Regular.ttf"
SCHRIFT_SERIF_KURSIV = "DMSerifDisplay-Italic.ttf"
SCHRIFT_SANS = "system"          # Inter, shipped with the program
SCHRIFT_SCRIPT_KLAR = "GreatVibes-Regular.ttf"     # a script whose "&" reads as "&"
SCHRIFT_SCHREIBMASCHINE = "CourierPrime-Regular.ttf"
#: The plain ampersand set into Pinyon lines (amp_font, see vorlagen_render).
ET_SERIF = "CormorantSC-Medium.ttf"

#: The longest pair of names a template is sized for (17 characters). Longer
#: names run wider than their column — the renderer does not shrink text, so
#: the operator then lowers the size in the editor.
LANGE_NAMEN = "Charlotte & Jonas"
BEISPIEL = {"{event_name}": LANGE_NAMEN, "{date}": "18.11.2026"}

#: Smallest text: 7 pt at 300 dpi is 29 px; the renderer's size is the em.
MIN_GROESSE = 30

QUER, HOCH = (1800, 1200), (1200, 1800)


def _font(datei: str, groesse: int) -> ImageFont.FreeTypeFont:
    if datei == "system":
        pfad = WURZEL / "booth" / "boothcore" / "media" / "fonts" / "Inter-Regular.ttf"
    elif datei == "system-bold":
        pfad = WURZEL / "booth" / "boothcore" / "media" / "fonts" / "Inter-SemiBold.ttf"
    elif datei in ("display", "display-bold"):
        pfad = WURZEL / "booth" / "boothcore" / "media" / "fonts" / (
            "Archivo-Bold.ttf" if datei == "display-bold" else "Archivo-SemiBold.ttf")
    else:
        pfad = MEDIEN / datei
    return ImageFont.truetype(str(pfad), groesse)


def masse(datei: str, groesse: int, text: str) -> Tuple[float, int, int]:
    """Width, top and bottom of the ink relative to the y the renderer is given."""
    for platz, wert in BEISPIEL.items():
        text = text.replace(platz, wert)
    f = _font(datei, groesse)
    l, o, r, u = f.getbbox(text, anchor="la")
    return r - l, o, u


class Zeile:
    """One line of a text stack."""

    def __init__(self, wert: str, schrift: str, groesse: int, farbe: str, *, luft: int = 0,
                 bold: bool = False, tid: Optional[str] = None, hoehe_wie: Optional[str] = None,
                 et: Optional[str] = None):
        self.wert, self.schrift, self.farbe = wert, schrift, farbe
        self.groesse = max(MIN_GROESSE, int(groesse))
        self.luft, self.bold, self.tid = luft, bold, tid
        # Measure the line height on a stand-in (a date has no descenders,
        # but the stack should not jump when the operator types one).
        self.hoehe_wie = hoehe_wie
        self.et = et                 # font for the "&" of this line


def stapel(zeilen: List[Zeile], x_mitte: float, oben: float, hoehe: float, breite: float,
           ids: str = "t") -> List[Dict]:
    """Lines centred on ``x_mitte``, the whole block centred in [oben, oben+hoehe].

    Sizes shrink until the longest expected value fits ``breite``.
    """
    gemessen = []
    for z in zeilen:
        groesse = z.groesse
        while groesse > MIN_GROESSE and masse(z.schrift, groesse, z.wert)[0] > breite:
            groesse -= 2
        _, o, u = masse(z.schrift, groesse, z.hoehe_wie or z.wert)
        gemessen.append((z, groesse, o, u))
    gesamt = sum(u - o for _, _, o, u in gemessen) + sum(z.luft for z, *_ in gemessen[:-1])
    y = oben + (hoehe - gesamt) / 2
    aus = []
    for n, (z, groesse, o, u) in enumerate(gemessen):
        aus.append({"id": z.tid or f"{ids}{n + 1}", "x": int(round(x_mitte)), "y": int(round(y - o)),
                    "size": int(groesse), "value": z.wert, "color": z.farbe, "align": "center",
                    "bold": z.bold, "font": z.schrift})
        if z.et:
            aus[-1]["amp_font"] = z.et
        y += (u - o) + z.luft
    return aus


def umriss(eintraege: List[Dict]) -> Tuple[int, int]:
    """Top and bottom of the ink of a text stack, as ``stapel`` placed it."""
    oben, unten = 10 ** 6, -10 ** 6
    for t in eintraege:
        _, o, u = masse(t["font"], t["size"], t["value"])
        oben, unten = min(oben, t["y"] + o), max(unten, t["y"] + u)
    return oben, unten


class Bau:
    """Collects the elements of one template and writes its layer order."""

    def __init__(self, tid: str, name: str, canvas: Tuple[int, int], paper: str, grund: str):
        self.tid, self.name, self.canvas, self.paper = tid, name, canvas, paper
        self.unten: List[Tuple[str, Dict]] = []     # (kind, element) under the photos
        self.slots: List[Dict] = []
        self.oben: List[Tuple[str, Dict]] = []      # above the photos, under the texts
        self.texte: List[Dict] = []
        self.kunst: Dict[str, Callable[[], Image.Image]] = {}
        self._n = 0
        self.form_unten("rect", 0, 0, canvas[0], canvas[1], fill=grund, fid="grund")

    def _id(self, praefix: str) -> str:
        self._n += 1
        return f"{praefix}-{self._n}"

    def _form(self, art, x, y, w, h, fill="", stroke="", stroke_width=0, opacity=100, radius=0, fid=None):
        f = {"id": fid or self._id("form"), "kind": art, "x": int(x), "y": int(y), "w": int(w), "h": int(h),
             "fill": fill, "stroke": stroke, "stroke_width": int(stroke_width), "opacity": int(opacity)}
        if art == "rect":
            f["radius"] = int(radius)
        return f

    def form_unten(self, art, x, y, w, h, **kw):
        self.unten.append(("shape", self._form(art, x, y, w, h, **kw)))

    def form_oben(self, art, x, y, w, h, **kw):
        self.oben.append(("shape", self._form(art, x, y, w, h, **kw)))

    def linie(self, x, y, w, farbe, staerke=3, oben=True):
        """A horizontal rule ``staerke`` px thick (at least 0.25 mm)."""
        staerke = max(3, int(staerke))
        f = self._form("line", x, y - staerke, w, 2 * staerke, stroke=farbe, stroke_width=staerke)
        (self.oben if oben else self.unten).append(("shape", f))

    def bild(self, datei: str, x, y, w, h, macher: Callable[[], Image.Image], *, oben=True,
             opacity=100, rotation=0):
        # The check moves a picture back onto the sheet; doing it here keeps
        # the design file and the saved template the same.
        x = max(0, min(self.canvas[0] - w, x))
        y = max(0, min(self.canvas[1] - h, y))
        b = {"id": self._id("bild"), "file": datei, "x": int(x), "y": int(y), "w": int(w), "h": int(h),
             "opacity": int(opacity)}
        if rotation:
            b["rotation"] = rotation
        self.kunst[datei] = macher
        (self.oben if oben else self.unten).append(("image", b))

    def foto(self, x, y, w, h, shot, radius=0, *, matte: Optional[Tuple[str, int]] = None,
             kontur: Optional[Tuple[str, int]] = None):
        """A photo slot, optionally on a mat (``matte``) or with a keyline (``kontur``)."""
        if matte:
            farbe, rand = matte
            self.form_unten("rect", x - rand, y - rand, w + 2 * rand, h + 2 * rand, fill=farbe,
                            radius=radius + rand if radius else 0)
        if kontur:
            farbe, rand = kontur
            self.form_unten("rect", x - rand, y - rand, w + 2 * rand, h + 2 * rand, fill=farbe)
        self.slots.append({"id": f"foto-{len(self.slots) + 1}", "x": int(x), "y": int(y), "w": int(w),
                           "h": int(h), "shot": int(shot), "radius": int(radius)})

    def text(self, eintraege: List[Dict]):
        self.texte.extend(eintraege)

    def vorlage(self, **extra) -> Dict:
        shapes = [e for k, e in self.unten + self.oben if k == "shape"]
        images = [e for k, e in self.unten + self.oben if k == "image"]
        # Bottom to top: the background, the style's frame layer (hidden
        # under the design), what lies under the photos, the photos, the
        # artwork over them, the texts.
        order = ["grund", "overlay"]
        order += [e["id"] for _, e in self.unten if e["id"] != "grund"]
        order += [s["id"] for s in self.slots]
        order += [e["id"] for _, e in self.oben]
        order += [t["id"] for t in self.texte]
        t = {"id": self.tid, "name": self.name, "paper": self.paper, "canvas": list(self.canvas),
             "background": "@hintergrund", "slots": self.slots, "texts": self.texte,
             "shapes": shapes, "order": order, "safe_area": 4.0}
        if images:
            t["images"] = images
        t.update(extra)
        t["_kunst"] = dict(self.kunst)     # taken off by the builder before the check
        return t
