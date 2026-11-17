#!/usr/bin/env python3
"""The README's screenshots, from a folder tools/marketing_shots.mjs wrote.

    python tools/marketing/readme_shots.py <shots dir>

Writes docs/screenshots/*.webp at 960 px and strip.webp, the four screens of
one run side by side. Small files on purpose: the repository should not grow
by megabytes every time the pictures are taken again.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "docs" / "screenshots"
PICTURES = {
    "booth-start": "kiosk-start.png",
    "booth-layouts": "kiosk-layouts.png",
    "booth-countdown": "kiosk-countdown.png",
    "booth-result": "kiosk-result.png",
    "gallery-phone": "guest-run-phone.png",
    "admin-layouts": "admin-event-layouts.png",
    "admin-template-editor": "admin-template-editor.png",
    "print-sheet": "print-kollektion.jpg",
}
STRIP = ["kiosk-layouts.png", "kiosk-result.png", "guest-run-phone.png", "admin-template-editor.png"]


def main() -> None:
    src = Path(sys.argv[1])
    OUT.mkdir(parents=True, exist_ok=True)
    for name, file in PICTURES.items():
        with Image.open(src / file) as im:
            im = im.convert("RGB")
            w = min(960, im.width)
            im.resize((w, round(im.height * w / im.width)), Image.Resampling.LANCZOS).save(
                OUT / f"{name}.webp", "WEBP", quality=78, method=6)
    height, gap = 520, 16
    tiles = []
    for file in STRIP:
        with Image.open(src / file) as im:
            im = im.convert("RGB")
            tiles.append(im.resize((round(im.width * height / im.height), height), Image.Resampling.LANCZOS))
    strip = Image.new("RGB", (sum(t.width for t in tiles) + gap * (len(tiles) - 1), height), (246, 244, 241))
    x = 0
    for t in tiles:
        strip.paste(t, (x, 0))
        x += t.width + gap
    strip.save(OUT / "strip.webp", "WEBP", quality=78, method=6)
    print("wrote", len(PICTURES) + 1, "pictures to", OUT)


if __name__ == "__main__":
    main()
