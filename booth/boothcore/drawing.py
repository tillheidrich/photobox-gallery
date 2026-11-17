"""Guests draw on their picture: checking what the kiosk sends, painting it on.

The kiosk sends strokes as vectors, never as a bitmap: a colour, a width as a
share of the picture's width, and points from 0 to 1. The booth paints them
onto the finished picture at full resolution. A bitmap from the browser would
be as large as the screen, not as the print, and would let any page that can
reach the route put any picture onto a guest's print.

Everything here is pure: no session, no files. ``app.py`` decides who may
draw and when; this module decides what a drawing may contain and how it
looks. The smoothing is the same as the kiosk's (``drawSmooth`` in app.js,
quadratic curves through the midpoints), so the print shows the line the
guest saw.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter

# Hard limits. The kiosk keeps inside them by itself (drawPayload); whatever
# arrives above them did not come from the kiosk and is refused, not cut.
MAX_BYTES = 512 * 1024
MAX_STROKES = 120
MAX_POINTS_PER_STROKE = 1500
MAX_POINTS_TOTAL = 12000
# Width as a share of the picture's width: a hairline that still prints, up to
# a felt pen. Anything outside is clamped.
WIDTH_MIN, WIDTH_MAX = 0.002, 0.05
MODES = ("photo", "collage")
DEFAULT_COLORS = ["#ffffff", "#111111", "#e5484d", "#f5b700", "#2f80ed", "#27ae60"]
_HEX = re.compile(r"#[0-9a-f]{6}")
# Pixels of the supersampled mask per stroke. A signature is small and gets
# four times the resolution; a heart across a 24-megapixel picture gets less,
# so memory stays bounded whatever the camera delivers.
_MASK_BUDGET = 32_000_000


class DrawingError(ValueError):
    """A drawing the booth does not take. ``code`` is the answer's detail."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def allowed_colors(cfg: Optional[Dict[str, Any]]) -> List[str]:
    """The colours the operator offers, as lower-case ``#rrggbb``; never empty."""
    out: List[str] = []
    for c in (cfg or {}).get("colors") or []:
        c = str(c).strip().lower()
        if _HEX.fullmatch(c) and c not in out:
            out.append(c)
    return out[:8] or list(DEFAULT_COLORS)


def offered(profile: Dict[str, Any], mode: str) -> bool:
    """Switched on, and this kind of run has a still to draw on."""
    cfg = profile.get("draw") or {}
    return bool(cfg.get("enabled")) and mode in MODES and mode in (cfg.get("modes") or [])


def _no_constants(name: str):
    # Python's json takes NaN and Infinity; JSON does not, and neither do we.
    raise DrawingError("draw.number")


def parse(raw: bytes, allowed: Sequence[str]) -> List[Dict[str, Any]]:
    """The body of ``POST /api/session/{uid}/draw`` as clean strokes."""
    if len(raw) > MAX_BYTES:
        raise DrawingError("draw.too_large", 413)
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_no_constants)
    except DrawingError:
        raise
    except (ValueError, UnicodeDecodeError):
        raise DrawingError("draw.invalid")
    if not isinstance(data, dict):
        raise DrawingError("draw.invalid")
    return clean(data.get("strokes"), allowed)


def _number(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise DrawingError("draw.number")
    f = float(v)                      # an int beyond float range raises OverflowError
    if not math.isfinite(f):
        raise DrawingError("draw.number")
    return f


def clean(strokes: Any, allowed: Sequence[str]) -> List[Dict[str, Any]]:
    if strokes is None:
        return []
    if not isinstance(strokes, list):
        raise DrawingError("draw.invalid")
    if len(strokes) > MAX_STROKES:
        raise DrawingError("draw.strokes", 413)
    out: List[Dict[str, Any]] = []
    total = 0
    for st in strokes:
        if not isinstance(st, dict):
            raise DrawingError("draw.invalid")
        color = str(st.get("color") or "").strip().lower() if isinstance(st.get("color"), str) else ""
        if not _HEX.fullmatch(color) or color not in allowed:
            raise DrawingError("draw.color")
        try:
            width = min(WIDTH_MAX, max(WIDTH_MIN, _number(st.get("width"))))
        except OverflowError:
            raise DrawingError("draw.number")
        pts = st.get("points")
        if not isinstance(pts, list):
            raise DrawingError("draw.invalid")
        if len(pts) > MAX_POINTS_PER_STROKE:
            raise DrawingError("draw.points", 413)
        total += len(pts)
        if total > MAX_POINTS_TOTAL:
            raise DrawingError("draw.points", 413)
        cleaned: List[Tuple[float, float]] = []
        for p in pts:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise DrawingError("draw.invalid")
            try:
                x, y = _number(p[0]), _number(p[1])
            except OverflowError:
                raise DrawingError("draw.number")
            # A finger that slides off the picture is ordinary: it stays at the
            # edge instead of the whole drawing being refused.
            cleaned.append((min(1.0, max(0.0, x)), min(1.0, max(0.0, y))))
        if cleaned:
            out.append({"color": color, "width": width, "points": cleaned})
    return out


def segments(points: Sequence[Sequence[float]]) -> List[tuple]:
    """The path through the points, as the kiosk draws it.

    ``("M", x, y)`` to start, ``("Q", cx, cy, x, y)`` a quadratic curve with
    each point as control and the midpoint to the next as end, ``("L", x, y)``
    the last piece. Mirrors ``drawSmooth`` in app.js.
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    if not pts:
        return []
    out: List[tuple] = [("M", pts[0][0], pts[0][1])]
    for i in range(1, len(pts) - 1):
        (cx, cy), (nx, ny) = pts[i], pts[i + 1]
        out.append(("Q", cx, cy, (cx + nx) / 2, (cy + ny) / 2))
    if len(pts) > 1:
        out.append(("L", pts[-1][0], pts[-1][1]))
    return out


def _polyline(points, w: int, h: int, step_px: float) -> List[Tuple[float, float]]:
    """The smoothed path in pixels, curves broken into short straight pieces."""
    segs = segments(points)
    if not segs:
        return []
    sx, sy = w - 1, h - 1
    cur = (segs[0][1] * sx, segs[0][2] * sy)
    line = [cur]
    for seg in segs[1:]:
        if seg[0] == "L":
            cur = (seg[1] * sx, seg[2] * sy)
            line.append(cur)
            continue
        c = (seg[1] * sx, seg[2] * sy)
        e = (seg[3] * sx, seg[4] * sy)
        length = math.dist(cur, c) + math.dist(c, e)
        n = max(1, min(16, int(length / max(1.0, step_px)) + 1))
        for k in range(1, n + 1):
            t = k / n
            u = 1 - t
            line.append((u * u * cur[0] + 2 * u * t * c[0] + t * t * e[0],
                         u * u * cur[1] + 2 * u * t * c[1] + t * t * e[1]))
        cur = e
    return line


def render(img: Image.Image, strokes: Sequence[Dict[str, Any]]) -> Image.Image:
    """The strokes painted on a copy of ``img``, smooth at any resolution.

    Each stroke is drawn as a white-on-black mask in its own bounding box,
    supersampled (up to four times, within a fixed pixel budget) and reduced,
    then the colour is pasted through it: round caps and joins, soft edges,
    and never a second full-size copy of the picture in memory.
    """
    if not strokes:
        return img
    out = img.convert("RGB") if img.mode != "RGB" else img.copy()
    w, h = out.size
    for st in strokes:
        stroke_px = max(1.0, float(st["width"]) * w)
        r = stroke_px / 2
        line = _polyline(st["points"], w, h, max(2.0, r / 2))
        if not line:
            continue
        xs = [p[0] for p in line]
        ys = [p[1] for p in line]
        x0, y0 = max(0, int(math.floor(min(xs) - r - 2))), max(0, int(math.floor(min(ys) - r - 2)))
        x1, y1 = min(w, int(math.ceil(max(xs) + r + 2))), min(h, int(math.ceil(max(ys) + r + 2)))
        bw, bh = x1 - x0, y1 - y0
        if bw <= 0 or bh <= 0:
            continue
        ss = int(max(1, min(4, math.sqrt(_MASK_BUDGET / (bw * bh)))))
        mask = Image.new("L", (bw * ss, bh * ss), 0)
        d = ImageDraw.Draw(mask)
        pts = [((x - x0) * ss, (y - y0) * ss) for x, y in line]
        rr = r * ss
        if len(pts) > 1:
            d.line(pts, fill=255, width=max(1, int(round(stroke_px * ss))), joint="curve")
        for x, y in (pts[0], pts[-1]):          # round caps; one point is a dot
            d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=255)
        if ss > 1:
            mask = mask.reduce(ss)
        else:
            # No room to supersample (a huge stroke on a huge picture): a soft
            # edge of under a pixel does the same job.
            mask = mask.filter(ImageFilter.GaussianBlur(0.7))
        rgb = tuple(int(st["color"][i:i + 2], 16) for i in (1, 3, 5))
        out.paste(Image.new("RGB", (bw, bh), rgb), (x0, y0), mask)
    return out
