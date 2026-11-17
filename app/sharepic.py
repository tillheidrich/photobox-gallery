"""Sharepic 9:16 (1080×1920) — schlicht: Galeriename, QR-Code, Adresse.

WhatsApp-Status und Instagram-Story schneiden oben und unten ~250 px ab bzw.
legen dort Bedienelemente darüber. Alles Wichtige liegt deshalb im mittleren Bereich.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

from app import brand, db, media, qrstyle

W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 250, 250


def _font(size: int, bold: bool):
    """Schrift der Marke: Inter arbeitet — der Titel nimmt weiter unten Archivo."""
    return brand.schrift(size, bold=bold)


# ── Gestaltung ────────────────────────────────────────────
#
# Zwei Ebenen: global für alle Events, und je Event, wenn ein Kunde etwas
# eigenes braucht. Das Event gewinnt, wo es etwas gesetzt hat; alles andere
# fällt auf die globale Einstellung zurück. Genau ein Wert pro Feld — kein
# Mischen, sonst erklärt niemand mehr, warum ein Bild aussieht, wie es aussieht.

#: Was gestaltbar ist, mit Standard. Die Reihenfolge ist die des Formulars.
FELDER = {
    "background": "blur",        # blur | image | color | dark | light
    "bg_path": "",               # eigenes Hintergrundbild (Pfad unter data/)
    "blur": 14,                  # Weichzeichnen des Hintergrunds, 0–40
    "dim": 45,                   # Abdunkeln in Prozent, damit Text lesbar bleibt
    "bg_color": "",              # eigene Hintergrundfarbe (#rrggbb), nur bei "color"
    "eyebrow": "Alle Fotos",     # kleine Zeile über dem Titel
    "title": "",                 # leer = Name des Events
    "align": "center",           # center | left | right — Ausrichtung des Titelblocks
    "hint": "",                  # Zeile unter dem QR-Code
    "show_url": True,
}
PREFIX = "sharepic_"


def _zahl(wert, standard: int, klein: int, gross: int) -> int:
    try:
        return max(klein, min(gross, int(float(wert))))
    except (TypeError, ValueError):
        return standard


def _farbe(wert: str) -> str:
    from app import branding
    return branding.normalize_hex(str(wert or "")) or ""


def _sauber(roh: dict) -> dict:
    """Aus Formular- oder JSON-Werten eine gültige Gestaltung machen."""
    aus = {}
    for key, standard in FELDER.items():
        if key not in roh:
            continue
        wert = roh[key]
        if key == "background":
            aus[key] = wert if wert in ("blur", "image", "color", "dark", "light") else standard
        elif key == "blur":
            aus[key] = _zahl(wert, standard, 0, 40)
        elif key == "dim":
            aus[key] = _zahl(wert, standard, 0, 85)
        elif key == "bg_color":
            aus[key] = _farbe(wert)
        elif key == "align":
            aus[key] = wert if wert in ("center", "left", "right") else standard
        elif key == "show_url":
            aus[key] = bool(wert)
        else:
            aus[key] = str(wert or "").strip()[:120]
    return aus


def global_design() -> dict:
    """Die globale Gestaltung aus den Einstellungen."""
    roh = {}
    for key, standard in FELDER.items():
        wert = db.get_setting(PREFIX + key)
        if wert is None:
            continue
        roh[key] = (wert == "1") if isinstance(standard, bool) else wert
    return {**FELDER, **_sauber(roh)}


def save_global(daten: dict) -> None:
    sauber = _sauber(daten)
    for key, wert in sauber.items():
        db.set_setting(PREFIX + key, "1" if wert is True else ("0" if wert is False else str(wert)))


def event_design(event: Optional[dict]) -> dict:
    """Was dieses Event selbst gesetzt hat — nur das, nicht der Rest."""
    from app import branding
    design = branding.parse_design((event or {}).get("design_json"))
    eigenes = design.get("sharepic")
    return _sauber(eigenes) if isinstance(eigenes, dict) else {}


def settings(event: Optional[dict] = None) -> dict:
    """Die fertige Gestaltung: global, vom Event überschrieben, plus QR-Teil."""
    try:
        style = json.loads(db.get_setting("qr_style_json") or "{}")
    except ValueError:
        style = {}
    cfg = {**global_design(), **event_design(event)}
    cfg["qr_style"] = qrstyle.normalize(style)
    cfg["qr_logo_path"] = (db.get_setting("qr_logo_path") or "").strip()
    return cfg


#: Die kompakte Marke in der Mitte des QR-Codes, solange kein eigenes Logo
#: hochgeladen ist. Erzeugt von ``tools/build_qr_mark.py``.
MARKE_IM_QR = Path(__file__).resolve().parent / "static" / "img" / "brand" / "qr-mark.png"


def logo_path(cfg: dict) -> Optional[Path]:
    """Welches Logo in die Mitte kommt: das des Betreibers, sonst unseres.

    Mit Logo schaltet der Code auf Fehlerkorrektur H — er bleibt also lesbar,
    auch wenn ein Viertel der Fläche verdeckt ist. Geprüft wird das nicht mit
    dem Auge, sondern mit einem Decoder (siehe tests/test_qr_logo.py).
    """
    rel = cfg.get("qr_logo_path")
    if rel:
        p = media.data_dir() / rel
        if p.exists():
            return p
    return MARKE_IM_QR if MARKE_IM_QR.is_file() else None


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if draw.textlength(cand, font=font) <= max_w:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def background_source(event: dict) -> Optional[Path]:
    if event.get("cover_path"):
        p = media.data_dir() / event["cover_path"]
        if p.exists():
            return p
    for m in db.list_media(event["id"], limit=12) or []:
        rel = m.get("path_webp")
        if rel and (media.data_dir() / rel).exists():
            return media.data_dir() / rel
    return None


def _hintergrund(event: dict, cfg: dict) -> tuple:
    """Den Hintergrund bauen und sagen, ob er hell ist.

    Hell oder dunkel entscheidet nicht der Geschmack, sondern die Helligkeit
    des Ergebnisses: Danach richtet sich die Schriftfarbe. Sonst schreibt ein
    eigenes Hintergrundbild den Text unlesbar.
    """
    mode = cfg.get("background") or "blur"
    quelle = None
    if mode == "image" and cfg.get("bg_path"):
        p = media.data_dir() / str(cfg["bg_path"])
        quelle = p if p.exists() else None
    elif mode == "blur":
        quelle = background_source(event)

    if quelle:
        with Image.open(quelle) as im:
            bg = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W // 4, H // 4),
                              Image.Resampling.BILINEAR)
        weich = _zahl(cfg.get("blur"), 14, 0, 40)
        if weich:
            bg = bg.filter(ImageFilter.GaussianBlur(weich))
        bg = bg.resize((W, H), Image.Resampling.BICUBIC)
        dunkler = _zahl(cfg.get("dim"), 45, 0, 85) / 100
        if dunkler:
            bg = Image.blend(bg, Image.new("RGB", (W, H), brand.rgb("ink", "1000")), dunkler)
        return bg, _ist_hell(bg)

    if mode == "color" and cfg.get("bg_color"):
        farbe = ImageColor.getrgb(cfg["bg_color"])
        bg = Image.new("RGB", (W, H), farbe)
        return bg, _ist_hell(bg)

    hell = mode == "light"
    return Image.new("RGB", (W, H), brand.rgb("ink", "50") if hell else brand.rgb("ink", "950")), hell


def _ist_hell(bild: Image.Image) -> bool:
    """Durchschnittliche Helligkeit des Bildes — grob, aber ehrlich."""
    klein = bild.resize((16, 28), Image.Resampling.BILINEAR).convert("L")
    from PIL import ImageStat
    return ImageStat.Stat(klein).mean[0] > 140


def render(event: dict, url: str, cfg: Optional[dict] = None) -> bytes:
    cfg = cfg or settings(event)
    bg, light = _hintergrund(event, cfg)
    # Farben aus tokens.json — dieselbe Quelle wie das CSS, damit ein
    # Sharepic nicht neben der Galerie steht, aus der es stammt.
    fg = brand.rgb("ink", "900") if light else brand.rgb("ink", "50")
    muted = brand.rgb("ink", "600") if light else brand.rgb("ink", "300")
    d = ImageDraw.Draw(bg)

    # Titelblock. Die Ausrichtung gehört dem Betreiber: Manche Marken setzen
    # alles links, und ein zentrierter Titel sähe daneben falsch aus.
    rand = 70
    ausrichtung = str(cfg.get("align") or "center")
    x, anker = {"left": (rand, "la"), "right": (W - rand, "ra")}.get(ausrichtung, (W // 2, "ma"))
    y = SAFE_TOP + 70
    if cfg["eyebrow"]:
        f = _font(38, False)
        d.text((x, y), cfg["eyebrow"].upper(), font=f, fill=muted, anchor=anker)
        y += 80
    titel = str(cfg.get("title") or "").strip() or event["name"]
    title_font = brand.schrift(96, bold=True, display=True)
    lines = _wrap(d, titel, title_font, W - 2 * rand)[:3]
    if len(lines) == 3:
        title_font = brand.schrift(78, bold=True, display=True)
        lines = _wrap(d, titel, title_font, W - 2 * rand)[:3]
    for line in lines:
        d.text((x, y), line, font=title_font, fill=fg, anchor=anker)
        y += int(title_font.size * 1.12)

    # QR-Karte
    card = 700
    qr_size = 580
    cy = max(y + 70, (H - card) // 2 + 40)
    cy = min(cy, H - SAFE_BOTTOM - card - 150)
    cx = (W - card) // 2
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((cx, cy + 18, cx + card, cy + card + 18), radius=56, fill=(0, 0, 0, 90))
    bg = Image.alpha_composite(bg.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(24))).convert("RGB")
    d = ImageDraw.Draw(bg)
    style = cfg["qr_style"]
    d.rounded_rectangle((cx, cy, cx + card, cy + card), radius=56, fill=ImageColor.getrgb(style["light"]))
    qr = qrstyle.render(url, qr_size, style, logo_path(cfg))
    bg.paste(qr, (cx + (card - qr_size) // 2, cy + (card - qr_size) // 2))

    # Hinweis + Adresse
    y = cy + card + 56
    if cfg["hint"]:
        d.text((W // 2, y), cfg["hint"], font=_font(40, False), fill=muted, anchor="ma")
    if cfg["show_url"]:
        short = url.split("://", 1)[-1].split("?", 1)[0]
        f = _font(40, True)
        while d.textlength(short, font=f) > W - 120 and f.size > 24:
            f = _font(f.size - 2, True)
        d.text((W // 2, y + 70), short, font=f, fill=fg, anchor="ma")

    out = BytesIO()
    bg.save(out, "PNG", optimize=True)
    return out.getvalue()
