"""Ein Windows zum Draufschlagen: powercfg und reg als Attrappe.

Damit sind die Maßnahmen des Dauerbetriebs auf jedem Rechner prüfbar, ohne
dass ein Test eine echte Einstellung anfasst. Die Attrappe ahmt genau das
nach, worauf sich ``dauerbetrieb.py`` verlässt — und zwar in der Form, in der
ein deutsches Windows antwortet, denn dort sind die Beschriftungen übersetzt
und nur die Reihenfolge der Werte ist verlässlich.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple


class FakeWindows:
    def __init__(self, *, admin: bool = True) -> None:
        #: Energieplan: (sub, setting) -> (Netzwert, Akkuwert)
        self.power: Dict[Tuple[str, str], Tuple[int, int]] = {}
        #: Registry: (pfad_klein, name_klein) -> (typ, wert)
        self.registry: Dict[Tuple[str, str], Tuple[str, str]] = {}
        self.admin = admin
        self.aktiviert = 0          # wie oft /setactive gerufen wurde
        self.aufrufe: List[List[str]] = []

    # ── einrichten ────────────────────────────────────────────────────

    def setze_power(self, sub: str, setting: str, netz: int, akku: Optional[int] = None) -> None:
        self.power[(sub, setting)] = (netz, netz if akku is None else akku)

    def setze_registry(self, pfad: str, name: str, wert: str, typ: str = "REG_DWORD") -> None:
        self.registry[(pfad.lower(), name.lower())] = (typ, wert)

    # ── der Läufer, den Dauerbetrieb bekommt ──────────────────────────

    def run(self, args: Sequence[str]) -> Tuple[int, str]:
        args = list(args)
        self.aufrufe.append(args)
        if args[0] == "powercfg":
            return self._powercfg(args[1:])
        if args[0] == "reg":
            return self._reg(args[1:])
        return 1, f"Unbekannter Befehl: {args[0]}"

    # ── powercfg ──────────────────────────────────────────────────────

    def _powercfg(self, rest: List[str]) -> Tuple[int, str]:
        if rest[:1] == ["/query"]:
            _, sub, setting = rest[1], rest[2], rest[3]
            werte = self.power.get((sub, setting))
            if werte is None:
                return 1, "Der Parameter ist ungültig."
            netz, akku = werte
            # So druckt es ein deutsches Windows: übersetzte Beschriftungen,
            # feste Reihenfolge — Mindest-, Höchst-, Schrittwert, Netz, Akku.
            return 0, (
                "Energieschema-GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Ausbalanciert)\n"
                f"  GUID der Untergruppe: {sub}  (Energie sparen)\n"
                f"    GUID der Energieeinstellung: {setting}  (Standbymodus nach)\n"
                "      Mindestwert des möglichen Einstellungsindex: 0x00000000\n"
                "      Höchstwert des möglichen Einstellungsindex: 0xffffffff\n"
                "      Schrittweite des möglichen Einstellungsindex: 0x00000001\n"
                f"      Aktueller Wechselstrom-Energieindex: 0x{netz:08x}\n"
                f"      Aktueller Gleichstrom-Energieindex: 0x{akku:08x}\n"
            )
        if rest[:1] == ["/setacvalueindex"]:
            _, sub, setting, wert = rest[1], rest[2], rest[3], rest[4]
            alt = self.power.get((sub, setting), (0, 0))
            self.power[(sub, setting)] = (int(wert), alt[1])
            return 0, ""
        if rest[:1] == ["/setactive"]:
            self.aktiviert += 1
            return 0, ""
        if rest[:1] == ["/hibernate"]:
            if not self.admin:
                return 1, "Zugriff verweigert."
            an = rest[1] == "on"
            self.setze_registry(r"HKLM\SYSTEM\CurrentControlSet\Control\Power",
                                "HibernateEnabled", "1" if an else "0")
            return 0, ""
        return 1, "Der Parameter ist ungültig."

    # ── reg ───────────────────────────────────────────────────────────

    def _reg(self, rest: List[str]) -> Tuple[int, str]:
        befehl, pfad = rest[0], rest[1]
        name = rest[rest.index("/v") + 1] if "/v" in rest else ""
        schluessel = (pfad.lower(), name.lower())
        schreibend = befehl in ("add", "delete")
        if schreibend and pfad.upper().startswith("HKLM") and not self.admin:
            return 1, "FEHLER: Zugriff verweigert."
        if befehl == "query":
            eintrag = self.registry.get(schluessel)
            if eintrag is None:
                return 1, "FEHLER: Das System konnte den angegebenen Registrierungs" \
                          "schlüssel oder -wert nicht finden."
            typ, wert = eintrag
            roh = f"0x{int(wert):x}" if typ == "REG_DWORD" else wert
            return 0, f"\n{pfad}\n    {name}    {typ}    {roh}\n"
        if befehl == "add":
            typ = rest[rest.index("/t") + 1] if "/t" in rest else "REG_DWORD"
            wert = rest[rest.index("/d") + 1] if "/d" in rest else ""
            self.registry[schluessel] = (typ, wert)
            return 0, "Der Vorgang wurde erfolgreich beendet."
        if befehl == "delete":
            if schluessel not in self.registry:
                return 1, "FEHLER: Das System konnte den angegebenen Wert nicht finden."
            self.registry.pop(schluessel)
            return 0, "Der Vorgang wurde erfolgreich beendet."
        return 1, "Ungültige Syntax."
