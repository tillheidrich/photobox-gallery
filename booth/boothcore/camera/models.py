"""Welche Kamera über welchen Weg angebunden wird.

Die Box spricht mit Kameras auf vier Arten, und welche passt, hängt an Marke
und Rechner — nicht an der Box:

* **EDSDK** — Canon am Kabel. Das SDK gibt es nur bei Canon selbst, liegt
  also nicht im Repo und wird vom Betreiber danebengelegt.
* **CCAPI** — Canon über WLAN, ganz ohne SDK. Muss an der Kamera freigeschaltet
  werden.
* **digiCamControl** — Windows, und der eigentliche Grund, warum Nikon, Sony,
  Fujifilm und Olympus hier überhaupt gehen: Das Programm bringt die
  Hersteller-SDKs mit, die wir selbst nicht einbinden dürfen. Die Box spricht
  nur HTTP damit.
* **gphoto2** — Linux und macOS, 2500+ Modelle, freie Software.

Dazu zwei Notausgänge, die immer funktionieren: der Ordner, in den eine
Hersteller-Software tethered speichert, und eine Webcam.

Diese Tabelle beantwortet die Frage, die beim Aufbau wirklich gestellt wird:
„Ich habe eine Sony A7 — was stelle ich ein?“ Sie ersetzt keinen Treiber und
verspricht nichts: Wo es Einschränkungen gibt (Sony-Liveview, Nikon-Z ohne
Kabel), stehen sie dabei.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: backend-Kennungen wie in ``camera/service.create_backend``.
WEGE = {
    "edsdk": "Canon am Kabel (EDSDK)",
    "ccapi": "Canon über WLAN (CCAPI)",
    "digicamcontrol": "digiCamControl (Windows)",
    "gphoto2": "gphoto2 (Linux/macOS)",
    "folder": "Hersteller-Software (Ordner)",
    "webcam": "Webcam",
}

MARKEN: List[Dict[str, Any]] = [
    {
        "id": "canon-dslr", "marke": "Canon", "name": "EOS DSLR/DSLM (z. B. 2000D, 90D, M50, R50)",
        "windows": "edsdk", "linux": "gphoto2",
        "hinweis": "Am Kabel über das EDSDK (kostenlos bei Canon anzufordern, liegt nicht im Repo). "
                   "Über WLAN geht CCAPI — dann ohne SDK, aber mit etwas mehr Verzögerung.",
        "liveview": True,
    },
    {
        "id": "canon-ccapi", "marke": "Canon", "name": "EOS mit CCAPI (WLAN)",
        "windows": "ccapi", "linux": "ccapi",
        "hinweis": "In der Kamera freischalten (Menü → CCAPI). Kein Kabel, kein SDK.",
        "liveview": True,
    },
    {
        "id": "nikon", "marke": "Nikon", "name": "Z-Serie und DSLR (Z6/Z50, D750 …)",
        "windows": "digicamcontrol", "linux": "gphoto2",
        "hinweis": "Unter Windows über digiCamControl; unter Linux direkt über gphoto2. "
                   "Am Kabel auslösen und Live-Bild funktionieren bei beiden.",
        "liveview": True,
    },
    {
        "id": "sony", "marke": "Sony", "name": "Alpha (A6400, A7 …)",
        "windows": "digicamcontrol", "linux": "gphoto2",
        "hinweis": "Kamera auf „PC-Fernsteuerung“ stellen. Das Live-Bild ist bei manchen Modellen "
                   "langsamer als bei Canon/Nikon — für die Vorschau reicht es, für Video nicht immer.",
        "liveview": True,
    },
    {
        "id": "fujifilm", "marke": "Fujifilm", "name": "X-Serie",
        "windows": "digicamcontrol", "linux": "gphoto2",
        "hinweis": "Im Kameramenü USB auf „Tether-Aufnahme“ stellen.",
        "liveview": True,
    },
    {
        "id": "olympus", "marke": "OM System / Olympus", "name": "OM-D / OM-1",
        "windows": "digicamcontrol", "linux": "gphoto2",
        "hinweis": "Funktioniert, Live-Bild je nach Modell eingeschränkt.",
        "liveview": False,
    },
    {
        "id": "panasonic", "marke": "Panasonic", "name": "Lumix G/S",
        "windows": "folder", "linux": "gphoto2",
        "hinweis": "Unter Windows am zuverlässigsten über die Lumix-Tethering-Software und den Ordner, "
                   "in den sie speichert.",
        "liveview": False,
    },
    {
        "id": "webcam", "marke": "", "name": "Webcam / Capture-Karte",
        "windows": "webcam", "linux": "webcam",
        "hinweis": "Sofort einsatzbereit, dafür Bildqualität und Schärfentiefe einer Webcam. "
                   "Als Notlösung und für Tests gedacht.",
        "liveview": True,
    },
    {
        "id": "andere", "marke": "", "name": "Andere Kamera (Tethering-Software)",
        "windows": "folder", "linux": "folder",
        "hinweis": "Jede Software, die tethered in einen Ordner speichert: Die Box nimmt die neueste Datei. "
                   "Auslösen geht dann über einen HTTP-Aufruf oder den Auslöser der Software.",
        "liveview": False,
    },
]

NACH_ID = {m["id"]: m for m in MARKEN}


def empfehlung(mid: Optional[str], windows: bool = True) -> str:
    """Welcher Anschluss für diese Marke auf diesem Rechner."""
    eintrag = NACH_ID.get(str(mid or ""), NACH_ID["andere"])
    return eintrag["windows"] if windows else eintrag["linux"]


def auswahl(windows: bool = True) -> List[Dict[str, Any]]:
    return [{"id": m["id"], "marke": m["marke"], "name": m["name"], "hinweis": m["hinweis"],
             "backend": m["windows"] if windows else m["linux"], "liveview": m["liveview"]}
            for m in MARKEN]
