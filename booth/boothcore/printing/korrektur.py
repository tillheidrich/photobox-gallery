"""Brightness, contrast and saturation per printer — on the copy that is printed.

Sublimation printers often print darker than the screen shows; dslrBooth and
the like have a slider per printer for it. This is that slider: three numbers
in a printer entry of the local settings (``brightness``, ``contrast``,
``saturation``), each -50 … +50, 0 = unchanged.

The correction belongs to the printer standing at that place, not to the
picture: it is applied right before the file goes to the printer, to a
temporary copy. The saved result and the gallery's picture never change, and
with all three at 0 the very same file goes out, byte for byte.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

GRENZE = 50
NAMEN = ("brightness", "contrast", "saturation")


def _zahl(wert: Any) -> int:
    if isinstance(wert, bool):
        return 0                      # True is no brightness of +1
    try:
        zahl = int(round(float(str(wert).strip())))
    except (TypeError, ValueError):
        return 0
    return max(-GRENZE, min(GRENZE, zahl))


def werte(cfg: Any) -> Dict[str, int]:
    """The three numbers of a printer entry, clamped; anything unreadable is 0."""
    cfg = cfg if isinstance(cfg, dict) else {}
    return {name: _zahl(cfg.get(name, 0)) for name in NAMEN}


def aktiv(w: Dict[str, int]) -> bool:
    return any(w.get(name) for name in NAMEN)


def anwenden(quelle: Path, w: Dict[str, int], ziel: Path) -> Path:
    """Write the corrected copy of ``quelle`` to ``ziel``; +50 is a factor of 1.5, -50 of 0.5."""
    from PIL import Image, ImageEnhance, ImageOps

    with Image.open(quelle) as roh:
        bild = ImageOps.exif_transpose(roh).convert("RGB")
    for name, enhancer in (("brightness", ImageEnhance.Brightness), ("contrast", ImageEnhance.Contrast),
                           ("saturation", ImageEnhance.Color)):
        if w.get(name):
            bild = enhancer(bild).enhance(1.0 + w[name] / 100.0)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    bild.save(ziel, "JPEG", quality=95, subsampling=0)
    return ziel
