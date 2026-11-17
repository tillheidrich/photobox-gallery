"""Schnittstelle, die jede Kamera erfüllen muss.

Alle Methoden werden ausschließlich vom Kamera-Thread des ``CameraService``
aufgerufen. Das ist für das Canon-SDK Pflicht (es ist nicht thread-sicher) und
macht die anderen Backends einfacher: kein Backend muss selbst Locks halten.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


class CameraError(RuntimeError):
    pass


class CaptureRefused(CameraError):
    """The camera is there and answering, it only refused this one shot.

    "No focus", a full card, the dial on movie: the link is fine, so the
    camera loop must not reconnect and the camera must not look broken
    (a reconnect shows as "Die Kamera antwortet nicht" to the operator).
    The guest's screen still gets the reason through the message's code.

    ``code`` is the camera's EDS_ERR_TAKE_PICTURE_* value when known (0
    otherwise; the service then reads it from the message). Only "no focus"
    may repeat for ever: two other refusals in a row reconnect the camera.
    """

    def __init__(self, message: str = "", code: int = 0):
        super().__init__(message)
        self.code = int(code or 0)


def check_photo(path) -> None:
    """Is this a whole JPEG? Raises ``CameraError`` if not.

    With the camera set to RAW or RAW+JPEG the first file it hands over is
    the raw one, saved under a .jpg name; a pulled cable leaves half a file.
    Found here, the guest retakes one shot. Found while the collage is built,
    the whole run is lost.
    """
    from PIL import Image
    try:
        with open(path, "rb") as f:
            start = f.read(2)
        if start != b"\xff\xd8":
            raise CameraError("Die Kamera liefert kein JPEG. Bitte an der Kamera die Bildqualität auf JPEG (L) stellen, nicht RAW.")
        with Image.open(path) as img:
            img.draft("RGB", (480, 320))        # decode small: quick, and still reads to the end
            img.load()
    except CameraError:
        raise
    except Exception as e:                       # noqa: BLE001
        raise CameraError(f"Das Bild der Kamera ist unvollständig (kein JPEG lesbar: {e}).") from e


class CameraBackend:
    name = "base"
    #: Liefert das Backend Live-Bilder? (sonst zeigt die UI ein Standbild)
    has_preview = True

    def open(self) -> None:  # pragma: no cover - Interface
        raise NotImplementedError

    def close(self) -> None:
        pass

    def preview_jpeg(self, max_width: int = 1280) -> Optional[bytes]:
        """Ein Live-Bild als JPEG oder None, wenn gerade keins bereit ist."""
        raise NotImplementedError

    def capture(self, target: Path) -> Path:
        """Ein Foto in voller Auflösung aufnehmen und als JPEG nach ``target`` schreiben."""
        raise NotImplementedError

    def keepalive(self) -> None:
        """Wird etwa jede Minute aufgerufen (Canon: Auto-Power-Off verhindern)."""

    def shooting_mode(self) -> str:
        """Betriebsart der Kamera (M, Av, Tv, P) oder "" — nicht jedes Backend kann das.

        Die Selbstdiagnose sagt lieber „nicht auslesbar" als etwas Erfundenes.
        """
        return ""

    def battery_level(self):
        """Battery in percent, "ac" on mains power, or None when unknown.

        Only the Canon SDK backend can tell (and the mock, for tests); every
        other backend says "unknown" and the booth shows no battery message.
        """
        return None

    #: Can the camera record a movie file of its own and hand it over
    #: (``record_movie``)? Only the Canon SDK backend (and the mock with a
    #: movie set, for tests). Every other backend: the booth films from the
    #: live view, as it always did.
    can_record_movie = False
    #: Set by ``record_movie`` when the camera could not be put back into
    #: photo state; the camera service then opens the camera afresh.
    restore_problem = ""

    def record_movie(self, seconds: float, folder: Path, *, tick=None, stopped=None) -> Path:
        """Record ``seconds`` of movie on the camera and download it into ``folder``.

        ``tick(recording)`` is called over and over while the camera works
        (the service keeps the live view running with it); ``stopped(elapsed)``
        once, the moment the recording ends. Raises ``CameraError`` on any
        failure; the camera is back in photo state either way.
        """
        raise CameraError("Dieser Kamera-Anschluss nimmt keinen Film auf der Kamera auf")

    def info(self) -> dict:
        return {"backend": self.name}

    #: Welche Belichtungswerte das Backend setzen kann (Teilmenge von iso, aperture, shutter, wb)
    settable: tuple = ()

    def apply_settings(self, settings: dict) -> dict:
        """ISO, Blende, Zeit, Weißabgleich setzen. Leere Werte bleiben unangetastet.

        Rückgabe: ``{"applied": {...}, "failed": {name: grund}}``. Nie eine Ausnahme wegen
        eines einzelnen Werts — die Box muss weiter auslösen können.
        """
        wanted = {k: v for k, v in (settings or {}).items() if v not in (None, "", "auto-keep")}
        return {"applied": {}, "failed": {k: "vom Kamera-Anschluss nicht unterstützt" for k in wanted}}

    def read_settings(self, keys) -> dict:
        """What the camera is set to now, for the named keys it can tell (``{}`` if none).

        Needed when the live view gets values of its own and the shot's values
        come from the camera's dial, not the profile: they are read before
        they are overwritten, and put back before the shutter.
        """
        return {}


SETTING_KEYS = ("iso", "aperture", "shutter", "wb")

