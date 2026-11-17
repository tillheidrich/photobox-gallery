"""Eigene Druckvorlagen prüfen — dieselbe Datei in Galerie und Box.

ACHTUNG: Diese Datei liegt zweimal im Baum (app/vorlagen_kern.py und
booth/boothcore/media/vorlagen_kern.py) und wird von tools/sync_shared.py
abgeglichen; ein Test vergleicht beide Fassungen byteweise. Geändert wird die
Fassung in app/, danach das Werkzeug laufen lassen.

Der Vorlageneditor läuft an zwei Stellen — im Web-Admin und an der Box —, und
beide speichern dasselbe JSON. Geprüft wird deshalb mit demselben Code: Slots
außerhalb des Blattes, Schriftgrößen von 4000 Punkt oder Farben, die keine
sind, sollen an keiner der beiden Stellen durchkommen.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _papier(name: str, canvas: List[int], mm: List[float], familie: str, *,
            anschnitt_mm: float = 0.0, sicher_mm: float = 4.0, **extra: Any) -> Dict[str, Any]:
    """One paper: trim size in mm, canvas = trim at 300 dpi, aspect family.

    ``anschnitt_mm`` is the bleed the renderer adds around the trim when it
    fits a design to this paper (``fuer_papier``); 0 = the sheet is rendered
    at trim size and the printer driver's own overprint does the rest, which
    is how every paper printed until November 2026. ``sicher_mm`` is the
    margin a design keeps free of anything important.
    """
    return dict({"name": name, "canvas": canvas, "mm": mm, "familie": familie,
                 "lage": "quer" if canvas[0] > canvas[1] else "hoch",
                 "anschnitt_mm": anschnitt_mm, "sicher_mm": sicher_mm}, **extra)


#: Die Blattformate, die der Editor anbietet. Alles bei 300 dpi.
#:
#: The bleed of the newer papers (2 mm) is what the DNP drivers print beyond
#: the trim on 4×6: 1844 px for 1800 (Gutenprint, print-dyesub.c, dnpds620).
#: The safe margins are a conservative 4 mm wherever no maker states one.
PAPIERE = {
    "10x15": _papier("Postkarte 10×15 hoch", [1200, 1800], [101.6, 152.4], "2:3"),
    "10x15-quer": _papier("Postkarte 10×15 quer", [1800, 1200], [152.4, 101.6], "2:3"),
    # The classic sheet with two strips drawn side by side by the template itself.
    "streifen": _papier("Streifen 2×6″ (doppelt auf 10×15)", [1200, 1800], [101.6, 152.4], "2:3"),
    "quadrat": _papier("Quadrat 10×10", [1200, 1200], [101.6, 101.6], "1:1"),
    "13x18": _papier("13×18 hoch", [1535, 2126], [130.0, 180.0], "5:7"),
    "13x18-quer": _papier("13×18 quer", [2126, 1535], [180.0, 130.0], "5:7"),
    # One strip, designed once; printed twice side by side on a 10×15 sheet
    # that the printer (DNP, Mitsubishi, HiTi: "2 inch cut") cuts in the middle.
    "streifen-2x6": _papier("Fotostreifen 5×15 (2×6″), zweimal auf 10×15", [600, 1800], [50.8, 152.4], "1:3",
                            anschnitt_mm=0.0, zwei_auf={"blatt": "10x15", "canvas": [1200, 1800]}),
    "15x20": _papier("15×20 hoch (6×8″)", [1800, 2400], [152.4, 203.2], "3:4", anschnitt_mm=2.0),
    "15x20-quer": _papier("15×20 quer (6×8″)", [2400, 1800], [203.2, 152.4], "3:4", anschnitt_mm=2.0),
    "15x23": _papier("15×23 hoch (6×9″)", [1800, 2700], [152.4, 228.6], "2:3", anschnitt_mm=2.0),
    "11x20": _papier("11×20 hoch (4,5×8″, QW410)", [1350, 2400], [114.3, 203.2], "9:16", anschnitt_mm=2.0),
    "20x25": _papier("20×25 hoch (8×10″, DS820)", [2400, 3000], [203.2, 254.0], "4:5", anschnitt_mm=2.0),
}

#: The aspect families a design can be made for, short side : long side.
FAMILIEN = {"2:3": 2 / 3, "1:3": 1 / 3, "5:7": 5 / 7, "3:4": 3 / 4, "1:1": 1.0, "9:16": 9 / 16,
            "4:5": 4 / 5}

MAX_SLOTS = 12
MAX_TEXTE = 6
MIN_KANTE = 80

_ID = re.compile(r"[^a-z0-9-]+")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_TOKEN = ("@hintergrund", "@schrift", "@gedaempft", "@akzent")


def slug(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return _ID.sub("-", text).strip("-")[:40] or "vorlage"


def _zahl(wert: Any, klein: int, gross: int, standard: int = 0) -> int:
    try:
        return max(klein, min(gross, int(round(float(wert)))))
    except (TypeError, ValueError):
        return standard


def _farbe(wert: Any, standard: str = "@schrift") -> str:
    wert = str(wert or "").strip()
    if wert in _TOKEN:
        return wert
    return wert if _HEX.match(wert) else standard


# ── Layers ────────────────────────────────────────────────
# Every element has an id; ``order`` lists the ids bottom to top. Templates
# without ids (the builtin catalogue, plugins, files from before the layer
# editor) get implicit ones — the same scheme ``sichereIds`` in the editor
# uses — and without ``order`` they are drawn in the sequence the renderer
# always used: photos, overlay and style frame, texts, QR code. "overlay" is
# a layer of its own even when the template names no overlay picture: it is
# where the style's frame line is drawn.

#: Element lists in the order their implicit ids are handed out.
LISTEN = (("slot", "slots"), ("text", "texts"), ("image", "images"), ("shape", "shapes"))
#: The default drawing order of the kinds, bottom to top. Pictures and shapes
#: are newer than the rest; by default they sit between frame and texts.
STANDARD_FOLGE = ("slot", "overlay", "image", "shape", "text", "qr")

MAX_FORMEN = 20
MAX_BILDER = 10
FORMEN = ("rect", "ellipse", "line")
#: Text families the renderer ships; anything else must be an uploaded file.
SCHRIFTEN = ("system", "display", "serif")
BILD_ENDUNGEN = (".png", ".jpg", ".jpeg", ".webp")
SCHRIFT_ENDUNGEN = (".ttf", ".otf")
_DATEI = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def datei_name(wert: Any, endungen: Tuple[str, ...]) -> Optional[str]:
    """A plain file name among the uploaded media — never a path, never a URL."""
    name = str(wert or "").strip()
    if not _DATEI.match(name) or ".." in name or not name.lower().endswith(endungen):
        return None
    return name


def _farbe_oder_leer(wert: Any) -> str:
    """A colour, a style token, or "" for none (a shape without outline)."""
    wert = str(wert or "").strip()
    return wert if (wert in _TOKEN or _HEX.match(wert)) else ""


#: How a photo goes into a slot (``fit``); equal to ``vorlagen_render.EINPASSEN``
#: (a test compares them; this module must not import Pillow).
EINPASSEN = ("auto", "beschneiden", "einpassen")


def _hintergrund(wert: Any) -> str:
    """The sheet's own background: a style token, ``#rrggbb`` or a picture among the media."""
    wert = str(wert or "").strip()
    if wert in _TOKEN or _HEX.match(wert):
        return wert
    return datei_name(wert, BILD_ENDUNGEN) or "@hintergrund"


_EL_ID = re.compile(r"[^a-z0-9-]+")


def _ids(vorlage: Dict[str, Any]) -> List[Tuple[str, str, Optional[Dict[str, Any]], int]]:
    """(kind, id, element, index) for every element, ids made unique."""
    vergeben = {"qr", "overlay"}
    aus = []
    for art, schluessel in LISTEN:
        for i, e in enumerate(vorlage.get(schluessel) or []):
            if not isinstance(e, dict):
                continue
            eid = _EL_ID.sub("-", str(e.get("id") or "").lower())[:24]
            if not eid or eid in vergeben:
                vorschlag = eid = f"{art}-{i + 1}"
                n = 2
                while eid in vergeben:
                    eid = f"{vorschlag}-{n}"
                    n += 1
            vergeben.add(eid)
            aus.append((art, eid, e, i))
    aus.append(("overlay", "overlay", None, 0))
    if isinstance(vorlage.get("qr"), dict):
        aus.append(("qr", "qr", vorlage["qr"], 0))
    return aus


def ebenen(vorlage: Dict[str, Any]) -> List[Tuple[str, str, Optional[Dict[str, Any]], int]]:
    """The layers bottom to top as (kind, id, element, index in its list).

    The renderer draws in this order and the check writes it as ``order``.
    Ids the order does not know are ignored; layers the order forgot are
    still drawn — on top, except the overlay, which goes right above the
    last photo where it always was.
    """
    alle = _ids(vorlage)
    rang = {art: i for i, art in enumerate(STANDARD_FOLGE)}
    standard = sorted(alle, key=lambda e: rang.get(e[0], len(rang)))
    gewuenscht = vorlage.get("order")
    if not isinstance(gewuenscht, list):
        return standard
    nach_id = {e[1]: e for e in alle}
    folge, gesehen = [], set()
    for eid in gewuenscht:
        eid = str(eid)
        if eid in nach_id and eid not in gesehen:
            folge.append(nach_id[eid])
            gesehen.add(eid)
    for e in standard:
        if e[1] in gesehen:
            continue
        if e[0] == "overlay":
            letzte_foto = max((i for i, f in enumerate(folge) if f[0] == "slot"), default=-1)
            folge.insert(letzte_foto + 1, e)
        else:
            folge.append(e)
    return folge


def _ebenen_felder(roh: Dict[str, Any], eintrag: Dict[str, Any]) -> Dict[str, Any]:
    """What every layer may carry besides its geometry. Defaults stay out of the file."""
    name = str(roh.get("name") or "").strip()[:40]
    if name:
        eintrag["name"] = name
    # "nur_mit_qr": printed only when the sheet carries a QR code — the
    # "scan the code" caption next to it, a frame around it. Older renderers
    # ignore the flag and print the layer as before.
    for flag in ("hidden", "locked", "nur_mit_qr"):
        if roh.get(flag) is True:
            eintrag[flag] = True
    # Degrees, clockwise, about the element's middle (the renderer and CSS
    # agree on the direction). Stored 0 <= r < 360 to one decimal.
    try:
        winkel = round(float(roh.get("rotation") or 0) % 360, 1)
    except (TypeError, ValueError):
        winkel = 0.0
    if winkel and winkel < 360:
        eintrag["rotation"] = int(winkel) if winkel.is_integer() else winkel
    return eintrag


def pruefe(roh: Dict[str, Any], *, medien: Optional[Path] = None,
           texte: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Aus dem, was der Editor schickt, eine gültige Vorlage machen.

    Zurück kommt die bereinigte Vorlage und eine Liste dessen, was dabei
    geradegerückt wurde — der Editor zeigt das an, statt es zu verschweigen.
    Dazu kommt, welche Zeile mit den Beispieltexten (``texte``, sonst
    „Mia & Tom") nicht in ihr Feld passt; Zeilen in einer hochgeladenen
    Schrift werden nur gemessen, wenn ``medien`` die Datei hat.
    """
    vorlage, hinweise = _pruefe(roh)
    try:
        from . import vorlagen_render
    except ImportError:                    # without Pillow there is nothing to measure with
        return vorlage, hinweise
    return vorlage, hinweise + vorlagen_render.textwarnungen(vorlage, texte, medien)


# ── The gate every template passes on its way in ──────────
# ``pruefe`` repairs what the editor sends (it clamps a slot dragged over the
# edge and says so). A file that arrives from elsewhere — an upload, MCP, a
# package, a customer's layout, a booth, a restore, a file someone put into
# the booth's media folder — first passes ``annehmen``: anything that is not
# a template of this format is refused with the fields named, and only then
# is it normalised by ``pruefe``. The rules, in one place:
#
# * the file is a JSON object of at most MAX_DATEI_BYTES;
# * at most MAX_SLOTS slots, MAX_TEXTE texts, MAX_FORMEN shapes,
#   MAX_BILDER pictures, one QR code, MAX_ORDER entries in ``order``;
# * numbers are JSON numbers (no strings, no booleans, finite); positions lie
#   on the paper (0 … width / height), sizes are at most the paper and at
#   least the documented minimum; an element that reaches over the edge is
#   moved back onto the paper by ``pruefe`` and that is said in the hints;
# * texts and names have the documented lengths and no control characters
#   (a line break is allowed in a text's value);
# * colours are ``#rrggbb`` or one of the four style tokens; fonts are
#   ``system``/``display``/``serif`` or the plain name of an uploaded
#   ``.ttf``/``.otf``; pictures, overlay and a picture background are plain
#   file names among the media (no path, no ``..``, no URL);
# * keys this format does not know are dropped and named in the hints —
#   everywhere, so a file never carries something the renderer might pick up.

MAX_DATEI_BYTES = 256 * 1024
MAX_ORDER = 200
_STEUER = re.compile(r"[\x00-\x1f\x7f]")
_STEUER_AUSSER_ZEILE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")

_EBENE = {"id", "name", "hidden", "locked", "nur_mit_qr", "rotation"}
FELDER = {
    "": {"id", "name", "paper", "canvas", "background", "plain", "slots", "texts", "shapes", "images",
         "qr", "overlay", "order", "shots", "updated_at", "safe_area"},
    "slots": _EBENE | {"x", "y", "w", "h", "shot", "radius", "fit"},
    "texts": _EBENE | {"x", "y", "w", "h", "size", "bold", "align", "value", "color", "font", "amp_font",
                       "line_height", "opacity"},
    "shapes": _EBENE | {"kind", "x", "y", "w", "h", "fill", "stroke", "stroke_width", "opacity", "radius"},
    "images": _EBENE | {"file", "x", "y", "w", "h", "opacity"},
    "qr": _EBENE | {"x", "y", "size"},
}


class VorlageAbgelehnt(ValueError):
    """A template that is refused, with every reason (``gruende``) in plain German."""

    def __init__(self, gruende: List[str]):
        self.gruende = list(gruende) or ["keine gültige Vorlage"]
        gezeigt = "; ".join(self.gruende[:10])
        rest = len(self.gruende) - 10
        super().__init__("Keine gültige Vorlage — " + gezeigt + (f"; und {rest} weitere" if rest > 0 else ""))


def _ist_zahl(wert: Any) -> bool:
    return isinstance(wert, (int, float)) and not isinstance(wert, bool) and math.isfinite(wert)


def _leer(wert: Any) -> bool:
    return wert is None or wert == ""


class _Gate:
    def __init__(self) -> None:
        self.gruende: List[str] = []
        self.unbekannt: List[str] = []

    def nein(self, feld: str, warum: str) -> None:
        self.gruende.append(f"{feld} {warum}")

    def felder(self, d: Dict[str, Any], art: str, wo: str) -> None:
        for k in d:
            if k not in FELDER[art]:
                self.unbekannt.append(f"{wo}.{k}" if wo else str(k))

    def zahl(self, d: Dict[str, Any], k: str, wo: str, klein: float, gross: float, *,
             ganz: bool = False) -> Optional[float]:
        if _leer(d.get(k)):
            return None
        w = d[k]
        if not _ist_zahl(w):
            self.nein(f"{wo}.{k}", "ist keine Zahl")
            return None
        if ganz and float(w) != int(w):
            self.nein(f"{wo}.{k}", "muss eine ganze Zahl sein")
            return None
        if w < klein or w > gross:
            self.nein(f"{wo}.{k}", f"liegt außerhalb von {_z(klein)} bis {_z(gross)}")
            return None
        return float(w)

    def text(self, d: Dict[str, Any], k: str, wo: str, laenge: int, *, zeilen: bool = False) -> Optional[str]:
        if d.get(k) is None:
            return None
        w = d[k]
        if not isinstance(w, str):
            self.nein(f"{wo}.{k}", "ist kein Text")
            return None
        if len(w) > laenge:
            self.nein(f"{wo}.{k}", f"ist länger als {laenge} Zeichen")
            return None
        if (_STEUER_AUSSER_ZEILE if zeilen else _STEUER).search(w):
            self.nein(f"{wo}.{k}", "enthält Steuerzeichen")
            return None
        return w

    def wahr(self, d: Dict[str, Any], k: str, wo: str) -> None:
        if d.get(k) is not None and not isinstance(d[k], bool):
            self.nein(f"{wo}.{k}", "muss true oder false sein")

    def wahl(self, d: Dict[str, Any], k: str, wo: str, werte: Tuple[str, ...]) -> None:
        if _leer(d.get(k)):
            return
        if d[k] not in werte:
            self.nein(f"{wo}.{k}", "muss " + " oder ".join(f"„{x}“" for x in werte) + " sein")

    def farbe(self, d: Dict[str, Any], k: str, wo: str, *, leer_ok: bool = False, bild_ok: bool = False) -> None:
        w = self.text(d, k, wo, 120)
        if w is None or (w == "" and leer_ok):
            return
        w = w.strip()
        if w in _TOKEN or _HEX.match(w) or (bild_ok and datei_name(w, BILD_ENDUNGEN)):
            return
        erlaubt = "#rrggbb, " + ", ".join(_TOKEN) + (" oder ein Bild aus den Medien" if bild_ok else "")
        self.nein(f"{wo}.{k}", f"ist keine erlaubte Farbe ({erlaubt})")

    def datei(self, d: Dict[str, Any], k: str, wo: str, endungen: Tuple[str, ...], *,
              familien: Tuple[str, ...] = ()) -> None:
        w = self.text(d, k, wo, 120)
        if w is None or w == "" or w.strip() in familien:
            return
        if not datei_name(w, endungen):
            self.nein(f"{wo}.{k}", "muss ein Dateiname aus den Medien sein (" + "/".join(endungen)
                      + "; kein Pfad, keine Adresse)" + (", oder " + "/".join(familien) if familien else ""))

    def ebene(self, d: Dict[str, Any], wo: str) -> None:
        self.text(d, "id", wo, 40)
        self.text(d, "name", wo, 40)
        for flag in ("hidden", "locked", "nur_mit_qr"):
            self.wahr(d, flag, wo)
        self.zahl(d, "rotation", wo, -3600, 3600)

    def kasten(self, d: Dict[str, Any], wo: str, breite: int, hoehe: int, kante: Tuple[int, int]) -> None:
        """Position on the paper, size between ``kante`` and the paper."""
        self.zahl(d, "x", wo, 0, breite)
        self.zahl(d, "y", wo, 0, hoehe)
        self.zahl(d, "w", wo, kante[0], breite)
        self.zahl(d, "h", wo, kante[1], hoehe)


def _z(wert: float) -> str:
    return str(int(wert)) if float(wert).is_integer() else str(wert)


def fehler(roh: Any) -> Tuple[List[str], List[str]]:
    """(reasons to refuse, unknown keys) of a raw template; nothing is changed."""
    g = _Gate()
    if not isinstance(roh, dict):
        return ["Die Datei ist kein JSON-Objekt."], []
    if roh.get("kind") == "motion_frame":
        return ["Das ist ein Rahmen für Boomerang/Video, keine Druckvorlage."], []
    g.felder(roh, "", "")
    g.text(roh, "id", "id", 80)
    g.text(roh, "name", "name", 60)
    papier = roh.get("paper")
    if not _leer(papier) and papier not in PAPIERE:
        g.nein("paper", "ist kein bekanntes Blattformat (" + ", ".join(PAPIERE) + ")")
        papier = None
    breite, hoehe = PAPIERE[papier or "10x15"]["canvas"]
    canvas = roh.get("canvas")
    if not _leer(canvas):
        if not (isinstance(canvas, list) and len(canvas) == 2 and all(_ist_zahl(c) for c in canvas)):
            g.nein("canvas", "muss [Breite, Höhe] in Pixeln sein")
        elif not all(400 <= c <= 4000 for c in canvas):
            g.nein("canvas", "liegt außerhalb von 400 bis 4000 Pixeln je Kante")
        else:
            breite, hoehe = int(round(canvas[0])), int(round(canvas[1]))
    g.farbe(roh, "background", "background", bild_ok=True)
    g.wahr(roh, "plain", "plain")
    g.zahl(roh, "shots", "shots", 0, MAX_SLOTS, ganz=True)
    g.zahl(roh, "updated_at", "updated_at", 0, 4102444800)
    g.zahl(roh, "safe_area", "safe_area", 0, 15)
    g.datei(roh, "overlay", "overlay", BILD_ENDUNGEN)
    order = roh.get("order")
    if order is not None:
        if not isinstance(order, list):
            g.nein("order", "muss eine Liste sein")
        elif len(order) > MAX_ORDER:
            g.nein("order", f"hat mehr als {MAX_ORDER} Einträge")
        else:
            for i, eid in enumerate(order):
                if not isinstance(eid, str) or len(eid) > 40 or _STEUER.search(eid):
                    g.nein(f"order[{i}]", "muss eine Kennung bis 40 Zeichen sein")
    kurz = min(breite, hoehe)
    grenzen = (("slots", MAX_SLOTS), ("texts", MAX_TEXTE), ("shapes", MAX_FORMEN), ("images", MAX_BILDER))
    for liste, hoechstens in grenzen:
        eintraege = roh.get(liste)
        if eintraege is None:
            continue
        if not isinstance(eintraege, list):
            g.nein(liste, "muss eine Liste sein")
            continue
        if len(eintraege) > hoechstens:
            g.nein(liste, f"hat {len(eintraege)} Einträge, erlaubt sind höchstens {hoechstens}")
            continue
        for i, e in enumerate(eintraege):
            wo = f"{liste}[{i}]"
            if not isinstance(e, dict):
                g.nein(wo, "ist kein Objekt")
                continue
            g.felder(e, liste, wo)
            g.ebene(e, wo)
            if liste == "slots":
                g.kasten(e, wo, breite, hoehe, (MIN_KANTE, MIN_KANTE))
                g.zahl(e, "shot", wo, 0, MAX_SLOTS - 1, ganz=True)
                if e.get("radius") != "@rund":
                    g.zahl(e, "radius", wo, 0, kurz // 2)
                g.wahl(e, "fit", wo, EINPASSEN)
            elif liste == "texts":
                wert = g.text(e, "value", wo, 120, zeilen=True)
                if wert is None and "value" not in e:
                    g.nein(f"{wo}.value", "fehlt")
                g.zahl(e, "x", wo, 0, breite)
                g.zahl(e, "y", wo, 0, hoehe)
                g.zahl(e, "w", wo, 0, breite)
                g.zahl(e, "h", wo, 0, hoehe)
                g.zahl(e, "size", wo, 12, 200)
                g.wahr(e, "bold", wo)
                g.wahl(e, "align", wo, ("left", "center", "right"))
                g.farbe(e, "color", wo)
                g.datei(e, "font", wo, SCHRIFT_ENDUNGEN, familien=SCHRIFTEN)
                g.datei(e, "amp_font", wo, SCHRIFT_ENDUNGEN, familien=SCHRIFTEN)
                g.zahl(e, "line_height", wo, 0.8, 3.0)
                g.zahl(e, "opacity", wo, 0, 100)
            elif liste == "shapes":
                g.wahl(e, "kind", wo, FORMEN)
                g.kasten(e, wo, breite, hoehe, (2, 2))
                g.farbe(e, "fill", wo, leer_ok=True)
                g.farbe(e, "stroke", wo, leer_ok=True)
                g.zahl(e, "stroke_width", wo, 0, 200)
                g.zahl(e, "opacity", wo, 0, 100)
                g.zahl(e, "radius", wo, 0, kurz // 2)
            else:
                if _leer(e.get("file")):
                    g.nein(f"{wo}.file", "fehlt")
                g.datei(e, "file", wo, BILD_ENDUNGEN)
                g.kasten(e, wo, breite, hoehe, (10, 10))
                g.zahl(e, "opacity", wo, 0, 100)
    qr = roh.get("qr")
    if qr not in (None, False):
        if not isinstance(qr, dict):
            g.nein("qr", "ist kein Objekt")
        else:
            g.felder(qr, "qr", "qr")
            g.ebene(qr, "qr")
            g.zahl(qr, "x", "qr", 0, breite)
            g.zahl(qr, "y", "qr", 0, hoehe)
            g.zahl(qr, "size", "qr", 80, kurz // 2)
    return g.gruende, g.unbekannt


def annehmen(daten: Any, *, medien: Optional[Path] = None, texte: Optional[Dict[str, str]] = None,
             messen: bool = True, zeit: Optional[int] = None) -> Tuple[Dict[str, Any], List[str]]:
    """The one way in for a template: bytes, text or an object -> (template, hints).

    Raises :class:`VorlageAbgelehnt` with every reason when it is not a
    template of this format (see the rules above); otherwise the template
    as ``pruefe`` makes it, with the dropped unknown keys named in the hints.
    ``messen=False`` skips measuring the sample lines (a booth reading its
    files on every state poll needs the gate, not the editor's hints).
    """
    if isinstance(daten, (bytes, bytearray, str)):
        roh_bytes = daten.encode("utf-8") if isinstance(daten, str) else bytes(daten)
        if len(roh_bytes) > MAX_DATEI_BYTES:
            raise VorlageAbgelehnt([f"Die Datei ist größer als {MAX_DATEI_BYTES // 1024} KB."])
        try:
            roh = json.loads(roh_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise VorlageAbgelehnt(["Die Datei ist kein lesbares JSON."]) from None
    else:
        roh = daten
        try:
            groesse = len(json.dumps(roh, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            raise VorlageAbgelehnt(["Die Vorlage lässt sich nicht als JSON schreiben."]) from None
        if groesse > MAX_DATEI_BYTES:
            raise VorlageAbgelehnt([f"Die Vorlage ist größer als {MAX_DATEI_BYTES // 1024} KB."])
    gruende, unbekannt = fehler(roh)
    if gruende:
        raise VorlageAbgelehnt(gruende)
    if zeit is not None and not roh.get("updated_at"):
        roh = dict(roh, updated_at=zeit)
    vorlage, hinweise = pruefe(roh, medien=medien, texte=texte) if messen else _pruefe(roh)
    if unbekannt:
        hinweise = ["Unbekannte Felder weggelassen: " + ", ".join(unbekannt[:20])
                    + (" …" if len(unbekannt) > 20 else "")] + hinweise
    return vorlage, hinweise


def lade(pfad: Path) -> Dict[str, Any]:
    """A template file from disk through the gate (raises ``VorlageAbgelehnt`` or ``OSError``)."""
    pfad = Path(pfad)
    if pfad.stat().st_size > MAX_DATEI_BYTES:
        raise VorlageAbgelehnt([f"Die Datei ist größer als {MAX_DATEI_BYTES // 1024} KB."])
    # A file without ``updated_at`` gets its modification time, not "now":
    # the booth's layout pictures are cached by the template's content.
    return annehmen(pfad.read_bytes(), messen=False, zeit=int(pfad.stat().st_mtime))[0]


def _pruefe(roh: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    hinweise: List[str] = []
    roh = dict(roh or {})

    papier = str(roh.get("paper") or "10x15")
    if papier not in PAPIERE:
        papier, hinweise = "10x15", hinweise + ["Unbekanntes Blattformat — 10×15 hoch genommen."]
    canvas = roh.get("canvas") or PAPIERE[papier]["canvas"]
    breite = _zahl(canvas[0] if len(canvas) > 0 else 0, 400, 4000, PAPIERE[papier]["canvas"][0])
    hoehe = _zahl(canvas[1] if len(canvas) > 1 else 0, 400, 4000, PAPIERE[papier]["canvas"][1])

    name = str(roh.get("name") or "Eigene Vorlage").strip()[:60] or "Eigene Vorlage"
    tid = slug(roh.get("id") or name)

    slots = []
    for s in (roh.get("slots") or [])[:MAX_SLOTS]:
        w = _zahl(s.get("w"), MIN_KANTE, breite, breite // 2)
        h = _zahl(s.get("h"), MIN_KANTE, hoehe, hoehe // 2)
        x = _zahl(s.get("x"), 0, max(0, breite - w), 0)
        y = _zahl(s.get("y"), 0, max(0, hoehe - h), 0)
        eintrag = {"x": x, "y": y, "w": w, "h": h, "shot": _zahl(s.get("shot"), 0, MAX_SLOTS - 1, 0)}
        radius = s.get("radius", "@rund")
        eintrag["radius"] = "@rund" if radius == "@rund" else _zahl(radius, 0, min(w, h) // 2, 0)
        if s.get("id"):
            eintrag["id"] = str(s["id"])
        # How a photo goes into this slot; the renderer uses it before the
        # booth's own setting (vorlagen_render._foto). Kept only when set.
        if s.get("fit") in EINPASSEN:
            eintrag["fit"] = s["fit"]
        slots.append(_ebenen_felder(s, eintrag))
    if not slots:
        slots = [{"x": 60, "y": 60, "w": breite - 120, "h": hoehe - 400, "shot": 0, "radius": "@rund"}]
        hinweise.append("Ohne Bildfeld gibt es nichts zu zeigen — eines wurde angelegt.")

    # Die Aufnahmen müssen lückenlos von 0 an zählen, sonst wartet die Box auf
    # ein Bild, das keinen Platz hat.
    benutzt = sorted({s["shot"] for s in slots})
    umbenannt = {alt: neu for neu, alt in enumerate(benutzt)}
    if list(umbenannt.keys()) != list(umbenannt.values()):
        hinweise.append("Die Nummern der Aufnahmen wurden lückenlos durchgezählt.")
    for s in slots:
        s["shot"] = umbenannt[s["shot"]]

    texte = []
    for t in (roh.get("texts") or [])[:MAX_TEXTE]:
        wert = str(t.get("value") or "").strip()[:120]
        if not wert:
            continue
        ausrichtung = t.get("align") if t.get("align") in ("left", "center", "right") else "left"
        eintrag = {
            "x": _zahl(t.get("x"), 0, breite, 60), "y": _zahl(t.get("y"), 0, hoehe - 20, hoehe - 200),
            "size": _zahl(t.get("size"), 12, 200, 48), "bold": bool(t.get("bold", True)),
            "align": ausrichtung, "value": wert, "color": _farbe(t.get("color")),
        }
        if t.get("id"):
            eintrag["id"] = str(t["id"])
        # The line's field (optional): the renderer sets a line that is too
        # long for it smaller or in two lines (vorlagen_render.textsatz).
        # Without it the field reaches to the paper's safe margin.
        for kante, groesst in (("w", breite), ("h", hoehe)):
            if t.get(kante) not in (None, "", 0):
                eintrag[kante] = _zahl(t.get(kante), 20, groesst, groesst)
        familie = str(t.get("font") or "").strip()
        if familie in SCHRIFTEN or datei_name(familie, SCHRIFT_ENDUNGEN):
            eintrag["font"] = familie
        # The "&" of this line in a plainer font: a script's swash ampersand
        # reads like "Et" on a print.
        et = str(t.get("amp_font") or "").strip()
        if et in SCHRIFTEN or datei_name(et, SCHRIFT_ENDUNGEN):
            eintrag["amp_font"] = et
        if t.get("line_height") not in (None, ""):
            try:
                eintrag["line_height"] = round(max(0.8, min(3.0, float(t["line_height"]))), 2)
            except (TypeError, ValueError):
                pass
        if _zahl(t.get("opacity"), 0, 100, 100) < 100:
            eintrag["opacity"] = _zahl(t.get("opacity"), 0, 100, 100)
        texte.append(_ebenen_felder(t, eintrag))

    formen = []
    for f in (roh.get("shapes") or [])[:MAX_FORMEN]:
        if not isinstance(f, dict):
            continue
        w = _zahl(f.get("w"), 2, breite, 200)
        h = _zahl(f.get("h"), 2, hoehe, 200)
        art = f.get("kind") if f.get("kind") in FORMEN else "rect"
        eintrag = {"kind": art, "x": _zahl(f.get("x"), 0, max(0, breite - w), 0),
                   "y": _zahl(f.get("y"), 0, max(0, hoehe - h), 0), "w": w, "h": h,
                   "fill": _farbe_oder_leer(f.get("fill")), "stroke": _farbe_oder_leer(f.get("stroke")),
                   "stroke_width": _zahl(f.get("stroke_width"), 0, 200, 0),
                   "opacity": _zahl(f.get("opacity"), 0, 100, 100)}
        if art == "rect":
            eintrag["radius"] = _zahl(f.get("radius"), 0, min(w, h) // 2, 0)
        if f.get("id"):
            eintrag["id"] = str(f["id"])
        formen.append(_ebenen_felder(f, eintrag))

    bilder = []
    for b in (roh.get("images") or [])[:MAX_BILDER]:
        if not isinstance(b, dict):
            continue
        datei = datei_name(b.get("file"), BILD_ENDUNGEN)
        if not datei:
            hinweise.append("Ein Bild ohne gültige Datei aus den Medien wurde weggelassen.")
            continue
        w = _zahl(b.get("w"), 10, breite, 300)
        h = _zahl(b.get("h"), 10, hoehe, 300)
        eintrag = {"file": datei, "x": _zahl(b.get("x"), 0, max(0, breite - w), 0),
                   "y": _zahl(b.get("y"), 0, max(0, hoehe - h), 0), "w": w, "h": h,
                   "opacity": _zahl(b.get("opacity"), 0, 100, 100)}
        if b.get("id"):
            eintrag["id"] = str(b["id"])
        bilder.append(_ebenen_felder(b, eintrag))

    vorlage: Dict[str, Any] = {
        "id": tid, "name": name, "paper": papier, "canvas": [breite, hoehe],
        "background": _hintergrund(roh.get("background")), "slots": slots, "texts": texte,
        "shots": len(benutzt) or 1,
        "updated_at": int(roh.get("updated_at") or time.time()),
    }
    # A plain sheet keeps its own paper tone and draws no style frame
    # (vorlagen_render.render_template); the built-in "Schlicht" family is one.
    if roh.get("plain") is True:
        vorlage["plain"] = True
    # The share of each side the printer may cut off — an editor guide only;
    # the renderer prints the whole sheet regardless.
    if roh.get("safe_area") not in (None, ""):
        try:
            vorlage["safe_area"] = round(max(0.0, min(15.0, float(roh["safe_area"]))), 1)
        except (TypeError, ValueError):
            pass
    if formen:
        vorlage["shapes"] = formen
    if bilder:
        vorlage["images"] = bilder
    qr = roh.get("qr")
    if qr:
        groesse = _zahl(qr.get("size"), 80, min(breite, hoehe) // 2, 200)
        vorlage["qr"] = {"size": groesse,
                         "x": _zahl(qr.get("x"), 0, max(0, breite - groesse), breite - groesse - 60),
                         "y": _zahl(qr.get("y"), 0, max(0, hoehe - groesse), hoehe - groesse - 60)}
        _ebenen_felder(qr, vorlage["qr"])
        vorlage["qr"].pop("name", None)
    if roh.get("overlay"):
        # A file name among the media, never a path: the renderer opens it
        # below the media folder.
        overlay = datei_name(roh["overlay"], BILD_ENDUNGEN)
        if overlay:
            vorlage["overlay"] = overlay
        else:
            hinweise.append("Ein Overlay ohne gültige Datei aus den Medien wurde weggelassen.")

    # Ids made unique and clean, then the order written out in full — the
    # file says how it is drawn, whoever reads it.
    if isinstance(roh.get("order"), list):
        vorlage["order"] = [str(x) for x in roh["order"][:200]]
    for _art, eid, element, _i in _ids(vorlage):
        if element is not None and element is not vorlage.get("qr"):
            element["id"] = eid
    # The raw order may name an id that was renamed just now; it then falls
    # back to the default position, which is the safe reading.
    vorlage["order"] = [e[1] for e in ebenen(vorlage)]
    return vorlage, hinweise


def dateiname(vorlage: Dict[str, Any]) -> str:
    return f"{vorlage['id']}.json"


def inhalt(vorlage: Dict[str, Any]) -> bytes:
    return (json.dumps(vorlage, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def pruefsumme(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


# ── One design, several papers ────────────────────────────
# A design is made for an aspect family (2:3 postcard, 1:3 strip, …), not for
# one printer's paper. Fitting it to another paper of the same family scales
# every element by one factor; a paper of another family would need a new
# layout, so it is refused instead of squeezed.

def familie_von(vorlage: Dict[str, Any]) -> str:
    """The family a template is made for: its paper's, else the nearest to its canvas."""
    papier = PAPIERE.get(str(vorlage.get("paper") or ""))
    if papier:
        return papier["familie"]
    w, h = (vorlage.get("canvas") or PAPIERE["10x15"]["canvas"])[:2]
    verhaeltnis = min(w, h) / max(w, h) if max(w, h) else 1.0
    return min(FAMILIEN, key=lambda f: abs(FAMILIEN[f] - verhaeltnis))


def _skaliere(element: Dict[str, Any], s: float, dx: int, dy: int, schluessel=("x", "y", "w", "h")):
    for k in schluessel:
        if k in element:
            versatz = dx if k == "x" else dy if k == "y" else 0
            element[k] = int(round(float(element[k]) * s)) + versatz


def fuer_papier(vorlage: Dict[str, Any], papier_id: str,
                anschnitt_mm: Optional[float] = None) -> Tuple[Dict[str, Any], List[str]]:
    """The template fitted to ``papier_id`` — same family only, original untouched.

    The design keeps its orientation (a portrait design stays portrait; the
    printer turns the sheet as it always did). Every element is scaled by
    one factor, chosen so the design covers the paper; the few pixels a
    neighbouring ratio differs by fall into the safe margin. With bleed
    (``anschnitt_mm``, by default the paper's own) the sheet grows by it on
    every side and whatever touches an edge of the trim is carried out to
    the edge of the bleed, so no white line can appear at the cut.
    """
    if papier_id not in PAPIERE:
        raise ValueError(f"Unbekanntes Papier: {str(papier_id)[:40]}")
    papier = PAPIERE[papier_id]
    quelle, ziel = familie_von(vorlage), papier["familie"]
    if quelle != ziel:
        raise ValueError(
            f"Diese Vorlage ist für das Seitenverhältnis {quelle} gestaltet, das Papier "
            f"„{papier['name']}“ hat {ziel}. Sie passt nicht ohne Neugestaltung — bitte eine "
            f"Vorlage im Format {ziel} wählen.")
    hinweise: List[str] = []
    neu = json.loads(json.dumps(vorlage))
    w, h = [int(x) for x in (vorlage.get("canvas") or PAPIERE["10x15"]["canvas"])[:2]]
    zw, zh = papier["canvas"]
    if (w > h) != (zw > zh) and w != h:
        zw, zh = zh, zw
    s = max(zw / w, zh / h)
    if abs(zw / w - zh / h) > 0.001:
        hinweise.append(f"Seitenverhältnis weicht leicht ab — der Rand wird um bis zu "
                        f"{round(abs(w * s - zw) / 2 + abs(h * s - zh) / 2)} px beschnitten.")
    b = int(round(float(papier["anschnitt_mm"] if anschnitt_mm is None else anschnitt_mm) / 25.4 * 300))
    dx = b - int(round((w * s - zw) / 2))
    dy = b - int(round((h * s - zh) / 2))
    for liste in ("slots", "shapes", "images"):
        for e in neu.get(liste) or []:
            rand = (e.get("x", 0) <= 0, e.get("y", 0) <= 0,
                    e.get("x", 0) + e.get("w", 0) >= w, e.get("y", 0) + e.get("h", 0) >= h)
            _skaliere(e, s, dx, dy)
            if b and liste != "images":
                # Carried into the bleed: pictures are fitted inside their box,
                # so a picture is left where it is.
                links, oben, rechts, unten = rand
                if links:
                    e["w"] += e["x"]
                    e["x"] = 0
                if oben:
                    e["h"] += e["y"]
                    e["y"] = 0
                if rechts:
                    e["w"] = zw + 2 * b - e["x"]
                if unten:
                    e["h"] = zh + 2 * b - e["y"]
            if "radius" in e and isinstance(e["radius"], (int, float)):
                e["radius"] = int(round(e["radius"] * s))
            if "stroke_width" in e:
                e["stroke_width"] = max(1, int(round(e["stroke_width"] * s))) if e["stroke_width"] else 0
    for t in neu.get("texts") or []:
        _skaliere(t, s, dx, dy, ("x", "y"))
        t["size"] = int(round(int(t.get("size", 48)) * s))
        for kante in ("w", "h"):
            if t.get(kante):
                t[kante] = int(round(float(t[kante]) * s))
    if isinstance(neu.get("qr"), dict):
        _skaliere(neu["qr"], s, dx, dy, ("x", "y"))
        neu["qr"]["size"] = int(round(neu["qr"].get("size", 200) * s))
    neu["paper"] = papier_id
    neu["canvas"] = [zw + 2 * b, zh + 2 * b]
    if b:
        neu["anschnitt_px"] = b
    return neu, hinweise
