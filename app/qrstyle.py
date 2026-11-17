"""Gebrandete QR-Codes — identisch in Galerie und Box (booth/boothcore/media/qrstyle.py).

Stil als kleines Dict, damit er in Einstellungen und Box-Profil passt:

    {"dark": "#111111", "light": "#ffffff", "modules": "square|rounded|dots|gapped",
     "eyes": "square|rounded", "logo": "<Pfad>", "logo_scale": 0.22, "logo_pad": True}

Mit Logo schaltet die Fehlerkorrektur automatisch auf H (30 %), das Logo
bekommt eine weiße Fläche dahinter und bleibt unter ~25 % der Kantenlänge —
so bleibt der Code auch klein gedruckt lesbar.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import qrcode
from PIL import Image, ImageColor, ImageDraw, ImageOps

DEFAULT_STYLE = {"dark": "#111111", "light": "#ffffff", "modules": "square", "eyes": "square",
                 "logo": "", "logo_scale": 0.22, "logo_pad": True}


def normalize(style: Optional[dict]) -> dict:
    s = dict(DEFAULT_STYLE)
    for k, v in (style or {}).items():
        if k in s and v is not None:
            s[k] = v
    for key in ("dark", "light"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(s[key])):
            s[key] = DEFAULT_STYLE[key]
    if s["modules"] not in ("square", "rounded", "dots", "gapped"):
        s["modules"] = "square"
    try:
        s["logo_scale"] = max(0.1, min(0.28, float(s["logo_scale"])))
    except (TypeError, ValueError):
        s["logo_scale"] = 0.22
    return s


def _contrast_ok(dark: str, light: str) -> bool:
    def lum(h):
        r, g, b = [c / 255 for c in ImageColor.getrgb(h)]
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    a, b = lum(dark), lum(light)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05) >= 4.0 and a < b


def render(data: str, size: int = 600, style: Optional[dict] = None, logo_path: Optional[Path] = None) -> Image.Image:
    s = normalize(style)
    if not _contrast_ok(s["dark"], s["light"]):     # Scanner brauchen dunkel auf hell
        s["dark"], s["light"] = DEFAULT_STYLE["dark"], DEFAULT_STYLE["light"]
    has_logo = bool(logo_path and Path(logo_path).exists())
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H if has_logo else qrcode.constants.ERROR_CORRECT_M,
                       border=2, box_size=16)
    qr.add_data(data)
    qr.make(fit=True)
    dark, light = ImageColor.getrgb(s["dark"]), ImageColor.getrgb(s["light"])
    if s["modules"] == "square":
        img = qr.make_image(fill_color=s["dark"], back_color=s["light"]).convert("RGB")
    else:
        from qrcode.image.styledpil import StyledPilImage
        from qrcode.image.styles.colormasks import SolidFillColorMask
        from qrcode.image.styles.moduledrawers.pil import (CircleModuleDrawer, GappedSquareModuleDrawer,
                                                           RoundedModuleDrawer, SquareModuleDrawer)
        drawer = {"rounded": RoundedModuleDrawer(), "dots": CircleModuleDrawer(), "gapped": GappedSquareModuleDrawer()}[s["modules"]]
        eye = RoundedModuleDrawer() if s.get("eyes") == "rounded" else SquareModuleDrawer()
        img = qr.make_image(image_factory=StyledPilImage, module_drawer=drawer, eye_drawer=eye,
                            color_mask=SolidFillColorMask(back_color=light, front_color=dark)).convert("RGB")
    img = img.resize((size, size), Image.Resampling.LANCZOS if s["modules"] != "square" else Image.Resampling.NEAREST)
    if has_logo:
        with Image.open(logo_path) as lg:
            lg = ImageOps.exif_transpose(lg).convert("RGBA")
            box = int(size * s["logo_scale"])
            lg.thumbnail((box, box), Image.Resampling.LANCZOS)
            if s.get("logo_pad", True):
                pad = int(box * 0.14)
                plate = Image.new("RGBA", (lg.width + 2 * pad, lg.height + 2 * pad), (0, 0, 0, 0))
                ImageDraw.Draw(plate).rounded_rectangle((0, 0, plate.width - 1, plate.height - 1),
                                                         radius=int(pad * 1.2), fill=light + (255,))
                plate.alpha_composite(lg, (pad, pad))
                lg = plate
            pos = ((size - lg.width) // 2, (size - lg.height) // 2)
            base = img.convert("RGBA")
            base.alpha_composite(lg, pos)
            img = base.convert("RGB")
    return img
