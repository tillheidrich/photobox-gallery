"""Render a print template — the same file in the gallery and at the booth.

ACHTUNG: This file exists twice (app/vorlagen_render.py and
booth/boothcore/media/vorlagen_render.py); tools/sync_editor.py copies the
app/ version to the booth and a test compares both byte for byte. Change
the one in app/, then run the tool.

The booth prints with it (boothcore.media.compose re-exports everything
here), the gallery draws the template editor's preview with it. One
renderer, so the preview cannot show a sheet the booth would not print.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import qrcode
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageOps, ImageStat

from . import vorlagen_kern

PRINT_CANVAS = (1200, 1800)   # 4×6 Zoll bei 300 dpi ≈ SELPHY-Postkarte 100×148 mm

ASPECTS = {"4:5": 4 / 5, "9:16": 9 / 16, "3:2": 3 / 2, "2:3": 2 / 3, "1:1": 1.0, "16:9": 16 / 9}


# ── Stil: eine Quelle für alle Vorlagen ───────────────────
# Eine Vorlage beschreibt die Anordnung, nicht das Aussehen. Farben, Schrift und
# die beiden Textzeilen stehen im Stil — sonst müsste der Betreiber vierzehn
# JSON-Dateien anfassen, um seine Hausfarbe durchzusetzen. In der Vorlage steht
# @hintergrund, @schrift, @gedaempft, @akzent; beim Rendern wird eingesetzt,
# was der Stil sagt.
#
# Die Anlässe sind Stile, keine eigenen Vorlagen: Dasselbe Raster wird zur
# Geburtstags-, Hochzeits- oder Firmenkarte, indem der Stil wechselt. Das
# halbiert den Katalog und verdoppelt, was man damit machen kann.

STYLE_KEYS = ("preset", "background", "ink", "muted", "accent", "headline", "subline",
              "font", "radius", "frame")

#: Mitgelieferte Anlässe. „clean" ist der Hausstil aus dem Erscheinungsbild.
STYLE_PRESETS: Dict[str, Dict] = {
    "clean": {
        "background": "#0f0d09", "ink": "#f5f3f0", "muted": "#898580", "accent": "#e7ad59",
        "headline": "{event_name}", "subline": "{date}", "font": "system", "radius": 14, "frame": 0,
    },
    "hell": {
        "background": "#faf8f5", "ink": "#1a1714", "muted": "#6f6a63", "accent": "#b07d2b",
        "headline": "{event_name}", "subline": "{date}", "font": "system", "radius": 14, "frame": 0,
    },
    "geburtstag": {
        "background": "#151033", "ink": "#fdf7ff", "muted": "#a79ecb", "accent": "#ffbf3c",
        "headline": "Happy Birthday", "subline": "{event_name} · {date}", "font": "display",
        "radius": 22, "frame": 0,
    },
    "hochzeit": {
        "background": "#f7f3ec", "ink": "#2a2620", "muted": "#8a8073", "accent": "#b39258",
        "headline": "{event_name}", "subline": "{date}", "font": "serif", "radius": 0, "frame": 10,
    },
    "feier": {
        "background": "#110d1a", "ink": "#fbf6ff", "muted": "#9b8fb3", "accent": "#ff5f9e",
        "headline": "{event_name}", "subline": "{date}", "font": "display", "radius": 24, "frame": 0,
    },
    "firma": {
        "background": "#ffffff", "ink": "#101418", "muted": "#5d6772", "accent": "#1d5fd0",
        "headline": "{event_name}", "subline": "{date}", "font": "system", "radius": 6, "frame": 0,
    },
}

DEFAULT_STYLE = dict(STYLE_PRESETS["clean"], preset="clean")


def style(cfg: Optional[Dict] = None) -> Dict:
    """Der fertige Stil: Voreinstellung, darüber der Anlass, darüber Eigenes.

    So bleibt „ich will den Hochzeitsstil, aber in unserem Blau" eine Einstellung
    und keine neue Vorlage.
    """
    cfg = dict(cfg or {})
    preset = str(cfg.get("preset") or "clean")
    aus = dict(DEFAULT_STYLE)
    aus.update(STYLE_PRESETS.get(preset, STYLE_PRESETS["clean"]))
    aus["preset"] = preset if preset in STYLE_PRESETS else "clean"
    for key in STYLE_KEYS:
        if key == "preset":
            continue                      # oben schon geprüft, hier nicht wieder aufmachen
        wert = cfg.get(key)
        if key in ("radius", "frame"):
            if wert not in (None, ""):
                try:
                    aus[key] = max(0, min(80, int(wert)))
                except (TypeError, ValueError):
                    pass
        elif wert not in (None, ""):
            aus[key] = str(wert)[:120]
    return aus


#: Welcher Stilwert hinter welchem Platzhalter steckt.
_STYLE_TOKENS = {"@hintergrund": "background", "@schrift": "ink", "@gedaempft": "muted",
                 "@akzent": "accent"}


def _farbe(wert: Optional[str], st: Dict, fallback: str = "#ffffff") -> str:
    """Einen Farbwert auflösen: Platzhalter aus dem Stil, sonst wie angegeben."""
    wert = str(wert or "")
    key = _STYLE_TOKENS.get(wert)
    if key:
        return str(st.get(key) or fallback)
    return wert or fallback


def parse_aspect(value: str) -> float:
    if value in ASPECTS:
        return ASPECTS[value]
    try:
        a, b = value.split(":")
        return float(a) / float(b)
    except Exception:
        return ASPECTS["4:5"]


def crop_to_aspect(img: Image.Image, aspect: str, focus_x: float = 0.5, focus_y: float = 0.5) -> Image.Image:
    """Größtmöglicher Ausschnitt im Seitenverhältnis, um den Fokuspunkt zentriert."""
    ratio = parse_aspect(aspect)
    w, h = img.size
    if w / h > ratio:
        nw, nh = int(round(h * ratio)), h
    else:
        nw, nh = w, int(round(w / ratio))
    left = int(round((w - nw) * min(1.0, max(0.0, focus_x))))
    top = int(round((h - nh) * min(1.0, max(0.0, focus_y))))
    return img.crop((left, top, left + nw, top + nh))


#: Die Schriften der Marke reisen mit der Box — ein Abzug darf nicht davon
#: abhängen, was auf dem Windows-Rechner zufällig installiert ist.
_HIER = Path(__file__).resolve().parent
#: At the booth the cuts lie next to this file; in the gallery they are the
#: static cuts in app/static/fonts/ttf — the same four files.
FONT_DIR = next((p for p in (_HIER / "fonts", _HIER / "static" / "fonts" / "ttf") if p.is_dir()),
                _HIER / "fonts")

_FONT_FILES = {
    ("system", True): ["Inter-SemiBold.ttf"], ("system", False): ["Inter-Regular.ttf"],
    ("display", True): ["Archivo-Bold.ttf"], ("display", False): ["Archivo-SemiBold.ttf"],
}
_SYSTEM_FALLBACK = {
    True: ["segoeuib.ttf", "C:/Windows/Fonts/segoeuib.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    False: ["segoeui.ttf", "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
}
_SERIF_FALLBACK = {
    True: ["georgiab.ttf", "C:/Windows/Fonts/georgiab.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
           "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"],
    False: ["georgia.ttf", "C:/Windows/Fonts/georgia.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/System/Library/Fonts/Supplemental/Georgia.ttf"],
}


def load_font(size: int, bold: bool = True, family: str = "system", assets_dir: Optional[Path] = None):
    """Schriftschnitt für den Druck. Mitgeliefertes zuerst, dann das System.

    ``family`` may also name an uploaded TTF/OTF among the booth's media; it
    is one file, so "bold" does not choose a cut there.
    """
    family = str(family or "system")
    if family.lower().endswith((".ttf", ".otf")):
        if assets_dir and vorlagen_kern.datei_name(family, vorlagen_kern.SCHRIFT_ENDUNGEN):
            try:
                return ImageFont.truetype(str(Path(assets_dir) / family), size)
            except OSError:
                pass
        family = "system"                 # a missing file leaves a readable line
    kandidaten = [str(FONT_DIR / n) for n in _FONT_FILES.get((family, bool(bold)), [])]
    if family == "serif":
        kandidaten += _SERIF_FALLBACK[bool(bold)]
    kandidaten += _SYSTEM_FALLBACK[bool(bold)]
    # Last resort: the bundled fonts. Pillow's built-in bitmap font is a few
    # pixels high whatever size is asked for -- a headline nobody can read on
    # a print. A sans instead of the serif is a blemish; that is not.
    kandidaten += [str(FONT_DIR / n) for n in _FONT_FILES[("system", bool(bold))]]
    for n in kandidaten:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def qr_image(url: str, size: int, dark: str = "#000000", light: str = "#ffffff") -> Image.Image:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2, box_size=10)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color=dark, back_color=light).convert("RGB")
    return img.resize((size, size), Image.Resampling.NEAREST)


def _background(canvas: Sequence[int], bg: Optional[str], assets_dir: Optional[Path]) -> Image.Image:
    size = (int(canvas[0]), int(canvas[1]))
    if bg and not bg.startswith("#") and assets_dir:
        p = (assets_dir / bg)
        if p.exists():
            with Image.open(p) as im:
                return ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), size, Image.Resampling.LANCZOS)
    try:
        color = ImageColor.getrgb(bg or "#111111")
    except ValueError:
        color = (17, 17, 17)
    return Image.new("RGB", size, color)


#: Wie ein Foto in ein Bildfeld kommt, wenn die Lagen nicht zusammenpassen.
#:   "auto"        — passen die Lagen, wird beschnitten; passen sie nicht,
#:                   wird das ganze Foto eingepasst. Die sichere Vorgabe.
#:   "beschneiden" — immer formatfüllend, mittig. Das Verhalten bis 20.09.2026.
#:   "einpassen"   — immer ganz sichtbar, der Rest bleibt Hintergrund.
EINPASSEN = ("auto", "beschneiden", "einpassen")

#: Wo der Ausschnitt sitzt, wenn über Kreuz beschnitten wird. 0.5 wäre die
#: Mitte — und die Mitte trifft den Bauch. Gerechnet für den Regelfall: Bei
#: einem stehenden 3:2-Foto in einem liegenden 3:2-Feld bleiben 44 % der Höhe
#: übrig. Sitzt der Kopf bei 10 % und das Kinn bei 30 % der Bildhöhe, muss der
#: Ausschnitt spätestens bei 18 % des freien Raums beginnen, um beides zu
#: fassen. 0.12 lässt darüber noch etwas Luft, statt den Kopf an die Kante zu
#: kleben.
FOKUS_Y_HOCH_IN_QUER = 0.12


def _lage(breite: int, hoehe: int) -> str:
    if breite > hoehe:
        return "quer"
    if hoehe > breite:
        return "hoch"
    return "quadrat"


def passt_ins_feld(foto: Image.Image, w: int, h: int, modus: str = "auto",
                   hintergrund: str = "#ffffff") -> Image.Image:
    """Ein Foto auf genau ``w x h`` bringen — ohne es je zu verzerren.

    Der Fall, um den es geht: Die Kamera hängt hochkant in der Box, die
    abgenommene Vorlage hat aber liegende Bildfelder. Bis zum 20.09.2026 hat
    ``ImageOps.fit`` dann stumm mittig beschnitten — bei 3:2 blieben 44 % des
    Bildes übrig, und zwar die mittleren 44 %: Köpfe ab, Füße ab, niemand
    merkt es bis der Abzug herauskommt.

    ``auto`` beschneidet weiter, solange die Lagen zusammenpassen, und passt
    das ganze Foto ein, sobald sie es nicht tun. Ein Blatt mit Rand ist
    besser als ein Blatt ohne Köpfe.
    """
    modus = modus if modus in EINPASSEN else "auto"
    gleich = _lage(foto.width, foto.height) == _lage(w, h)
    if modus == "beschneiden" or (modus == "auto" and gleich):
        if gleich or modus == "beschneiden":
            # Beim Beschneiden über Kreuz den Ausschnitt nach oben ziehen —
            # sonst trifft es genau die Gesichter.
            fokus = 0.5
            if not gleich and _lage(foto.width, foto.height) == "hoch":
                fokus = FOKUS_Y_HOCH_IN_QUER
            zugeschnitten = crop_to_aspect(foto, f"{w}:{h}", 0.5, fokus)
            return zugeschnitten.resize((w, h), Image.Resampling.LANCZOS)
    # Einpassen: das ganze Foto, der Rest bleibt Blattfarbe.
    kopie = foto.copy()
    kopie.thumbnail((w, h), Image.Resampling.LANCZOS)
    flaeche = Image.new("RGB", (w, h), hintergrund)
    flaeche.paste(kopie, ((w - kopie.width) // 2, (h - kopie.height) // 2))
    return flaeche


def render_template(template: Dict, shots: List[Image.Image], *, assets_dir: Optional[Path] = None,
                    background: Optional[str] = None, qr_url: Optional[str] = None,
                    texts: Optional[Dict[str, str]] = None,
                    style_cfg: Optional[Dict] = None,
                    einpassen: str = "auto") -> Image.Image:
    """Collage/Druckkarte aus einer JSON-Vorlage rendern.

    ``background`` (Asset-Pfad oder Farbe) überschreibt den Vorlagen-Hintergrund —
    so wählt der Gast seinen Hintergrund, ohne dass es eine eigene Vorlage braucht.
    ``style_cfg`` ist der Stil des Betreibers: Er füllt alle @-Platzhalter der
    Vorlage, sodass dieselbe Anordnung zur Hochzeits- oder Firmenkarte wird.
    """
    st = style(style_cfg)
    if template.get("plain"):
        # A plain layout keeps its paper tone everywhere, also in the margin
        # of a photo that is fitted rather than cropped (otherwise a black
        # sheet showed the style's cream around a turned photo).
        st = dict(st, background=_farbe(template.get("background"), st, st["background"]), frame=0)
    canvas = template.get("canvas", PRINT_CANVAS)
    out = _background(canvas, background or _farbe(template.get("background"), st, st["background"]), assets_dir)
    values = werte(texts, st)
    # A QR layer is only printed when there is a link to put into it. Without
    # one (booth not paired, QR off for the print, no own link), nothing of it
    # may show: no empty box, no "scan the code" next to it.
    qr_da = qr_gedruckt(template, qr_url)

    # Layer by layer, bottom to top, in the order the template editor shows
    # (vorlagen_kern.ebenen). Without "order" that is the sequence this
    # function always drew: photos, overlay and frame, texts, QR code.
    for art, _eid, element, i in vorlagen_kern.ebenen(template):
        if element is not None and element.get("hidden"):
            continue                      # hidden in the editor = not printed
        if element is not None and element.get("nur_mit_qr") and not qr_da:
            continue                      # a caption that belongs to a QR code that is not there
        if art == "slot":
            if shots:
                out = _lege(out, element, _mitte(element),
                            lambda ziel, s=element, n=i: _foto(ziel, s, n, shots, st, einpassen))
        elif art == "overlay":
            out = _rahmen(out, template, st, canvas, assets_dir, qr_da)
        elif art == "text":
            satz = textsatz(element, values, st, template, assets_dir)
            out = _lege(out, element, _text_mitte(element, values, st, assets_dir, satz),
                        lambda ziel, t=element, sz=satz: _text(ziel, t, values, st, assets_dir, sz),
                        _farbe(element.get("color"), st, st["ink"]), _deckkraft(element))
        elif art == "shape":
            out = _lege(out, element, _mitte(element), lambda ziel, f=element: _form(ziel, f, st),
                        deckkraft=_deckkraft(element))
        elif art == "image":
            out = _lege(out, element, _mitte(element), lambda ziel, b=element: _bild(ziel, b, assets_dir),
                        deckkraft=_deckkraft(element))
        elif art == "qr" and qr_da:
            size = int(element.get("size", 220))
            qr_bild = qr_image(qr_url, size)
            out = _lege(out, element, _mitte(dict(element, w=size, h=size)),
                        lambda ziel, q=element: ziel.paste(qr_bild, (int(q["x"]), int(q["y"]))))
    return out


def druckbogen(bild: Image.Image, papier: Optional[str]) -> Image.Image:
    """The sheet that goes to the printer for a picture rendered on ``papier``.

    Only a two-up paper changes anything: a single 2×6″ strip is laid twice
    side by side on its 10×15 sheet, exactly half the width each, so the cut
    in the middle (DNP, Mitsubishi, HiTi "2 inch cut", or scissors) gives two
    whole strips. Every other paper returns the very same picture object.
    """
    angaben = vorlagen_kern.PAPIERE.get(str(papier or ""), {})
    zwei = angaben.get("zwei_auf")
    if not zwei:
        return bild
    bw, bh = zwei["canvas"]
    halb = bw // 2
    streifen = bild.convert("RGB")
    if streifen.size != (halb, bh):
        streifen = ImageOps.fit(streifen, (halb, bh), Image.Resampling.LANCZOS)
    bogen = Image.new("RGB", (bw, bh), "#ffffff")
    bogen.paste(streifen, (0, 0))
    bogen.paste(streifen, (halb, 0))
    return bogen


def drehung(element: Dict) -> float:
    """The element's rotation in degrees, clockwise, 0 <= r < 360."""
    try:
        return float(element.get("rotation") or 0) % 360
    except (TypeError, ValueError):
        return 0.0


def _mitte(element: Dict) -> Tuple[float, float]:
    return (float(element["x"]) + float(element["w"]) / 2, float(element["y"]) + float(element["h"]) / 2)


def _deckkraft(element: Dict) -> int:
    try:
        return max(0, min(100, int(element.get("opacity", 100))))
    except (TypeError, ValueError):
        return 100


def _lege(out: Image.Image, element: Dict, mitte: Tuple[float, float], zeichne,
          farbe: str = "#000000", deckkraft: int = 100) -> Image.Image:
    """Draw one element; turned ones on a layer of their own, turned about ``mitte``.

    Unturned elements are drawn straight onto the sheet — exactly the
    operations the renderer always did, so an old template does not change
    by a pixel. A turned one is drawn upright on a transparent sheet, turned
    clockwise (as CSS turns it in the editor) and laid on top.
    """
    winkel = drehung(element)
    if not winkel and deckkraft >= 100:
        zeichne(out)
        return out
    # Transparent, but in the element's own colour: anti-aliased edges blend
    # toward the transparent pixel's colour, and for text that is the ink —
    # a light line on a dark sheet keeps no dark fringe.
    try:
        grund = ImageColor.getrgb(farbe)[:3] + (0,)
    except ValueError:
        grund = (0, 0, 0, 0)
    lage = Image.new("RGBA", out.size, grund)
    zeichne(lage)
    if winkel:
        lage = lage.rotate(-winkel, resample=Image.Resampling.BICUBIC, center=mitte)
    if deckkraft < 100:
        lage.putalpha(lage.getchannel("A").point(lambda a: a * deckkraft // 100))
    return Image.alpha_composite(out.convert("RGBA"), lage).convert("RGB")


def _form(ziel: Image.Image, f: Dict, st: Dict) -> None:
    """Rectangle, ellipse or line. Outlines lie inside the box, like CSS border-box."""
    x, y, w, h = int(f["x"]), int(f["y"]), int(f["w"]), int(f["h"])
    fuellung = _farbe(f.get("fill"), st, "") or None
    kontur = _farbe(f.get("stroke"), st, "") or None
    staerke = max(0, int(f.get("stroke_width") or 0))
    draw = ImageDraw.Draw(ziel)
    art = f.get("kind") or "rect"
    if art == "line":
        dicke = max(1, staerke)
        mitte = y + h / 2
        oben = int(round(mitte - dicke / 2))
        # A line is a bar of its stroke width across the box, so it is
        # exactly as thick as the editor draws it.
        draw.rectangle((x, oben, x + w - 1, oben + dicke - 1), fill=kontur or fuellung or "#000000")
        return
    box = (x, y, x + w - 1, y + h - 1)
    breite = staerke if kontur else 0
    if art == "ellipse":
        draw.ellipse(box, fill=fuellung, outline=kontur, width=breite)
    else:
        radius = max(0, min(int(f.get("radius") or 0), min(w, h) // 2))
        draw.rounded_rectangle(box, radius=radius, fill=fuellung, outline=kontur, width=breite)


def _bild(ziel: Image.Image, b: Dict, assets_dir: Optional[Path]) -> None:
    """A picture from the media, fitted into its box and centred; the rest stays clear."""
    name = vorlagen_kern.datei_name(b.get("file"), vorlagen_kern.BILD_ENDUNGEN)
    if not name or not assets_dir or not (Path(assets_dir) / name).is_file():
        return                            # a missing logo must not cost the print
    try:
        with Image.open(Path(assets_dir) / name) as roh:
            bild = ImageOps.exif_transpose(roh).convert("RGBA")
    except (OSError, ValueError):
        return
    w, h = int(b["w"]), int(b["h"])
    bild = ImageOps.contain(bild, (w, h), Image.Resampling.LANCZOS)
    pos = (int(b["x"]) + (w - bild.width) // 2, int(b["y"]) + (h - bild.height) // 2)
    ziel.paste(bild, pos, bild)


def _text_zeilen(t: Dict, values: Dict[str, str], st: Dict, assets_dir: Optional[Path] = None):
    value = str(t.get("value", "")).format_map(_SafeDict(values))
    familie = str(t.get("font") or ("display" if t.get("display") else st.get("font") or "system"))
    font = load_font(int(t.get("size", 48)), bool(t.get("bold", True)), familie, assets_dir)
    return value, font


def _zeilenabstand(t: Dict) -> Optional[int]:
    """Baseline to baseline in pixels, when the template sets a line spacing."""
    try:
        faktor = float(t["line_height"])
    except (KeyError, TypeError, ValueError):
        return None
    return int(round(int(t.get("size", 48)) * max(0.8, min(3.0, faktor))))


def _text_mitte(t: Dict, values: Dict[str, str], st: Dict,
                assets_dir: Optional[Path] = None, satz: Optional[Dict] = None) -> Tuple[float, float]:
    """Middle of the line: half its advance width, half of ascent plus descent.

    The editor turns the line about the same point — it measures the same
    two numbers in the browser — so a turned line lands where it is shown.
    A line fitted to its field (``satz``) turns about the middle of what is
    printed: its own size, its own lines.
    """
    if not drehung(t):
        return (0.0, 0.0)
    if satz and satz["geaendert"]:
        font = satz["font"]
        try:
            oben, unten = font.getmetrics()
        except AttributeError:
            oben, unten = satz["size"], 0
        hoehe = (len(satz["zeilen"]) - 1) * satz["abstand"] + oben + unten
        breite = satz["breite_gesetzt"]
        x = float(satz["x"])
        links = {"center": x - breite / 2, "right": x - breite}.get(satz["align"], x)
        return (links + breite / 2, float(t.get("y", 0)) + hoehe / 2)
    value, font = _text_zeilen(t, values, st, assets_dir)
    abstand = _zeilenabstand(t)
    zeilen = value.split("\n") if abstand else [value]
    messen = ImageDraw.Draw(Image.new("L", (1, 1)))
    breite = max(messen.textlength(z, font=font) for z in zeilen)
    try:
        oben, unten = font.getmetrics()
    except AttributeError:
        oben, unten = int(t.get("size", 48)), 0
    hoehe = (len(zeilen) - 1) * (abstand or 0) + oben + unten
    x = float(t.get("x", 0))
    links = {"center": x - breite / 2, "right": x - breite}.get(t.get("align", "left"), x)
    return (links + breite / 2, float(t.get("y", 0)) + hoehe / 2)


def _foto(out: Image.Image, slot: Dict, i: int, shots: List[Image.Image], st: Dict, einpassen: str) -> None:
    idx = int(slot.get("shot", i))
    shot = shots[idx % len(shots)]
    w, h = int(slot["w"]), int(slot["h"])
    # Je Bildfeld darf die Vorlage das letzte Wort haben — ein Feld, das
    # als Anschnitt gedacht ist, bleibt Anschnitt.
    modus = str(slot.get("fit") or einpassen)
    fitted = passt_ins_feld(shot, w, h, modus, st["background"])
    roh = slot.get("radius", 0)
    # „@rund" heißt: so rund, wie der Stil es sagt — eine Ecke, ein Ort.
    radius = int(st["radius"]) if roh == "@rund" else int(roh or 0)
    radius = max(0, min(radius, min(w, h) // 2))
    if radius:
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
        out.paste(fitted, (int(slot["x"]), int(slot["y"])), mask)
    else:
        out.paste(fitted, (int(slot["x"]), int(slot["y"])))


def _rahmen(out: Image.Image, template: Dict, st: Dict, canvas: Sequence[int],
            assets_dir: Optional[Path], qr_da: bool = True) -> Image.Image:
    """The overlay picture and the style's frame line — one layer."""
    overlay = template.get("overlay")
    if overlay and assets_dir and (assets_dir / overlay).exists():
        with Image.open(assets_dir / overlay) as ov:
            ov = ov.convert("RGBA").resize(out.size, Image.Resampling.LANCZOS)
            out = Image.alpha_composite(out.convert("RGBA"), ov).convert("RGB")
            if not qr_da:
                out = _qr_fenster_schliessen(out, ov, template.get("qr"))
    # Ein schmaler Rahmen, wenn der Stil ihn will — das ist der Unterschied
    # zwischen „Foto auf Papier" und „Karte". A plain layout ("Schlicht",
    # ``plain``) is photos only: no frame line, whatever the style says.
    if int(st.get("frame") or 0) and not template.get("plain"):
        stark = int(st["frame"])
        ImageDraw.Draw(out).rectangle((stark, stark, canvas[0] - stark - 1, canvas[1] - stark - 1),
                                      outline=st["accent"], width=max(1, stark // 4))
    return out


def _qr_fenster_schliessen(out: Image.Image, ov: Image.Image, qr: Optional[Dict]) -> Image.Image:
    """Close the window an overlay leaves for a QR code that is not printed.

    Template sets made as one picture punch a transparent hole where the
    code goes. Without a code the sheet's background showed through it — a
    pale empty box in the corner of the print. The hole is filled with the
    overlay's own colour right around it, so the paper looks uninterrupted.
    """
    if not isinstance(qr, dict):
        return out
    try:
        x, y, n = int(qr["x"]), int(qr["y"]), int(qr.get("size", 220))
    except (KeyError, TypeError, ValueError):
        return out
    box = (max(0, x), max(0, y), min(out.width, x + n), min(out.height, y + n))
    if box[2] - box[0] < 2 or box[3] - box[1] < 2:
        return out
    alpha = ov.getchannel("A").crop(box)
    if alpha.getextrema()[0] >= 250:
        return out                        # no window there: nothing to close
    # The window's soft edge (the overlay was scaled) reaches a few pixels past
    # the box: the patch covers the box and that edge, and its colour is the
    # overlay's on a ring just outside, where it is opaque.
    rand = max(3, n // 40)

    def um(d: int):
        return (max(0, box[0] - d), max(0, box[1] - d), min(out.width, box[2] + d), min(out.height, box[3] + d))

    flaeche, aussen = um(rand), um(2 * rand)
    ring = ov.crop(aussen)
    maske = Image.new("L", ring.size, 255)
    ImageDraw.Draw(maske).rectangle((flaeche[0] - aussen[0], flaeche[1] - aussen[1],
                                     flaeche[2] - aussen[0] - 1, flaeche[3] - aussen[1] - 1), fill=0)
    maske = ImageChops.multiply(maske, ring.getchannel("A").point(lambda a: 255 if a >= 250 else 0))
    if not maske.getbbox():
        return out
    farbe = tuple(int(round(v)) for v in ImageStat.Stat(ring.convert("RGB"), maske).median)
    flicken = Image.new("RGB", (flaeche[2] - flaeche[0], flaeche[3] - flaeche[1]), farbe)
    out = out.copy()
    # Where the overlay is transparent the patch shows, where it is opaque the
    # overlay stays — the same blend the overlay itself was laid with. Scaling
    # also left a lighter seam in the overlay's own opaque pixels along the
    # window; pixels within a few shades of the paper colour take it too.
    durch = ov.getchannel("A").crop(flaeche).point(lambda a: 255 - a)
    naehe = ImageChops.difference(ov.crop(flaeche).convert("RGB"), flicken).convert("L").point(
        lambda d: 255 if d <= 24 else 0)
    out.paste(flicken, flaeche[:2], ImageChops.lighter(durch, naehe))
    return out


def _text(out: Image.Image, t: Dict, values: Dict[str, str], st: Dict,
          assets_dir: Optional[Path] = None, satz: Optional[Dict] = None) -> None:
    value, font = _text_zeilen(t, values, st, assets_dir)
    if not value.strip():
        return
    x, y = int(t.get("x", 0)), int(t.get("y", 0))
    anchor = {"left": "la", "center": "ma", "right": "ra"}.get(t.get("align", "left"), "la")
    farbe = _farbe(t.get("color"), st, st["ink"])
    draw = ImageDraw.Draw(out)
    if satz and satz["geaendert"]:
        _gesetzt(draw, t, satz, farbe, {"left": "la", "center": "ma", "right": "ra"}[satz["align"]], assets_dir)
        return
    abstand = _zeilenabstand(t)
    if t.get("amp_font") and "&" in value and not abstand:
        _text_mit_et(draw, t, value, font, farbe, assets_dir)
        return
    if not abstand:
        draw.text((x, y), value, font=font, fill=farbe, anchor=anchor)
        return
    # With a line spacing every line is set on its own, baseline to baseline
    # ``abstand`` apart — the editor stacks its lines the same way.
    for i, zeile in enumerate(value.split("\n")):
        if zeile:
            draw.text((x, y + i * abstand), zeile, font=font, fill=farbe, anchor=anchor)


def _text_mit_et(draw: ImageDraw.ImageDraw, t: Dict, value: str, font, farbe: str,
                 assets_dir: Optional[Path]) -> None:
    """One line whose "&" is set in ``amp_font``, all parts on the line's baseline.

    The ampersand is sized so its height matches the line font's capitals:
    a script's em is mostly flourish, so the same point size would look
    twice too large in a serif.
    """
    teile, breiten = _et_teile(t, value, font, int(t.get("size", 48)), assets_dir)
    gesamt = sum(breiten)
    x = float(t.get("x", 0))
    x = {"center": x - gesamt / 2, "right": x - gesamt}.get(t.get("align", "left"), x)
    # The renderer's y is the top of the line font's ascender ("la").
    grundlinie = float(t.get("y", 0)) + font.getmetrics()[0]
    for (s, f), b in zip(teile, breiten):
        draw.text((x, grundlinie), s, font=f, fill=farbe, anchor="ls")
        x += b


def _et_teile(t: Dict, value: str, font, groesse: int, assets_dir: Optional[Path]):
    """The pieces of a line with its "&" in ``amp_font``, and their advance widths."""
    mass = ImageDraw.Draw(Image.new("L", (1, 1)))
    l0, o0, r0, u0 = mass.textbbox((0, 0), "H", font=font, anchor="ls")
    probe = load_font(groesse, False, str(t["amp_font"]), assets_dir)
    l1, o1, r1, u1 = mass.textbbox((0, 0), "&", font=probe, anchor="ls")
    et_groesse = max(8, int(round(groesse * (u0 - o0) / max(1, (u1 - o1)) * 0.92)))
    et = load_font(et_groesse, False, str(t["amp_font"]), assets_dir)
    teile: List[Tuple[str, object]] = []
    for i, stueck in enumerate(value.split("&")):
        if i:
            teile.append(("&", et))
        if stueck:
            teile.append((stueck, font))
    return teile, [mass.textlength(s, font=f) for s, f in teile]


# ── Text that stays in its field ──────────────────────────
# A template places a line by its anchor (x, y, align) and its size; how long
# the line gets is up to the event: "Mia & Tom" fits anywhere, a couple's two
# full first names may not. Until November 2026 a long line simply ran on —
# off the photo, off the paper. Now every line has a field, and a line that
# does not fit it is set smaller, then in two lines, never cut and never
# shortened with "…": on a wedding print a name is the one thing that must
# be whole. The template editor (vorlagen-editor.js, ``textSatz``) applies
# the very same rule in the browser, so its sheet shows what is printed.
#
# The field: ``w``/``h`` when the template sets them (additive, optional).
# Otherwise the width is what the alignment leaves to the safe margin of the
# paper (left-aligned: from x to the right margin; centred: the same distance
# on both sides of x; right-aligned: from the left margin to x) and the
# height reaches down to the next text, photo or QR code beneath the line, or
# to the bottom margin. Either way the field never extends past the margin.

#: A line that is too wide shrinks first, down to this share of its set size.
SCHRUMPF_MIN = 0.6
#: Below that it is broken into two lines if the field is tall enough, else
#: it shrinks on; below this share the check warns that it gets hard to read.
SCHRUMPF_BODEN = 0.4
#: Line spacing of a line broken in two when the template sets none.
UMBRUCH_ABSTAND = 1.15
#: Safe margin when the template names no known paper.
STANDARD_RAND_MM = 4.0
#: Templates are made at 300 dpi.
PX_PRO_MM = 300 / 25.4


def werte(texts: Optional[Dict[str, str]], st: Dict) -> Dict[str, str]:
    """The values a template's ``{…}`` fields are filled with."""
    values = dict(texts or {})
    # Die beiden Zeilen des Stils stehen den Vorlagen als {headline}/{subline}
    # zur Verfügung — dort steht dann „Happy Birthday" statt des Eventnamens,
    # ohne dass die Vorlage etwas davon weiß.
    values.setdefault("headline", str(st.get("headline") or "").format_map(_SafeDict(values)))
    values.setdefault("subline", str(st.get("subline") or "").format_map(_SafeDict(values)))
    return values


def qr_gedruckt(template: Dict, qr_url: Optional[str]) -> bool:
    """Whether this sheet carries a QR code: a QR layer, shown, and a link for it."""
    qr = template.get("qr")
    return bool(qr_url) and isinstance(qr, dict) and not qr.get("hidden")


def sicherer_rand(template: Dict) -> int:
    """The paper's safe margin in pixels (plus the bleed, when the sheet has one)."""
    papier = vorlagen_kern.PAPIERE.get(str(template.get("paper") or ""))
    mm = float(papier.get("sicher_mm", STANDARD_RAND_MM)) if papier else STANDARD_RAND_MM
    try:
        anschnitt = int(template.get("anschnitt_px") or 0)
    except (TypeError, ValueError):
        anschnitt = 0
    return int(round(mm * PX_PRO_MM)) + max(0, anschnitt)


def _zahl_oder(wert, standard: Optional[float] = None) -> Optional[float]:
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return standard
    return zahl if zahl > 0 else standard


#: Room kept between a line's field and a QR code beside it, in sizes.
SEITENLUFT = 0.25


def _kaesten(template: Dict) -> List[Tuple[float, float, float, float]]:
    """(left, top, right, bottom) of the QR code: what a line must not run into sideways.

    Only the code: a line may lie on a photo on purpose (a "30" between the
    pictures, a name across the bottom of one), but text over a QR code
    makes it unreadable for the camera.
    """
    aus = []
    qr = template.get("qr")
    if isinstance(qr, dict) and not qr.get("hidden"):
        x, y, n = float(qr.get("x", 0) or 0), float(qr.get("y", 0) or 0), float(qr.get("size", 220) or 220)
        aus.append((x, y, x + n, y + n))
    return aus


def _waagrecht(t: Dict, breite: float, rand: int,
               kaesten: Sequence[Tuple[float, float, float, float]] = ()) -> Tuple[float, float, float, str]:
    """(left, right, anchor x, alignment) of a text's field.

    An anchor outside the safe area is pulled onto its edge and the line is
    set from there inwards (right-aligned at the right edge, left-aligned at
    the left one): a line anchored past the paper is still printed, whole.
    A QR code beside the line (overlapping its height, not under its anchor)
    ends the field a quarter of the line's size before it.
    """
    lo, hi = float(rand), float(breite - rand)
    roh = float(t.get("x", 0) or 0)
    if roh > hi or roh < lo:
        return lo, hi, (hi if roh > hi else lo), ("right" if roh > hi else "left")
    x = roh
    y, groesse = float(t.get("y", 0) or 0), float(t.get("size", 48) or 48)
    luft = round(groesse * SEITENLUFT)
    for kl, ko, kr, ku in kaesten:
        if ko < y + groesse * 1.2 and ku > y and not (kl <= x <= kr):
            if kl > x:
                hi = min(hi, kl - luft)
            else:
                lo = max(lo, kr + luft)
    ausrichtung = t.get("align", "left")
    w = _zahl_oder(t.get("w"))
    if w:
        links, rechts = {"center": (x - w / 2, x + w / 2), "right": (x - w, x)}.get(ausrichtung, (x, x + w))
    elif ausrichtung == "center":
        halb = max(0.0, min(x - lo, hi - x))
        links, rechts = x - halb, x + halb
    elif ausrichtung == "right":
        links, rechts = lo, x
    else:
        links, rechts = x, hi
    return max(links, lo), max(min(rechts, hi), max(links, lo)), x, ausrichtung


def textfeld(t: Dict, template: Dict, rand: Optional[int] = None) -> Dict[str, float]:
    """The field a text layer is set in: left, right, top, bottom, its anchor x and alignment.

    ``verschoben``: the anchor lay outside the safe area and was moved onto it.
    """
    w, h = [float(v) for v in (template.get("canvas") or PRINT_CANVAS)[:2]]
    rand = sicherer_rand(template) if rand is None else rand
    kaesten = _kaesten(template)
    links, rechts, x, ausrichtung = _waagrecht(t, w, rand, kaesten)
    verschoben = x != float(t.get("x", 0) or 0)
    oben = float(t.get("y", 0) or 0)
    unten = h - rand
    eigene = _zahl_oder(t.get("h"))
    if eigene:
        unten = min(unten, oben + eigene)
    else:
        # Down to the next thing beneath the line that the line would run into.
        for art, _eid, e, _i in vorlagen_kern.ebenen(template):
            if e is None or e is t or e.get("hidden"):
                continue
            if art == "text":
                l, r, _x, _a = _waagrecht(e, w, rand, kaesten)
            elif art == "slot":
                l, r = float(e.get("x", 0)), float(e.get("x", 0)) + float(e.get("w", 0))
            elif art == "qr":
                l, r = float(e.get("x", 0)), float(e.get("x", 0)) + float(e.get("size", 220))
            else:
                continue
            top = float(e.get("y", 0) or 0)
            if top > oben and l < rechts and r > links:
                unten = min(unten, top)
    return {"links": links, "rechts": rechts, "oben": oben, "unten": unten, "x": x, "align": ausrichtung,
            "verschoben": verschoben}


def textsatz(t: Dict, values: Dict[str, str], st: Dict, template: Dict,
             assets_dir: Optional[Path] = None) -> Dict:
    """How a text layer is printed: size, lines, spacing, anchor — fitted to its field.

    ``geaendert`` is False when the line fits as the template sets it; it is
    then drawn exactly as before (same call, same pixels). ``stufe`` says
    what was done: "" (nothing), "kleiner", "zweizeilig", "sehr_klein".
    """
    value, font = _text_zeilen(t, values, st, assets_dir)
    groesse = int(t.get("size", 48))
    feld = textfeld(t, template)
    platz = max(1.0, feld["rechts"] - feld["links"])
    abstand_satz = _zeilenabstand(t)
    absaetze = value.split("\n") if (abstand_satz or "\n" in value) else [value]
    familie = str(t.get("font") or ("display" if t.get("display") else st.get("font") or "system"))
    fett = bool(t.get("bold", True))
    mass = ImageDraw.Draw(Image.new("L", (1, 1)))
    schriften: Dict[int, object] = {groesse: font}

    def schrift(s: int):
        if s not in schriften:
            schriften[s] = load_font(s, fett, familie, assets_dir)
        return schriften[s]

    mit_et = bool(t.get("amp_font")) and not abstand_satz

    def breite(zeile: str, s: int) -> float:
        if not zeile:
            return 0.0
        if mit_et and "&" in zeile:
            return float(sum(_et_teile(t, zeile, schrift(s), s, assets_dir)[1]))
        return float(mass.textlength(zeile, font=schrift(s)))

    def abstand(s: int) -> int:
        try:
            faktor = float(t["line_height"])
        except (KeyError, TypeError, ValueError):
            faktor = UMBRUCH_ABSTAND
        return int(round(s * max(0.8, min(3.0, faktor))))

    def hoehe(n: int, s: int) -> float:
        try:
            a, d = schrift(s).getmetrics()
        except AttributeError:
            a, d = s, 0
        return (n - 1) * abstand(s) + a + d

    natur = max([breite(z, groesse) for z in absaetze] or [0.0])
    aus = {"size": groesse, "zeilen": absaetze, "abstand": abstand_satz or 0, "x": float(t.get("x", 0) or 0),
           "font": font, "geaendert": False, "stufe": "", "breite": natur, "breite_gesetzt": natur,
           "platz": platz, "feld": feld, "text": value, "align": t.get("align", "left")}
    if not value.strip() or (natur <= platz + 0.5 and not feld["verschoben"]):
        return aus

    def passend(zeilen: List[str], hoechstens: int, mindestens: int) -> Optional[int]:
        """The largest size in [mindestens, hoechstens] at which every line fits the width."""
        breitest = max(zeilen, key=lambda z: breite(z, hoechstens))
        s = min(hoechstens, int(hoechstens * platz / max(1.0, breite(breitest, hoechstens))))
        s = max(s, 1)
        while s > mindestens and max(breite(z, s) for z in zeilen) > platz:
            s -= 1
        return s if s >= mindestens and max(breite(z, s) for z in zeilen) <= platz else None

    def fertig(s: int, zeilen: List[str], stufe: str, mehrzeilig: bool) -> Dict:
        anker = {"center": (feld["links"] + feld["rechts"]) / 2, "right": feld["rechts"]}.get(
            feld["align"], feld["links"])
        aus.update(size=s, zeilen=zeilen, font=schrift(s), geaendert=True, stufe=stufe, x=anker, align=feld["align"],
                   abstand=abstand(s) if mehrzeilig else 0,
                   breite_gesetzt=max(breite(z, s) for z in zeilen))
        return aus

    mehrzeilig = len(absaetze) > 1
    minimum = max(1, int(-(-groesse * SCHRUMPF_MIN // 1)))
    if natur <= platz + 0.5:              # only moved onto the sheet: same size, same lines
        return fertig(groesse, absaetze, "verschoben", mehrzeilig)
    # 1. smaller, down to 60 %
    s = passend(absaetze, groesse, minimum)
    if s:
        return fertig(s, absaetze, "kleiner", mehrzeilig)
    # 2. broken into lines at spaces (two, as even as possible; more only if
    #    two will not do), at the largest size that fits width and field height
    hoch = feld["unten"] - feld["oben"]

    def umbrochen(s: int, zwei: bool = False) -> Optional[List[str]]:
        zeilen = _umbrechen(absaetze, lambda z, s=s: breite(z, s), platz, zwei)
        if zeilen and len(zeilen) > len(absaetze) and hoehe(len(zeilen), s) <= hoch:
            return zeilen
        return None

    for zwei in ((True, False) if len(absaetze) == 1 else (False,)):
        for s in range(groesse, minimum - 1, -1):
            zeilen = umbrochen(s, zwei)
            if zeilen:
                return fertig(s, zeilen, "zweizeilig", True)
    # 3. smaller still: in one line or broken, whichever stays larger. Below
    #    40 % the check warns. Never off the field, never cut.
    boden = max(1, int(groesse * SCHRUMPF_BODEN))
    einzeilig = passend(absaetze, minimum, 1) or 1
    for s in range(minimum - 1, einzeilig, -1):
        zeilen = umbrochen(s)
        if zeilen:
            return fertig(s, zeilen, "zweizeilig" if s >= boden else "sehr_klein", True)
    return fertig(einzeilig, absaetze, "kleiner" if einzeilig >= boden else "sehr_klein", mehrzeilig)


def _umbrechen(absaetze: List[str], breite, platz: float, zwei: bool = False) -> Optional[List[str]]:
    """Lines that fit ``platz``: a single line split once where the two halves are
    most even (``zwei``: only that); otherwise every paragraph wrapped word by word."""
    if len(absaetze) == 1:
        woerter = absaetze[0].split(" ")
        beste, beste_breite = None, None
        for i in range(1, len(woerter)):
            a, b = " ".join(woerter[:i]).strip(), " ".join(woerter[i:]).strip()
            if not a or not b:
                continue
            w = max(breite(a), breite(b))
            if w <= platz and (beste_breite is None or w < beste_breite):
                beste, beste_breite = [a, b], w
        if beste or zwei:
            return beste
    zeilen: List[str] = []
    for absatz in absaetze:
        aktuell = ""
        # A lone "&" goes with the word after it, never on a line of its own.
        woerter: List[str] = []
        for wort in absatz.split(" "):
            if woerter and woerter[-1] == "&":
                woerter[-1] += " " + wort
            else:
                woerter.append(wort)
        for wort in woerter:
            probe = (aktuell + " " + wort) if aktuell else wort
            if breite(probe) <= platz:
                aktuell = probe
            elif not aktuell or breite(wort) > platz:
                return None
            else:
                zeilen.append(aktuell)
                aktuell = wort
        zeilen.append(aktuell)
    return zeilen


def _gesetzt(draw: ImageDraw.ImageDraw, t: Dict, satz: Dict, farbe: str, anchor: str,
             assets_dir: Optional[Path]) -> None:
    """Draw a text layer as ``textsatz`` fitted it: line by line from the line's top."""
    y = float(t.get("y", 0) or 0)
    for i, zeile in enumerate(satz["zeilen"]):
        if not zeile:
            continue
        oben = y + i * satz["abstand"]
        if t.get("amp_font") and "&" in zeile and not _zeilenabstand(t):
            _text_mit_et(draw, dict(t, x=satz["x"], y=oben, size=satz["size"], align=satz["align"]), zeile, satz["font"], farbe,
                         assets_dir)
        else:
            draw.text((satz["x"], oben), zeile, font=satz["font"], fill=farbe, anchor=anchor)


def textwarnungen(vorlage: Dict, texts: Optional[Dict[str, str]] = None,
                  assets_dir: Optional[Path] = None, style_cfg: Optional[Dict] = None) -> List[str]:
    """What the check says about lines that do not fit their field with ``texts``.

    A line set in an uploaded font is only measured when that font is at hand
    (``assets_dir``): measuring it in a stand-in would report a wrong width.
    """
    st = style(style_cfg)
    values = werte(dict({"date": "18.11.2026"}, **(texts or BEISPIEL_TEXTE)), st)
    aus = []
    for t in vorlage.get("texts") or []:
        if not isinstance(t, dict) or t.get("hidden"):
            continue
        familie = str(t.get("font") or "")
        for f in (familie, str(t.get("amp_font") or "")):
            if f.lower().endswith((".ttf", ".otf")) and not (assets_dir and (Path(assets_dir) / f).is_file()):
                break
        else:
            satz = textsatz(t, values, st, vorlage, assets_dir)
            if not satz["geaendert"]:
                continue
            wie = {"kleiner": f"wird in {satz['size']} px statt {int(t.get('size', 48))} px gedruckt",
                   "zweizeilig": f"wird zweizeilig in {satz['size']} px gedruckt",
                   "verschoben": "steht außerhalb des sicheren Bereichs und wird an dessen Rand gesetzt",
                   "sehr_klein": f"wird in {satz['size']} px gedruckt — kaum lesbar, bitte das Feld "
                                 f"vergrößern oder die Schrift verkleinern"}[satz["stufe"]]
            if satz["stufe"] != "sehr_klein" and satz["size"] < int(int(t.get("size", 48)) * SCHRUMPF_MIN):
                wie += " — kleiner als lesbar gedacht"
            aus.append(f"Text passt nicht in sein Feld: „{satz['text'][:40]}“ ist {round(satz['breite'])} px "
                       f"breit, Platz sind {round(satz['platz'])} px; {wie}.")
    return aus


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


# ── Test prints and the editor preview ────────────────────
# The gallery's preview and a test print of a template at the booth use the
# same stand-ins, so what Till sees in the editor is what comes out of the
# printer — the photos are plainly samples, never guests' pictures.

#: The texts a sample sheet is filled with ({event_name}, {date}).
BEISPIEL_TEXTE = {"event_name": "Mia & Tom"}
BEISPIEL_QR = "https://example.invalid/vorschau"


def beispielbilder(anzahl: int) -> List[Image.Image]:
    """Stand-in photos: numbered like the shots, labelled as samples, 3:2 like the camera."""
    farben = [((92, 112, 140), (196, 170, 132)), ((120, 92, 120), (214, 184, 150)),
              ((70, 110, 100), (200, 196, 160)), ((130, 100, 80), (220, 200, 170))]
    aus = []
    for i in range(max(1, min(12, int(anzahl or 1)))):
        oben, unten = farben[i % len(farben)]
        bild = Image.new("RGB", (1500, 1000))
        zeichnen = ImageDraw.Draw(bild)
        for y in range(1000):
            t = y / 999
            zeichnen.line([(0, y), (1500, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(oben, unten)))
        zeichnen.text((750, 500), str(i + 1), font=load_font(360, True, "display"),
                      fill=(255, 255, 255), anchor="mm")
        zeichnen.text((750, 820), "Beispielbild · sample", font=load_font(56, False, "system"),
                      fill=(255, 255, 255), anchor="mm")
        aus.append(bild)
    return aus


def testdruck_marke(bild: Image.Image, text: str = "Testdruck") -> Image.Image:
    """A small label in the bottom right corner, so a test sheet is never taken for a guest's.

    Small on purpose: it must not hide the part of the layout that is being
    judged, and a white pill keeps it readable on any background.
    """
    out = bild.convert("RGB").copy()
    groesse = max(14, int(min(out.size) * 0.018))
    schrift = load_font(groesse, True, "system")
    zeichnen = ImageDraw.Draw(out)
    links, oben, rechts, unten = zeichnen.textbbox((0, 0), text, font=schrift)
    rand = max(4, groesse // 3)
    w, h = rechts - links + 2 * rand, unten - oben + 2 * rand
    x, y = out.width - w - groesse, out.height - h - groesse
    zeichnen.rounded_rectangle((x, y, x + w, y + h), radius=rand, fill=(255, 255, 255), outline=(120, 120, 120))
    zeichnen.text((x + rand - links, y + rand - oben), text, font=schrift, fill=(60, 60, 60))
    return out
