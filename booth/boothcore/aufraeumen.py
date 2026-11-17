"""Alte Aufnahmen von der Platte nehmen — aber nur, was nachweislich oben ist.

Die einzige Lücke im Dauerbetrieb, die mit der Zeit von allein schlimmer wird.
Ein Abend füllt ein paar Gigabyte, und das ist egal. Eine Box, die in einem
Foyer steht und jeden Tag läuft, ist nach ein paar Wochen voll — und dann
gehen Aufnahmen verloren, statt dass welche gelöscht werden.

Die Regel, an der hier alles hängt: **Gelöscht wird nur, was die Galerie
bestätigt hat.** Lieber behält die Box eine Woche zu lang als eine Aufnahme
zu früh. Jeder Zweifel — Warteschlange nicht leer, kein Beleg für die
Zustellung, kein Ziel eingerichtet — bedeutet behalten.

Nicht zu verwechseln mit zwei Dingen, die ähnlich klingen:

* ``library.delete()`` heißt „der Gast will das weg" und reist als
  Löschauftrag zur Galerie. Aufräumen fasst die Galerie nicht an; es entfernt
  nur die Kopie auf dieser Platte.
* Der datensparsame Betrieb (``ephemeral``) löscht sofort nach dem Druck und
  hat mit Speicherplatz nichts zu tun. Wo er läuft, gibt es hier nichts zu
  tun.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

log = logging.getLogger("boothcore.aufraeumen")

#: Unter diesem Wert wird auch aufgeräumt, was noch nicht alt genug ist —
#: aber weiterhin nur Bestätigtes. Eine volle Platte ist der einzige Grund,
#: die Frist zu unterschreiten.
NOTFALL_GB = 2.0


class Aufraeumer:
    """Sucht abgeschlossene Durchgänge, prüft die Zustellung, löscht Dateien."""

    def __init__(self, *, sessions_dir: Path, library, outbox,
                 config: Callable[[], dict], hat_ziele: Callable[[], bool],
                 clock: Optional[Callable[[], float]] = None,
                 unbestaetigt_erlaubt: Optional[Callable[[], bool]] = None) -> None:
        self.sessions_dir = Path(sessions_dir)
        self.library = library
        self.outbox = outbox
        self._config = config
        self._hat_ziele = hat_ziele
        self._clock = clock or time.time
        # Deleting what the gallery never confirmed needs a yes from this PC
        # (R2-N1). Without one, "nur_bestaetigte": false is read as true.
        self._unbestaetigt_erlaubt = unbestaetigt_erlaubt or (lambda: False)
        self.zuletzt: Dict[str, object] = {"at": 0.0, "geprueft": 0, "geloescht": 0,
                                           "behalten": 0, "bytes": 0, "grund": ""}

    # ── Einstellungen ─────────────────────────────────────────────────

    def _cfg(self) -> dict:
        roh = dict(self._config() or {})
        return {
            "enabled": bool(roh.get("enabled")),
            "nach_tagen": max(1, int(roh.get("nach_tagen") or 14)),
            "nur_bestaetigte": bool(roh.get("nur_bestaetigte", True)) or not self._erlaubt(),
            "behalten_mindestens": max(0, int(roh.get("behalten_mindestens") or 0)),
        }

    def _erlaubt(self) -> bool:
        try:
            return bool(self._unbestaetigt_erlaubt())
        except Exception:                                   # noqa: BLE001 — in doubt: keep
            return False

    # ── ist dieser Durchgang oben? ────────────────────────────────────

    def _zugestellt(self, uid: str, item: dict) -> Optional[bool]:
        """``True`` = oben, ``False`` = nicht oben, ``None`` = keine Auskunft.

        ``None`` ist der wichtigste Rückgabewert: Er heißt „ich weiß es
        nicht", und damit wird nichts gelöscht. Er kommt vor, wenn die
        Warteschlange ihre erledigten Einträge längst weggeräumt hat und
        niemand vorher eine Notiz gemacht hat.
        """
        if item.get("geliefert"):
            return True
        try:
            offen = self.outbox.offen_fuer_session(uid)
        except AttributeError:                              # ältere Warteschlange
            return None
        if offen is None:
            return None
        if offen["offen"]:
            return False
        if offen["erledigt"]:
            # Jetzt gesehen, also jetzt festhalten — die Warteschlange
            # vergisst es in dreißig Tagen.
            self.library.mark_geliefert(uid)
            return True
        return None

    # ── ein Durchgang ─────────────────────────────────────────────────

    def _ordner(self, item: dict) -> Optional[Path]:
        """Der Sitzungsordner — und nur, wenn er wirklich unter uns liegt."""
        for schluessel in ("result", "thumb", "print_file", "anim"):
            wert = item.get(schluessel)
            if not wert:
                continue
            pfad = Path(str(wert))
            ordner = pfad.parent
            try:
                ordner = ordner.resolve()
            except OSError:                                 # pragma: no cover
                continue
            # Ein Pfad aus der Datenbank ist kein Freibrief: Gelöscht wird
            # ausschließlich innerhalb des Sitzungsordners.
            if self.sessions_dir.resolve() in ordner.parents and ordner.name == item["uid"]:
                return ordner
        return None

    def _entfernen(self, item: dict) -> int:
        ordner = self._ordner(item)
        if ordner is None or not ordner.exists():
            # Dateien schon weg — der Eintrag wird trotzdem abgehakt, sonst
            # prüft die Box ihn bis in alle Ewigkeit erneut.
            self.library.mark_aufgeraeumt(item["uid"])
            return 0
        groesse = 0
        for p in ordner.rglob("*"):
            if p.is_file():
                try:
                    groesse += p.stat().st_size
                except OSError:                             # pragma: no cover
                    pass
        shutil.rmtree(ordner, ignore_errors=True)
        self.library.mark_aufgeraeumt(item["uid"])
        return groesse

    # ── der Durchgang über alles ──────────────────────────────────────

    def lauf(self, *, frei_gb: Optional[float] = None) -> Dict[str, object]:
        cfg = self._cfg()
        jetzt = self._clock()
        bericht = {"at": jetzt, "geprueft": 0, "geloescht": 0, "behalten": 0,
                   "bytes": 0, "grund": ""}
        if not cfg["enabled"]:
            bericht["grund"] = "Aufräumen ist aus."
            self.zuletzt = bericht
            return bericht
        if cfg["nur_bestaetigte"] and not self._hat_ziele():
            # Ohne Ziel gibt es keine zweite Kopie. Diese Platte ist dann das
            # Original, und ein Original löscht man nicht nach Zeitplan.
            bericht["grund"] = ("Kein Ziel eingerichtet — ohne zweite Kopie wird nichts gelöscht.")
            self.zuletzt = bericht
            return bericht

        knapp = frei_gb is not None and frei_gb < NOTFALL_GB
        grenze = jetzt - cfg["nach_tagen"] * 86400
        if knapp:
            # Die Frist wird unterschritten, die Bestätigung nie.
            grenze = jetzt
        kandidaten = self.library.aufraeum_kandidaten(grenze)
        if cfg["behalten_mindestens"]:
            # Die jüngsten N bleiben immer liegen — der Betreiber will am
            # Morgen danach noch nachdrucken können.
            kandidaten = kandidaten[:max(0, len(kandidaten) - cfg["behalten_mindestens"])]

        for item in kandidaten:
            bericht["geprueft"] += 1
            uid = str(item["uid"])
            zugestellt = self._zugestellt(uid, item)
            if cfg["nur_bestaetigte"] and zugestellt is not True:
                bericht["behalten"] += 1
                continue
            try:
                bericht["bytes"] += self._entfernen(item)
                bericht["geloescht"] += 1
            except OSError as e:                            # pragma: no cover - Systemfehler
                log.warning("Aufräumen von %s fehlgeschlagen: %s", uid, e)
                bericht["behalten"] += 1

        if knapp and bericht["geloescht"]:
            bericht["grund"] = (f"Platte knapp ({frei_gb:.1f} GB) — die Frist von "
                                f"{cfg['nach_tagen']} Tagen wurde vorgezogen.")
        elif bericht["behalten"] and not bericht["geloescht"]:
            bericht["grund"] = "Nichts war bestätigt — es wurde nichts gelöscht."
        log.info("Aufgeräumt: %s von %s Durchgängen, %.1f MB frei",
                 bericht["geloescht"], bericht["geprueft"], bericht["bytes"] / 1e6)
        self.zuletzt = bericht
        return bericht

    def stand(self) -> Dict[str, object]:
        cfg = self._cfg()
        zahlen = self.library.zahlen()
        return {**cfg, "hat_ziele": self._hat_ziele(), "zuletzt": self.zuletzt, **zahlen}
