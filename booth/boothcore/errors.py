"""Fehler in zwei Sprachen: eine für Gäste, eine für den Betreiber.

Gäste sehen nie „EDSDK 0x00000081". Sie sehen, was zu tun ist. Der
Betreiber sieht im Verwaltungsbereich und im Log die technische Ursache.
"""
from __future__ import annotations

CAMERA_HINTS = [
    ("keine canon-kamera", "camera.off"),
    ("nicht gefunden", "camera.off"),
    ("0x00000081", "camera.busy"),          # DEVICE_BUSY
    ("0x00008d01", "camera.focus"),         # AF fehlgeschlagen
    ("0x00008d06", "camera.card"), ("0x00008d07", "camera.card"), ("0x00008d08", "camera.card"),
    ("0x00008d0a", "camera.flash"),
    ("0x00008d0b", "camera.lens"), ("0x00008d0f", "camera.lens"),
    ("0x00008d03", "camera.mode"), ("0x00008d05", "camera.mode"), ("0x00008d0c", "camera.mode"),
    ("0x00008d0d", "camera.mode"), ("0x00008d0e", "camera.mode"),
    ("kein jpeg", "camera.format"),
    ("0x0000a102", "camera.busy"),
    ("0x000000c0", "camera.mode"),          # Session nicht offen / falscher Modus
    ("0x00000002", "camera.mode"),
    ("timeout", "camera.timeout"),
    ("edsdk nicht gefunden", "camera.sdk"),
]


def camera_code(message: str | None) -> str:
    m = (message or "").lower()
    for needle, code in CAMERA_HINTS:
        if needle in m:
            return code
    return "camera.generic" if m else ""


def printer_code(state: str | None, detail: str | None = None) -> str:
    return {"offline": "printer.offline", "error": "printer.error", "none": "printer.none"}.get(state or "", "")
