"""Live view with flash exposure: brightness of a frame, auto ISO steps, software gain.

With a flash the camera sits in M at about 1/125 s, f/5.6, ISO 200. Its live
view simulates that exposure without the flash that only fires at the shot:
almost black (booth PC, 15.11.2026). The owner wants a bright live view and
the flash only at the shot. Two tools, both pure functions here:

* a preview exposure of its own (CameraService switches the camera to it
  between shots); ``"auto"`` walks a ladder until the mean brightness of the
  live frame sits in a band: the ISO a stop at a time up to a cap, then the
  shutter slower down to a floor (``next_auto_step``);
* a software gain on the frames the booth serves (a lookup table), for what
  the camera's own settings cannot reach — on request, and always once the
  ladder is at its end. Never applied to a captured picture.
"""
from __future__ import annotations

import io
from typing import Optional

from PIL import Image, ImageStat

#: Full stops the automatic preview ISO moves through, up to
#: ``camera.preview_iso_max``. A noisy live view is fine; a black one is not
#: (booth PC, 15.11.2026: mean 3/255 in a dim room, guests saw nothing).
ISO_STOPS = ("100", "200", "400", "800", "1600", "3200", "6400", "12800", "25600")
ISO_MAX_CHOICES = ("3200", "6400", "12800", "25600")
ISO_MAX_DEFAULT = "12800"
#: Full-stop shutter speeds the automatic preview walks once the ISO is at its
#: cap, from the shot's shutter down to ``camera.preview_shutter_min``. A live
#: view may smear a little at 1/30; it must not be black.
SHUTTER_STOPS = ("1/4000", "1/2000", "1/1000", "1/500", "1/250", "1/125", "1/60", "1/30", "1/15")
SHUTTER_MIN_CHOICES = ("1/60", "1/30", "1/15")
SHUTTER_MIN_DEFAULT = "1/30"
#: Mean brightness (0–255) a live frame should have: a face reads, nothing burns out.
TARGET_BAND = (80.0, 150.0)
#: Software gain: aimed at this mean, never more than this factor. 8 is three
#: stops: the last rung after ISO and shutter, on frames that are mostly noise.
GAIN_TARGET = 115.0
GAIN_CAP = 8.0
#: Above this output level the gain bends into a soft shoulder instead of
#: clipping, so a lamp or a window in a dark room stays a shape.
GAIN_KNEE = 192.0
#: A picture taken with the flash darker than this (mean, 0–255) did not get
#: its light. The M50 wrote Flash=9 ("fired") into a frame of mean 2/255 on
#: the booth PC, so EXIF is no evidence; the pixels are.
DARK_MEAN = 12.0


def jpeg_mean(data: bytes, scale_to: int = 160) -> Optional[float]:
    """Mean brightness of a JPEG (0–255), decoded at a reduced scale; None if unreadable."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            return image_mean(im, scale_to)
    except Exception:                                 # noqa: BLE001 — a frame we cannot read says nothing
        return None


def image_mean(im: Image.Image, scale_to: int = 160) -> float:
    """Mean brightness of an opened image; a JPEG is decoded at 1/8 scale or smaller."""
    w, h = im.size
    if im.format == "JPEG" and max(w, h) > scale_to:
        im.draft("L", (max(1, w * scale_to // max(w, h)), max(1, h * scale_to // max(w, h))))
    g = im.convert("L")
    if max(g.size) > scale_to:
        g.thumbnail((scale_to, scale_to))
    return float(ImageStat.Stat(g).mean[0])


def file_mean(path) -> Optional[float]:
    try:
        with Image.open(path) as im:
            return image_mean(im)
    except Exception:                                 # noqa: BLE001
        return None


def _num(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def shutter_seconds(value) -> Optional[float]:
    """"1/125" -> 0.008, "0.5" -> 0.5, "2" -> 2.0; None if it is no shutter speed."""
    text = str(value or "").strip()
    try:
        if "/" in text:
            a, b = text.split("/", 1)
            return float(a) / float(b)
        return float(text)
    except (ValueError, ZeroDivisionError):
        return None


def next_auto_iso(current: str, mean: Optional[float], band=TARGET_BAND,
                  cap: str = ISO_MAX_DEFAULT, refused=frozenset()) -> Optional[str]:
    """The next preview ISO, one full stop at a time, never above ``cap``; None = stay.

    ``current`` may be a third stop ("250") or "auto": it moves to the
    nearest full stop in the needed direction. A value in ``refused`` (the
    camera said no) ends the axis in that direction: nothing at or beyond it.
    """
    if mean is None:
        return None
    low, high = band
    value = _num(current, 400.0)
    limit = _num(cap, _num(ISO_MAX_DEFAULT, 12800.0))
    if mean < low:
        for s in ISO_STOPS:
            v = float(s)
            if v <= value:
                continue
            if v > limit or s in refused:
                return None
            return s
        return None
    if mean > high:
        for s in reversed(ISO_STOPS):
            if float(s) < value:
                return None if s in refused else s
    return None


def next_auto_shutter(current: str, shot: str, mean: Optional[float], band=TARGET_BAND,
                      floor: str = SHUTTER_MIN_DEFAULT, refused=frozenset()) -> Optional[str]:
    """The next preview shutter speed; None = stay.

    Darker than the band: one full stop slower, never slower than ``floor``.
    Brighter: one full stop faster, never faster than the shot's own shutter,
    which may be a third stop ("1/100") and is then the last step back.
    """
    if mean is None:
        return None
    cur, base, lim = shutter_seconds(current), shutter_seconds(shot), shutter_seconds(floor)
    if cur is None or base is None:
        return None
    lim = lim if lim is not None else shutter_seconds(SHUTTER_MIN_DEFAULT)
    low, high = band
    eps = 1e-9
    if mean < low:
        for s in SHUTTER_STOPS:
            v = shutter_seconds(s)
            if v <= cur * (1 + 1e-6):
                continue
            if v > lim + eps or s in refused:
                return None
            return s
        return None
    if mean > high and cur > base * (1 + 1e-6):
        faster = [s for s in SHUTTER_STOPS if base * (1 + 1e-6) < shutter_seconds(s) < cur * (1 - 1e-6)]
        step = faster[-1] if faster else shot
        return None if step in refused else step
    return None


def next_auto_step(iso: str, shutter: Optional[str], shot_shutter: Optional[str], mean: Optional[float], *,
                   iso_max: str = ISO_MAX_DEFAULT, shutter_min: str = SHUTTER_MIN_DEFAULT,
                   auto_shutter: bool = True, refused=frozenset(), band=TARGET_BAND):
    """One rung of the automatic live-view ladder: ``("iso"|"shutter", value)`` or None.

    Too dark: the ISO first, stop by stop up to ``iso_max``, then (with
    ``auto_shutter``) the shutter slower down to ``shutter_min``. Too bright:
    the reverse order, the shutter back to the shot's first, then the ISO
    down. ``refused`` holds ``(axis, value)`` pairs the camera turned down.
    The aperture is never touched: it changes the depth of field of the shot.
    """
    if mean is None:
        return None
    low, high = band
    iso_refused = {v for a, v in refused if a == "iso"}
    tv_refused = {v for a, v in refused if a == "shutter"}
    can_shutter = auto_shutter and shutter and shot_shutter
    if mean < low:
        step = next_auto_iso(iso, mean, band, iso_max, iso_refused)
        if step:
            return ("iso", step)
        if can_shutter:
            step = next_auto_shutter(shutter, shot_shutter, mean, band, shutter_min, tv_refused)
            if step:
                return ("shutter", step)
        return None
    if mean > high:
        if can_shutter:
            step = next_auto_shutter(shutter, shot_shutter, mean, band, shutter_min, tv_refused)
            if step:
                return ("shutter", step)
        step = next_auto_iso(iso, mean, band, iso_max, iso_refused)
        if step:
            return ("iso", step)
    return None


def gain_factor(setting, mean: Optional[float]) -> float:
    """The factor for ``flash.preview_gain``: a percentage (0–100, +100 % = twice as bright)
    or ``"auto"`` (stretch the frame's mean towards GAIN_TARGET, at most GAIN_CAP)."""
    if isinstance(setting, str) and setting.strip().lower() == "auto":
        if not mean or mean <= 0:
            return GAIN_CAP if mean == 0 else 1.0
        return max(1.0, min(GAIN_CAP, GAIN_TARGET / mean))
    try:
        pct = float(setting or 0)
    except (TypeError, ValueError):
        return 1.0
    return 1.0 + max(0.0, min(100.0, pct)) / 100.0


def gain_lut(factor: float) -> list:
    """A linear gain up to GAIN_KNEE, then a soft shoulder that never reaches 255.

    Linear, not a gamma: on a frame that is mostly sensor noise a gamma lifts
    the lowest values (the noise floor) the most and turns the picture into
    grey mush; a linear factor keeps the contrast between a face and the noise.
    """
    import math
    head = 255.0 - GAIN_KNEE
    lut = []
    for v in range(256):
        y = v * factor
        if y > GAIN_KNEE:
            y = GAIN_KNEE + (head - 2.0) * (1.0 - math.exp(-(y - GAIN_KNEE) / head))
        lut.append(min(255, int(y + 0.5)))
    return lut


def brighten_jpeg(data: bytes, setting, quality: int = 80) -> bytes:
    """A live frame with the software gain applied (a lookup table per channel).

    Returns the frame unchanged when the factor is 1 or the frame cannot be read.
    """
    mean = jpeg_mean(data) if isinstance(setting, str) else None
    factor = gain_factor(setting, mean)
    if factor <= 1.001:
        return data
    try:
        with Image.open(io.BytesIO(data)) as im:
            rgb = im.convert("RGB")
        out = rgb.point(gain_lut(factor) * 3)
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
    except Exception:                                 # noqa: BLE001 — a dark frame beats none
        return data
