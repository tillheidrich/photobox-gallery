"""Druckwarteschlange mit Zähler und Limit.

Der SELPHY braucht rund eine Minute pro Karte. Deshalb: Aufträge sofort
annehmen, seriell abarbeiten, Status für Oberfläche und Heartbeat bereithalten.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Callable, List, Optional

from .backends import (CupsPrinter, FolderPrinter, HotfolderPrinter, IppPrinter, PrintCancelled, PrinterBackend,
                       WindowsPrinter)
from .spool import Spool

log = logging.getLogger("booth.print")


def create_printer(cfg: dict, data: Path) -> Optional[PrinterBackend]:
    kind = (cfg.get("backend") or "auto").lower()
    if kind == "none":
        return None
    if kind == "auto":
        kind = "windows" if os.name == "nt" else ("cups" if _has_lp() else "folder")
    try:
        if kind == "windows":
            return WindowsPrinter(cfg.get("name", ""), follow_jobs=_schalter(cfg.get("follow_jobs"), True))
        if kind == "cups":
            return CupsPrinter(cfg.get("name", ""), cfg.get("paper", "postcard"))
        if kind == "ipp":
            return IppPrinter(cfg.get("uri", ""), cfg.get("media", ""))
        if kind == "hotfolder":
            return HotfolderPrinter(Path(cfg.get("folder") or data / "hotfolder"))
    except Exception as e:  # pywin32 fehlt o. Ä.
        log.warning("Drucker-Backend %s nicht verfügbar: %s — nutze Ordner", kind, e)
    return FolderPrinter(data / "print-out")


def erkennen(cfg: dict, data: Path) -> dict:
    """Was steht da eigentlich? Modell und eingelegtes Medium selbst herausfinden.

    Ein Betreiber weiß, dass ein DS620 dasteht — welche Kennung unsere
    Modelltabelle dafür führt, weiß er nicht, und die Blattzahl einer vollen
    Kassette erst recht nicht. Beides lässt sich aus dem Namen ableiten, den
    das Betriebssystem ohnehin nennt, und aus dem Papier, das der Treiber
    eingestellt hat.

    Zurückkommt ein Vorschlag, keine Tatsache: Was die Box nicht erkennt,
    bleibt leer, und was der Betreiber eingestellt hat, wird nicht angerührt.
    """
    from . import models

    aus = {"name": "", "model": None, "medium": None, "papier": "", "drucker": []}
    backend = create_printer(cfg, data)
    if backend is None:
        return aus
    try:
        aus["drucker"] = [n for n in backend.list_printers() if n]
    except Exception as e:                       # noqa: BLE001 — Suchen darf nichts umwerfen
        log.warning("Druckerliste nicht lesbar: %s", e)
    # Der eingestellte Name gilt; ist keiner eingestellt, nimmt die Box den,
    # den das System nennt — bei einem Kiosk hängt meistens genau einer dran.
    name = str(cfg.get("name") or "").strip()
    if not name:
        gemeldet = (backend.status() or {}).get("name") or ""
        name = gemeldet if gemeldet in aus["drucker"] else (aus["drucker"][0] if len(aus["drucker"]) == 1 else "")
    aus["name"] = name
    aus["model"] = models.erkenne(name)
    try:
        aus["papier"] = backend.papier() or ""
    except Exception as e:                       # noqa: BLE001
        log.warning("Papierformat nicht lesbar: %s", e)
    if aus["model"]:
        aus["medium"] = models.erkenne_medium(aus["model"], aus["papier"])
    return aus


def uebernehmen(cfg: dict, data: Path) -> dict:
    """Erkanntes in eine Druckerkonfiguration eintragen — nur, was noch leer ist.

    Eine Einstellung, die jemand von Hand gesetzt hat, ist eine Entscheidung.
    Die Box überschreibt sie nicht, auch wenn sie es besser zu wissen glaubt.
    """
    fund = erkennen(cfg, data)
    neu = dict(cfg)
    geaendert = []
    if fund["model"] and not str(cfg.get("model") or "").strip():
        neu["model"] = fund["model"]
        geaendert.append("model")
    if fund["medium"] and not str(cfg.get("medium") or "").strip():
        neu["medium"] = fund["medium"]
        geaendert.append("medium")
    if fund["name"] and not str(cfg.get("name") or "").strip():
        neu["name"] = fund["name"]
        geaendert.append("name")
    return {"config": neu, "erkannt": fund, "geaendert": geaendert}


def printer_configs(local: dict) -> list:
    """Druckerliste aus der lokalen Konfiguration (``printers``; ältere Boxen: ``printer``/``printer2``).

    Dabei wird ergänzt, was das Modell über sich weiß: Ein DS620 mit 10×15 hat
    400 Blatt auf der Kassette, ein SELPHY 108. Der Betreiber darf die Zahl
    überschreiben — aber er soll sie nicht nachschlagen müssen.
    """
    from . import models

    items = [p for p in (local.get("printers") or []) if isinstance(p, dict)]
    if not items:
        items = [local.get("printer") or {}]
        if (local.get("printer2") or {}).get("backend", "none") != "none":
            items.append(local["printer2"])
    aus = []
    for p in items:
        if not p.get("enabled", True) or (p.get("backend") or "auto") == "none":
            continue
        eintrag = dict(p)
        if not int(eintrag.get("sheets_total") or 0):
            eintrag["sheets_total"] = models.blatt_je_kassette(eintrag.get("model"), eintrag.get("medium"))
        # Tray and ribbon (SELPHY): an operator's number wins, 0 or empty
        # takes the model's default. The admin form sends strings.
        vorgabe = models.verbrauch(eintrag.get("model"))
        eintrag["tray_total"] = _zahl(eintrag.get("tray_total")) or vorgabe.get("tray", 0)
        eintrag["ribbon_total"] = _zahl(eintrag.get("ribbon_total")) or vorgabe.get("ribbon", 0)
        aus.append(eintrag)
    return aus


def _schalter(wert, vorgabe: bool = True) -> bool:
    """An on/off setting: bool as is; the admin form may send "false", "0", "", "off". Missing = default."""
    if wert is None:
        return vorgabe
    if isinstance(wert, str):
        return wert.strip().lower() not in ("", "0", "false", "off", "no", "nein", "aus")
    return bool(wert)


def _zahl(wert) -> int:
    """A capacity from the settings: a whole number from 0 up, anything else is 0."""
    try:
        return max(0, int(float(wert or 0)))
    except (TypeError, ValueError):
        return 0


def _has_lp() -> bool:
    from shutil import which

    return which("lp") is not None


@dataclass
class PrintJob:
    path: Path
    copies: int = 1
    session_uid: str = ""
    created: float = field(default_factory=time.time)
    orientation: str = ""          # "portrait" | "landscape" — wird beim Einreihen ermittelt
    tried: list = field(default_factory=list)
    attempts: int = 0
    #: Nummer im Zettelkasten (``spool.py``). 0 = nicht vorgemerkt (Testbetrieb).
    spool_id: int = 0
    #: Accepted while the printer had no paper (``print.queue_while_empty``).
    deferred: bool = False


ROUTING = ("balance", "failover", "orientation")   # orientation = Kurzform: 1 hoch, 2 quer
UNAVAILABLE = ("offline", "error")


class Station:
    """Ein Drucker mit eigener Warteschlange und eigenem Thread."""

    def __init__(self, index: int, backend: PrinterBackend, service: "PrintService", only: str = "any",
                 label: str = "", model: str = "", medium: str = "", sheets_total: int = 0,
                 tray_total: int = 0, ribbon_total: int = 0):
        self.index = index
        self.only = only if only in ("portrait", "landscape") else "any"
        self.label = label
        # Welches Gerät hier steht und was drin ist. Daraus kennt die Box die
        # Blattzahl einer vollen Kassette — die kein Fotodrucker je meldet.
        self.model = model
        self.medium = medium
        self.sheets_total = int(sheets_total or 0)
        self.printed = 0                 # seit dem letzten „Papier gewechselt"
        # A SELPHY needs the operator twice, at different counts: an empty
        # paper tray and a used-up ink ribbon. Each has its own capacity and
        # its own count since it was last refilled/changed. Both zero = this
        # printer keeps the single count per cassette above.
        self.tray_total = int(tray_total or 0)
        self.ribbon_total = int(ribbon_total or 0)
        self.fach_gedruckt = 0           # since "Papier nachgelegt"
        self.band_gedruckt = 0           # since "Farbband gewechselt"
        self.backend = backend
        self.service = service
        self.q: "queue.Queue[PrintJob]" = queue.Queue()
        #: Angenommen, aber noch nicht fertig. Nicht dasselbe wie qsize+busy:
        #: Zwischen ``q.get()`` und ``busy = True`` liegt ein Augenblick, in dem
        #: ein Auftrag nirgendwo gezählt wurde. Wer in diesem Fenster einen
        #: zweiten Auftrag einreicht, sah beide Drucker bei null und bekam
        #: wieder den ersten — bei zwei Druckern genau der Fall, für den man
        #: zwei Drucker hat. Deshalb zählt die Vergabe selbst hoch, nicht der
        #: Drucker-Thread.
        self.angenommen = 0
        self.busy = False
        #: Spool id of the job this printer works on (0 = none): a discard
        #: of exactly this job asks the backend to take it back.
        self.current = 0
        self.last_error: Optional[str] = None
        self._status: dict = {"state": "unknown"}
        threading.Thread(target=self._run, name=f"printer{index + 1}", daemon=True).start()
        threading.Thread(target=self._poll, name=f"printer{index + 1}-status", daemon=True).start()

    def _poll(self) -> None:
        """Status im Hintergrund holen — die Oberfläche liest nur den letzten Wert und hängt nie am Netz."""
        while True:
            self._auffrischen()
            self.service._retry_failed(self)
            self.service._retry_held(self)
            try:
                self.service.drop_stale()
            except Exception as e:                   # noqa: BLE001 — a status poll must not die
                log.warning("Alte vorgemerkte Drucke nicht geprüft: %s", e)
            time.sleep(5)

    def _auffrischen(self) -> None:
        try:
            st = self.backend.status()
        except Exception as e:
            st = {"state": "error", "detail": str(e)[:200]}
        self._status = st

    @property
    def pending(self) -> int:
        return self.angenommen

    def state(self) -> str:
        return self._status.get("state", "unknown")

    def available(self) -> bool:
        return self.state() not in UNAVAILABLE

    def _grund(self) -> str:
        st = self._status
        zustand = {"offline": "offline", "error": "meldet einen Fehler"}.get(st.get("state"), "unbekannt")
        return str(st.get("hinweis") or st.get("detail") or self.last_error or zustand)[:200]

    @property
    def zwei_zaehler(self) -> bool:
        return self.tray_total > 0 and self.ribbon_total > 0

    def sheets(self) -> dict:
        """Was noch da ist — und woher die Box das weiß.

        ``quelle`` ist der ehrliche Teil: „gemeldet" heißt, der Drucker hat es
        gesagt; „gezählt" heißt, die Box rechnet mit, seit der Betreiber zuletzt
        Papier eingelegt hat; „unbekannt" heißt unbekannt. Geraten wird nie.
        """
        gemeldet = self._status.get("supply_percent")
        if gemeldet is not None:
            return {"quelle": "gemeldet", "prozent": int(gemeldet),
                    "leer": bool(self._status.get("reasons") and "media-empty" in self._status["reasons"])}
        if self.zwei_zaehler:
            fach = max(0, self.tray_total - self.fach_gedruckt)
            band = max(0, self.ribbon_total - self.band_gedruckt)
            # "rest" stays what it meant: prints before the operator is needed.
            return {"quelle": "gezählt", "rest": min(fach, band), "gesamt": self.tray_total,
                    "fach": {"rest": fach, "gesamt": self.tray_total},
                    "farbband": {"rest": band, "gesamt": self.ribbon_total},
                    "leer": fach == 0 or band == 0 or "media-empty" in (self._status.get("reasons") or [])}
        if self.sheets_total:
            rest = max(0, self.sheets_total - self.printed)
            return {"quelle": "gezählt", "rest": rest, "gesamt": self.sheets_total,
                    "leer": rest == 0 or "media-empty" in (self._status.get("reasons") or [])}
        return {"quelle": "unbekannt",
                "leer": "media-empty" in (self._status.get("reasons") or [])}

    def status(self) -> dict:
        st = dict(self._status)
        st.update({"slot": self.index + 1, "label": self.label, "only": self.only, "backend": self.backend.name, "pending": self.pending,
                   "last_error": self.last_error, "model": self.model, "medium": self.medium,
                   "sheets": self.sheets()})
        return st

    def _run(self) -> None:
        while True:
            job = self.q.get()
            # Zwischen dem Einreihen und diesem Augenblick können Minuten
            # liegen — ein SELPHY braucht für ein Bild fast eine. In dieser
            # Zeit kann der Gast seinen Durchgang gelöscht haben; dann liegt
            # der Auftrag auf „lost" und darf nicht mehr aus dem Drucker
            # kommen. Bis zur Durchsicht am 19.09.2026 kam er trotzdem.
            # Check and take in one conditional UPDATE: a discard between a
            # separate check and "printing" used to be overwritten.
            if not self.service.spool.started(job.spool_id, self.index):
                log.info("Druck verworfen (Durchgang gelöscht oder Druck verworfen): %s", job.path.name)
                self.service._vergessen(job.spool_id)
                self._abgearbeitet()
                continue
            self.busy = True
            self.current = int(job.spool_id or 0)
            self.service._note(job, "printing", self)
            pfad = job.path
            try:
                reset = getattr(self.backend, "reset_cancel", None)
                if reset:
                    reset()
                # A discard between "started" above and the reset set a flag
                # the reset just cleared; the spool row still says so.
                if job.spool_id and self.service.spool.zustand(job.spool_id) == "lost":
                    raise PrintCancelled("Druck vom Betreiber verworfen")
                # A fresh look, not the last poll's: that may be five seconds
                # old, or the "error" this thread wrote after the previous
                # sheet failed — the paper may have been refilled since.
                self._auffrischen()
                if not self.available():
                    # Known to be off or in error right now: the job
                    # is not handed over. A spooler would take it and keep it
                    # — and the booth used to count that as printed. It waits
                    # on the booth's side instead (failed list, then held).
                    raise RuntimeError(f"Drucker nicht bereit: {self._grund()}")
                pfad = self.service._fuer_drucker(job, self)
                # Returns when the backend has its evidence: Windows waits
                # until the job left the spooler; folder, hotfolder, CUPS and
                # IPP return on hand-over (see each backend's print_file).
                self.backend.print_file(pfad, job.copies)
                self.last_error = None
                self.service._zaehle_blatt(job, self)
                self.service._note(job, "printed", self)
                self.service._done(job)
            except PrintCancelled as e:
                # Thrown away on purpose: not a printer fault, no retry, no hold.
                log.info("Druck auf Drucker %s verworfen: %s (%s)", self.index + 1, job.path.name, e)
                self.service._vergessen(job.spool_id)
            except Exception as e:
                self.last_error = str(e)
                # An "offline" stays offline: the guest's words differ
                # ("nicht erreichbar" vs. "braucht Papier oder Farbband").
                if self.available():
                    self._status = {**self._status, "state": "error", "detail": str(e)[:200]}
                log.error("Druck auf Drucker %s fehlgeschlagen: %s", self.index + 1, e)
                self.service._failed(job, self)
            finally:
                if pfad != job.path:
                    try:
                        pfad.unlink()             # the corrected copy was only for this printer
                    except OSError:
                        pass
                self.busy = False
                self.current = 0
                self._abgearbeitet()

    def _abgearbeitet(self) -> None:
        with self.service._lock:
            self.angenommen = max(0, self.angenommen - 1)


def _orientation(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return "landscape" if im.width > im.height else "portrait"
    except Exception:
        return "portrait"


class PrintService:
    """Ein oder zwei Drucker (SELPHY hält etwa eine Minute pro Karte — zwei halbieren die Wartezeit).

    Verteilung (``routing``):

    * ``balance``     — der Drucker mit der kürzesten Schlange (Standard)
    * ``failover``    — immer Drucker 1, Drucker 2 nur, wenn 1 aus ist oder Fehler meldet
    * ``orientation`` — Hochformat auf Drucker 1, Querformat auf Drucker 2

    Schlägt ein Druck fehl, geht er einmal an den anderen Drucker.
    """

    def __init__(self, backend, counter_file: Path, on_printed: Optional[Callable[[PrintJob], None]] = None,
                 routing: str = "balance", options: Optional[list] = None,
                 on_failed: Optional[Callable[[PrintJob], None]] = None,
                 spool: Optional["Spool"] = None,
                 korrektur_fuer: Optional[Callable[[int], dict]] = None,
                 on_error: Optional[Callable[[PrintJob], None]] = None):
        raw = list(backend) if isinstance(backend, (list, tuple)) else [backend]
        options = options or [{} for _ in raw]
        pairs = [(b, o) for b, o in zip(raw, options + [{}] * (len(raw) - len(options))) if b is not None]
        self.counter_file = counter_file
        self.on_printed = on_printed
        self.on_failed = on_failed
        #: Printer entry of a station (index) with its brightness, contrast and
        #: saturation, read at each print: a change made remotely at night is
        #: in the next sheet, without a restart. None = no correction.
        self.korrektur_fuer = korrektur_fuer
        #: Every failed attempt, also one another printer then takes over.
        self.on_error = on_error
        # Der Zettelkasten auf der Platte. Ohne ihn liegt die Warteschlange nur
        # im Arbeitsspeicher — und ein Neustart während eines Papierwechsels
        # kostet jeden offenen Druck.
        # Der Zettelkasten gehört zu genau diesem Zähler: Zwei Druckdienste im
        # selben Ordner (Tests, zweite Instanz) dürfen sich nicht gegenseitig
        # die Aufträge wegnehmen.
        self.spool = spool if spool is not None else Spool(counter_file.with_suffix(".spool.sqlite"))
        self.failed: list = []          # wartet, bis ein Drucker wieder bereit ist
        self._unterwegs: set = set()    # Aufträge im Speicher — siehe _merken
        self.max_attempts = 3
        #: Where the latest print of a run stands, for the guest's screen:
        #: {"state": queued|printing|printed|stuck, "slot": n, "moved_to": n}.
        #: ``moved_to`` stays once set, so a guest who looks a moment later
        #: still learns that the print comes out of the other printer.
        self._outcome: Dict[str, dict] = {}
        self.routing = routing if routing in ROUTING else "balance"
        self.printed = self._read_counter()
        self.last_error: Optional[str] = None
        self._lock = threading.Lock()
        self.stations = [Station(i, b, self, o.get("only", "any"), o.get("label", ""),
                                 model=o.get("model", ""), medium=o.get("medium", ""),
                                 sheets_total=int(o.get("sheets_total") or 0),
                                 tray_total=_zahl(o.get("tray_total")), ribbon_total=_zahl(o.get("ribbon_total")))
                         for i, (b, o) in enumerate(pairs)]
        self._load_sheets()
        if self.routing == "orientation" and len(self.stations) > 1:
            self.stations[0].only, self.stations[1].only = "portrait", "landscape"
        self._wieder_einreihen()

    def _wieder_einreihen(self) -> None:
        """Was beim letzten Mal offen blieb, kommt zurück in die Reihe.

        Wartende (``held``) bleiben liegen, bis der Betreiber sagt, dass wieder
        Papier da ist — sonst laufen sie sofort in denselben Fehler.
        """
        if not self.stations:
            return
        for eintrag in self.spool.open_jobs():
            if eintrag["state"] != "queued":
                continue
            with self._lock:
                schon_da = int(eintrag["id"]) in self._unterwegs
            if schon_da:
                continue                    # steckt schon im Speicher, siehe _unterwegs
            pfad = Path(eintrag["path"])
            if not pfad.exists():
                self.spool.drop(eintrag["id"])
                continue
            job = PrintJob(path=pfad, copies=int(eintrag["copies"]), session_uid=eintrag["session_uid"] or "",
                           orientation=eintrag["orientation"] or "", spool_id=int(eintrag["id"]))
            log.info("Liegengebliebener Druck wird nachgeholt: %s", pfad.name)
            self._merken(job.spool_id)
            self.pick(job).q.put(job)

    def _merken(self, spool_id: int) -> None:
        """Dieser Auftrag steckt jetzt im Speicher, nicht nur in der Datenbank.

        Ein Auftrag bleibt in der Datenbank ``queued``, bis ein Drucker ihn
        anfasst. Ohne diese Liste reihte „Papier gewechselt" alles, was gerade
        in der Warteschlange stand, ein zweites Mal ein — und der Gast bekam
        sein Bild doppelt. Gefunden bei der Durchsicht am 19.09.2026.
        """
        if spool_id:
            with self._lock:
                self._unterwegs.add(int(spool_id))

    def _note(self, job: PrintJob, state: str, station: Optional["Station"] = None, moved: bool = False) -> None:
        if not job.session_uid:
            return
        with self._lock:
            out = dict(self._outcome.get(job.session_uid) or {})
            out.update({"state": state, "at": time.time()})
            if station is not None:
                out["slot"] = station.index + 1
                if moved:
                    out["moved_to"] = station.index + 1
            self._outcome[job.session_uid] = out
            if len(self._outcome) > 500:               # an evening has a few hundred runs, not more
                for uid in sorted(self._outcome, key=lambda u: self._outcome[u]["at"])[:100]:
                    self._outcome.pop(uid, None)

    def outcome(self, session_uid: str) -> dict:
        with self._lock:
            return dict(self._outcome.get(session_uid) or {})

    def _vergessen(self, spool_id: int) -> None:
        if spool_id:
            with self._lock:
                self._unterwegs.discard(int(spool_id))

    @property
    def backend(self) -> Optional[PrinterBackend]:
        return self.stations[0].backend if self.stations else None

    # ── Blätter je Drucker ────────────────────────────────
    # Der Gesamtzähler sagt, wie viel die Box insgesamt gedruckt hat. Für „ist
    # noch Papier da" zählt aber jeder Drucker für sich, und zwar seit dem
    # letzten Papierwechsel. Das steht neben dem Zähler auf der Platte, damit
    # ein Neustart mitten im Abend die Zahl nicht vergisst.

    @property
    def _sheets_file(self) -> Path:
        return self.counter_file.with_suffix(".sheets.json")

    def _load_sheets(self) -> None:
        try:
            daten = json.loads(self._sheets_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for st in self.stations:
            st.printed = int(daten.get(str(st.index), 0) or 0)
            # Absent in files written before tray and ribbon were counted:
            # then both start full, which is what the operator last confirmed.
            st.fach_gedruckt = int(daten.get(f"fach:{st.index}", 0) or 0)
            st.band_gedruckt = int(daten.get(f"farbband:{st.index}", 0) or 0)

    def _save_sheets(self) -> None:
        try:
            daten = {}
            for s in self.stations:
                daten.update({str(s.index): s.printed, f"fach:{s.index}": s.fach_gedruckt,
                              f"farbband:{s.index}": s.band_gedruckt})
            self._sheets_file.write_text(json.dumps(daten), encoding="utf-8")
        except OSError as e:                          # noqa: BLE001 — ein Zähler kostet keinen Druck
            log.warning("Blattzähler nicht gespeichert: %s", e)

    def paper_changed(self, slot: Optional[int] = None) -> int:
        """„Papier gewechselt" — der Zähler dieses Druckers fängt von vorn an.

        Ohne Angabe gilt es für alle. Zurück kommt, wie viele Drucker
        zurückgesetzt wurden. On a printer with tray and ribbon this is
        "Papier nachgelegt": the tray is full again, the ribbon is not.
        """
        betroffen = [s for s in self.stations if slot is None or s.index == int(slot)]
        for st in betroffen:
            st.printed = 0
            st.fach_gedruckt = 0
        self._save_sheets()
        return len(betroffen)

    def ribbon_changed(self, slot: Optional[int] = None) -> int:
        """„Farbband gewechselt" — the ribbon count of this printer starts again.

        Only printers that count a ribbon are touched and counted; the paper
        tray keeps its count. Without ``slot`` it applies to all.
        """
        betroffen = [s for s in self.stations
                     if s.zwei_zaehler and (slot is None or s.index == int(slot))]
        for st in betroffen:
            st.band_gedruckt = 0
        self._save_sheets()
        return len(betroffen)

    def sheets(self) -> List[dict]:
        return [{"slot": s.index + 1, "label": s.label, **s.sheets()} for s in self.stations]

    def _read_counter(self) -> int:
        try:
            return int(self.counter_file.read_text().strip() or 0)
        except (OSError, ValueError):
            return 0

    def reset_counter(self) -> None:
        self.printed = 0
        self.counter_file.write_text("0")

    @property
    def pending(self) -> int:
        return sum(s.pending for s in self.stations) + len(self.failed)

    @property
    def busy(self) -> bool:
        return any(s.busy for s in self.stations)

    def remaining(self, limit: int) -> Optional[int]:
        if not limit:
            return None
        return max(0, limit - self.printed - self.pending)

    def pick(self, job: PrintJob) -> Station:
        candidates = [s for s in self.stations if s.index not in job.tried] or list(self.stations)
        fitting = [s for s in candidates if s.only in ("any", job.orientation)] or candidates
        up = [s for s in fitting if s.available()] or [s for s in candidates if s.available()] or fitting
        if self.routing == "failover":
            return up[0]
        return min(up, key=lambda s: (s.pending, s.index))

    def submit(self, job: PrintJob) -> int:
        if not self.stations:
            raise RuntimeError("Kein Drucker konfiguriert")
        if not job.orientation:
            job.orientation = _orientation(job.path)
        if not job.spool_id:
            # Erst auf die Platte, dann in den Speicher: Ein Absturz zwischen
            # diesen beiden Zeilen kostet höchstens einen Zettel zu viel.
            job.spool_id = self.spool.add(path=str(job.path), copies=job.copies,
                                          session_uid=job.session_uid, orientation=job.orientation,
                                          deferred=job.deferred)
        # Auswählen und Hochzählen unter demselben Schloss: Sonst wählen zwei
        # gleichzeitige Aufträge denselben Drucker, weil beide den alten Stand
        # gelesen haben.
        with self._lock:
            station = self.pick(job)
            station.angenommen += 1
        moved = len(self.stations) > 1 and self.routing == "failover" and station.index > 0
        self._note(job, "queued", station, moved=moved)
        self._merken(job.spool_id)
        station.q.put(job)
        return station.index + 1

    def _fuer_drucker(self, job: PrintJob, station: "Station") -> Path:
        """The file this printer gets: the job's own, or a corrected copy.

        A correction that fails costs the correction, not the print.
        """
        from . import korrektur

        if self.korrektur_fuer is None:
            return job.path
        try:
            w = korrektur.werte(self.korrektur_fuer(station.index))
        except Exception as e:                       # noqa: BLE001 — settings unreadable: print as is
            log.warning("Druckkorrektur nicht lesbar: %s", e)
            return job.path
        if not korrektur.aktiv(w):
            return job.path
        ziel = self.counter_file.parent / "druck-korrektur" / f"{job.spool_id or id(job)}-{station.index + 1}.jpg"
        try:
            return korrektur.anwenden(job.path, w, ziel)
        except Exception as e:                       # noqa: BLE001
            log.warning("Druckkorrektur fehlgeschlagen (%s) — drucke unverändert", e)
            return job.path

    def _zaehle_blatt(self, job: PrintJob, station: "Station") -> None:
        """Ein durchgelaufenes Blatt weniger in der Kassette dieses Druckers."""
        blatt = max(1, int(job.copies))
        # A sheet that goes through while the count says "empty" proves the
        # count wrong: somebody refilled without pressing "Papier nachgelegt"
        # (a host without the PIN). Start a new tray or ribbon instead of
        # saying "rechnerisch leer" for the rest of the evening and never
        # warning about the next empty tray.
        if station.tray_total and station.fach_gedruckt >= station.tray_total:
            log.info("Drucker %s druckt trotz rechnerisch leerem Fach — Papier wurde nachgelegt", station.index + 1)
            station.fach_gedruckt = 0
        if station.ribbon_total and station.band_gedruckt >= station.ribbon_total:
            log.info("Drucker %s druckt trotz rechnerisch leerem Farbband — Band wurde gewechselt", station.index + 1)
            station.band_gedruckt = 0
        if station.sheets_total and not station.zwei_zaehler and station.printed >= station.sheets_total:
            station.printed = 0
        station.printed += blatt
        station.fach_gedruckt += blatt
        station.band_gedruckt += blatt
        self._save_sheets()

    def _done(self, job: PrintJob) -> None:
        self._vergessen(job.spool_id)
        self.spool.done(job.spool_id)
        with self._lock:
            self.printed += job.copies
            self.counter_file.write_text(str(self.printed))
        self.last_error = None
        if self.on_printed:
            self.on_printed(job)

    def _failed(self, job: PrintJob, station: Station) -> None:
        job.tried.append(station.index)
        job.attempts += 1
        self.last_error = station.last_error
        if self.on_error:
            try:
                self.on_error(job)
            except Exception as e:                   # noqa: BLE001 — a counter costs no retry
                log.warning("Fehlversuch nicht gezählt: %s", e)
        others = [s for s in self.stations if s.index not in job.tried]
        if others:
            nxt = self.pick(job)
            log.info("Druck wird an Drucker %s weitergegeben", nxt.index + 1)
            self._note(job, "queued", nxt, moved=True)
            nxt.q.put(job)
            return
        # No printer left to try: the guest is told now, not after the last retry.
        self._note(job, "stuck")
        if job.attempts >= self.max_attempts * max(1, len(self.stations)):
            # Nicht verwerfen: Papier, Farbband, Kabel — das sind Dinge, die
            # jemand in zwei Minuten behebt. Der Auftrag wartet sichtbar, bis
            # der Betreiber „weiter" sagt (release_held).
            log.error("Druck wartet auf den Betreiber: %s (%s)", job.path.name, self.last_error)
            self._vergessen(job.spool_id)   # liegt jetzt auf „held", nicht mehr im Speicher
            if not self.spool.hold(job.spool_id, self.last_error or ""):
                return                       # thrown away meanwhile: stays lost, allowance already back
            if self.on_failed:
                self.on_failed(job)          # z. B. Kontingent des Gasts zurückgeben
            return
        # Requeue and remember under the service lock: a discard marks the
        # row lost first and then clears ``failed`` under this lock, so either
        # it sees the job here, or requeue already refuses a lost job.
        with self._lock:
            if self.spool.requeue(job.spool_id, self.last_error or ""):
                self.failed.append(job)      # nicht verwerfen: nachdrucken, sobald ein Drucker bereit ist
            else:
                self._unterwegs.discard(int(job.spool_id or 0))

    def release_held(self) -> int:
        """„Kassette gewechselt" — alles Wartende noch einmal versuchen."""
        anzahl = self.spool.release()
        if anzahl:
            log.info("%s wartende Drucke werden erneut versucht", anzahl)
            self._wieder_einreihen()
        return anzahl

    def drop_session(self, session_uid: str) -> int:
        """Gelöschter Durchgang: nichts davon geht noch in den Drucker.

        Like a discard: the rows go through ``on_dropped``, so the allowance of
        a sheet that never came out is given back (once, by the spool marker).
        """
        rows = self.spool.drop_session(session_uid)
        self._forget_rows(rows)
        return len(rows)

    #: Called with each spool row thrown away by ``discard_waiting`` or
    #: ``drop_stale``: the booth gives the guest's allowance back.
    on_dropped: Optional[Callable[[dict], None]] = None
    #: Seconds a print accepted while the printer had no paper may wait;
    #: 0 = no limit. Set by the booth from ``print.queue_max_hours``.
    deferred_max_age: Callable[[], float] = staticmethod(lambda: 0.0)

    def _forget_rows(self, rows: List[dict]) -> None:
        ids = {int(r["id"]) for r in rows}
        with self._lock:
            self.failed = [j for j in self.failed if int(j.spool_id or 0) not in ids]
            self._unterwegs -= ids
        for r in rows:
            if self.on_dropped:
                try:
                    self.on_dropped(r)
                except Exception as e:               # noqa: BLE001 — the allowance costs no drop
                    log.warning("Kontingent nach Verwerfen nicht zurückgegeben: %s", e)

    def discard_waiting(self) -> int:
        """The operator (PIN) throws away every print that waits — queued or held.

        A job sitting in a printer's queue is skipped there (its row is
        "lost", see Station._run). A job a Windows printer is already
        following is taken back out of the Windows queue (``_cancel_on_printer``);
        other backends have handed it over and it is not stopped.
        """
        rows = self.spool.drop_waiting()
        self._forget_rows(rows)
        self._cancel_on_printer({int(r["id"]) for r in rows})
        if rows:
            log.info("%d wartende Drucke vom Betreiber verworfen", len(rows))
        return len(rows)

    def discard_one(self, spool_id: int) -> bool:
        """The operator throws away one print. False: it was not open (printed or gone).

        Like ``discard_waiting`` for a single row: the guest's allowance comes
        back, a printer that has the job skips it, and a Windows printer that
        is already following it takes its own job out of the Windows queue.
        """
        row = self.spool.drop_one(int(spool_id))
        if row is None:
            return False
        self._forget_rows([row])
        self._cancel_on_printer({int(row["id"])})
        log.info("Druck %s vom Betreiber verworfen (%s)", row["id"], Path(row["path"]).name)
        return True

    def _cancel_on_printer(self, ids: set) -> int:
        """Ask each printer working on one of ``ids`` to take it back.

        Only backends that can name their own job do anything (Windows: the
        job id StartDoc returned). Folder, hotfolder, CUPS and IPP have handed
        the file over by the time a row says "printing"; there is nothing of
        ours left to delete, and deleting someone else's job is not on offer.
        """
        n = 0
        for st in self.stations:
            if st.current and st.current in ids:
                cancel = getattr(st.backend, "cancel_current", None)
                if cancel:
                    cancel()
                    n += 1
        return n

    def queue_view(self, limit: int = 50) -> List[dict]:
        """Every print that is not out yet, oldest first, for the operator.

        ``state``: queued (waits for a printer), printing (a printer has it),
        held (waits for the operator: paper, ribbon, cable). ``reason`` is the
        last error, if there was one.
        """
        out = []
        for r in self.spool.open_list(limit):
            out.append({"id": int(r["id"]), "run": r.get("session_uid") or "", "created": float(r["created"]),
                        "copies": int(r.get("copies") or 1), "state": r["state"],
                        "reason": str(r.get("last_error") or "")[:200],
                        "slot": (int(r["station"]) + 1) if r.get("station") is not None else None,
                        "deferred": bool(r.get("deferred"))})
        return out

    def drop_stale(self) -> int:
        """Prints accepted while empty that waited longer than allowed: dropped, logged."""
        try:
            limit = float(self.deferred_max_age() or 0)
        except Exception:                            # noqa: BLE001
            limit = 0.0
        if limit <= 0:
            return 0
        rows = self.spool.drop_waiting(older_than=time.time() - limit, deferred_only=True)
        for r in rows:
            log.warning("Vorgemerkter Druck nach %.1f h ohne Papier verworfen: %s (Durchgang %s)",
                        (time.time() - float(r["created"])) / 3600, Path(r["path"]).name, r["session_uid"])
        self._forget_rows(rows)
        return len(rows)

    #: How often held prints are tried again by themselves while a printer
    #: reports ready. The person who refills the tray at a wedding has no PIN
    #: and presses nothing; without this the guests' cards waited until the
    #: operator came back. Long enough not to hammer a printer that still has
    #: no paper (each release costs at most ``max_attempts`` tries).
    HELD_RETRY_SECONDS = 120.0

    def _retry_held(self, station: Station) -> None:
        if station.state() in UNAVAILABLE or station.pending or self.failed:
            return
        try:
            gehalten = int(self.spool.counts().get("held") or 0)
        except Exception as e:                       # noqa: BLE001 — a status poll must not die
            log.warning("Wartende Drucke nicht lesbar: %s", e)
            return
        jetzt = time.monotonic()
        with self._lock:
            if not gehalten:
                self._held_seit = None
                return
            # The interval starts when a held print is first seen, so a fresh
            # hold is not retried within the same few seconds.
            if getattr(self, "_held_seit", None) is None:
                self._held_seit = jetzt
                return
            if jetzt - self._held_seit < float(self.HELD_RETRY_SECONDS):
                return
            self._held_seit = jetzt
        log.info("%s wartende Drucke werden von selbst erneut versucht (Drucker meldet bereit)", gehalten)
        self.release_held()

    def _retry_failed(self, station: Station) -> None:
        if not self.failed or station.state() in UNAVAILABLE or station.pending:
            return
        with self._lock:
            jobs, self.failed = self.failed, []
        for job in jobs:
            job.tried = []
            log.info("Erneuter Druckversuch: %s", job.path.name)
            self._note(job, "queued", station)
            station.q.put(job)

    def status(self) -> dict:
        if not self.stations:
            return {"state": "none", "backend": "none", "printed": self.printed, "pending": 0,
                    "last_error": None, "stations": [], "takeover": None}
        stations = [s.status() for s in self.stations]
        order = ["printing", "ready", "unknown", "offline", "error"]
        states = [st.get("state", "unknown") for st in stations]
        best = min(states, key=lambda x: order.index(x) if x in order else 2)
        st = dict(stations[0])
        zettel = self.spool.counts()
        wartend = self.spool.held()
        # One printer down, another still up: the booth prints on, and the
        # operator should know. The best state alone would say "ready".
        down = [x["slot"] for x in stations if x.get("state") in UNAVAILABLE]
        up = [x["slot"] for x in stations if x.get("state") not in UNAVAILABLE]
        st["takeover"] = {"down": down, "up": up} if down and up else None
        st.update({"state": best, "printed": self.printed, "waiting_retry": len(self.failed), "pending": self.pending, "last_error": self.last_error,
                   "backend": self.stations[0].backend.name, "routing": self.routing, "stations": stations,
                   # Was auf den Betreiber wartet — Papier, Farbband, Kabel. Der
                   # Wert gehört in den Heartbeat: Aus der Ferne ist das die
                   # einzige Stelle, an der ein leeres Farbband sichtbar wird.
                   "held": zettel.get("held", 0),
                   "held_reason": (wartend[-1].get("last_error") or "") if wartend else "",
                   # Accepted while the paper was out (queue_while_empty), not out yet.
                   "deferred": self.spool.deferred_open(),
                   "spool": zettel})
        if len(stations) > 1:
            st["name"] = " + ".join(str(x.get("name") or x.get("backend")) for x in stations)
        return st
