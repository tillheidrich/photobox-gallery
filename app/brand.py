"""Das Erscheinungsbild „Abzug" für alles, was Python zeichnet.

CSS liest die Marke aus ``photobox-brand.css``. Pillow kann das nicht: kein
oklch, kein woff2. Deshalb liegt dieselbe Marke zweimal vor — als CSS für den
Browser und als ``tokens.json`` samt TTF-Schnitten für Sharepic, Druckkarte
und QR-Code.

Beides kommt aus derselben Quelle (``_design/brand``). Wer eine Farbe ändern
will, ändert sie dort und spielt die Dateien neu ein — nicht hier, und schon
gar nicht als Hex-Wert mitten im Zeichencode.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

from PIL import ImageFont

BASE = Path(__file__).resolve().parent
TOKENS = BASE / "static" / "css" / "brand" / "tokens.json"
TTF = BASE / "static" / "fonts" / "ttf"

# Nur als Notnagel, falls tokens.json fehlt: die Galerie soll ein Sharepic
# zeichnen können, auch wenn jemand beim Ausrollen eine Datei vergisst.
NOTFALL = {"amber": {"500": "#d89534", "600": "#bf7b0f", "400": "#e7ad59"},
           "ink": {"0": "#fefdfc", "900": "#1d1a16", "950": "#0f0d09", "500": "#7f7973"}}


@lru_cache(maxsize=1)
def tokens() -> dict:
    try:
        return json.loads(TOKENS.read_text(encoding="utf-8"))
    except Exception:
        return NOTFALL


def farbe(gruppe: str, stufe: str = "500", fallback: str = "#d89534") -> str:
    """Eine Markenfarbe als Hex, z. B. ``farbe("amber", "600")``."""
    eintrag = (tokens().get(gruppe) or {}).get(str(stufe))
    if isinstance(eintrag, dict):
        return str(eintrag.get("hex") or fallback)
    if isinstance(eintrag, str):
        return eintrag
    return fallback


def rgb(gruppe: str, stufe: str = "500", fallback: str = "#d89534") -> Tuple[int, int, int]:
    h = farbe(gruppe, stufe, fallback).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))          # type: ignore[return-value]


# ── Schrift ───────────────────────────────────────────────
#
# Archivo zeigt (Titel, Wortmarke), Inter arbeitet (alles andere). Die
# statischen Schnitte liegen im Repo, weil Pillow keine variablen Achsen
# einstellen kann. Fehlt eine Datei, fällt die Reihe auf das zurück, was das
# System hat — ein Sharepic ohne Schrift wäre schlimmer als eines in DejaVu.
SCHNITTE = {
    ("display", True): ["Archivo-Bold.ttf", "Archivo-SemiBold.ttf"],
    ("display", False): ["Archivo-SemiBold.ttf"],
    ("text", True): ["Inter-SemiBold.ttf"],
    ("text", False): ["Inter-Regular.ttf"],
}
SYSTEM = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
          "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
SYSTEM_FETT = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]


def schrift(size: int, bold: bool = False, display: bool = False) -> ImageFont.FreeTypeFont:
    """Eine Schrift der Marke in der gewünschten Größe."""
    namen = SCHNITTE[("display" if display else "text", bool(bold))]
    pfade = [str(TTF / n) for n in namen] + (SYSTEM_FETT if bold else SYSTEM)
    for p in pfade:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def schrift_datei(bold: bool = False, display: bool = False) -> Optional[Path]:
    """Pfad des Schnitts — für Code, der die Datei selbst braucht."""
    for name in SCHNITTE[("display" if display else "text", bool(bold))]:
        p = TTF / name
        if p.is_file():
            return p
    return None
