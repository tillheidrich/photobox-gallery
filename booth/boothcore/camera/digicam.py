"""Nikon, Sony, Canon, Fujifilm … unter Windows über digiCamControl (Open Source, MIT).

digiCamControl bringt die Hersteller-SDKs mit, die wir nicht selbst einbinden dürfen oder
wollen (Nikon, Sony, Canon), und hat einen eingebauten Webserver. Die Box spricht nur HTTP:

* Live-Bild:  ``GET http://127.0.0.1:5513/liveview.jpg`` (vorher ``?CMD=LiveViewWnd_Show``)
* Auslösen:  ``GET ?slc=capture``, danach die neue Datei im Sitzungsordner abholen
* Werte:     ``GET ?slc=set&param1=iso&param2=400`` (iso, aperture, shutterspeed, whitebalance)

Einrichtung: digiCamControl installieren, Webserver in den Einstellungen anhaken —
mehr nicht. Läuft das Programm nicht, startet die Box es selbst; den Sitzungsordner
erfragt sie beim Programm, wenn hier keiner eingetragen ist.

STATUS: nach Doku gebaut, noch nicht an echter Hardware getestet.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from .base import CameraBackend, CameraError

log = logging.getLogger("booth.camera.digicam")

IMAGE_EXT = (".jpg", ".jpeg")

# Standardpfade der Installation. digiCamControl ist 32-bit, liegt also meist
# unter "Program Files (x86)" — auch auf 64-bit-Windows.
EXE_CANDIDATES = (
    r"%ProgramFiles(x86)%\digiCamControl\CameraControl.exe",
    r"%ProgramFiles%\digiCamControl\CameraControl.exe",
    r"%LOCALAPPDATA%\Programs\digiCamControl\CameraControl.exe",
)

# So heißt der Sitzungsordner je nach Fassung. Wir fragen der Reihe nach.
SESSION_PARAMS = ("session.folder", "sessionfolder", "session")

START_TIMEOUT = 25.0


class DigiCamControlCamera(CameraBackend):
    name = "digicamcontrol"
    settable = ("iso", "aperture", "shutter", "wb")
    PARAMS = {"iso": "iso", "aperture": "aperture", "shutter": "shutterspeed", "wb": "whitebalance"}

    def __init__(self, url: str = "http://127.0.0.1:5513", session_folder: str = "", timeout: float = 20.0):
        self.url = (url or "http://127.0.0.1:5513").rstrip("/")
        self.folder = Path(session_folder) if session_folder else None
        self.timeout = timeout
        self.capture_timeout = timeout * 2 + 2
        self._client = None

    def _get(self, params: dict | None = None, path: str = "/", timeout: float = 5.0):
        import httpx

        if self._client is None:
            self._client = httpx.Client(timeout=timeout)
        r = self._client.get(self.url + path, params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r

    # ── Programm finden und notfalls selbst starten ───────
    @staticmethod
    def find_exe() -> Optional[Path]:
        """Wo digiCamControl installiert ist — oder None."""
        for candidate in EXE_CANDIDATES:
            path = Path(os.path.expandvars(candidate))
            if path.is_file():
                return path
        found = shutil.which("CameraControl.exe")
        return Path(found) if found else None

    def alive(self) -> bool:
        try:
            self._get(timeout=2.0)
            return True
        except Exception:
            return False

    def _start_program(self) -> bool:
        """digiCamControl starten, wenn es nicht läuft. Nur unter Windows sinnvoll."""
        if os.name != "nt":
            return False
        exe = self.find_exe()
        if not exe:
            return False
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            log.warning("digiCamControl ließ sich nicht starten: %s", e)
            return False
        log.info("digiCamControl gestartet: %s", exe)
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self.alive():
                return True
            time.sleep(1.0)
        return False

    def session_folder(self) -> Optional[Path]:
        """Sitzungsordner: eingetragen, sonst beim Programm erfragt."""
        if self.folder and self.folder.is_dir():
            return self.folder
        for param in SESSION_PARAMS:
            try:
                answer = self._get({"slc": "get", "param1": param}).text.strip().strip('"')
            except Exception:
                continue
            if answer and Path(answer).is_dir():
                self.folder = Path(answer)
                log.info("Sitzungsordner von digiCamControl: %s", self.folder)
                return self.folder
        try:                              # letzte Möglichkeit: Ordner des letzten Fotos
            last = self._get({"slc": "get", "param1": "lastcaptured"}).text.strip().strip('"')
            if last and Path(last).parent.is_dir():
                self.folder = Path(last).parent
                return self.folder
        except Exception:
            pass
        return None

    def open(self) -> None:
        if not self.alive() and not self._start_program():
            hint = ("läuft das Programm mit eingeschaltetem Webserver?"
                    if self.find_exe() else "ist digiCamControl überhaupt installiert?")
            raise CameraError(f"digiCamControl nicht erreichbar ({self.url}) — {hint}")
        try:
            self._get({"CMD": "LiveViewWnd_Show"})
        except Exception as e:
            raise CameraError(f"digiCamControl antwortet nicht auf Befehle ({self.url}): {e}") from e
        self.session_folder()             # früh holen, damit das erste Foto nicht wartet

    def close(self) -> None:
        try:
            self._get({"CMD": "LiveViewWnd_Hide"})
        except Exception:
            pass
        if self._client is not None:
            self._client.close()
            self._client = None

    def preview_jpeg(self, max_width: int = 1280) -> Optional[bytes]:
        try:
            r = self._get(path="/liveview.jpg", timeout=3.0)
        except Exception as e:
            raise CameraError(f"Kein Live-Bild von digiCamControl: {e}") from e
        return r.content if r.content[:2] == b"\xff\xd8" else None

    def _newest(self, since: float) -> Optional[Path]:
        folder = self.folder if (self.folder and self.folder.is_dir()) else self.session_folder()
        if not folder:
            return None
        self.folder = folder
        files = [p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXT and p.stat().st_mtime >= since]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def capture(self, target: Path) -> Path:
        started = time.time() - 1
        self._get({"slc": "capture"}, timeout=self.timeout)
        deadline = time.monotonic() + self.timeout
        last_size = -1
        while time.monotonic() < deadline:
            found = self._newest(started)
            if found is None:
                try:     # Fallback: Pfad direkt erfragen
                    name = self._get({"slc": "get", "param1": "lastcaptured"}).text.strip()
                    p = Path(name)
                    if name and p.is_file() and p.stat().st_mtime >= started:
                        found = p
                except Exception:
                    pass
            if found is not None:
                size = found.stat().st_size
                if size and size == last_size:            # fertig geschrieben
                    shutil.copy(found, target)
                    return target
                last_size = size
            time.sleep(0.25)
        raise CameraError("Foto kam nicht an — Sitzungsordner in digiCamControl und hier gleich? Kamera fokussiert?")

    def apply_settings(self, settings: dict) -> dict:
        applied, failed = {}, {}
        for key, value in (settings or {}).items():
            if value in (None, ""):
                continue
            if key not in self.PARAMS:
                failed[key] = "unbekannt"
                continue
            try:
                self._get({"slc": "set", "param1": self.PARAMS[key], "param2": str(value)})
                applied[key] = value
            except Exception as e:
                failed[key] = str(e)[:120]
        return {"applied": applied, "failed": failed}

    def keepalive(self) -> None:
        pass


class FolderCamera(CameraBackend):
    """Jede Kamera mit eigener Tethering-Software (Sony Imaging Edge, Nikon NX Tether, Fujifilm X Acquire,
    Capture One …): die Software legt Fotos in einen Ordner, die Box nimmt das neueste.

    Live-Bild optional über eine Webcam-Quelle (``preview_webcam``), z. B. das USB-Webcam-Programm
    des Herstellers. Ausgelöst wird per Kamera-Fernauslöser oder per ``trigger_url`` (HTTP GET).
    """

    name = "folder"
    has_preview = False

    def __init__(self, folder: str, preview_webcam: Optional[int] = None, trigger_url: str = "", timeout: float = 30.0):
        if not folder:
            raise CameraError("Ordner für die Kamera-Software fehlt")
        self.folder = Path(folder)
        self.trigger_url = trigger_url
        self.timeout = timeout
        self.capture_timeout = timeout + 12
        self._preview = None
        if preview_webcam is not None and preview_webcam != "":
            from .webcam import WebcamCamera

            self._preview = WebcamCamera(int(preview_webcam))
            self.has_preview = True

    def open(self) -> None:
        if not self.folder.is_dir():
            raise CameraError(f"Ordner {self.folder} fehlt")
        if self._preview:
            self._preview.open()

    def close(self) -> None:
        if self._preview:
            self._preview.close()

    def preview_jpeg(self, max_width: int = 1280) -> Optional[bytes]:
        if self._preview:
            return self._preview.preview_jpeg(max_width)
        time.sleep(0.2)
        return None

    def capture(self, target: Path) -> Path:
        started = time.time() - 0.5
        if self.trigger_url:
            import httpx

            httpx.get(self.trigger_url, timeout=10)
        deadline = time.monotonic() + self.timeout
        last = (None, -1)
        while time.monotonic() < deadline:
            files = [p for p in self.folder.iterdir() if p.suffix.lower() in IMAGE_EXT and p.stat().st_mtime >= started]
            if files:
                newest = max(files, key=lambda p: p.stat().st_mtime)
                size = newest.stat().st_size
                if last == (newest, size) and size:
                    shutil.copy(newest, target)
                    return target
                last = (newest, size)
            time.sleep(0.25)
        raise CameraError("Kein neues Foto im Ordner angekommen")
