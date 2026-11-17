"""Leichte Looks, die der Gast nach der Aufnahme wählt.

Bewusst ohne KI und ohne schwere Abhängigkeiten: Tonkurven und Farbmischung
mit Pillow. Jeder Look ist eine Funktion Image -> Image. Plugins können
weitere Looks registrieren (Hook ``booth_register_looks``).
"""
from __future__ import annotations

from typing import Callable, Dict, List

from PIL import Image, ImageEnhance, ImageOps

LookFn = Callable[[Image.Image], Image.Image]


def _curve(points: List[tuple]) -> List[int]:
    """Stückweise lineare Tonkurve aus (in, out)-Punkten -> 256er-LUT."""
    lut = []
    pts = sorted(points)
    for x in range(256):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= x <= x1:
                t = (x - x0) / max(1, (x1 - x0))
                lut.append(int(round(y0 + (y1 - y0) * t)))
                break
        else:
            lut.append(pts[-1][1] if x > pts[-1][0] else pts[0][1])
    return lut


def _rgb_lut(img: Image.Image, r, g, b) -> Image.Image:
    return img.convert("RGB").point(r + g + b)


def original(img: Image.Image) -> Image.Image:
    return img


def warm(img: Image.Image) -> Image.Image:
    base = _curve([(0, 0), (128, 132), (255, 255)])
    out = _rgb_lut(img, _curve([(0, 8), (128, 140), (255, 255)]), base, _curve([(0, 0), (128, 118), (255, 240)]))
    return ImageEnhance.Color(out).enhance(1.08)


def cool(img: Image.Image) -> Image.Image:
    out = _rgb_lut(img, _curve([(0, 0), (128, 120), (255, 245)]), _curve([(0, 0), (128, 128), (255, 252)]),
                   _curve([(0, 10), (128, 138), (255, 255)]))
    return ImageEnhance.Contrast(out).enhance(1.04)


def mono(img: Image.Image) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = g.point(_curve([(0, 6), (60, 50), (190, 205), (255, 250)]))
    return g.convert("RGB")


def film(img: Image.Image) -> Image.Image:
    # angehobene Schatten, weiche Lichter, leicht entsättigt, grün-gelber Stich
    out = _rgb_lut(img, _curve([(0, 24), (128, 134), (255, 244)]), _curve([(0, 22), (128, 130), (255, 240)]),
                   _curve([(0, 30), (128, 120), (255, 226)]))
    return ImageEnhance.Color(out).enhance(0.86)


def punch(img: Image.Image) -> Image.Image:
    out = ImageEnhance.Contrast(img.convert("RGB")).enhance(1.18)
    return ImageEnhance.Color(out).enhance(1.22)


def soft(img: Image.Image) -> Image.Image:
    # weiche Haut ohne KI: leichter Glow aus unscharfer Kopie, Kontrast etwas zurück
    from PIL import ImageFilter

    base = img.convert("RGB")
    small = base.resize((max(1, base.width // 4), max(1, base.height // 4)))
    glow = small.filter(ImageFilter.GaussianBlur(6)).resize(base.size, Image.Resampling.BILINEAR)
    out = Image.blend(base, glow, 0.28)
    c = _curve([(0, 10), (128, 134), (255, 252)])
    out = _rgb_lut(out, c, c, c)
    return ImageEnhance.Contrast(out).enhance(0.96)


LOOKS: Dict[str, LookFn] = {
    "original": original,
    "warm": warm,
    "cool": cool,
    "mono": mono,
    "film": film,
    "punch": punch,
    "soft": soft,
}

LABELS = {
    "original": "Original",
    "warm": "Warm",
    "cool": "Kühl",
    "mono": "Schwarzweiß",
    "film": "Film",
    "punch": "Kräftig",
    "soft": "Weich",
}


def apply(name: str, img: Image.Image) -> Image.Image:
    fn = LOOKS.get(name) or original
    return fn(img)
