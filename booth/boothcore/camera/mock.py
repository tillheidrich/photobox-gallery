"""Mock-Kamera: die komplette Box läuft ohne Hardware.

Ohne das entwickelt man nur, wenn die Box aufgebaut ist — also nie.
"""
from __future__ import annotations

import io
import math
import os
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .base import CameraBackend

_W, _H = 1200, 800          # Live-Bild (3:2 wie die M50)
_SW, _SH = 3000, 2000       # "Foto" in voller Auflösung


def _font(size: int):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


class MockCamera(CameraBackend):
    name = "mock"
    settable = ("iso", "aperture", "shutter", "wb")
    settings: dict = {}

    def apply_settings(self, settings: dict) -> dict:
        applied = {k: v for k, v in (settings or {}).items() if v not in (None, "") and k in self.settable}
        self.settings = {**self.settings, **applied}
        return {"applied": applied, "failed": {}}

    def __init__(self):
        self._t0 = time.monotonic()
        self._font = _font(64)
        self._shots = 0
        #: What ``battery_level`` answers: None (unknown, the default), a
        #: percent or "ac". Tests set it; ``BOOTH_MOCK_BATTERY`` sets it at start.
        raw = os.environ.get("BOOTH_MOCK_BATTERY", "").strip().lower()
        self.battery_value = raw if raw == "ac" else (int(raw) if raw.isdigit() else None)
        # BOOTH_MOCK_MOVIE=<an .mp4>: the mock "films on the camera" with it.
        movie = os.environ.get("BOOTH_MOCK_MOVIE", "").strip()
        if movie:
            self.movie_file = Path(movie)

    def battery_level(self):
        return self.battery_value

    def open(self) -> None:
        self._t0 = time.monotonic()

    def _render(self, w: int, h: int, label: str) -> Image.Image:
        t = time.monotonic() - self._t0
        img = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(img)
        # ruhiger Farbverlauf
        for y in range(0, h, 4):
            k = y / h
            d.rectangle([0, y, w, y + 4], fill=(int(40 + 60 * k), int(52 + 30 * k), int(80 - 30 * k)))
        # "Personen": drei Kreise, leicht bewegt
        for i, col in enumerate([(236, 196, 160), (210, 170, 130), (240, 210, 180)]):
            cx = w * (0.3 + 0.2 * i) + math.sin(t * 1.3 + i) * w * 0.02
            cy = h * 0.55 + math.cos(t * 1.7 + i) * h * 0.02
            r = h * 0.12
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
            d.rectangle([cx - r * 1.2, cy + r * 1.1, cx + r * 1.2, h], fill=(30 + 40 * i, 40, 70))
        f = self._font if w <= 1600 else _font(int(64 * w / 1200))
        d.text((w * 0.04, h * 0.05), label, fill=(255, 255, 255), font=f)
        return img

    def preview_jpeg(self, max_width: int = 1280) -> Optional[bytes]:
        buf = io.BytesIO()
        self._render(_W, _H, time.strftime("LIVE %H:%M:%S")).save(buf, "JPEG", quality=70)
        return buf.getvalue()

    #: A movie file ``record_movie`` hands out (tests make one with ffmpeg);
    #: None = the mock cannot film, like every camera but the Canon SDK one.
    movie_file: Optional[Path] = None
    #: Where a test wants the movie to fail: "start" (before recording),
    #: "file" (recorded, no file), "download" (file, download breaks).
    movie_fail = ""
    movies_recorded = 0
    movie_restored = 0

    @property
    def can_record_movie(self) -> bool:
        return self.movie_file is not None

    def record_movie(self, seconds: float, folder: Path, *, tick=None, stopped=None) -> Path:
        from .base import CameraError
        import shutil
        try:
            if not self.movie_file:
                raise CameraError("Mock: kein Film eingestellt")
            if self.movie_fail == "start":
                raise CameraError("Mock: Filmstart abgelehnt")
            t0 = time.monotonic()
            end = t0 + float(seconds)
            while time.monotonic() < end:
                if tick:
                    tick(True)
                time.sleep(0.02)
            self.movies_recorded += 1
            if stopped:
                stopped(time.monotonic() - t0)
            if self.movie_fail == "file":
                raise CameraError("Mock: kein Film auf der Karte")
            if self.movie_fail == "download":
                raise CameraError("Mock: Download abgebrochen")
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"MVI_{self.movies_recorded:04d}.MP4"
            shutil.copyfile(self.movie_file, target)
            return target
        finally:
            self.movie_restored += 1

    def capture(self, target: Path) -> Path:
        self._shots += 1
        time.sleep(0.25)  # realistische Verzögerung
        self._render(_SW, _SH, f"FOTO #{self._shots}").save(target, "JPEG", quality=92)
        return target
