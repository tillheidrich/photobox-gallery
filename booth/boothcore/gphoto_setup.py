"""Kamera am Kabel unter Linux: prüfen, was fehlt, und den Weg frei machen.

Sony, Nikon, Fujifilm, Panasonic und auch Canon laufen unter Linux über
libgphoto2 — die Unterstützung ist da, der Code dafür steht in
``camera/gphoto2cam.py``. Was fehlt, ist nicht der Treiber, sondern die
Einrichtung. Und dort gibt es genau eine Hürde, an der fast jeder hängen
bleibt:

**Der Schreibtisch nimmt die Kamera zuerst.** Sobald eine Kamera am USB-Kabel
steckt, greift bei GNOME, KDE und den meisten anderen Oberflächen ein Dienst
namens ``gvfs-gphoto2-volume-monitor`` zu und hängt sie als Wechseldatenträger
ein. Danach gehört das Gerät ihm, und jeder Auslöseversuch endet mit
``Could not claim the USB device``. Kein Fehler der Box, keine Einstellung in
der Kamera — ein zweites Programm hält den Hörer.

Auf einem Kiosk-Rechner ohne Schreibtischoberfläche tritt das nie auf. Auf dem
Laptop, an dem eingerichtet und geprobt wird, immer. Deshalb muss die Box es
erkennen und benennen können, statt den Betreiber raten zu lassen.

Dieses Modul stellt nur fest und schlägt vor. Eingegriffen wird erst auf
ausdrückliche Anweisung — ein Dienst, den jemand anders gestartet hat, wird
nicht ungefragt beendet.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

#: Die Dienste, die eine angesteckte Kamera in Beschlag nehmen.
STOERENFRIEDE = ("gvfs-gphoto2-volume-monitor", "gvfsd-gphoto2")

#: Was in der Kamera selbst eingestellt sein muss, je Marke. Das sind die
#: Punkte, an denen Anrufe entstehen — sie stehen deshalb im Klartext hier
#: und nicht in einer Anleitung, die niemand liest.
KAMERA_EINSTELLUNGEN: Dict[str, str] = {
    "sony": "USB-Verbindung auf „PC-Fernsteuerung“ stellen (nicht „Massenspeicher“). "
            "Bei den meisten Alpha-Modellen: Menü → Setup → USB → USB-Verbindung.",
    "nikon": "USB auf „MTP/PTP“ stellen. Bei der Z-Serie zusätzlich darauf achten, dass "
             "der Ruhezustand nicht nach wenigen Sekunden zuschlägt — sonst wacht sie "
             "zwischen zwei Gästen nicht rechtzeitig auf.",
    "canon": "Nichts umzustellen; Canon meldet sich direkt. Unter Windows ist das EDSDK "
             "der schnellere Weg, unter Linux ist gphoto2 der richtige.",
    "fujifilm": "Im Kameramenü USB auf „Tether-Aufnahme“ (USB RAW/Tether) stellen.",
    "panasonic": "USB-Modus auf „PC(Tether)“ stellen.",
}


@dataclass
class Befund:
    """Was an dieser Maschine vorliegt — und was daraus folgt."""
    linux: bool = True
    gphoto2_programm: bool = False
    python_gphoto2: bool = False
    kameras: List[str] = field(default_factory=list)
    blockierer: List[str] = field(default_factory=list)
    hinweise: List[str] = field(default_factory=list)

    @property
    def bereit(self) -> bool:
        """Kann jetzt ausgelöst werden?"""
        return bool(self.python_gphoto2 and self.kameras and not self.blockierer)

    def zusammenfassung(self) -> str:
        """Alles, was im Weg steht — nicht nur das Erste.

        Die Gründe sind voneinander unabhängig: Die Anbindung kann fehlen
        UND der Schreibtisch die Kamera festhalten. Wer nur den ersten Grund
        zu sehen bekommt, installiert nach, probiert erneut und steht vor
        der nächsten Wand. Deshalb stehen sie alle da.
        """
        if not self.linux:
            return "Dieser Weg gilt für Linux. Unter Windows läuft Sony/Nikon über digiCamControl."
        gruende = []
        if not self.python_gphoto2:
            gruende.append("Es fehlt die Anbindung: python-gphoto2 ist nicht installiert.")
        if not self.kameras:
            gruende.append("Keine Kamera gefunden. Steckt das Kabel, ist die Kamera an, "
                           "und steht der USB-Modus richtig?")
        if self.blockierer:
            erkannt = f" ({self.kameras[0]})" if self.kameras else ""
            gruende.append(f"Der Schreibtisch hält die Kamera{erkannt} fest: "
                           + ", ".join(self.blockierer)
                           + ". Bis die Dienste weg sind, lässt sich nicht auslösen.")
        if not gruende:
            return f"Bereit: {self.kameras[0]}"
        return " ".join(gruende)


def _lauf(befehl: List[str], timeout: float = 8.0) -> subprocess.CompletedProcess:
    return subprocess.run(befehl, capture_output=True, text=True, timeout=timeout, check=False)


def _prozesse_die_stoeren(lauf: Callable = _lauf) -> List[str]:
    """Läuft gerade einer der Dienste, die die Kamera wegnehmen?"""
    gefunden = []
    for dienst in STOERENFRIEDE:
        try:
            r = lauf(["pgrep", "-f", dienst])
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and (r.stdout or "").strip():
            gefunden.append(dienst)
    return gefunden


def _kameras(lauf: Callable = _lauf) -> List[str]:
    """Was gphoto2 sieht. Leer heißt: nichts angesteckt oder falscher USB-Modus."""
    try:
        r = lauf(["gphoto2", "--auto-detect"])
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    namen = []
    for zeile in (r.stdout or "").splitlines()[2:]:      # Kopfzeile und Trennstrich weg
        name = zeile.split("usb:")[0].strip()
        if name:
            namen.append(name)
    return namen


def pruefen(lauf: Callable = _lauf, system: Optional[str] = None) -> Befund:
    """Den Stand feststellen, ohne irgendetwas zu verändern."""
    b = Befund(linux=(system or platform.system()) == "Linux")
    if not b.linux:
        b.hinweise.append("Unter Windows läuft Sony/Nikon über digiCamControl, "
                          "unter macOS ebenfalls über gphoto2.")
        return b
    b.gphoto2_programm = bool(shutil.which("gphoto2"))
    try:
        import gphoto2                                   # noqa: F401
        b.python_gphoto2 = True
    except Exception:
        b.python_gphoto2 = False
        b.hinweise.append("Installieren mit: pip install gphoto2 "
                          "(braucht libgphoto2-dev bzw. libgphoto2-devel)")
    if b.gphoto2_programm:
        b.kameras = _kameras(lauf)
    else:
        b.hinweise.append("Das Programm gphoto2 fehlt — für die Erkennung hilfreich, "
                          "zum Auslösen reicht python-gphoto2.")
    b.blockierer = _prozesse_die_stoeren(lauf)
    if b.blockierer:
        b.hinweise.append("Der Schreibtisch hat die Kamera als Wechseldatenträger eingehängt. "
                          "Auf einem Kiosk-Rechner ohne Oberfläche passiert das nicht.")
    return b


def freigeben(lauf: Callable = _lauf) -> Dict[str, object]:
    """Die störenden Dienste beenden — nur auf ausdrückliche Anweisung.

    Beendet wird gezielt und ohne Gewalt: ``pkill`` auf genau die beiden
    bekannten Namen. Die Dienste starten beim nächsten Anstecken von selbst
    wieder, das ist kein dauerhafter Eingriff ins System des Betreibers.
    """
    beendet, fehler = [], []
    for dienst in _prozesse_die_stoeren(lauf):
        try:
            r = lauf(["pkill", "-f", dienst])
            (beendet if r.returncode in (0, 1) else fehler).append(dienst)
        except (OSError, subprocess.SubprocessError) as e:
            fehler.append(f"{dienst}: {e}")
    rest = _prozesse_die_stoeren(lauf)
    return {"beendet": beendet, "fehler": fehler, "noch_da": rest,
            "ok": not rest,
            "hinweis": ("Die Dienste kommen beim nächsten Anstecken wieder. Für den Dauerbetrieb "
                        "gehört die Box auf einen Rechner ohne Schreibtischoberfläche — "
                        "dort tritt das gar nicht erst auf.")}


def kamera_hinweis(marke: str) -> str:
    """Was in der Kamera selbst eingestellt sein muss."""
    return KAMERA_EINSTELLUNGEN.get(str(marke or "").strip().lower(), "")
