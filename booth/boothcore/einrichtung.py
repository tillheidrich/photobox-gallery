"""Die Testseite: Was fehlt dieser Box noch, damit der Abend läuft?

Bis hierher verteilte sich die Antwort auf vier Stellen — ein Textfeld für den
Pfad zur ``EDSDK.dll``, ein Kommandozeilenaufruf zum Einrichten, eine Prüfung
für Linux und drei Knöpfe zum Testen. Wer die Box zum ersten Mal aufstellt,
muss alle vier kennen. Das ist eine Zumutung, und sie rächt sich immer am
selben Abend.

Dieses Modul beantwortet **eine** Frage: Was ist mit dem gewählten Weg, und
was ist der nächste Handgriff? Es prüft nur und verändert nichts.

Der Grundsatz bei den Antworten: Ein Zustand, der nicht sicher festgestellt
werden kann, heißt ``unklar`` und nicht ``in Ordnung``. Eine Testseite, die im
Zweifel grün zeigt, ist schlimmer als keine — sie ersetzt die berechtigte
Unruhe des Betreibers durch falsche Sicherheit.
"""
from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

#: Die Wege, die es gibt, und wofür sie stehen.
WEGE = {
    "edsdk": "Canon am Kabel (EDSDK)",
    "ccapi": "Canon über WLAN (CCAPI)",
    "digicamcontrol": "Sony/Nikon/Fuji über digiCamControl (Windows)",
    "gphoto2": "Sony/Nikon/Canon am Kabel über gphoto2 (Linux/macOS)",
    "folder": "Hersteller-Software legt Bilder in einen Ordner",
    "webcam": "Webcam",
    "mock": "Testbild (keine Kamera)",
}

OK, FEHLT, UNKLAR = "ok", "fehlt", "unklar"


def _system() -> str:
    s = platform.system()
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(s, s.lower())


def _erreichbar(url: str, oeffner: Optional[Callable] = None, timeout: float = 2.5) -> Optional[bool]:
    """Antwortet dort etwas? ``None`` heißt: nicht feststellbar."""
    if not url:
        return None
    try:
        import urllib.request
        oeffnen = oeffner or urllib.request.urlopen
        with oeffnen(url, timeout=timeout) as r:        # noqa: S310
            return 200 <= getattr(r, "status", 200) < 500
    except Exception:
        return False


def _edsdk_stand(lokal: dict) -> Dict[str, object]:
    from . import edsdk_setup
    pfad = str((lokal.get("camera") or {}).get("edsdk_dll") or "")
    erwartet = edsdk_setup.target_dir() / edsdk_setup.library_name()
    da = bool(pfad and Path(pfad).exists()) or erwartet.exists()
    if not edsdk_setup.has_sdk_platform():
        return {"stand": FEHLT,
                "text": "Für Linux gibt es kein Canon-SDK. Am Kabel läuft Canon hier über gphoto2.",
                "tun": "Kameraweg auf gphoto2 umstellen.",
                "ziel": str(erwartet)}
    if da:
        return {"stand": OK, "text": f"SDK liegt bereit ({pfad or erwartet}).",
                "tun": "", "ziel": str(erwartet)}
    return {"stand": FEHLT,
            "text": "Die EDSDK-Bibliothek fehlt. Canon gibt sie kostenlos heraus, "
                    "sie darf aber nicht mitgeliefert werden.",
            "tun": "Das ZIP von Canon hier hochladen — die Box packt es aus und trägt den Pfad ein.",
            "ziel": str(erwartet)}


def _gphoto_stand() -> Dict[str, object]:
    from . import gphoto_setup
    b = gphoto_setup.pruefen()
    if b.bereit:
        return {"stand": OK, "text": b.zusammenfassung(), "tun": ""}
    tun = ""
    if b.blockierer:
        tun = "Unten „Kamera freigeben“ — der Schreibtisch hält sie fest."
    elif not b.python_gphoto2:
        tun = "pip install gphoto2 (braucht libgphoto2-dev)"
    elif not b.kameras:
        tun = "Kabel und USB-Modus der Kamera prüfen."
    return {"stand": FEHLT if (b.blockierer or not b.python_gphoto2) else UNKLAR,
            "text": b.zusammenfassung(), "tun": tun,
            "blockierer": b.blockierer}


def _digicam_stand(lokal: dict, oeffner=None) -> Dict[str, object]:
    url = str((lokal.get("camera") or {}).get("digicam_url") or "").strip()
    if _system() != "windows":
        return {"stand": FEHLT,
                "text": "digiCamControl gibt es nur für Windows.",
                "tun": "Unter Linux/macOS stattdessen gphoto2 nehmen."}
    if not url:
        return {"stand": FEHLT, "text": "Keine Adresse für digiCamControl eingetragen.",
                "tun": "digiCamControl installieren, Webserver einschalten "
                       "(Datei → Einstellungen → Webserver), Adresse hier eintragen."}
    antwortet = _erreichbar(url, oeffner)
    if antwortet:
        return {"stand": OK, "text": f"digiCamControl antwortet unter {url}.", "tun": ""}
    return {"stand": FEHLT, "text": f"Unter {url} antwortet nichts.",
            "tun": "Läuft digiCamControl, und ist sein Webserver eingeschaltet?"}


def _ccapi_stand(lokal: dict, oeffner=None) -> Dict[str, object]:
    url = str((lokal.get("camera") or {}).get("ccapi_url") or "").strip()
    if not url:
        return {"stand": FEHLT, "text": "Keine CCAPI-Adresse eingetragen.",
                "tun": "In der Kamera CCAPI freischalten (Menü → CCAPI), "
                       "dann die angezeigte Adresse hier eintragen. Kein SDK nötig."}
    antwortet = _erreichbar(url, oeffner)
    if antwortet:
        return {"stand": OK, "text": f"Die Kamera antwortet unter {url}.", "tun": ""}
    return {"stand": FEHLT, "text": f"Unter {url} antwortet nichts.",
            "tun": "Hängen Box und Kamera im selben WLAN? Ist CCAPI in der Kamera noch aktiv? "
                   "Die Freischaltung geht beim Ausschalten mancher Modelle verloren."}


def kamera_stand(lokal: dict, oeffner=None) -> Dict[str, object]:
    """Der gewählte Kameraweg, sein Zustand und der nächste Handgriff."""
    weg = str((lokal.get("camera") or {}).get("backend") or "auto")
    if weg == "auto":
        weg = "edsdk" if _system() in ("windows", "macos") else "gphoto2"
        automatisch = True
    else:
        automatisch = False
    if weg == "edsdk":
        stand = _edsdk_stand(lokal)
    elif weg == "gphoto2":
        stand = _gphoto_stand()
    elif weg == "digicamcontrol":
        stand = _digicam_stand(lokal, oeffner)
    elif weg == "ccapi":
        stand = _ccapi_stand(lokal, oeffner)
    elif weg in ("webcam", "folder", "mock"):
        stand = {"stand": UNKLAR,
                 "text": f"{WEGE.get(weg, weg)} — lässt sich nur durch ein Testfoto beantworten.",
                 "tun": "Oben „Testfoto“ drücken."}
    else:
        stand = {"stand": UNKLAR, "text": f"Unbekannter Kameraweg: {weg}", "tun": ""}
    return {**stand, "weg": weg, "weg_name": WEGE.get(weg, weg), "automatisch": automatisch}


def drucker_stand(lokal: dict, hat_lp: Optional[Callable] = None) -> Dict[str, object]:
    """Ob überhaupt ein Druckweg da ist. Ob er richtig sitzt, sagt nur das Blatt."""
    drucker = lokal.get("printers") or []
    if not drucker:
        einer = lokal.get("printer") or {}
        drucker = [einer] if einer.get("backend") else []
    aktive = [d for d in drucker if str(d.get("backend") or "none") != "none"]
    if not aktive:
        return {"stand": FEHLT, "text": "Kein Drucker eingerichtet.",
                "tun": "Oben unter „Drucker“ einen Weg wählen — oder „Ordner“, wenn nicht gedruckt wird."}
    namen = ", ".join(str(d.get("backend")) for d in aktive)
    if any(str(d.get("backend")) == "cups" for d in aktive):
        vorhanden = (hat_lp or (lambda: bool(shutil.which("lp"))))()
        if not vorhanden:
            return {"stand": FEHLT, "text": "CUPS gewählt, aber der Befehl lp fehlt.",
                    "tun": "CUPS installieren (Paket cups-client)."}
    return {"stand": UNKLAR, "text": f"{len(aktive)} Druckweg(e) eingerichtet: {namen}.",
            "tun": "Die Kalibrierungsseite drucken und mit dem Lineal nachmessen — "
                   "ob Größe und Rand stimmen, sagt nur das Blatt."}


def stand(lokal: dict, profil: dict, oeffner=None, hat_lp=None) -> Dict[str, object]:
    """Alles auf einen Blick: System, Kamera, Drucker, offene Handgriffe."""
    kam = kamera_stand(lokal, oeffner)
    dru = drucker_stand(lokal, hat_lp)
    offen = [t for t in (kam.get("tun"), dru.get("tun")) if t]
    return {
        "system": _system(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "kamera": kam,
        "drucker": dru,
        "offen": offen,
        # Grün gibt es nur, wenn beides wirklich grün ist. „unklar" bleibt
        # unklar — eine Testseite, die im Zweifel beruhigt, ist wertlos.
        "gesamt": OK if (kam["stand"] == OK and dru["stand"] == OK)
                  else (FEHLT if FEHLT in (kam["stand"], dru["stand"]) else UNKLAR),
    }
