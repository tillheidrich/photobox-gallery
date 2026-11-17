"""Die kompakte Marke als PNG für die Mitte des QR-Codes.

Pillow kann kein SVG, und ein Rasterizer wäre eine Abhängigkeit mehr für ein
Bild aus zwei Rechtecken. Deshalb wird es hier gezeichnet — aus derselben
Geometrie wie ``logo/mark-compact.svg`` (viewBox 64) und mit den Farben aus
``tokens.json``. Ändert sich die Marke, läuft dieses Skript neu:

    python tools/build_qr_mark.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import brand                                     # noqa: E402

ZIEL = ROOT / "app" / "static" / "img" / "brand" / "qr-mark.png"
KANTE = 512                                               # großzügig; im Code sitzt es klein


def main() -> int:
    s = KANTE / 64                                        # Maßstab aus der viewBox
    img = Image.new("RGBA", (KANTE, KANTE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    papier = brand.rgb("ink", "900", "#1d1a16") + (255,)
    bild = brand.rgb("amber", "500", "#d89534") + (255,)
    d.rounded_rectangle((13 * s, 7 * s, 51 * s, 57 * s), radius=3 * s, fill=papier)
    d.rounded_rectangle((17 * s, 11 * s, 47 * s, 41 * s), radius=1.5 * s, fill=bild)
    img.save(ZIEL, "PNG", optimize=True)
    print(f"ok — {ZIEL.relative_to(ROOT)} ({ZIEL.stat().st_size} Byte)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
