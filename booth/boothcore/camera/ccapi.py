"""Canon-Kameras über die CCAPI — die HTTP-Schnittstelle in der Kamera selbst.

Anders als beim EDSDK liegt hier nichts von Canon im Programm: Die Kamera
betreibt einen kleinen Webserver, und die Box spricht HTTP mit ihm. Das hat drei
Folgen, die für dieses Projekt schwer wiegen.

* Es läuft auf jedem System gleich. Windows, macOS, Linux, Raspberry Pi: dieselbe
  Datei, keine Architekturfrage, keine Bibliothek zum Ausliefern.
* Es ist lizenzrechtlich unauffällig. Die verkaufte Anwendung enthält keinen
  fremden Binärteil.
* Es hängt am Netz statt am Kabel. Das ist der Preis: Auf einer Feier mit
  hundert Handys im 2,4-GHz-Band gehört ein eigener Zugangspunkt an die Box.

Voraussetzungen: ein Modell, das die CCAPI kann, einmalig freigeschaltet über
Canons Aktivierungswerkzeug, und eine erreichbare Adresse.

Ablauf einer Aufnahme: auslösen, dann die Kamera nach neuen Dateien fragen
(Ereignis-Abruf), die gemeldete Datei herunterladen. Die Kamera schreibt dabei
weiter auf ihre Karte; das ist gewollt, denn eine Karte ist die beste Sicherung,
die eine Fotobox haben kann.

STATUS: gegen die CCAPI-Referenz 1.4.0 geschrieben und gegen einen nachgebauten
Kameraserver getestet, **noch nie an einer echten Kamera gelaufen**. Vor dem
ersten Einsatz: ``python -m boothcore.tools.cameratest --backend ccapi --shots 50``.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx

from .base import CameraBackend, CameraError

log = logging.getLogger("boothcore.camera.ccapi")

# Wie lange die Kamera nach dem Auslösen Zeit bekommt, die Datei zu melden.
# Ein Bild mit langer Belichtung und Rauschunterdrückung braucht spürbar länger
# als ein Schnappschuss, deshalb großzügig.
CAPTURE_TIMEOUT = 25.0
POLL_INTERVAL = 0.35
BUSY_RETRIES = 3

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".heif", ".hif")


class CcapiCamera(CameraBackend):
    name = "ccapi"
    has_preview = True
    settable = ("iso", "aperture", "shutter", "wb")

    # Die Kamera meldet je Endpunkt, welche Versionen sie kann. Diese Namen
    # suchen wir in ihrer Liste; die höchste vorhandene Fassung gewinnt.
    PATHS = {
        "shutter": "shooting/control/shutterbutton",
        "liveview": "shooting/liveview",
        "liveview_image": "shooting/liveview/flip",
        "polling": "event/polling",
        "device": "deviceinformation",
        "settings": "shooting/settings",
    }

    def __init__(self, base_url: str = "", liveview_size: str = "medium", camera_display: str = "on"):
        self.base_url = (base_url or "").rstrip("/")
        self.liveview_size = liveview_size if liveview_size in ("small", "medium", "off") else "medium"
        self.camera_display = "off" if camera_display == "off" else "on"
        self._client: Optional[httpx.Client] = None
        self._api: Dict[str, str] = {}          # Kurzname -> vollständige Adresse
        self._model = ""
        self._liveview_on = False

    # ── Verbindung ───────────────────────────────────────
    def open(self) -> None:
        if not self.base_url:
            raise CameraError("Keine Adresse für die Kamera eingetragen. In der Verwaltung die "
                              "Adresse der Kamera angeben, etwa http://192.168.1.2:8080")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise CameraError(f"Adresse sieht nicht nach einer Kamera aus: {self.base_url}")

        self._client = httpx.Client(timeout=httpx.Timeout(8.0, read=20.0), follow_redirects=False)
        try:
            listing = self._get_json("/ccapi")
        except Exception as e:                  # noqa: BLE001 — hier ist jeder Fehler dieselbe Lage
            raise CameraError(
                f"Kamera unter {self.base_url} nicht erreichbar ({e}). Steht sie an? Ist die CCAPI "
                "freigeschaltet und die Box im selben Netz?") from e

        self._api = self._resolve(listing)
        missing = [name for name in ("shutter", "polling") if name not in self._api]
        if missing:
            raise CameraError(
                "Diese Kamera meldet die nötigen Funktionen nicht (fehlt: "
                + ", ".join(self.PATHS[m] for m in missing)
                + "). Unterstützt das Modell die CCAPI in dieser Fassung?")

        try:
            info = self._get_json(self._api["device"]) if "device" in self._api else {}
            self._model = str(info.get("productname") or info.get("model") or "")
        except Exception:                        # noqa: BLE001 — Name ist Beiwerk
            self._model = ""
        log.info("CCAPI verbunden: %s (%s)", self._model or "Kamera", self.base_url)
        self._drain_events()                     # alte Meldungen wegräumen
        if self.liveview_size != "off":
            self._start_liveview()

    def close(self) -> None:
        if self._client:
            if self._liveview_on:
                try:
                    self._post(self._api["liveview"], {"liveviewsize": "off", "cameradisplay": "on"})
                except Exception:                # noqa: BLE001 — beim Beenden egal
                    pass
            self._client.close()
        self._client = None
        self._liveview_on = False

    # ── Aufnahme ─────────────────────────────────────────
    def capture(self, target: Path) -> Path:
        self._drain_events()
        self._press_shutter()
        url = self._wait_for_new_file()
        data = self._download(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _press_shutter(self) -> None:
        last = ""
        for attempt in range(BUSY_RETRIES):
            try:
                # af=true lässt die Kamera vor dem Auslösen scharfstellen. Ohne das
                # bekommt man auf einer Feier reihenweise unscharfe Gruppenbilder.
                self._post(self._api["shutter"], {"af": True})
                return
            except CameraError as e:
                last = str(e)
                if "busy" not in last.lower():
                    raise
                time.sleep(0.6 * (attempt + 1))
        raise CameraError(f"Kamera war nicht bereit: {last}")

    def _wait_for_new_file(self) -> str:
        """Auf die Meldung warten, dass eine neue Datei auf der Karte liegt."""
        deadline = time.monotonic() + CAPTURE_TIMEOUT
        while time.monotonic() < deadline:
            event = self._poll_event()
            for url in event.get("addedcontents") or []:
                if str(url).lower().endswith(IMAGE_SUFFIXES):
                    return str(url)
            time.sleep(POLL_INTERVAL)
        raise CameraError("Die Kamera hat ausgelöst, aber keine Datei gemeldet. Karte voll oder "
                          "schreibgeschützt?")

    def _download(self, url: str) -> bytes:
        # Die Kamera nennt sich selbst mit der Adresse, unter der sie erreicht wurde.
        # Steht die Box in einem anderen Netz, zeigt das ins Leere, deshalb den Pfad
        # an der eigenen Basisadresse neu zusammensetzen.
        path = urlparse(url).path
        r = self._request("GET", path, stream=False)
        if not r.content:
            raise CameraError("Die Kamera hat eine leere Datei geliefert.")
        return r.content

    # ── Live-Bild ────────────────────────────────────────
    def _start_liveview(self) -> None:
        if "liveview" not in self._api:
            return
        try:
            self._post(self._api["liveview"], {"liveviewsize": self.liveview_size,
                                               "cameradisplay": self.camera_display})
            self._liveview_on = True
        except CameraError as e:
            # Ohne Live-Bild lässt sich weiter auslösen. Das ist kein Grund, die
            # Box nicht zu starten.
            log.warning("Live-Bild ließ sich nicht einschalten: %s", e)

    def preview_jpeg(self, max_width: int = 1280) -> Optional[bytes]:
        if not self._liveview_on:
            self._start_liveview()
            if not self._liveview_on:
                return None
        try:
            r = self._request("GET", self._api.get("liveview_image", ""))
            return r.content or None
        except CameraError:
            return None

    def keepalive(self) -> None:
        try:
            self._poll_event()
        except CameraError:
            pass

    # ── Belichtung ───────────────────────────────────────
    def apply_settings(self, settings: dict) -> dict:
        wanted = {k: v for k, v in (settings or {}).items() if v not in (None, "", "auto-keep")}
        applied, failed = {}, {}
        names = {"iso": "iso", "aperture": "av", "shutter": "tv", "wb": "wb"}
        for key, value in wanted.items():
            endpoint = names.get(key)
            if not endpoint:
                failed[key] = "unbekannte Einstellung"
                continue
            try:
                self._request("PUT", f"{self._version_for('settings')}/shooting/settings/{endpoint}",
                              json={"value": str(value)})
                applied[key] = value
            except CameraError as e:
                failed[key] = str(e)
        return {"applied": applied, "failed": failed}

    def shooting_mode(self) -> str:
        """Was am Moduswahlrad steht — die Kamera sagt es selbst."""
        try:
            data = self._get_json(self._version_for("settings") + "/shooting/settings/shootingmode")
            return str(data.get("value") or "").replace("manual_exposure", "M").replace(
                "aperture_priority", "Av").replace("shutter_priority", "Tv").replace("program", "P")
        except Exception:                        # noqa: BLE001
            return ""

    def info(self) -> dict:
        return {"backend": self.name, "model": self._model, "url": self.base_url,
                "liveview": self._liveview_on, "mode": self.shooting_mode()}

    # ── HTTP ─────────────────────────────────────────────
    def _resolve(self, listing: dict) -> Dict[str, str]:
        """Aus der Liste der Kamera die höchste Fassung je gebrauchtem Endpunkt wählen."""
        best: Dict[str, str] = {}
        for version in sorted(listing.keys()):           # ver100 < ver110 < ver120 …
            for entry in listing.get(version) or []:
                path = urlparse(str(entry.get("url", ""))).path
                for short, tail in self.PATHS.items():
                    if path.endswith("/" + tail):
                        best[short] = path
        return best

    def _version_for(self, short: str) -> str:
        """Der Versionsteil des Pfades, etwa /ccapi/ver100."""
        path = self._api.get(short) or self._api.get("shutter") or "/ccapi/ver100"
        parts = path.strip("/").split("/")
        return "/" + "/".join(parts[:2])

    def _poll_event(self) -> dict:
        # timeout=immediately: die Kamera antwortet sofort, auch wenn nichts
        # passiert ist. Ein hängender Abruf würde den Kamera-Thread blockieren.
        try:
            return self._get_json(self._api["polling"] + "?timeout=immediately")
        except CameraError:
            return {}

    def _drain_events(self) -> None:
        for _ in range(3):
            if not (self._poll_event().get("addedcontents") or []):
                return

    def _get_json(self, path: str) -> dict:
        r = self._request("GET", path)
        try:
            return r.json()
        except ValueError as e:
            raise CameraError(f"Antwort der Kamera war kein JSON ({path})") from e

    def _post(self, path: str, body: dict) -> dict:
        r = self._request("POST", path, json=body)
        try:
            return r.json()
        except ValueError:
            return {}

    def _request(self, method: str, path: str, json: Optional[dict] = None, stream: bool = False):
        if not self._client:
            raise CameraError("Kamera ist nicht verbunden.")
        if not path:
            raise CameraError("Diese Kamera kennt die angefragte Funktion nicht.")
        url = self.base_url + (path if path.startswith("/") else "/" + path)
        try:
            r = self._client.request(method, url, json=json)
        except httpx.HTTPError as e:
            raise CameraError(f"Kamera antwortet nicht ({e.__class__.__name__})") from e
        if r.status_code >= 400:
            raise CameraError(self._explain(r))
        return r

    @staticmethod
    def _explain(response) -> str:
        """Canons Meldungen in Sätze übersetzen, die am Eventabend weiterhelfen."""
        try:
            message = str(response.json().get("message", "")).strip()
        except Exception:                        # noqa: BLE001
            message = ""
        known = {
            "Device busy": "Die Kamera ist gerade beschäftigt.",
            "During shooting or recording": "Die Kamera nimmt noch auf.",
            "Mode not supported": "In diesem Kameramodus geht das nicht. Steht das Rad auf Foto?",
            "Card not available": "Keine oder defekte Speicherkarte in der Kamera.",
            "Card protected": "Die Speicherkarte ist schreibgeschützt.",
            "Invalid parameter": "Die Kamera hat den Wert nicht angenommen.",
            "URL not found": "Diese Funktion kennt die Kamera nicht.",
        }
        if message in known:
            return f"{known[message]} ({message})"
        if response.status_code == 503:
            return f"Die Kamera ist gerade beschäftigt ({message or response.status_code})"
        return f"Die Kamera meldet einen Fehler: {message or response.status_code}"
