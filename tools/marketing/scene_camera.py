"""A booth camera plugin that draws a lakeside summer scene instead of filming.

Used by tools/marketing_shots.mjs so the product screenshots show something
nicer than the mock camera's test card, and still no person: the pictures are
drawn with Pillow, nothing is downloaded and no photo of anyone is used.

The booth loads it like any plugin (copied into <BOOTH_DATA>/plugins) and the
local configuration selects it with ``camera.backend = "szene"``. Every capture
is a different time of day and a slightly different view, so a gallery of
twenty runs does not look like twenty copies.
"""
from __future__ import annotations

import io
import math
import random
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

try:  # inside the booth; the __main__ preview below works without it
    from boothcore.camera.base import CameraBackend
    from boothcore.plugins import hookimpl
except ImportError:  # pragma: no cover
    CameraBackend = object

    def hookimpl(f):
        return f

LIVE = (1200, 800)
FULL = (3000, 2000)

# sky top, sky at the horizon, sun, far hills, near hills, water, jetty, bunting
PALETTES = [
    ((86, 140, 196), (246, 214, 170), (255, 236, 196), (120, 148, 160), (70, 104, 92), (74, 128, 156),
     (122, 86, 60), [(214, 92, 72), (240, 190, 80), (74, 140, 132), (236, 236, 226), (92, 120, 176)]),
    ((120, 120, 170), (250, 186, 120), (255, 222, 160), (150, 120, 130), (82, 82, 84), (110, 112, 140),
     (104, 70, 52), [(232, 120, 84), (246, 206, 110), (140, 170, 120), (240, 232, 214), (196, 96, 112)]),
    ((40, 52, 96), (214, 140, 150), (255, 210, 190), (86, 84, 118), (46, 50, 70), (60, 70, 108),
     (70, 52, 46), [(238, 200, 120), (222, 120, 130), (150, 200, 196), (240, 236, 220), (180, 150, 220)]),
    ((60, 132, 210), (196, 226, 240), (255, 250, 230), (126, 168, 168), (80, 132, 96), (60, 140, 170),
     (138, 100, 70), [(226, 70, 70), (250, 204, 64), (60, 150, 120), (250, 250, 244), (60, 110, 190)]),
]


def _lerp(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def _ridge(rnd, w, base, amp, step):
    phase = rnd.random() * 10
    return [(x, base - amp * (0.55 * math.sin(x / w * 5.1 + phase) + 0.3 * math.sin(x / w * 13.7 + phase * 2)
                              + 0.15 * math.sin(x / w * 31.0 + phase * 3)))
            for x in range(0, w + step, step)]


def draw_scene(w: int, h: int, variant: int, t: float = 0.0) -> Image.Image:
    rnd = random.Random(variant * 7919)
    sky_top, sky_low, sun_c, far, near, water, jetty, flags = PALETTES[variant % len(PALETTES)]
    horizon = int(h * (0.52 + 0.06 * rnd.random()))
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(0, horizon, 2):
        d.rectangle([0, y, w, y + 2], fill=_lerp(sky_top, sky_low, (y / horizon) ** 1.4))
    sx = int(w * (0.25 + 0.5 * rnd.random()))
    sy = int(horizon * (0.45 + 0.35 * rnd.random()))
    glow = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(glow)
    for r, a in ((int(h * 0.28), 40), (int(h * 0.16), 70), (int(h * 0.09), 120)):
        gd.ellipse([sx - r, sy - r, sx + r, sy + r], fill=tuple(int(c * a / 255) for c in sun_c))
    glow = glow.filter(ImageFilter.GaussianBlur(h * 0.05))
    img = Image.fromarray(np.clip(np.asarray(img, dtype="int16") + np.asarray(glow, dtype="int16"),
                                  0, 255).astype("uint8"))
    d = ImageDraw.Draw(img)
    rs = int(h * 0.045)
    d.ellipse([sx - rs, sy - rs, sx + rs, sy + rs], fill=sun_c)
    step = max(4, w // 300)
    for col, base, amp in ((far, horizon - h * 0.02, h * 0.07), (near, horizon + 2, h * 0.035)):
        d.polygon(_ridge(rnd, w, base, amp, step) + [(w, horizon + 4), (0, horizon + 4)], fill=col)
    for y in range(horizon, h, 2):
        k = (y - horizon) / (h - horizon)
        d.rectangle([0, y, w, y + 2], fill=_lerp(_lerp(water, sky_low, 0.35), water, k ** 0.7))
    for i in range(int((h - horizon) / (h * 0.012))):
        y = horizon + i * h * 0.012 + 3
        spread = (w * 0.03) * (1 + i * 0.35)
        wob = math.sin(i * 1.7 + t * 2.2) * w * 0.01
        d.rectangle([sx - spread / 2 + wob, y, sx + spread / 2 + wob, y + h * 0.004],
                    fill=_lerp(sun_c, water, 0.25 + min(0.6, i * 0.03)))
    for _ in range(rnd.randint(1, 3)):
        bx = rnd.random() * w + math.sin(t * 0.3) * w * 0.004
        by = horizon + h * (0.03 + rnd.random() * 0.06)
        bs = h * (0.02 + rnd.random() * 0.02)
        d.polygon([(bx, by), (bx + bs * 0.05, by - bs * 1.6), (bx + bs * 0.9, by)], fill=(246, 242, 232))
        d.polygon([(bx - bs * 0.3, by + 2), (bx + bs * 1.1, by + 2), (bx + bs * 0.8, by + bs * 0.25),
                   (bx, by + bs * 0.25)], fill=_lerp(jetty, (40, 40, 40), 0.3))
    jx = w * (0.08 + 0.2 * rnd.random())
    top_y = horizon + h * 0.12
    d.polygon([(jx + w * 0.10, top_y), (jx + w * 0.16, top_y), (jx + w * 0.42, h), (jx - w * 0.08, h)], fill=jetty)
    for i in range(14):
        k = (i / 14) ** 1.8
        y = top_y + (h - top_y) * k
        d.line([(jx + w * 0.10 - w * 0.18 * k, y), (jx + w * 0.16 + w * 0.26 * k, y)],
               fill=_lerp(jetty, (20, 16, 12), 0.35), width=max(1, int(h * 0.002 * (1 + 4 * k))))
    sag = h * 0.07
    for row in range(2):
        yb = h * 0.05 + row * h * 0.07
        n = 16
        pts = [(w * i / n, yb + sag * math.sin(math.pi * i / n) + math.sin(t * 1.4 + i + row) * h * 0.004)
               for i in range(n + 1)]
        d.line(pts, fill=(60, 50, 40), width=max(1, int(h * 0.002)))
        for i in range(n):
            (x0, ya), (x1, yb2) = pts[i], pts[i + 1]
            col = flags[(i + row * 2 + variant) % len(flags)]
            d.polygon([(x0 + 2, ya), (x1 - 2, yb2), ((x0 + x1) / 2, (ya + yb2) / 2 + h * 0.06)], fill=col)
    img = img.filter(ImageFilter.GaussianBlur(max(0.6, h / 1400)))
    a = np.asarray(img, dtype="float32")
    yy, xx = np.mgrid[0:h, 0:w]
    a *= (1 - 0.14 * (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2))[..., None]
    a += np.random.default_rng(variant).normal(0, 3.0, a.shape[:2])[..., None]
    out = Image.fromarray(np.clip(a, 0, 255).astype("uint8"))
    # every second round of four the view is from the other side of the jetty
    return out.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if (variant // 4) % 2 else out


class SceneCamera(CameraBackend):
    name = "szene"
    has_preview = True
    settable = ("iso", "aperture", "shutter", "wb")

    def __init__(self):
        self._t0 = time.monotonic()
        self._shots = 0

    def open(self) -> None:
        self._t0 = time.monotonic()

    def apply_settings(self, settings: dict) -> dict:
        return {"applied": {}, "failed": {}}

    def preview_jpeg(self, max_width: int = 1280):
        # The live picture shows the view the next capture will have, so the
        # countdown and the result match.
        im = draw_scene(*LIVE, variant=self._shots, t=time.monotonic() - self._t0)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return buf.getvalue()

    def capture(self, target: Path) -> Path:
        draw_scene(*FULL, variant=self._shots, t=time.monotonic() - self._t0).save(target, "JPEG", quality=90)
        self._shots += 1
        return target

    def info(self) -> dict:
        return {"backend": self.name, "model": "Demo scene (drawn)"}


@hookimpl
def booth_register_cameras():
    return {"szene": lambda cfg: SceneCamera()}


if __name__ == "__main__":  # quick look: python scene_camera.py /tmp/x  -> /tmp/x-0.jpg ...
    import sys
    for v in range(4):
        draw_scene(1200, 800, v).save(f"{sys.argv[1]}-{v}.jpg", quality=88)
