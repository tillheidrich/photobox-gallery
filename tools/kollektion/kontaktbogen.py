"""Contact sheets of the collection: one per family, one overview.

    python -m tools.kollektion.kontaktbogen            # all, into docs/vorlagen/
    python -m tools.kollektion.kontaktbogen gruen      # one family

Every sheet is the real renderer's output with the sample pictures, scaled
down for viewing — what the booth prints, not a mock-up.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

WURZEL = Path(__file__).resolve().parents[2]
if str(WURZEL) not in sys.path:
    sys.path.insert(0, str(WURZEL))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from tools.kollektion import bauen, familien  # noqa: E402

ZIEL = WURZEL / "docs" / "vorlagen"
FONTS = WURZEL / "booth" / "boothcore" / "media" / "fonts"
GRUND = "#e8e5e0"
REIHE = ("quer-1", "quer-3", "quer-4", "quer-6", "hoch-1", "hoch-4", "hoch-6", "streifen-3", "streifen-4")


def _schrift(groesse: int, fett: bool = False):
    return ImageFont.truetype(str(FONTS / ("Inter-SemiBold.ttf" if fett else "Inter-Regular.ttf")), groesse)


def _blatt(bild: Image.Image, breite: int) -> Image.Image:
    k = bild.resize((breite, round(bild.height * breite / bild.width)), Image.Resampling.LANCZOS)
    out = Image.new("RGB", (k.width + 2, k.height + 2), "#bdb8b0")
    out.paste(k, (1, 1))
    return out


def familienbogen(fam: Dict, bilder: Dict[str, Image.Image]) -> Image.Image:
    """Landscape layouts in the first row, portrait ones in the second."""
    W, rand, luft = 2400, 60, 36
    quer = [l for l in REIHE if l in bilder and bilder[l].width > bilder[l].height]
    hoch = [l for l in REIHE if l in bilder and bilder[l].width <= bilder[l].height]
    qb = (W - 2 * rand - (len(quer) - 1) * luft) // max(1, len(quer))
    hb = min(520, (W - 2 * rand - (len(hoch) - 1) * luft) // max(1, len(hoch)))
    qh, hh = qb * 2 // 3, hb * 3 // 2
    H = rand + 80 + qh + 60 + hh + 60 + rand
    out = Image.new("RGB", (W, H), GRUND)
    d = ImageDraw.Draw(out)
    d.text((rand, rand - 10), f"{fam['nummer']} · {fam['name']}", font=_schrift(48, True), fill="#2a2724")
    klein = _schrift(26)
    y = rand + 80
    for i, lid in enumerate(quer):
        x = rand + i * (qb + luft)
        out.paste(_blatt(bilder[lid], qb), (x, y))
        d.text((x, y + qh + 10), familien.GEOMETRIE[lid]["name"], font=klein, fill="#57524c")
    y += qh + 60
    x0 = (W - (len(hoch) * hb + (len(hoch) - 1) * luft)) // 2
    for i, lid in enumerate(hoch):
        x = x0 + i * (hb + luft)
        out.paste(_blatt(bilder[lid], hb), (x, y))
        d.text((x, y + hh + 10), familien.GEOMETRIE[lid]["name"], font=klein, fill="#57524c")
    return out


def uebersicht(alle: Dict[str, tuple]) -> Image.Image:
    spalten, W, rand, luft = 4, 2400, 60, 40
    bw = (W - 2 * rand - (spalten - 1) * luft) // spalten
    bh = bw * 2 // 3
    zeilen = (len(alle) + spalten - 1) // spalten
    H = rand + 90 + zeilen * (bh + 80 + luft) + rand
    out = Image.new("RGB", (W, H), GRUND)
    d = ImageDraw.Draw(out)
    d.text((rand, rand - 10), "Vorlagen-Bibliothek · alle Familien", font=_schrift(48, True), fill="#2a2724")
    for n, (fam, bilder) in enumerate(alle.values()):
        r, c = divmod(n, spalten)
        x, y = rand + c * (bw + luft), rand + 90 + r * (bh + 80 + luft)
        out.paste(_blatt(bilder["quer-3"], bw), (x, y))
        d.text((x, y + bh + 12), f"{fam['nummer']} · {fam['name']}", font=_schrift(30, True), fill="#2a2724")
        d.text((x, y + bh + 48), fam["kategorie"], font=_schrift(24), fill="#6b665f")
    return out


def main(argv: List[str]) -> int:
    nur = [a for a in argv if not a.startswith("--")] or None
    ZIEL.mkdir(parents=True, exist_ok=True)
    alle: Dict[str, tuple] = {}
    for fam, lid, v, _ in bauen.bauen(nur):
        alle.setdefault(fam["id"], (fam, {}))[1][lid] = bauen.rendern(v)
    for fid, (fam, bilder) in alle.items():
        bogen = familienbogen(fam, bilder)
        bogen.save(ZIEL / f"{fam['nummer']:02d}-{fid}.png", optimize=True)
        print(f"{fam['nummer']:02d}-{fid}.png")
    if not nur:
        uebersicht(alle).save(ZIEL / "00-uebersicht.png", optimize=True)
        print("00-uebersicht.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
