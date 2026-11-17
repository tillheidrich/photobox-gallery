"""Frames for boomerang and video ("Rahmen") -- the same file in gallery and booth.

ACHTUNG: This file exists twice (app/motion_frames.py and
booth/boothcore/media/motion_frames.py); tools/sync_editor.py copies the
app/ version to the booth and a test compares both byte for byte. Change
the one in app/, then run the tool.

Owner, 17.11.2026: "9:16-Hintergründe und Querformat-Hintergründe hochladen,
die dann als Hintergrund für Boomerang oder Video funktionieren, wie bei der
Einzelbild-Druckvariante." A motion frame is a small JSON file of its own kind
(``"kind": "motion_frame"``) next to the print templates: a canvas in one of
three formats, a background (picture or colour), one window the film plays
in, an optional overlay PNG with transparency above it, and text lines with
the placeholders and fonts the print templates use. Nothing in it moves:
the booth renders the still layers once, keeps them on disk under a key of
everything that changes them, and only the window is encoded per frame.

Layers, bottom to top: background (with its shapes and the window's
shadow), the film in the window ("cover": scaled and cropped to fill it,
any aspect), overlay, window border, texts. Rounded window corners are made
by the top layer: it carries the background's pixels outside the rounding.

No QR code: the films carry none today (the link is on the result screen
and the print), so a frame has no QR layer either.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageOps

from . import vorlagen_kern, vorlagen_render

KIND = "motion_frame"
#: Canvas of each format in pixels. 9:16 and 16:9 are the phone formats of
#: the boomerang and the film; 4:5 is the photo format a boomerang can be
#: chosen in (``formats``), so it gets a canvas too.
FORMATS: Dict[str, Tuple[int, int]] = {"9:16": (1080, 1920), "16:9": (1920, 1080), "4:5": (1080, 1350)}
MODES = ("boomerang", "video")
#: The pseudo id of "Ohne Rahmen" in choices and in the kiosk.
NONE_ID = "none"
#: The window must be large enough to see someone in it.
MIN_WINDOW = 120
MAX_BORDER = 40
MAX_SHADOW = 60
#: Bump when the way layers are made changes, so cached files are not reused.
REVISION = 1

_HEX = vorlagen_kern._HEX


class FrameError(ValueError):
    """The frame cannot be used; the message is a German sentence for the operator."""


def is_motion_frame(data: Any) -> bool:
    return isinstance(data, dict) and data.get("kind") == KIND


def _int(value: Any, low: int, high: int, default: int) -> int:
    return vorlagen_kern._zahl(value, low, high, default)


def _colour(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if _HEX.match(text) else default


def check(raw: Any, *, media: Optional[Path] = None) -> Tuple[Dict[str, Any], List[str]]:
    """A valid frame from what the editor, an upload or an assistant sends.

    Returns the cleaned frame and what was put right (like the print
    templates' check). Raises :class:`FrameError` for what cannot be put
    right: unknown format, no window, a file name that is none, and with
    ``media`` a background or overlay that is not among the media.
    """
    if not isinstance(raw, dict):
        raise FrameError("Ein Rahmen ist ein JSON-Objekt.")
    errors: List[str] = []
    hints: List[str] = []
    if raw.get("kind") != KIND:
        errors.append(f"kind muss \"{KIND}\" sein, sonst ist es eine Druckvorlage.")
    fmt = str(raw.get("format") or "")
    if fmt not in FORMATS:
        errors.append(f"Unbekanntes Format „{fmt[:12]}“ — möglich sind {', '.join(FORMATS)}.")
        fmt = "9:16"
    width, height = FORMATS[fmt]
    canvas = raw.get("canvas")
    if canvas and list(canvas)[:2] != [width, height]:
        hints.append(f"Die Fläche folgt dem Format: {width}×{height} Pixel.")
    name = str(raw.get("name") or "Eigener Rahmen").strip()[:60] or "Eigener Rahmen"
    fid = vorlagen_kern.slug(raw.get("id") or name)
    if fid == NONE_ID:
        fid = "rahmen-none"

    background = str(raw.get("background") or "").strip()
    if not background:
        background = "#111111"
        hints.append("Ohne Hintergrund wird die Fläche dunkelgrau (#111111).")
    elif background.startswith("#"):
        if not _HEX.match(background):
            errors.append("Der Hintergrund ist eine Farbe wie #1a1a1a oder ein Bild aus den Medien.")
    elif not vorlagen_kern.datei_name(background, (".png", ".jpg", ".jpeg", ".webp")):
        errors.append(f"Der Hintergrund „{background[:60]}“ ist kein Bildname aus den Medien "
                      "(PNG oder JPG, ohne Pfad).")
    background_to = _colour(raw.get("background_to"))
    if raw.get("background_to") and not background_to:
        hints.append("Die zweite Verlaufsfarbe ist keine Farbe wie #222222 und wurde weggelassen.")

    overlay = str(raw.get("overlay") or "").strip()
    if overlay and not vorlagen_kern.datei_name(overlay, (".png",)):
        errors.append("Das Overlay muss eine PNG-Datei mit Transparenz aus den Medien sein.")

    win = raw.get("window")
    window: Dict[str, Any] = {}
    if not isinstance(win, dict):
        errors.append("Es fehlt das Videofenster: window mit x, y, w und h in Pixeln.")
    else:
        parsed = True
        try:
            w = int(round(float(win["w"])))
            h = int(round(float(win["h"])))
            x = int(round(float(win.get("x", 0))))
            y = int(round(float(win.get("y", 0))))
        except (KeyError, TypeError, ValueError, OverflowError):
            errors.append("Das Videofenster braucht Zahlen für x, y, w und h (Pixel).")
            w = h = x = y = 0
            parsed = False
        if parsed:
            if w < MIN_WINDOW or h < MIN_WINDOW:
                errors.append(f"Das Videofenster ist zu klein: mindestens {MIN_WINDOW}×{MIN_WINDOW} Pixel.")
            if w > width or h > height:
                errors.append(f"Das Videofenster ist größer als die Fläche ({width}×{height}).")
            nx, ny = max(0, min(x, width - w)), max(0, min(y, height - h))
            if (nx, ny) != (x, y) and w <= width and h <= height:
                hints.append("Das Videofenster ragte über den Rand und wurde hineingeschoben.")
            window = {"x": nx, "y": ny, "w": w, "h": h,
                      "radius": _int(win.get("radius"), 0, max(0, min(w, h) // 2), 0)}
            border = win.get("border") if isinstance(win.get("border"), dict) else {}
            bw = _int(border.get("width"), 0, MAX_BORDER, 0)
            if bw:
                window["border"] = {"width": bw, "color": _colour(border.get("color"), "#ffffff")}
            shadow = _int(win.get("shadow"), 0, MAX_SHADOW, 0)
            if shadow:
                window["shadow"] = shadow

    # Texts and shapes: the print templates' own check, on a sheet of this size.
    pseudo = {"canvas": [width, height], "slots": [{"x": 0, "y": 0, "w": width, "h": height}],
              "texts": raw.get("texts") or [], "shapes": raw.get("shapes") or []}
    try:
        checked, _ = vorlagen_kern._pruefe(pseudo)
    except (TypeError, AttributeError, ValueError, IndexError):
        errors.append("texts und shapes sind Listen von Objekten (wie bei den Druckvorlagen).")
        checked = {"texts": [], "shapes": []}
    texts = checked.get("texts") or []
    shapes = checked.get("shapes") or []
    modes = [m for m in (raw.get("modes") or list(MODES)) if m in MODES] or list(MODES)

    if media is not None:
        for label, file in (("Hintergrundbild", background), ("Overlay", overlay)):
            if file and not file.startswith("#") and not (Path(media) / file).is_file():
                errors.append(f"{label} „{file}“ fehlt in den Medien — erst hochladen (add_asset), "
                              "dann den Rahmen speichern.")
    if errors:
        raise FrameError(" ".join(errors))
    frame: Dict[str, Any] = {"kind": KIND, "id": fid, "name": name, "format": fmt, "canvas": [width, height],
                             "modes": modes, "background": background, "window": window,
                             "updated_at": int(raw.get("updated_at") or time.time())}
    if background_to and background.startswith("#"):
        frame["background_to"] = background_to
    if overlay:
        frame["overlay"] = overlay
    if texts:
        frame["texts"] = texts
    if shapes:
        frame["shapes"] = shapes
    if raw.get("builtin") is True:
        frame["builtin"] = True
    return frame, hints


def file_name(frame: Dict[str, Any]) -> str:
    """Own frames are stored as ``rahmen-<id>.json`` among the media."""
    return f"rahmen-{frame['id']}.json"


def content(frame: Dict[str, Any]) -> bytes:
    return vorlagen_kern.inhalt(frame)


def media_of(frame: Dict[str, Any]) -> List[str]:
    """Files the frame names: background picture, overlay, uploaded fonts."""
    out = []
    for value in (frame.get("background"), frame.get("overlay")):
        name = str(value or "").strip()
        if name and not name.startswith("#"):
            out.append(name)
    for t in frame.get("texts") or []:
        font = str((t or {}).get("font") or "")
        if font.lower().endswith((".ttf", ".otf")) and font not in out:
            out.append(font)
    return out


# ── Geometry ──────────────────────────────────────────────

def output_size(frame: Dict[str, Any], short_edge: Optional[int] = None) -> Tuple[int, int]:
    """The film's size: the canvas, its short edge brought to ``short_edge`` when given.

    Both sides even (yuv420p needs that).
    """
    cw, ch = FORMATS.get(frame.get("format"), tuple(frame.get("canvas") or (1080, 1920)))
    short = min(cw, ch)
    k = 1.0 if not short_edge else max(240, int(short_edge)) / short
    w, h = int(round(cw * k)), int(round(ch * k))
    return w - w % 2, h - h % 2


def window_box(frame: Dict[str, Any], size: Sequence[int]) -> Tuple[int, int, int, int]:
    """(x, y, w, h) of the window in a film of ``size``."""
    cw, _ch = FORMATS.get(frame.get("format"), tuple(frame.get("canvas") or (1080, 1920)))
    k = size[0] / cw
    win = frame.get("window") or {}
    x, y = int(round(win.get("x", 0) * k)), int(round(win.get("y", 0) * k))
    w, h = max(2, int(round(win.get("w", 2) * k))), max(2, int(round(win.get("h", 2) * k)))
    return x, y, min(w, size[0] - x), min(h, size[1] - y)


def window_aspect(frame: Dict[str, Any]) -> float:
    win = frame.get("window") or {}
    return float(win.get("w") or 1) / float(win.get("h") or 1)


def fit(picture: Image.Image, w: int, h: int) -> Image.Image:
    """The picture scaled and cropped to fill ``w`` x ``h``, centred ("cover")."""
    return ImageOps.fit(picture.convert("RGB"), (int(w), int(h)), Image.Resampling.BILINEAR)


# ── Layers ────────────────────────────────────────────────

def _gradient(size: Tuple[int, int], top: str, bottom: str) -> Image.Image:
    a, b = ImageColor.getrgb(top)[:3], ImageColor.getrgb(bottom)[:3]
    strip = Image.new("RGB", (1, 256))
    for i in range(256):
        strip.putpixel((0, i), tuple(int(round(a[c] + (b[c] - a[c]) * i / 255)) for c in range(3)))
    return strip.resize(size, Image.Resampling.BILINEAR)


def _rounded(size: Tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _pseudo(frame: Dict[str, Any]) -> Dict[str, Any]:
    """The frame as the print renderer's text functions expect a template."""
    return {"canvas": list(FORMATS[frame["format"]]), "texts": frame.get("texts") or [],
            "paper": "", "slots": []}


def render_canvas_layers(frame: Dict[str, Any], assets_dir: Optional[Path], texts: Optional[Dict[str, str]],
                         style_cfg: Optional[Dict] = None) -> Tuple[Image.Image, Image.Image]:
    """(bottom RGB, top RGBA) at canvas size."""
    vr = vorlagen_render
    st = vr.style(style_cfg)
    size = tuple(FORMATS[frame["format"]])
    bg = str(frame.get("background") or "#111111")
    if bg.startswith("#") and frame.get("background_to"):
        bottom = _gradient(size, bg, frame["background_to"])
    else:
        bottom = vr._background(size, bg, Path(assets_dir) if assets_dir else None)
    for shape in frame.get("shapes") or []:
        bottom = vr._lege(bottom, shape, vr._mitte(shape), lambda z, f=shape: vr._form(z, f, st),
                          deckkraft=vr._deckkraft(shape))
    win = frame["window"]
    x, y, w, h, r = int(win["x"]), int(win["y"]), int(win["w"]), int(win["h"]), int(win.get("radius") or 0)
    shadow = int(win.get("shadow") or 0)
    if shadow:
        lage = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(lage).rounded_rectangle((x, y + shadow // 3, x + w - 1, y + h - 1 + shadow // 3),
                                               radius=r, fill=(0, 0, 0, 120))
        lage = lage.filter(ImageFilter.GaussianBlur(max(1, shadow // 2)))
        bottom = Image.alpha_composite(bottom.convert("RGBA"), lage).convert("RGB")

    top = Image.new("RGBA", size, (0, 0, 0, 0))
    if r:
        # The background outside the rounding covers the film's square corners.
        patch = bottom.crop((x, y, x + w, y + h)).convert("RGBA")
        patch.putalpha(_rounded((w, h), r).point(lambda a: 255 - a))
        top.paste(patch, (x, y))
    overlay = str(frame.get("overlay") or "")
    if overlay and assets_dir and (Path(assets_dir) / overlay).is_file():
        with Image.open(Path(assets_dir) / overlay) as ov:
            top = Image.alpha_composite(top, ov.convert("RGBA").resize(size, Image.Resampling.LANCZOS))
    border = win.get("border") or {}
    if int(border.get("width") or 0):
        bw = int(border["width"])
        lage = Image.new("RGBA", size, ImageColor.getrgb(border.get("color") or "#ffffff")[:3] + (0,))
        ImageDraw.Draw(lage).rounded_rectangle((x - bw, y - bw, x + w - 1 + bw, y + h - 1 + bw),
                                               radius=r + bw if r else 0,
                                               outline=border.get("color") or "#ffffff", width=bw)
        top = Image.alpha_composite(top, lage)
    values = vr.werte(texts, st)
    pseudo = _pseudo(frame)
    for t in frame.get("texts") or []:
        if t.get("hidden"):
            continue
        ink = vr._farbe(t.get("color"), st, st["ink"])
        satz = vr.textsatz(t, values, st, pseudo, assets_dir)
        lage = Image.new("RGBA", size, ImageColor.getrgb(ink)[:3] + (0,))
        vr._text(lage, t, values, st, assets_dir, satz)
        winkel = vr.drehung(t)
        if winkel:
            lage = lage.rotate(-winkel, resample=Image.Resampling.BICUBIC,
                               center=vr._text_mitte(t, values, st, assets_dir, satz))
        deck = vr._deckkraft(t)
        if deck < 100:
            lage.putalpha(lage.getchannel("A").point(lambda a, d=deck: a * d // 100))
        top = Image.alpha_composite(top, lage)
    return bottom, top


def render_layers(frame: Dict[str, Any], size: Sequence[int], assets_dir: Optional[Path],
                  texts: Optional[Dict[str, str]], style_cfg: Optional[Dict] = None) -> Tuple[Image.Image, Image.Image]:
    """(bottom, top) at the film's ``size``."""
    bottom, top = render_canvas_layers(frame, assets_dir, texts, style_cfg)
    size = (int(size[0]), int(size[1]))
    if bottom.size != size:
        bottom = bottom.resize(size, Image.Resampling.LANCZOS)
        top = top.resize(size, Image.Resampling.LANCZOS)
    return bottom, top


def layer_key(frame: Dict[str, Any], size: Sequence[int], assets_dir: Optional[Path],
              texts: Optional[Dict[str, str]], style_cfg: Optional[Dict] = None) -> str:
    """Everything that changes the still layers, hashed: frame, size, texts, style, media files."""
    files = []
    for name in media_of(frame):
        p = Path(assets_dir) / name if assets_dir else None
        try:
            st = p.stat() if p else None
            files.append([name, st.st_size, int(st.st_mtime)] if st else [name])
        except OSError:
            files.append([name])
    raw = json.dumps([REVISION, frame, list(size), texts or {}, style_cfg or {}, files],
                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def cached_layers(frame: Dict[str, Any], size: Sequence[int], assets_dir: Optional[Path],
                  texts: Optional[Dict[str, str]], cache_dir: Path,
                  style_cfg: Optional[Dict] = None) -> Dict[str, Any]:
    """The still layers as PNG files, rendered once per key.

    Returns ``{"bottom": Path, "top": Path, "box": (x, y, w, h), "size": (w, h), "key": str}``.
    """
    size = (int(size[0]), int(size[1]))
    key = layer_key(frame, size, assets_dir, texts, style_cfg)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    bottom_p, top_p = cache_dir / f"{key}-bottom.png", cache_dir / f"{key}-top.png"
    if not (bottom_p.is_file() and top_p.is_file()):
        bottom, top = render_layers(frame, size, assets_dir, texts, style_cfg)
        for img, p in ((bottom, bottom_p), (top, top_p)):
            tmp = p.with_suffix(".part")
            img.save(tmp, "PNG", compress_level=1)
            tmp.replace(p)
    return {"bottom": bottom_p, "top": top_p, "box": window_box(frame, size), "size": size, "key": key}


def compose(picture: Image.Image, bottom: Image.Image, top: Image.Image,
            box: Sequence[int]) -> Image.Image:
    """One finished frame: background, the picture in the window, top layer."""
    x, y, w, h = [int(v) for v in box]
    out = bottom.convert("RGB").copy()
    out.paste(fit(picture, w, h) if picture.size != (w, h) else picture.convert("RGB"), (x, y))
    return Image.alpha_composite(out.convert("RGBA"), top).convert("RGB")


def preview(frame: Dict[str, Any], assets_dir: Optional[Path], texts: Optional[Dict[str, str]] = None,
            still: Optional[Image.Image] = None, max_px: int = 600,
            style_cfg: Optional[Dict] = None) -> Image.Image:
    """The frame with a neutral sample picture in its window (tiles, editor, docs)."""
    bottom, top = render_canvas_layers(frame, assets_dir, texts or vorlagen_render.BEISPIEL_TEXTE, style_cfg)
    if still is None:
        still = vorlagen_render.beispielbilder(1)[0]
    win = frame["window"]
    out = compose(still, bottom, top, (win["x"], win["y"], win["w"], win["h"]))
    out.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    return out


# ── Built-ins ─────────────────────────────────────────────

def builtins(data_file: Path) -> List[Dict[str, Any]]:
    """The shipped frames (tools/build_motion_frames.py writes the file), each checked."""
    try:
        raw = json.loads(Path(data_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for item in raw.get("frames") or []:
        try:
            frame, _ = check(dict(item, builtin=True))
        except FrameError:
            continue
        out.append(frame)
    return out


def choice_ids(cfg: Dict[str, Any], builtin_ids: Sequence[str]) -> Tuple[List[str], List[str], bool]:
    """(own files, built-in ids, "Ohne Rahmen" allowed) a mode's settings name.

    Nothing named: every built-in. "Ohne Rahmen" is on unless switched off.
    """
    own = [str(x) for x in (cfg.get("frame_templates") or []) if str(x)]
    ids = [str(x) for x in (cfg.get("frame_builtins") or []) if str(x) in builtin_ids]
    if not own and not ids:
        ids = list(builtin_ids)
    return own, ids, cfg.get("frame_none", True) is not False


def default_choice(cfg: Dict[str, Any], offered: Sequence[str]) -> str:
    """The pre-selected choice: ``frame_default`` when offered, else the first one."""
    want = str(cfg.get("frame_default") or "")
    if want and want in offered:
        return want
    return offered[0] if offered else NONE_ID


__all__ = ["FORMATS", "FrameError", "KIND", "MODES", "NONE_ID", "REVISION", "builtins", "cached_layers",
           "check", "choice_ids", "compose", "content", "default_choice", "file_name", "fit", "is_motion_frame",
           "layer_key", "media_of", "output_size", "preview", "render_canvas_layers", "render_layers",
           "window_aspect", "window_box"]
