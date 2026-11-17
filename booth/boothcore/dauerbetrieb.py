"""Windows für den Dauerbetrieb einrichten — jede Einstellung einzeln, jede umkehrbar.

Eine Fotobox steht acht Stunden im Saal und darf in dieser Zeit nicht schlafen,
den Bildschirm nicht abschalten, den USB-Anschluss der Kamera nicht stromlos
machen und keine Benachrichtigung über den Gast legen. Das sind lauter kleine
Windows-Einstellungen, und bisher hat ``winsetup.install()`` sie alle auf einmal
gesetzt — ohne zu merken, was vorher dastand.

Das ist der Fehler, den dieses Modul behebt: Es ist nicht unser Rechner. Wer die
Box auf dem Büro-Laptop ausprobiert, bekommt einen Laptop zurück, der nie wieder
in den Standby geht, und findet nie heraus, warum. ``winsetup.uninstall()``
entfernt heute die Aufgabe und die Firewall-Regel, aber keine einzige
Energieeinstellung.

Deshalb hier: Vor jeder Änderung wird der Istwert gesichert, und
„Zurücksetzen“ stellt genau diesen Wert wieder her — nicht einen geratenen
Standard. Was nicht gesichert ist, wird auch nicht zurückgesetzt; dann sagt die
Box das, statt etwas zu erfinden.

Jede Maßnahme sagt außerdem, ob sie Administratorrechte braucht und ob sie erst
nach einem Neustart wirkt. Beides steht in der Oberfläche, bevor jemand drückt.

Läuft überall: Auf allem, was nicht Windows ist, meldet jede Maßnahme
``nicht-windows`` und verändert nichts. So ist das Modul auf dem Entwicklungs-
rechner prüfbar, ohne dass ein Test das System anfasst.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

#: Untergruppen und Einstellungen des Energieplans. Die GUIDs sind bei
#: Microsoft dokumentiert und über alle Sprachfassungen gleich — anders als
#: die Beschriftungen, die powercfg daneben druckt.
SUB_SLEEP = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
SUB_VIDEO = "7516b95f-f776-4464-8c53-06167f40cc99"
SUB_DISK = "0012ee47-9041-4b5d-9b77-535fba8b1442"
SUB_USB = "2a737441-1930-4402-8d77-b2bebba308a3"

STANDBYIDLE = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"
VIDEOIDLE = "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e"
DISKIDLE = "6738e2c4-e8a5-4a42-b16a-e040e769756e"
USBSELECTIVE = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"

_HEX = re.compile(r"0x[0-9a-fA-F]{8}")


@dataclass(frozen=True)
class Massnahme:
    """Eine einzelne Einstellung: was sie tut, wie man sie liest und setzt."""

    key: str
    titel: str
    warum: str
    #: "power" = Energieplan über powercfg, "registry" = ein Wert in der Registry
    art: str
    ziel: str
    admin: bool = False
    neustart: bool = False
    empfohlen: bool = True
    # art == "power"
    sub: str = ""
    setting: str = ""
    # art == "registry"
    pfad: str = ""
    name: str = ""
    typ: str = "REG_DWORD"
    #: Manche Einstellungen liest man in der Registry, setzt sie aber besser
    #: mit einem eigenen Befehl — der Ruhezustand zum Beispiel, weil powercfg
    #: dabei auch die mehrere Gigabyte große hiberfil.sys entfernt.
    befehl: Dict[str, Sequence[str]] = field(default_factory=dict)


MASSNAHMEN: Tuple[Massnahme, ...] = (
    Massnahme(
        key="standby", titel="Kein Standby", art="power", sub=SUB_SLEEP, setting=STANDBYIDLE,
        ziel="0",
        warum="Die Box geht mitten im Event schlafen und die Kamera verliert die Verbindung.",
    ),
    Massnahme(
        key="bildschirm", titel="Bildschirm bleibt an", art="power", sub=SUB_VIDEO, setting=VIDEOIDLE,
        ziel="0",
        warum="Ein schwarzer Bildschirm sieht aus wie eine kaputte Box. Der Gast geht weiter.",
    ),
    Massnahme(
        key="festplatte", titel="Festplatte bleibt an", art="power", sub=SUB_DISK, setting=DISKIDLE,
        ziel="0",
        warum="Das Aufwachen der Platte kostet die Sekunden, in denen das Foto entstehen soll.",
    ),
    Massnahme(
        key="usb", titel="USB-Energiesparen aus", art="power", sub=SUB_USB, setting=USBSELECTIVE,
        ziel="0",
        warum="Windows schaltet den Anschluss der Kamera ab; danach ist sie weg, bis jemand "
              "das Kabel zieht.",
    ),
    Massnahme(
        key="bildschirmschoner", titel="Bildschirmschoner aus", art="registry",
        pfad=r"HKCU\Control Panel\Desktop", name="ScreenSaveActive", typ="REG_SZ", ziel="0",
        warum="Derselbe schwarze Bildschirm, nur über einen anderen Weg.",
    ),
    Massnahme(
        key="benachrichtigungen", titel="App-Benachrichtigungen aus", art="registry",
        pfad=r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\PushNotifications",
        name="ToastEnabled", ziel="0",
        warum="„Ihr OneDrive ist voll“ steht sonst auf dem Startbildschirm der Box — und auf "
              "jedem Foto, das jemand davon macht.",
    ),
    Massnahme(
        key="ruhezustand", titel="Ruhezustand aus", art="registry", admin=True,
        pfad=r"HKLM\SYSTEM\CurrentControlSet\Control\Power", name="HibernateEnabled", ziel="0",
        befehl={"0": ("powercfg", "/hibernate", "off"), "1": ("powercfg", "/hibernate", "on")},
        warum="Aus dem Ruhezustand kommt die Box mit halb initialisierter Kamera zurück. "
              "Nebenbei gibt das mehrere Gigabyte frei.",
    ),
    Massnahme(
        key="schnellstart", titel="Windows-Schnellstart aus", art="registry",
        admin=True, neustart=True,
        pfad=r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power",
        name="HiberbootEnabled", ziel="0",
        warum="Der Schnellstart fährt den Rechner nicht wirklich herunter. Genau daher kommen "
              "die USB- und Treiberzustände, die nach dem Einschalten nicht stimmen.",
    ),
    Massnahme(
        key="update_neustart", titel="Update-Neustart bei Anmeldung verhindern", art="registry",
        admin=True,
        pfad=r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU",
        name="NoAutoRebootWithLoggedOnUsers", ziel="1",
        warum="Windows startet sonst mitten im Event neu, weil ein Update fertig ist.",
    ),
    # Active hours: Windows 11 restarts for an update outside them, and the
    # default (8 to 17 h, or "adjusted automatically" from office use) puts
    # a wedding night outside. 10:00 to 04:00 is the longest span Windows
    # accepts (18 h). The policy above alone does not hold on Windows 11
    # Home/Pro without scheduled installs; this does.
    Massnahme(
        key="aktivzeit_automatik", titel="Update-Nutzungszeit nicht automatisch anpassen",
        art="registry", admin=True,
        pfad=r"HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings", name="SmartActiveHoursState", ziel="0",
        warum="Sonst legt Windows die Nutzungszeit selbst fest, nach dem Bürotag — und der Abend "
              "einer Hochzeit liegt außerhalb. Dann darf es für ein Update neu starten.",
    ),
    Massnahme(
        key="aktivzeit_beginn", titel="Update-Nutzungszeit ab 10 Uhr", art="registry", admin=True,
        pfad=r"HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings", name="ActiveHoursStart", ziel="10",
        warum="Innerhalb der Nutzungszeit startet Windows nicht für Updates neu. 10 bis 4 Uhr "
              "deckt Aufbau, Feier und Abbau ab.",
    ),
    Massnahme(
        key="aktivzeit_ende", titel="Update-Nutzungszeit bis 4 Uhr", art="registry", admin=True,
        pfad=r"HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings", name="ActiveHoursEnd", ziel="4",
        warum="Das Ende der Nutzungszeit: Ein Update-Neustart kommt frühestens um 4 Uhr morgens, "
              "wenn die Gäste weg sind.",
    ),
    Massnahme(
        key="randgesten", titel="Windows-Randgesten unterbinden", art="registry",
        admin=True, empfohlen=False,
        pfad=r"HKLM\SOFTWARE\Policies\Microsoft\Windows\EdgeUI", name="AllowEdgeSwipe", ziel="0",
        warum="Auf einem Touchscreen wischt der Gast von rechts das Info-Center auf. "
              "Nicht empfohlen voreingestellt, weil es auch dem Betreiber die Geste nimmt.",
    ),
    # The Windows key opens the start menu over the kiosk; a keyboard left at
    # the booth or a buzzer box with a full keyboard inside is enough. A
    # Scancode Map maps both Windows keys (E0 5B, E0 5C) to nothing, for every
    # keyboard of the machine and every user - that is why it is not
    # recommended by default, and why it needs a restart (the keyboard driver
    # reads it at boot). An own map of the operator is saved and comes back.
    Massnahme(
        key="windows_taste", titel="Windows-Taste sperren", art="registry",
        admin=True, neustart=True, empfohlen=False,
        pfad=r"HKLM\SYSTEM\CurrentControlSet\Control\Keyboard Layout", name="Scancode Map",
        typ="REG_BINARY", ziel="00000000000000000300000000005BE000005CE000000000",
        warum="Ein Druck auf die Windows-Taste öffnet das Startmenü über dem Kiosk. Gilt für alle "
              "Tastaturen und alle Benutzer dieses Rechners, auch für den Betreiber.",
    ),
    # Windows 11, Settings > Bluetooth & devices > Touch: "Three- and
    # four-finger touch gestures" (switch apps, show the desktop). Per user,
    # read at sign-in.
    Massnahme(
        key="touchgesten", titel="Drei- und Vierfinger-Gesten aus", art="registry",
        neustart=True, empfohlen=False,
        pfad=r"HKCU\Control Panel\Desktop", name="TouchGestureSetting", ziel="0",
        warum="Mit drei oder vier Fingern wischt ein Gast sonst zum Desktop oder in die "
              "Fensterübersicht. Wirkt nach dem nächsten Anmelden.",
    ),
)

NACH_KEY: Dict[str, Massnahme] = {m.key: m for m in MASSNAHMEN}


def ist_windows() -> bool:
    return os.name == "nt"


# Windows' own tools (powercfg, reg, schtasks, netsh) answer in the OEM code
# page, not in the ANSI one Python assumes: a German "ü" then cannot be decoded
# and the whole set-up died on its first umlaut.
_OEM = "oem" if os.name == "nt" else None


def _run(args: Sequence[str]) -> Tuple[int, str]:
    try:
        out = subprocess.run(list(args), capture_output=True, text=True, encoding=_OEM,
                             errors="replace", timeout=60)
        return out.returncode, ((out.stdout or "") + (out.stderr or "")).strip()
    except Exception as e:                      # noqa: BLE001 - a set-up step must never raise
        return 1, str(e)


class Dauerbetrieb:
    """Die Einstellungen lesen, einzeln setzen und einzeln zurücknehmen."""

    def __init__(self, zustand: Path, *,
                 run: Optional[Callable[[Sequence[str]], Tuple[int, str]]] = None,
                 windows: Optional[bool] = None) -> None:
        self.zustand = Path(zustand)
        self._run = run or _run
        self.windows = ist_windows() if windows is None else bool(windows)

    # ── das Gedächtnis ────────────────────────────────────────────────
    # Ohne diese Datei wäre „Zurücksetzen“ nur geraten. Sie hält je Maßnahme
    # den Wert, der vor unserem Eingriff dastand — auch den Fall „stand gar
    # nicht da" (None), denn dann ist das Zurücksetzen ein Löschen.

    def _gesichert(self) -> Dict[str, Optional[str]]:
        try:
            daten = json.loads(self.zustand.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return daten if isinstance(daten, dict) else {}

    def _schreiben(self, daten: Dict[str, Optional[str]]) -> None:
        try:
            self.zustand.parent.mkdir(parents=True, exist_ok=True)
            self.zustand.write_text(json.dumps(daten, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
        except OSError:                                     # pragma: no cover - Systemfehler
            pass

    def _sichern(self, key: str, wert: Optional[str]) -> None:
        daten = self._gesichert()
        # Zweimal „Anwenden“ darf den ursprünglichen Wert nicht überschreiben,
        # sonst sichert der zweite Aufruf unseren eigenen Zielwert und das
        # Zurücksetzen führt nirgendwohin.
        if key in daten:
            return
        daten[key] = wert
        self._schreiben(daten)

    def _vergessen(self, key: str) -> None:
        daten = self._gesichert()
        if key in daten:
            daten.pop(key)
            self._schreiben(daten)

    # ── lesen ─────────────────────────────────────────────────────────

    def lesen(self, m: Massnahme) -> Optional[str]:
        """Istwert als Text, oder ``None`` wenn nichts gesetzt/lesbar ist."""
        if not self.windows:
            return None
        if m.art == "power":
            return self._power_lesen(m)
        return self._registry_lesen(m)

    def _power_lesen(self, m: Massnahme) -> Optional[str]:
        code, text = self._run(["powercfg", "/query", "SCHEME_CURRENT", m.sub, m.setting])
        if code != 0:
            return None
        # powercfg druckt sechs Hexzahlen: Mindest-, Höchst- und Schrittwert,
        # dann der aktuelle Netz- und der aktuelle Akkuwert. Die Beschriftungen
        # sind übersetzt („Aktueller Wechselstrom-Energieindex“), die Reihenfolge
        # ist es nicht — deshalb wird gezählt statt gelesen. Uns interessiert der
        # Netzwert: die Box hängt an der Steckdose.
        werte = _HEX.findall(text)
        if len(werte) < 2:
            return None
        return str(int(werte[-2], 16))

    def _registry_lesen(self, m: Massnahme) -> Optional[str]:
        code, text = self._run(["reg", "query", m.pfad, "/v", m.name])
        if code != 0:
            return None
        for zeile in text.splitlines():
            # Zeile: "    ToastEnabled    REG_DWORD    0x1" — der Typname ist
            # nicht übersetzt, daran hängt sich das Auslesen auf. The name is
            # matched as a whole, because some carry a space ("Scancode Map").
            zeile = zeile.strip()
            if not zeile.lower().startswith(m.name.lower()):
                continue
            teile = zeile[len(m.name):].split()
            if len(teile) >= 2 and teile[0].startswith("REG_"):
                roh = teile[1]
                return str(int(roh, 16)) if roh.lower().startswith("0x") else roh
        return None

    # ── setzen ────────────────────────────────────────────────────────

    def _power_setzen(self, m: Massnahme, wert: str) -> Tuple[bool, str]:
        code, text = self._run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT",
                                m.sub, m.setting, wert])
        if code != 0:
            return False, text
        # Ohne setactive steht der Wert zwar im Plan, wirkt aber erst beim
        # nächsten Planwechsel. Das hat früher wie ein Fehlschlag ausgesehen.
        code, text = self._run(["powercfg", "/setactive", "SCHEME_CURRENT"])
        return code == 0, text

    def _registry_setzen(self, m: Massnahme, wert: Optional[str]) -> Tuple[bool, str]:
        if m.befehl:
            cmd = m.befehl.get(str(wert))
            if cmd:
                code, text = self._run(cmd)
                return code == 0, text
        if wert is None:
            code, text = self._run(["reg", "delete", m.pfad, "/v", m.name, "/f"])
            return code == 0, text
        code, text = self._run(["reg", "add", m.pfad, "/v", m.name, "/t", m.typ,
                                "/d", wert, "/f"])
        return code == 0, text

    def _setzen(self, m: Massnahme, wert: Optional[str]) -> Tuple[bool, str]:
        if m.art == "power":
            if wert is None:
                return False, "Für eine Energieeinstellung gibt es kein „nicht gesetzt“."
            return self._power_setzen(m, wert)
        return self._registry_setzen(m, wert)

    # ── Status ────────────────────────────────────────────────────────

    def eintrag(self, m: Massnahme) -> Dict[str, object]:
        gesichert = self._gesichert()
        ist = self.lesen(m)
        if not self.windows:
            zustand = "nicht-windows"
        elif ist is None:
            zustand = "unbekannt"
        elif ist == m.ziel:
            zustand = "optimiert"
        else:
            zustand = "offen"
        return {
            "key": m.key, "titel": m.titel, "warum": m.warum,
            "admin": m.admin, "neustart": m.neustart, "empfohlen": m.empfohlen,
            "ist": ist, "ziel": m.ziel, "zustand": zustand,
            "gesichert": m.key in gesichert,
        }

    def status(self) -> List[Dict[str, object]]:
        return [self.eintrag(m) for m in MASSNAHMEN]

    # ── anwenden und zurücknehmen ─────────────────────────────────────

    def anwenden(self, key: str) -> Dict[str, object]:
        m = NACH_KEY.get(key)
        if m is None:
            return {"key": key, "ok": False, "meldung": "Unbekannte Einstellung."}
        if not self.windows:
            return {"key": key, "ok": False, "meldung": "Diese Einstellung gibt es nur unter Windows."}
        vorher = self.lesen(m)
        if vorher == m.ziel:
            return {"key": key, "ok": True, "meldung": "Steht schon so.", "geaendert": False}
        self._sichern(key, vorher)
        ok, text = self._setzen(m, m.ziel)
        if not ok:
            # Nichts geändert heißt: auch nichts gemerkt. Sonst stünde in der
            # Datei ein Wert, zu dem es keine Änderung gibt, und „Zurücksetzen“
            # würde etwas anfassen, das wir nie angefasst haben.
            self._vergessen(key)
            hinweis = " Administratorrechte nötig." if m.admin else ""
            return {"key": key, "ok": False, "geaendert": False,
                    "meldung": (text or "Der Befehl ist fehlgeschlagen.") + hinweis}
        meldung = "Übernommen."
        if m.neustart:
            meldung += " Wirkt nach einem Neustart."
        return {"key": key, "ok": True, "geaendert": True, "meldung": meldung}

    def zuruecksetzen(self, key: str) -> Dict[str, object]:
        m = NACH_KEY.get(key)
        if m is None:
            return {"key": key, "ok": False, "meldung": "Unbekannte Einstellung."}
        if not self.windows:
            return {"key": key, "ok": False, "meldung": "Diese Einstellung gibt es nur unter Windows."}
        gesichert = self._gesichert()
        if key not in gesichert:
            # Der wichtigste Fall dieser ganzen Datei: Wir haben es nicht
            # verstellt, also stellen wir es auch nicht zurück. Ein geratener
            # „Standardwert“ wäre hier eine Änderung am Rechner des Betreibers,
            # die niemand angefordert hat.
            return {"key": key, "ok": False, "geaendert": False,
                    "meldung": "Von der Fotobox nicht verändert — es gibt nichts zurückzunehmen."}
        ok, text = self._setzen(m, gesichert[key])
        if not ok:
            hinweis = " Administratorrechte nötig." if m.admin else ""
            return {"key": key, "ok": False, "geaendert": False,
                    "meldung": (text or "Der Befehl ist fehlgeschlagen.") + hinweis}
        self._vergessen(key)
        meldung = "Zurückgesetzt."
        if m.neustart:
            meldung += " Wirkt nach einem Neustart."
        return {"key": key, "ok": True, "geaendert": True, "meldung": meldung}

    def empfohlen_anwenden(self) -> List[Dict[str, object]]:
        return [self.anwenden(m.key) for m in MASSNAHMEN if m.empfohlen]

    def alles_zuruecksetzen(self) -> List[Dict[str, object]]:
        """Nur das, was wir selbst verstellt haben — in umgekehrter Reihenfolge."""
        gesichert = self._gesichert()
        return [self.zuruecksetzen(m.key) for m in reversed(MASSNAHMEN) if m.key in gesichert]

    def bericht(self) -> str:
        zeichen = {"optimiert": "OK  ", "offen": "--  ", "unbekannt": "?   ",
                   "nicht-windows": "    "}
        zeilen = []
        for e in self.status():
            marke = zeichen.get(str(e["zustand"]), "    ")
            zusatz = []
            if e["admin"]:
                zusatz.append("Administrator")
            if e["neustart"]:
                zusatz.append("Neustart nötig")
            schwanz = f" ({', '.join(zusatz)})" if zusatz else ""
            zeilen.append(f"{marke}{e['titel']}{schwanz}")
        return "\n".join(zeilen)
