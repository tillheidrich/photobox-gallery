"""Das Blatt, das aus dem Drucker kommt: Papierformat, Ränder, Streifen.

Fotodrucker kennen keine Streifen. Wer 5x15-Streifen will, druckt ein
10x15-Blatt mit zwei Streifen nebeneinander und lässt den Drucker in der Mitte
schneiden (bei DNP eine Treibereinstellung, „2 inch cut"). Genauso machen es
die gängigen Fotobox-Programme, und genauso macht es die Box hier.

Alles, was hier passiert, ist reines Einpassen von Pixeln. Die Aufnahme selbst
bleibt unangetastet; das Blatt entsteht als eigene Datei kurz vor dem Druck.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from PIL import Image

# Papierformate in Millimetern, immer als (lange Kante, kurze Kante).
# Die Box dreht später, wenn das Bild hochkant ist.
PAPERS: Dict[str, Tuple[float, float]] = {
    "10x15": (152.4, 101.6),     # 6x4 Zoll, der Standard bei DNP und SELPHY
    "postcard": (148.0, 100.0),  # SELPHY Postkarte
    "13x18": (177.8, 127.0),
    "15x20": (203.2, 152.4),     # 8x6 Zoll, DNP DS620 und ähnliche
    "5x15": (152.4, 50.8),       # 2x6 Zoll — der klassische Streifen
    "card": (86.0, 54.0),        # SELPHY Kartengröße
}

# Welche Formate sinnvoll zu zweit auf ein Blatt passen, und auf welches.
SHEET_FOR_STRIP = {"5x15": "10x15"}


@dataclass
class Layout:
    paper: str = "10x15"
    per_sheet: int = 1
    margin_mm: float = 0.0
    dpi: int = 300
    fit: str = "cover"           # cover = beschneiden, contain = ganz sichtbar mit Rand
    rotate_to_fit: bool = True
    background: str = "#ffffff"

    @classmethod
    def from_profile(cls, data: dict) -> "Layout":
        data = data or {}
        paper = data.get("paper") if data.get("paper") in PAPERS else "10x15"
        per_sheet = 2 if int(data.get("per_sheet") or 1) == 2 else 1
        return cls(
            paper=paper,
            per_sheet=per_sheet,
            margin_mm=max(0.0, min(20.0, float(data.get("margin_mm") or 0))),
            dpi=int(data.get("dpi") or 300),
            fit="contain" if data.get("fit") == "contain" else "cover",
            rotate_to_fit=data.get("rotate_to_fit", True) is not False,
            background=str(data.get("background") or "#ffffff"),
        )

    @property
    def sheet_paper(self) -> str:
        """Auf welches Papier das Blatt tatsächlich geht."""
        if self.per_sheet == 2:
            return SHEET_FOR_STRIP.get(self.paper, self.paper)
        return self.paper


def mm_zu_px(mm: float, dpi: int) -> int:
    """Millimeter in Pixel. Öffentlich, weil die Kalibrierungsseite dasselbe
    Maß braucht — zwei Umrechnungen nebeneinander wären zwei Wahrheiten."""
    return max(1, int(round(mm / 25.4 * dpi)))


_px = mm_zu_px          # der alte Name, damit diese Datei unverändert bleibt


def sheet_size_px(layout: Layout, portrait: bool) -> Tuple[int, int]:
    """Blattgröße in Pixeln. ``portrait`` dreht das Papier hochkant."""
    long_mm, short_mm = PAPERS[layout.sheet_paper]
    w, h = (short_mm, long_mm) if portrait else (long_mm, short_mm)
    return _px(w, layout.dpi), _px(h, layout.dpi)


def _fit(image: Image.Image, box: Tuple[int, int], mode: str, background: str) -> Image.Image:
    target_w, target_h = box
    if mode == "contain":
        out = Image.new("RGB", box, background)
        copy = image.copy()
        copy.thumbnail(box, Image.LANCZOS)
        out.paste(copy, ((target_w - copy.width) // 2, (target_h - copy.height) // 2))
        return out
    # cover: Seitenverhältnis halten, Überstand mittig abschneiden
    scale = max(target_w / image.width, target_h / image.height)
    size = (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale))))
    scaled = image.resize(size, Image.LANCZOS)
    left = (scaled.width - target_w) // 2
    top = (scaled.height - target_h) // 2
    return scaled.crop((left, top, left + target_w, top + target_h))


def compose_sheet(source: Path, layout: Layout, target: Path) -> Path:
    """Aus einer Aufnahme das Blatt machen, das gedruckt wird."""
    with Image.open(source) as im:
        image = im.convert("RGB")

        # Ein hochkantes Bild auf querformatiges Papier: das Papier drehen,
        # nicht das Bild. Sonst steht der Druck auf dem Kopf.
        portrait = image.height > image.width
        if layout.per_sheet == 2:
            # Ein 5x15-Streifen ist hochkant. Zwei davon nebeneinander ergeben
            # 10x15 hochkant, nicht quer: 2 x 50,8 mm breit, 152,4 mm hoch.
            portrait = True
        elif not layout.rotate_to_fit:
            portrait = PAPERS[layout.sheet_paper][1] > PAPERS[layout.sheet_paper][0]

        sheet_w, sheet_h = sheet_size_px(layout, portrait)
        sheet = Image.new("RGB", (sheet_w, sheet_h), layout.background)
        margin = _px(layout.margin_mm, layout.dpi)

        if layout.per_sheet == 2:
            # Zwei gleiche Streifen nebeneinander; der Drucker schneidet in der Mitte.
            cell_w = sheet_w // 2
            cell = (max(1, cell_w - 2 * margin), max(1, sheet_h - 2 * margin))
            fitted = _fit(image, cell, layout.fit, layout.background)
            for i in range(2):
                sheet.paste(fitted, (i * cell_w + margin, margin))
        else:
            cell = (max(1, sheet_w - 2 * margin), max(1, sheet_h - 2 * margin))
            sheet.paste(_fit(image, cell, layout.fit, layout.background), (margin, margin))

    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, "JPEG", quality=95, dpi=(layout.dpi, layout.dpi))
    return target


def describe(layout: Layout) -> str:
    """Ein Satz für die Verwaltung und die Selbstdiagnose."""
    if layout.per_sheet == 2:
        return (f"Zwei {layout.paper}-Streifen auf einem {layout.sheet_paper}-Blatt. "
                "Der Drucker muss auf dieses Blatt eingestellt sein und in der Mitte schneiden.")
    return f"Ein Bild auf {layout.paper}."
