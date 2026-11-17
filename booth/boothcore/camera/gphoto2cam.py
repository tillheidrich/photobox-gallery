"""Canon/Nikon/Sony über libgphoto2 — der Linux-Weg.

Benötigt ``python-gphoto2`` (``pip install gphoto2``). Unterstützt über 2 500 Modelle
(Canon, Nikon, Sony, Fujifilm, Panasonic, Olympus …) — Liste: http://www.gphoto.org/proj/libgphoto2/support.php
Bei vielen Modellen bleibt das Kameradisplay während des Live-Bilds dunkel. Vor dem Einsatz Dauertest machen.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from .base import CameraBackend, CameraError


class Gphoto2Camera(CameraBackend):
    name = "gphoto2"
    settable = ("iso", "aperture", "shutter", "wb")
    #: Namen der Konfig-Knoten unterscheiden sich je Hersteller — erster Treffer gewinnt
    NODES = {"iso": ("iso", "isospeed"), "aperture": ("aperture", "f-number", "fnumber"),
             "shutter": ("shutterspeed", "shutterspeed2", "exptime"), "wb": ("whitebalance",)}
    WB_NAMES = {"auto": ("auto", "automatic", "awb"), "daylight": ("daylight", "sunny"), "cloudy": ("cloudy",),
                "tungsten": ("tungsten", "incandescent"), "fluorescent": ("fluorescent",), "flash": ("flash",),
                "shade": ("shade",)}

    def apply_settings(self, settings: dict) -> dict:
        applied, failed = {}, {}
        if self._cam is None:
            return {"applied": {}, "failed": {k: "Kamera nicht verbunden" for k in settings or {}}}
        cfg = self._cam.get_config()
        for key, value in (settings or {}).items():
            if value in (None, ""):
                continue
            node = None
            for name in self.NODES.get(key, ()):
                try:
                    node = cfg.get_child_by_name(name)
                    break
                except Exception:
                    continue
            if node is None:
                failed[key] = "Kamera bietet die Einstellung nicht an"
                continue
            try:
                choices = [str(c) for c in node.get_choices()]
            except Exception:
                choices = []
            want = str(value).replace("f/", "")
            match = None
            for c in choices:
                cc = c.lower().replace("f/", "").replace("s", "").strip()
                if cc == want.lower() or (key == "wb" and any(n in c.lower() for n in self.WB_NAMES.get(want, (want,)))):
                    match = c
                    break
            if match is None:
                failed[key] = f"Wert {value} nicht verfügbar"
                continue
            node.set_value(match)
            applied[key] = value
        try:
            self._cam.set_config(cfg)
        except Exception as e:
            return {"applied": {}, "failed": {k: str(e) for k in list(applied) + list(failed)}}
        return {"applied": applied, "failed": failed}

    def __init__(self):
        self._gp = None
        self._cam = None

    def open(self) -> None:
        try:
            import gphoto2 as gp  # type: ignore
        except ImportError as e:
            raise CameraError("python-gphoto2 ist nicht installiert") from e
        self._gp = gp
        cam = gp.Camera()
        try:
            cam.init()
        except gp.GPhoto2Error as e:
            raise CameraError(f"Kamera nicht gefunden: {e}") from e
        self._cam = cam
        # Aufnahmen im RAM der Kamera statt auf der Karte
        try:
            cfg = cam.get_config()
            target = cfg.get_child_by_name("capturetarget")
            target.set_value(target.get_choice(0))
            cam.set_config(cfg)
        except Exception:
            pass

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.exit()
            except Exception:
                pass
            self._cam = None

    def preview_jpeg(self, max_width: int = 1280) -> Optional[bytes]:
        if self._cam is None:
            raise CameraError("Kamera nicht verbunden")
        f = self._cam.capture_preview()
        return bytes(memoryview(f.get_data_and_size()))

    def capture(self, target: Path) -> Path:
        gp = self._gp
        path = self._cam.capture(gp.GP_CAPTURE_IMAGE)
        f = self._cam.file_get(path.folder, path.name, gp.GP_FILE_TYPE_NORMAL)
        f.save(str(target))
        try:
            self._cam.file_delete(path.folder, path.name)
        except Exception:
            pass
        time.sleep(0.1)
        return target
