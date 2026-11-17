"""Webcam über OpenCV — Entwicklung am Laptop und günstiger Ersatz."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import CameraBackend, CameraError


class WebcamCamera(CameraBackend):
    name = "webcam"

    def __init__(self, index: int = 0):
        self.index = index
        self._cap = None

    def open(self) -> None:
        import cv2

        api = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") and __import__("os").name == "nt" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, api)
        if not cap.isOpened():
            raise CameraError(f"Webcam {self.index} nicht gefunden")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        self._cap = cap

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _frame(self):
        if self._cap is None:
            raise CameraError("Webcam nicht geöffnet")
        ok, frame = self._cap.read()
        if not ok:
            raise CameraError("Webcam liefert kein Bild")
        return frame

    def preview_jpeg(self, max_width: int = 1280) -> Optional[bytes]:
        import cv2

        frame = self._frame()
        h, w = frame.shape[:2]
        if w > max_width:
            frame = cv2.resize(frame, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72 if max_width <= 1280 else 88])
        return buf.tobytes() if ok else None

    def capture(self, target: Path) -> Path:
        import cv2

        for _ in range(2):  # Puffer leeren, sonst kommt ein altes Bild
            self._frame()
        frame = self._frame()
        cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 94])
        return target
