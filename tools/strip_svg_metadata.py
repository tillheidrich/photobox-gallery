"""SVG-Dateien der Marke von Herkunfts-Metadaten befreien.

Die Dateien aus der Markenquelle tragen einen C2PA-Block im ``<metadata>``.
Das ist in der Quelle richtig — dort belegt es die Herkunft. Ausgeliefert an
jeden Gast ist es Ballast: Ein Symbol von 300 Byte wächst damit auf 8 KB, und
auf einer Seite mit vierzig Symbolen sind das 300 KB, die über ein schlechtes
Netz an einem Veranstaltungsort niemand hat.

Gezeichnet wird nichts verändert — nur der Metadaten-Block fällt weg. Nach
einem erneuten Import aus der Markenquelle einfach wieder laufen lassen:

    python tools/strip_svg_metadata.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORDNER = [ROOT / "app" / "static" / "img",
          ROOT / "app" / "static" / "img" / "brand",
          ROOT / "app" / "static" / "img" / "icons",
          ROOT / "booth" / "boothcore" / "web" / "static" / "img",
          ROOT / "booth" / "boothcore" / "web" / "static" / "img" / "brand"]
BLOCK = re.compile(r"<metadata>.*?</metadata>", re.S)


def main() -> int:
    gespart = 0
    for ordner in ORDNER:
        for datei in sorted(ordner.glob("*.svg")):
            text = datei.read_text(encoding="utf-8")
            sauber = BLOCK.sub("", text)
            sauber = sauber.replace(' xmlns:c2pa="http://c2pa.org/manifest"', "")
            if sauber != text:
                gespart += len(text) - len(sauber)
                datei.write_text(sauber, encoding="utf-8")
    print(f"ok — {gespart // 1024} KB Metadaten entfernt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
