"""Branding pro Event — Logo und Akzentfarbe je Galerie.

Global gibt es weiterhin ein Logo für die ganze Installation. Ein Event kann
das überschreiben: eigenes Logo, eigene Akzentfarbe. Das ist die Voraussetzung
dafür, dass ein Vermieter am selben Wochenende drei Kunden mit drei Marken
bedienen kann.

Wichtiger Grundsatz (von Picdrop abgeschaut): Der Betreiber darf sich **nicht**
in einen unlesbaren Zustand konfigurieren. Deshalb rechnen wir die Textfarbe
auf dem Akzent serverseitig aus, statt sie einstellbar zu machen.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex(value: Optional[str]) -> str:
    """Gibt '#rrggbb' zurück oder '' wenn die Eingabe unbrauchbar ist."""
    v = (value or "").strip()
    if not v:
        return ""
    if not v.startswith("#"):
        v = "#" + v
    if not HEX_RE.match(v):
        return ""
    if len(v) == 4:                      # #abc -> #aabbcc
        v = "#" + "".join(c * 2 for c in v[1:])
    return v.lower()


def _srgb_channel(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG-Relativluminanz (0 = schwarz, 1 = weiß)."""
    h = normalize_hex(hex_color) or "#000000"
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * _srgb_channel(r) + 0.7152 * _srgb_channel(g) + 0.0722 * _srgb_channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def on_color(hex_color: str) -> str:
    """Lesbare Textfarbe auf der Akzentfarbe — dunkel oder hell, je nach Kontrast."""
    dark, light = "#1a1206", "#ffffff"
    return dark if contrast_ratio(hex_color, dark) >= contrast_ratio(hex_color, light) else light


def parse_design(raw: Optional[str]) -> Dict[str, Any]:
    """design_json robust einlesen — kaputtes JSON darf keine Galerie zerlegen."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def dump_design(data: Dict[str, Any]) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


# ── Schriften ────────────────────────────────────────────────
# Klein und kuratiert. Alle drei sind Gerätestapel: kein Google-Fonts-Aufruf,
# kein Nachladen, keine IP-Adresse an Dritte — und nichts, was beim Laden
# springt. Wer eine eigene Schrift lizenziert hat, legt sie als @font-face in
# die eigene CSS-Datei; die Auswahl hier bleibt die sichere Voreinstellung.
FONTS: Dict[str, str] = {
    "system": '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
    "serif": 'Georgia, "Iowan Old Style", "Palatino Linotype", Palatino, "Times New Roman", serif',
    "geometric": '"Avenir Next", Avenir, Futura, "Century Gothic", "Trebuchet MS", system-ui, sans-serif',
}
FONT_IDS = list(FONTS)

# Ab hier gilt Text als lesbar (WCAG AA für normalen Text).
AA_TEXT = 4.5
AA_LARGE = 3.0


def font_id(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    return v if v in FONTS else ""


def font_stack(value: Optional[str]) -> str:
    return FONTS.get(font_id(value), "")


def _global_design() -> Dict[str, str]:
    """Branding der Installation — der Rückfall für jedes Event."""
    from app import db
    return {
        "accent": normalize_hex(db.get_setting("branding_accent")),
        "bg": normalize_hex(db.get_setting("branding_bg")),
        "text": normalize_hex(db.get_setting("branding_text")),
        "font": font_id(db.get_setting("branding_font")),
        "banner_path": (db.get_setting("branding_banner_path") or "").strip(),
    }


def event_branding(event: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Aufbereitetes Branding für die Templates.

    Reihenfolge: Event → Installation → Standard-Theme (also „nichts setzen").

    Rückgabe:
      accent      – '#rrggbb' oder '' (dann gilt das Standard-Theme)
      accent_on   – lesbare Textfarbe auf dem Akzent
      bg / text   – Hintergrund- und Textfarbe der Galerie
      font/font_stack – Kennung und CSS-Stapel der gewählten Schrift
      logo_url    – '/data/…' oder '' (dann gilt das globale Logo)
      banner_url  – Bannerbild der Galerie oder ''
      has_custom  – ob überhaupt etwas Eigenes gesetzt ist
    """
    design = parse_design((event or {}).get("design_json"))
    try:
        fallback = _global_design()
    except Exception:                    # ohne DB (z. B. reiner Funktionstest)
        fallback = {"accent": "", "bg": "", "text": "", "font": "", "banner_path": ""}

    accent = normalize_hex(design.get("accent")) or fallback["accent"]
    bg = normalize_hex(design.get("bg")) or fallback["bg"]
    text = normalize_hex(design.get("text")) or fallback["text"]
    font = font_id(design.get("font")) or fallback["font"]
    logo_rel = (design.get("logo_path") or "").strip()
    banner_rel = (design.get("banner_path") or "").strip() or fallback["banner_path"]

    # Der Betreiber darf sich nicht in einen unlesbaren Zustand konfigurieren:
    # fehlt die Textfarbe, rechnen wir sie aus dem Hintergrund.
    if bg and not text:
        text = on_color(bg)

    return {
        "accent": accent,
        "accent_on": on_color(accent) if accent else "",
        "bg": bg,
        "text": text,
        "font": font,
        "font_stack": FONTS.get(font, ""),
        "logo_url": f"/data/{logo_rel}" if logo_rel else "",
        "banner_url": f"/data/{banner_rel}" if banner_rel else "",
        "has_custom": bool(accent or bg or text or font or logo_rel or banner_rel),
    }


def mix_hex(a: str, b: str, t: float) -> str:
    """Zwei Farben mischen; ``t=0`` ist ``a``, ``t=1`` ist ``b``."""
    ha, hb = normalize_hex(a) or "#000000", normalize_hex(b) or "#ffffff"
    t = min(1.0, max(0.0, t))
    parts = []
    for i in (1, 3, 5):
        ca, cb = int(ha[i:i + 2], 16), int(hb[i:i + 2], 16)
        parts.append(round(ca + (cb - ca) * t))
    return "#" + "".join(f"{v:02x}" for v in parts)


def lift(hex_color: str, amount: float) -> str:
    """Fläche vom Hintergrund abheben — heller bei dunklem, dunkler bei hellem."""
    toward = "#ffffff" if relative_luminance(hex_color) < 0.5 else "#000000"
    return mix_hex(hex_color, toward, amount)


def css_vars(brand: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """CSS-Variablen für das Style-Fragment — nur was wirklich gesetzt ist.

    Eine eigene Hintergrundfarbe zieht die ganze Palette mit: Flächen, Linien
    und abgestufte Textfarben werden daraus gerechnet. Sonst stünde weißer Text
    auf weißen Karten — genau der unlesbare Zustand, den wir ausschließen.
    """
    b = brand or {}
    out: Dict[str, str] = {}
    if b.get("accent"):
        out["--color-primary"] = b["accent"]
        out["--color-primary-hover"] = b["accent"]
        out["--color-on-primary"] = b.get("accent_on") or on_color(b["accent"])
        out["--color-link"] = b["accent"]
    bg = b.get("bg")
    if bg:
        out["--color-bg"] = bg
        out["--color-surface-2"] = bg                 # Seitenhintergrund
        out["--color-surface"] = lift(bg, 0.06)       # Karten, Kopfzeile
        out["--color-surface-3"] = lift(bg, 0.13)     # Felder, Chips
        out["--color-border"] = lift(bg, 0.22)
        out["--color-border-subtle"] = lift(bg, 0.14)
    text = b.get("text")
    if text:
        out["--color-text"] = text
        out["--color-text-2"] = mix_hex(text, bg or lift(text, 1.0), 0.3)
        out["--color-text-secondary"] = out["--color-text-2"]
        out["--color-text-muted"] = mix_hex(text, bg or lift(text, 1.0), 0.5)
    if b.get("font_stack"):
        out["--font"] = b["font_stack"]
    return out


def contrast_warnings(brand: Optional[Dict[str, Any]]) -> list:
    """Kombinationen unter WCAG AA — als Warnung, nicht als Verbot.

    Jeder Eintrag: ``{"kind": …, "ratio": 2.1, "need": 4.5}``. Der Aufrufer
    übersetzt die Meldung; gespeichert wird trotzdem.
    """
    b = brand or {}
    bg = b.get("bg") or "#ffffff"
    warnings = []
    if b.get("text"):
        ratio = contrast_ratio(bg, b["text"])
        if ratio < AA_TEXT:
            warnings.append({"kind": "text_on_bg", "ratio": round(ratio, 2), "need": AA_TEXT})
    if b.get("accent") and (b.get("bg") or b.get("accent")):
        ratio = contrast_ratio(bg, b["accent"])
        if ratio < AA_LARGE:
            warnings.append({"kind": "accent_on_bg", "ratio": round(ratio, 2), "need": AA_LARGE})
    return warnings


# ── The installation's logo ──────────────────────────────────
def is_dark(hex_color: Optional[str]) -> bool:
    """A surface on which a dark logo would vanish."""
    h = normalize_hex(hex_color)
    return bool(h) and relative_luminance(h) < 0.3


def global_logo(dark_surface: bool = False) -> Dict[str, Any]:
    """Where the logo comes from, for every page that shows it (admin, login, client area, gallery).

    ``light``: the normal logo (for light surfaces), ``dark``: the optional one
    for dark surfaces. ``src`` is the one for the surface asked for; on a dark
    surface without a dark logo the normal one is shown on a light chip
    (``chip`` True) so it stays readable. The address carries the file's
    time, so browsers may cache it and still see a new logo at once.
    """
    from app import db, media

    def addr(key: str, variant: str) -> str:
        rel = (db.get_setting(key) or "").strip()
        if not rel:
            return ""
        try:
            stamp = int((media.data_dir() / rel).stat().st_mtime)
        except OSError:
            return ""
        return f"/branding/logo?{'variant=dark&' if variant == 'dark' else ''}v={stamp}"

    light = addr("branding_logo_path", "light")
    dark = addr("branding_logo_dark_path", "dark")
    if dark_surface:
        src, chip = (dark, False) if dark else (light, bool(light))
    else:
        src, chip = (light, False) if light else (dark, bool(dark))
    return {"has": bool(light or dark), "light": light, "dark": dark, "src": src, "chip": chip}
