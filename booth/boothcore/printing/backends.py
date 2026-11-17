"""Drucker-Backends.

* ``windows`` — GDI über pywin32. Zeichnet randlos auf die physische Seite
  (Offset des nicht bedruckbaren Rands wird herausgerechnet). Randlos muss im
  Treiber des SELPHY CP1300 aktiviert sein.
* ``cups``   — Linux/macOS über ``lp``. CP1300 per USB mit Gutenprint
  (~1 mm Rand, bekannter Bug) oder per WLAN driverless IPP.
* ``ipp``    — direkt ans Netz: AirPrint-, IPP-Everywhere- und die meisten WLAN/LAN-Fotodrucker,
  ohne Treiber (``ipp://192.168.1.40/ipp/print``). JPEG wird unverändert gesendet.
* ``hotfolder`` — Datei in einen überwachten Ordner legen, z. B. für DNP Hot Folder Print,
  Mitsubishi/HiTi-Tools oder einen Print-Server.
* ``folder`` — schreibt Druckdateien in einen Ordner. Für Tests und Boxen ohne Drucker.

DNP (DS620, DS-RX1, QW410), Mitsubishi, HiTi & Co. laufen unter Windows über ihren
normalen Treiber mit ``windows``; ein im Windows installierter Netzwerk-/AirPrint-Drucker ebenso.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import List

from PIL import Image


class PrintCancelled(RuntimeError):
    """The operator threw the job away while a printer had it.

    Not a printer fault: the job is neither retried, nor held, nor counted as
    a failed attempt.
    """


#: Die Gründe, die ein Drucker melden kann — einmal auf Deutsch.
_KLARTEXT = {"media-empty": "Papier leer", "media-needed": "Papier nachlegen",
             "media-jam": "Papierstau", "toner-empty": "Farbband leer",
             "marker-supply-empty": "Farbband leer", "marker-supply-low": "Farbband fast leer",
             "cover-open": "Klappe offen", "door-open": "Klappe offen",
             "offline-report": "Drucker offline", "paused": "angehalten",
             "job-error": "Der Drucker nimmt den Auftrag nicht an – eingeschaltet, USB-Kabel steckt?",
             "user-intervention": "Der Drucker braucht jemanden – bitte auf seine Anzeige schauen",
             "printer-error": "Der Drucker meldet einen Fehler – bitte auf seine Anzeige schauen",
             "media-needed": "Papier nachlegen"}


# ── Windows spooler bits (winspool.h) ─────────────────────────────────
# PRINTER_INFO_2.Status
_PS_PAUSED, _PS_ERROR, _PS_PAPER_JAM, _PS_PAPER_OUT = 0x1, 0x2, 0x8, 0x10
_PS_PAPER_PROBLEM, _PS_OFFLINE, _PS_PRINTING, _PS_NOT_AVAILABLE = 0x40, 0x80, 0x400, 0x1000
_PS_TONER_LOW, _PS_NO_TONER, _PS_USER_INTERVENTION = 0x20000, 0x40000, 0x100000
_PS_DOOR_OPEN, _PS_SERVER_UNKNOWN = 0x400000, 0x800000
# PRINTER_INFO_2.Attributes
_PA_WORK_OFFLINE = 0x400
# JOB_INFO_1.Status
_JS_ERROR, _JS_DELETING, _JS_SPOOLING, _JS_PRINTING, _JS_OFFLINE = 0x2, 0x4, 0x8, 0x10, 0x20
_JS_PAPEROUT, _JS_PRINTED, _JS_DELETED, _JS_BLOCKED_DEVQ = 0x40, 0x80, 0x100, 0x200
_JS_USER_INTERVENTION, _JS_COMPLETE = 0x400, 0x1000
#: A job with one of these will not reach paper without someone acting.
_JS_STUCK = _JS_ERROR | _JS_OFFLINE | _JS_PAPEROUT | _JS_BLOCKED_DEVQ | _JS_USER_INTERVENTION


def windows_printer_state(status: int, attributes: int = 0, job_statuses=()) -> dict:
    """What the spooler's bits say about the printer: ``state`` and ``reasons``.

    Pure, so the table can be tested anywhere. ``job_statuses`` are the
    ``Status`` bits of the jobs in this printer's queue. They matter because a
    USB printer that is switched off or unplugged often shows nothing on the
    printer itself (status 0) — the first visible sign is a job the spooler
    cannot write to the port ("Fehler – Drucken"). Before 14.11.2026 such a
    queue read as "printing", i.e. available.

    offline: Windows marks it offline, or a job cannot reach the device.
    error:   the device is there but needs someone (paper, ribbon, jam, door,
             paused queue). Both are unavailable to the booth.
    """
    status, attributes = int(status or 0), int(attributes or 0)
    jobs = [int(j or 0) for j in job_statuses]
    reasons = []

    def add(reason):
        if reason not in reasons:
            reasons.append(reason)

    offline = bool(attributes & _PA_WORK_OFFLINE
                   or status & (_PS_OFFLINE | _PS_NOT_AVAILABLE | _PS_SERVER_UNKNOWN))
    if offline:
        add("offline-report")
    error = False
    for mask, reason in ((_PS_PAPER_OUT, "media-empty"), (_PS_PAPER_JAM, "media-jam"),
                         (_PS_PAPER_PROBLEM, "media-needed"), (_PS_NO_TONER, "marker-supply-empty"),
                         (_PS_DOOR_OPEN, "door-open"), (_PS_PAUSED, "paused"),
                         (_PS_USER_INTERVENTION, "user-intervention")):
        if status & mask:
            add(reason)
            error = True
    if status & _PS_ERROR:
        error = True
        if not reasons:
            add("printer-error")
    for job in jobs:
        if job & _JS_PAPEROUT:
            add("media-empty")
            error = True
        elif job & _JS_USER_INTERVENTION:
            add("user-intervention")
            error = True
        elif job & _JS_OFFLINE:
            add("offline-report")
            offline = True
        elif job & (_JS_ERROR | _JS_BLOCKED_DEVQ):
            # The spooler could not write to the port. With a SELPHY on USB
            # that is: switched off, cable out — "nicht erreichbar" is the
            # honest word, not "Papier oder Farbband".
            if not reasons:
                add("job-error")
            offline = True
    if status & _PS_TONER_LOW:
        add("marker-supply-low")                     # a hint, not a stop
    if offline:
        state = "offline"
    elif error:
        state = "error"
    elif status & _PS_PRINTING or jobs:
        state = "printing"
    else:
        state = "ready"
    return {"state": state, "reasons": reasons}


def windows_job_outcome(job_status) -> str:
    """Where one of our jobs stands: ``done``, ``waiting`` or ``failed``.

    ``None`` = the job is no longer in the queue: the spooler handed it to the
    device and removed it, which is what it does after a successful print.
    (If someone deletes it by hand between two looks, it reads as done too —
    the booth cannot tell those apart.) A job the spooler keeps after printing
    carries PRINTED or COMPLETE. A job someone is deleting failed.
    """
    if job_status is None:
        return "done"
    job_status = int(job_status)
    if job_status & (_JS_DELETING | _JS_DELETED):
        return "failed"
    if job_status & _JS_STUCK:
        return "failed"
    if job_status & (_JS_PRINTED | _JS_COMPLETE):
        return "done"
    return "waiting"


def plain_jpeg(path: Path) -> Path:
    """The file as a baseline JPEG; the file itself when it already is one.

    CUPS and IPP hand a JPEG to a photo printer unchanged. A SELPHY CP1300
    answered a progressive one with "Daten nicht lesbar" (14.11.2026) and the
    queue called the job done. The original is never touched: the plain copy
    lands next to it.
    """
    path = Path(path)
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.format != "JPEG" or not (im.info.get("progressive") or im.info.get("progression")):
                return path
            plain = path.with_name(path.stem + ".druck.jpg")
            im.convert("RGB").save(plain, "JPEG", quality=95, subsampling=0, optimize=False)
            return plain
    except Exception:                                   # noqa: BLE001 — then the printer gets what there is
        return path


class PrinterBackend:
    name = "base"

    def list_printers(self) -> List[str]:
        return []

    def papier(self) -> str:
        """Was das System über das eingelegte Papier sagt. Leer = es sagt nichts.

        Roh, so wie der Treiber es nennt („4x6.Fullbleed", „Postcard (4x6in)").
        Die Übersetzung in unsere Medien macht ``printing.models``.
        """
        return ""

    def status(self) -> dict:
        return {"state": "unknown"}

    def print_file(self, path: Path, copies: int = 1) -> None:  # pragma: no cover
        raise NotImplementedError


class FolderPrinter(PrinterBackend):
    name = "folder"

    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict:
        return {"state": "ready", "name": f"Ordner {self.folder}"}

    def print_file(self, path: Path, copies: int = 1) -> None:
        # Done means "the file is in the folder". Whatever reads the folder
        # (a print server, nobody in tests) is out of sight: there is no
        # evidence of paper to wait for, so the booth counts the sheet here.
        for i in range(max(1, copies)):
            shutil.copy(path, self.folder / f"{int(time.time()*1000)}_{i}_{path.name}")


class HotfolderPrinter(FolderPrinter):
    name = "hotfolder"

    def status(self) -> dict:
        ok = self.folder.is_dir() and os.access(self.folder, os.W_OK)
        return {"state": "ready" if ok else "offline", "name": f"Hotfolder {self.folder.name}"}

    def print_file(self, path: Path, copies: int = 1) -> None:
        # As with the folder: the hot-folder software gives no answer back,
        # so "printed" is "handed over". The booth cannot know more.
        for i in range(max(1, copies)):
            tmp = self.folder / f".{int(time.time()*1000)}_{i}_{path.name}.part"
            shutil.copy(path, tmp)
            os.replace(tmp, self.folder / tmp.name[1:-5])     # erst fertig kopiert sichtbar


class IppPrinter(PrinterBackend):
    """Minimaler IPP-Client (RFC 8010/8011): Print-Job und Get-Printer-Attributes."""

    name = "ipp"
    STATES = {3: "ready", 4: "printing", 5: "error"}

    def __init__(self, uri: str, media: str = "", timeout: float = 20.0):
        if not uri:
            raise ValueError("IPP-Adresse fehlt")
        self.uri = uri if "://" in uri else f"ipp://{uri}/ipp/print"
        scheme, rest = self.uri.split("://", 1)
        host, _, path = rest.partition("/")
        if ":" not in host:
            host += ":631" if scheme == "ipp" else ":443"
        self.http_url = f"{'https' if scheme == 'ipps' else 'http'}://{host}/{path}"
        self.media = media
        self.timeout = timeout
        self._req = 0

    @staticmethod
    def _attr(tag: int, name: str, value) -> bytes:
        if isinstance(value, int):
            v = value.to_bytes(4, "big", signed=True)
        else:
            v = str(value).encode("utf-8")
        n = name.encode()
        return bytes([tag]) + len(n).to_bytes(2, "big") + n + len(v).to_bytes(2, "big") + v

    def _request(self, op: int, attrs: bytes, job_attrs: bytes = b"", data: bytes = b"") -> bytes:
        import httpx

        self._req += 1
        head = (b"\x02\x00" + op.to_bytes(2, "big") + self._req.to_bytes(4, "big") + b"\x01"
                + self._attr(0x47, "attributes-charset", "utf-8")
                + self._attr(0x48, "attributes-natural-language", "de")
                + self._attr(0x45, "printer-uri", self.uri) + attrs)
        if job_attrs:
            head += b"\x02" + job_attrs
        body = head + b"\x03" + data
        r = httpx.post(self.http_url, content=body, headers={"Content-Type": "application/ipp"},
                       timeout=httpx.Timeout(self.timeout if data else 4.0, connect=3.0), verify=False)
        r.raise_for_status()
        if len(r.content) < 8:
            raise RuntimeError("IPP: leere Antwort")
        status = int.from_bytes(r.content[2:4], "big")
        if status >= 0x0400:
            raise RuntimeError(f"IPP-Fehler 0x{status:04X}")
        return r.content

    @staticmethod
    def _find_int(resp: bytes, name: str):
        key = name.encode()
        i = resp.find(len(key).to_bytes(2, "big") + key)
        if i < 0:
            return None
        j = i + 2 + len(key)
        ln = int.from_bytes(resp[j:j + 2], "big")
        return int.from_bytes(resp[j + 2:j + 2 + ln], "big") if ln == 4 else None

    #: Gründe, bei denen kein Blatt mehr kommt — egal, was „printer-state" sagt.
    SCHLIMM = ("media-empty", "media-jam", "marker-supply-empty", "toner-empty", "cover-open", "door-open")

    def status(self) -> dict:
        try:
            # One attribute with four values: a further value carries an
            # empty name (RFC 8010). Four attributes of the same name got
            # only one of them answered.
            resp = self._request(0x000B, self._attr(0x44, "requested-attributes", "printer-state")
                                 + self._attr(0x44, "", "printer-state-reasons")
                                 + self._attr(0x44, "", "marker-levels")
                                 + self._attr(0x44, "", "marker-names"))
        except Exception as e:
            return {"state": "offline", "name": self.uri, "detail": str(e)[:200]}
        aus = {"state": self.STATES.get(self._find_int(resp, "printer-state"), "unknown"), "name": self.uri}
        gruende = self._find_texts(resp, "printer-state-reasons")
        # „none" ist die IPP-Art, nichts zu melden — das ist kein Grund.
        gruende = [g for g in gruende if g and g != "none"]
        if gruende:
            aus["reasons"] = gruende
            aus["hinweis"] = ", ".join(gruende)
            if any(g.split("-report")[0].split("-warning")[0] in self.SCHLIMM for g in gruende):
                aus["state"] = "error"
            if any(g.startswith("offline") for g in gruende):
                aus["state"] = "offline"             # the device itself is gone
        stand = self._find_int(resp, "marker-levels")
        if stand is not None and 0 <= stand <= 100:
            # Prozent, nicht Blätter — genau so wird es auch angezeigt.
            aus["supply_percent"] = stand
        return aus

    def papier(self) -> str:
        """Das voreingestellte Medium (``media-default``), wenn der Drucker es nennt."""
        try:
            resp = self._request(0x000B, self._attr(0x44, "requested-attributes", "media-default"))
        except Exception:
            return ""
        werte = self._find_texts(resp, "media-default")
        return werte[0] if werte else ""

    @staticmethod
    def _find_texts(resp: bytes, name: str) -> List[str]:
        """Alle Textwerte eines Attributs (IPP wiederholt sie mit leerem Namen)."""
        key = name.encode()
        i = resp.find(len(key).to_bytes(2, "big") + key)
        if i < 0:
            return []
        j = i + 2 + len(key)
        aus: List[str] = []
        while j + 2 <= len(resp):
            ln = int.from_bytes(resp[j:j + 2], "big")
            wert = resp[j + 2:j + 2 + ln]
            try:
                aus.append(wert.decode("utf-8"))
            except UnicodeDecodeError:
                break
            j += 2 + ln
            # Weiterer Wert desselben Attributs? Dann folgt Tag + Namenslänge 0.
            if j + 3 > len(resp) or resp[j + 1:j + 3] != b"\x00\x00":
                break
            j += 3
        return aus

    def print_file(self, path: Path, copies: int = 1) -> None:
        # Returns when the printer accepted the job, not when the sheet is
        # out: the job state (Get-Job-Attributes) is not followed yet, so an
        # IPP print counts as printed on acceptance. Known gap, see
        # business_logic/index.md.
        path = plain_jpeg(Path(path))
        data = path.read_bytes()
        job = self._attr(0x21, "copies", max(1, int(copies))) + self._attr(0x44, "print-scaling", "fill")
        if self.media:
            job += self._attr(0x44, "media", self.media)
        self._request(0x0002, self._attr(0x42, "requesting-user-name", "photobox")
                      + self._attr(0x42, "job-name", path.name)
                      + self._attr(0x49, "document-format", "image/jpeg"), job, data)


class CupsPrinter(PrinterBackend):
    name = "cups"

    def __init__(self, printer: str = "", media: str = "postcard"):
        self.printer = printer
        self.media = {"postcard": "Postcard.Fullbleed", "4x6": "4x6.Fullbleed"}.get(media, media)

    def list_printers(self) -> List[str]:
        try:
            out = subprocess.run(["lpstat", "-a"], capture_output=True, text=True, timeout=5).stdout
            return [line.split()[0] for line in out.splitlines() if line.strip()]
        except Exception:
            return []

    def papier(self) -> str:
        """Das voreingestellte Medium dieser Warteschlange (``lpoptions``)."""
        try:
            args = ["lpoptions"] + (["-p", self.printer] if self.printer else [])
            out = subprocess.run(args, capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return ""
        for teil in out.split():
            for schluessel in ("media=", "PageSize="):
                if teil.lower().startswith(schluessel.lower()):
                    return teil.split("=", 1)[1]
        return ""

    #: Was CUPS an Gründen nennt und was das für den Betreiber heißt.
    GRUENDE = {"media-empty": "Papier leer", "media-needed": "Papier nachlegen",
               "media-jam": "Papierstau", "toner-empty": "Farbband leer",
               "marker-supply-empty": "Farbband leer", "marker-supply-low": "Farbband fast leer",
               "cover-open": "Klappe offen", "door-open": "Klappe offen",
               "input-tray-missing": "Papierkassette fehlt",
               "offline-report": "Drucker offline", "paused": "angehalten"}

    def status(self) -> dict:
        try:
            args = ["lpstat", "-p"] + ([self.printer] if self.printer else [])
            out = subprocess.run(args, capture_output=True, text=True, timeout=5).stdout.lower()
        except Exception as e:
            return {"state": "error", "detail": str(e)}
        zustand = "unknown"
        # lpstat answers in the language of the system, on macOS also with
        # LC_ALL=C — so the German words are known here too.
        if "disabled" in out or "deaktiviert" in out:
            zustand = "error"
        elif "idle" in out or "leerlauf" in out or "inaktiv" in out:
            zustand = "ready"
        elif "printing" in out or "druckt" in out:
            zustand = "printing"
        aus = {"state": zustand, "name": self.printer}
        if zustand == "unknown":
            aus["detail"] = out.strip()[:200]
        aus.update(self._vorrat())
        # Ein gemeldeter Grund wiegt schwerer als „idle": Ein Drucker ohne
        # Papier ist nicht bereit, auch wenn er das behauptet.
        if aus.get("reasons") and any(r.startswith(self.SCHLIMM) for r in aus["reasons"]):
            aus["state"] = "error"
            aus["detail"] = aus.get("hinweis") or aus.get("detail") or ""
        # The USB backend says "offline-report" when the device is switched
        # off or unplugged; the queue itself still says "idle".
        if aus.get("reasons") and any(r.startswith("offline") for r in aus["reasons"]):
            aus["state"] = "offline"
            aus["detail"] = aus.get("hinweis") or aus.get("detail") or ""
        return aus

    #: Reasons after which no sheet comes, whatever "idle" says.
    SCHLIMM = ("media-empty", "media-needed", "media-jam", "marker-supply-empty", "toner-empty",
               "cover-open", "door-open", "input-tray-missing")

    def _vorrat(self) -> dict:
        """Reasons the print service knows, in words for the operator.

        Asked from the local CUPS daemon over IPP: its keywords are the same
        in every language. ``lpstat`` translates its text — on a German Mac
        the reasons were simply not found. The text is only the fallback.
        """
        gruende: List[str] = []
        if self.printer:
            try:
                from urllib.parse import quote
                ipp = IppPrinter(f"ipp://localhost:631/printers/{quote(self.printer)}", timeout=4.0)
                gruende = [g for g in (ipp.status().get("reasons") or []) if g and g != "none"]
            except Exception:                         # noqa: BLE001
                gruende = []
        if not gruende:
            try:
                args = ["lpstat", "-l", "-p"] + ([self.printer] if self.printer else [])
                roh = subprocess.run(args, capture_output=True, text=True, timeout=5).stdout
            except Exception:
                return {}
            gruende = [g for g in self.GRUENDE if g in roh]
        if not gruende:
            return {}
        worte = []
        for g in gruende:
            wort = next((text for key, text in self.GRUENDE.items() if g.startswith(key)), "")
            if not wort and g.startswith("other"):
                wort = "Der Drucker meldet einen Fehler – bitte auf seine Anzeige schauen"
            if (wort or g) not in worte:
                worte.append(wort or g)
        return {"reasons": gruende, "hinweis": ", ".join(worte)}

    def print_file(self, path: Path, copies: int = 1) -> None:
        # Returns when CUPS took the job (``lp`` exits), not when the sheet is
        # out: the job is not followed yet, so a CUPS print counts as printed
        # on acceptance. Known gap, see business_logic/index.md.
        path = plain_jpeg(Path(path))
        cmd = ["lp", "-n", str(max(1, copies)), "-o", f"media={self.media}", "-o", "fit-to-page"]
        if self.printer:
            cmd += ["-d", self.printer]
        subprocess.run(cmd + [str(path)], check=True, capture_output=True, timeout=30)


class WindowsPrinter(PrinterBackend):
    name = "windows"
    # GetDeviceCaps-Indizes
    HORZRES, VERTRES = 8, 10
    PHYSICALWIDTH, PHYSICALHEIGHT = 110, 111
    PHYSICALOFFSETX, PHYSICALOFFSETY = 112, 113

    def __init__(self, printer: str = "", follow_jobs: bool = True):
        import win32print  # noqa: F401  (Fehler früh sichtbar machen)

        self.printer = printer
        #: Operator switch (setting ``follow_jobs``): False = do not follow the queue.
        self.follow_jobs = bool(follow_jobs)
        #: Set when the operator throws away the job this printer is on. The
        #: only thing it ever deletes from the Windows queue is that job, by
        #: the id StartDoc gave us (``_zuruecknehmen``) — never anything else
        #: in a queue that other programs may share.
        self._cancel = threading.Event()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    def cancel_current(self) -> None:
        self._cancel.set()

    def _name(self) -> str:
        import win32print

        return self.printer or win32print.GetDefaultPrinter()

    def list_printers(self) -> List[str]:
        import win32print

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [p[2] for p in win32print.EnumPrinters(flags)]

    def papier(self) -> str:
        """Die eingestellte Papiergröße aus dem DEVMODE des Druckers.

        Windows liefert eine Nummer und, bei Fotodruckern meist, einen eigenen
        Namen (``dmFormName``). Der Name ist brauchbarer — Hersteller schreiben
        dort „4x6in" oder „Postcard" hinein.
        """
        import win32print

        try:
            h = win32print.OpenPrinter(self._name())
            try:
                dm = win32print.GetPrinter(h, 2).get("pDevMode")
            finally:
                win32print.ClosePrinter(h)
        except Exception:
            return ""
        if not dm:
            return ""
        return str(getattr(dm, "FormName", "") or "").strip()

    def status(self) -> dict:
        import win32print

        try:
            h = win32print.OpenPrinter(self._name())
            try:
                info = win32print.GetPrinter(h, 2)
                jobs = self._jobs(win32print, h)
            finally:
                win32print.ClosePrinter(h)
        except Exception as e:
            return {"state": "error", "detail": str(e)}
        lesbar = jobs is not None
        jobs = jobs or []
        st = int(info.get("Status", 0))
        attrs = int(info.get("Attributes", 0))
        anzahl = max(int(info.get("cJobs", 0)), len(jobs))
        bild = windows_printer_state(st, attrs, [int(j.get("Status", 0) or 0) for j in jobs])
        aus = {"state": bild["state"], "name": self._name(), "jobs": anzahl, "raw_status": st,
               # False: EnumJobs failed — the empty job list below says nothing.
               "queue_readable": lesbar,
               # Raw bits for the check on the booth PC: what does Windows
               # really set when the SELPHY is switched off or unplugged?
               "raw_attributes": attrs, "raw_job_status": [int(j.get("Status", 0) or 0) for j in jobs]}
        if bild["reasons"]:
            aus["reasons"] = bild["reasons"]
            aus["hinweis"] = ", ".join(_KLARTEXT.get(g, g) for g in bild["reasons"])
            if bild["state"] in ("offline", "error"):
                aus["detail"] = aus["hinweis"]
        return aus

    @staticmethod
    def _jobs(win32print, h):
        """The queue as JOB_INFO_1 dicts; None when Windows will not say."""
        try:
            return list(win32print.EnumJobs(h, 0, 256, 1) or [])
        except Exception:                                    # noqa: BLE001
            return None

    #: How often to look at the queue while our job is in it.
    JOB_POLL = 1.0
    #: How long a job may show an error before the booth takes it back. The
    #: spooler retries a port write by itself; a few seconds of "error" while
    #: the SELPHY wakes up must not cost the sheet. To be confirmed at the PC.
    ERROR_PATIENCE = 15.0
    #: Longest a single sheet may sit in the queue without an error. A SELPHY
    #: needs about a minute per card; a job still there after four is stuck.
    JOB_DEADLINE = 240.0

    def print_file(self, path: Path, copies: int = 1) -> None:
        """Draw each copy, hand it to the spooler, and return only when it left.

        Until 14.11.2026 this returned as soon as the spooler had the job, and
        the booth counted a sheet that was never printed when the SELPHY was
        switched off. Now each copy is followed in the queue: gone (or marked
        printed) = done; an error that lasts, or the deadline = the job is
        deleted from the Windows queue and this raises. Deleting matters: the
        booth keeps its own copy and retries it, so a spooler copy left behind
        would come out a second time when the printer returns.

        With ``follow_jobs`` off (operator switch, in case Windows behaves
        unexpectedly) this is the old behaviour: counted once the spooler has the job.
        """
        path = Path(path)
        for _ in range(max(1, copies)):
            if self._cancel.is_set():
                raise PrintCancelled("Druck vom Betreiber verworfen")
            # Unique per copy, so the job is found again in the queue.
            dokument = f"{path.name} #{time.time_ns() % 10**12:x}"
            job_id = self._spoolen(path, dokument)
            if self.follow_jobs:
                self._warte_auf_auftrag(dokument, job_id)

    def _spoolen(self, path: Path, dokument: str):
        """One copy through GDI into the spooler (StartDoc … EndDoc); the job id or None.

        Plain Win32 GDI through win32gui and win32print, not win32ui: win32ui
        is pywin32's MFC wrapper and needs mfc140u.dll from the Visual C++
        runtime. On the booth PC (15.11.2026) ``import win32ui`` failed with
        "DLL load failed" until that runtime was installed by hand; win32gui
        and win32print need nothing beyond Windows itself. The geometry is the
        same as before: borderless, scaled to the physical page, offset off.
        """
        import win32gui
        import win32print
        from PIL import ImageWin

        with Image.open(path) as im:
            img = im.convert("RGB")
        hdc = win32gui.CreateDC("WINSPOOL", self._name(), None)
        job_id = None
        try:
            def caps(index):
                return int(win32print.GetDeviceCaps(hdc, index))

            phys_w, phys_h = caps(self.PHYSICALWIDTH), caps(self.PHYSICALHEIGHT)
            off_x, off_y = caps(self.PHYSICALOFFSETX), caps(self.PHYSICALOFFSETY)
            page = img
            if (page.width > page.height) != (phys_w > phys_h):
                page = page.rotate(90, expand=True)
            # Randlos: auf die physische Seite skalieren (cover), Offset abziehen
            scale = max(phys_w / page.width, phys_h / page.height)
            w, h = int(page.width * scale), int(page.height * scale)
            x = (phys_w - w) // 2 - off_x
            y = (phys_h - h) // 2 - off_y
            # GDI's StartDoc answers with the spooler's job id. Kept, so the
            # job is followed by its id and not only by its name.
            job_id = win32print.StartDoc(hdc, (dokument, None, None, 0))
            try:
                win32print.StartPage(hdc)
                ImageWin.Dib(page).draw(int(hdc), (x, y, x + w, y + h))
                win32print.EndPage(hdc)
                win32print.EndDoc(hdc)
            except Exception:
                # A half-drawn job must not stay in the queue: the booth retries
                # with its own copy, and the half one would come out as well.
                try:
                    win32print.AbortDoc(hdc)
                except Exception:                            # noqa: BLE001
                    pass
                raise
        finally:
            win32gui.DeleteDC(hdc)
        try:
            job_id = int(job_id or 0)
        except (TypeError, ValueError):
            job_id = 0
        return job_id if job_id > 0 else None

    #: How long the queue may stay unreadable in a row before the booth stops
    #: following one job. Seen on the booth PC (15.11.2026): EnumJobs failed on
    #: every call (win32timezone missing in the build); the booth looked again
    #: for the full 240 s, then failed without deleting its job (it never saw
    #: its id), retried and spooled the same picture again — duplicates once
    #: the printer was back. A job the booth cannot prove failed is never retried.
    QUEUE_UNREADABLE_SECONDS = 5.0

    def _warte_auf_auftrag(self, dokument: str, job_id=None) -> None:
        import win32print

        frist = time.monotonic() + float(self.JOB_DEADLINE)
        fehler_seit = None
        unlesbar_seit = None
        while True:
            h = None
            try:
                h = win32print.OpenPrinter(self._name())
                info = win32print.GetPrinter(h, 2)
                jobs = list(win32print.EnumJobs(h, 0, 256, 1) or [])
            except Exception as e:                           # noqa: BLE001
                # An unreadable queue is no evidence either way: our job may
                # still be in it. Look again for a few seconds; then the job
                # counts as handed over (as before following existed).
                if h is not None:
                    try:
                        win32print.ClosePrinter(h)
                    except Exception:                        # noqa: BLE001
                        pass
                jetzt = time.monotonic()
                unlesbar_seit = jetzt if unlesbar_seit is None else unlesbar_seit
                if jetzt - unlesbar_seit >= float(self.QUEUE_UNREADABLE_SECONDS):
                    self._nicht_verfolgbar(dokument, e)
                    return
                time.sleep(float(self.JOB_POLL))
                continue
            unlesbar_seit = None
            try:
                if job_id:
                    unserer = next((j for j in jobs if int(j.get("JobId") or 0) == int(job_id)), None)
                else:
                    unserer = next((j for j in jobs if j.get("pDocument") == dokument), None)
                    if unserer is not None and unserer.get("JobId"):
                        job_id = int(unserer.get("JobId"))      # found by name once, by id from now on
                job_status = None if unserer is None else int(unserer.get("Status", 0) or 0)
                ergebnis = windows_job_outcome(job_status)
                if ergebnis == "done":
                    return
                if self._cancel.is_set() and unserer is not None and unserer.get("JobId"):
                    # Thrown away by the operator: take our own job back out
                    # of the Windows queue, the same way a failed one is.
                    if not self._zuruecknehmen(win32print, h, int(unserer.get("JobId")), dokument):
                        return              # could not be deleted: it may still print (logged there)
                    raise PrintCancelled("Druck vom Betreiber verworfen – Auftrag aus der Windows-Warteschlange genommen")
                bild = windows_printer_state(int(info.get("Status", 0)), int(info.get("Attributes", 0)),
                                             [job_status])
                jetzt = time.monotonic()
                if ergebnis == "failed" or bild["state"] in ("offline", "error"):
                    fehler_seit = jetzt if fehler_seit is None else fehler_seit
                else:
                    fehler_seit = None
                grund = ""
                if fehler_seit is not None and jetzt - fehler_seit >= float(self.ERROR_PATIENCE):
                    grund = ", ".join(_KLARTEXT.get(g, g) for g in bild["reasons"]) or "Druckauftrag gescheitert"
                elif jetzt >= frist:
                    grund = f"Druckauftrag nach {int(self.JOB_DEADLINE)} s nicht fertig"
                if grund and job_status is not None and job_status & (_JS_DELETING | _JS_DELETED):
                    # Windows is deleting it already (0x2016 on the booth PC: it
                    # stays in the queue while the port is stuck). It will not
                    # print; a delete command would only be answered "ok" again.
                    # The printer reads offline while it sits there, so the
                    # booth's own copy waits instead of being spooled behind it.
                    raise RuntimeError(f"Drucker: {grund} – Auftrag wird von Windows gelöscht")
                if grund:
                    if not self._zuruecknehmen(win32print, h, int(unserer.get("JobId")), dokument):
                        # Still in the Windows queue: it may print when the
                        # printer is back. A retry would make it two sheets.
                        return
                    raise RuntimeError(f"Drucker: {grund} – Auftrag zurückgenommen")
            finally:
                win32print.ClosePrinter(h)
            time.sleep(float(self.JOB_POLL))

    def _nicht_verfolgbar(self, dokument: str, e: BaseException) -> None:
        """Say once per printer that its queue cannot be followed; the job counts as handed over."""
        import logging
        logger = logging.getLogger("booth.print")
        if not getattr(self, "_unlesbar_gemeldet", False):
            self._unlesbar_gemeldet = True
            logger.warning("Windows-Druckwarteschlange von %s nicht lesbar (%s: %s) – Druckaufträge gelten "
                           "ab der Übergabe als gedruckt und werden nicht wiederholt",
                           self._name(), type(e).__name__, e)
        else:
            logger.info("Druckauftrag %s nicht verfolgbar (%s) – gilt als übergeben", dokument, e)

    @staticmethod
    def _zuruecknehmen(win32print, h, job_id: int, dokument: str) -> bool:
        """Delete our job from the Windows queue, so it cannot come out later as well."""
        try:
            win32print.SetJob(h, job_id, 0, None, getattr(win32print, "JOB_CONTROL_DELETE", 5))
            return True
        except Exception as e:                               # noqa: BLE001
            # Then the spooler may still print it when the printer returns;
            # the booth does not retry it (that would be two sheets). Said
            # loudly, once.
            import logging
            logging.getLogger("booth.print").error(
                "Druckauftrag %s ließ sich nicht aus der Windows-Warteschlange nehmen (%s) – "
                "er gilt als übergeben und wird nicht wiederholt", dokument, e)
            return False
