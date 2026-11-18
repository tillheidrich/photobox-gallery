"""Selbstdiagnose: was an dieser Box nicht stimmt, in Sätzen statt in Fehlercodes.

Gedacht für den Moment vor dem Event, wenn der Rechner auf dem Tisch steht und
etwas klemmt. Jede Prüfung steht für sich: eine kann rot sein, ohne die anderen
zu verhindern, und jede sagt, was zu tun ist — nicht, was intern schiefging.

    PhotoboxBooth.exe --doctor     (oder in der Verwaltung: „Prüfen“)
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .media import luts

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"


def _is_windows() -> bool:
    return sys.platform == "win32"


# Unter dieser Grenze wird es auf einem Event eng: Videos brauchen Platz.
DISK_WARN_GB = 10.0
DISK_FAIL_GB = 2.0

# So weit darf die Uhr der Box von der Galerie abweichen, bevor Zeitstempel lügen.
CLOCK_WARN_SECONDS = 120

# Ab hier staut sich die Warteschlange sichtbar.
QUEUE_WARN = 50

NO_EVENT = "Dieser Box ist in der Galerie noch kein Event zugewiesen – Uploads werden abgelehnt."
NO_QR = "Dem Event fehlt die Galerie-Adresse oder die Kennung – ohne sie gibt es keinen QR-Code."
QR_UNPUBLISHED = ("Der QR-Code auf dem Startbildschirm ist an, aber das Event ist in der Galerie nicht "
                  "veröffentlicht – Gäste stehen vor einer verschlossenen Galerie.")
QR_UNKNOWN = ("Ob das Event in der Galerie veröffentlicht ist, kann die Box nicht feststellen "
              "(die Galerie meldet es nicht) – bitte dort nachsehen, der QR-Code auf dem Startbildschirm ist an.")


def event_problems(profile: Dict[str, Any], local: Dict[str, Any]) -> List[tuple]:
    """What is wrong with the event of a booth that is paired with a gallery.

    One list for the admin card "Galerie-Server" and the self-check, so both
    say the same. ``[(state, sentence)]``; empty for a booth without gallery.
    The gallery answers an upload without an assigned event with 422, and the
    QR code needs the event's address and slug.
    """
    server = local.get("server") or {}
    if not (server.get("url") and server.get("api_key")):
        return []
    ev = profile.get("event") or {}
    if not ev.get("slug"):
        return [(WARN, NO_EVENT)]
    out = []
    if not ev.get("base_url"):
        out.append((WARN, NO_QR))
    if (profile.get("start") or {}).get("show_qr"):
        if ev.get("published") is False:
            out.append((WARN, QR_UNPUBLISHED))
        elif ev.get("published") is None:
            out.append((OK, QR_UNKNOWN))
    return out


@dataclass
class Check:
    name: str
    state: str = SKIP
    message: str = ""
    hint: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "state": self.state, "message": self.message,
                "hint": self.hint, **({"detail": self.detail} if self.detail else {})}


def _join(*hints: str) -> str:
    return " ".join(h.strip() for h in hints if h and h.strip())


def _gb(value: float) -> str:
    return f"{value:.1f} GB"


def _loaded_edsdk() -> str:
    """The EDSDK library this process loaded, or "" (see camera/edsdk.py)."""
    try:
        from .camera import edsdk
    except Exception:  # noqa: BLE001 - the check must not fail over an import
        return ""
    return edsdk.LOADED_DLL or ""


class Doctor:
    """Führt die Prüfungen gegen eine laufende :class:`~boothcore.app.Booth` aus."""

    def __init__(self, booth):
        self.b = booth

    # Reihenfolge wie am Tisch: erst der Rechner, dann die Geräte, dann das Netz.
    CHECKS = (
        ("Datensparsamer Betrieb", "check_privacy"),
        ("Datenordner", "check_data_dir"), ("Speicherplatz", "check_disk"),
        ("Kamera", "check_camera"), ("Canon-SDK", "check_edsdk"), ("Canon über WLAN", "check_ccapi"),
        ("Blitz und Belichtung", "check_flash"), ("Drucker", "check_printer"),
        ("Druckmodul", "check_print_module"),
        ("Papiervorrat", "check_media"),
        ("Galerie", "check_gallery"), ("Event", "check_event"), ("Uhrzeit", "check_clock"),
        ("Warteschlange", "check_queue"), ("Zweite Ablage", "check_storage_targets"),
        ("E-Mail", "check_mail"), ("Video-Werkzeug", "check_ffmpeg"),
        ("LUT-Filter", "check_luts"), ("Medien aus dem Profil", "check_profile_media"),
        ("Weboberfläche", "check_port"),
        ("Zugang", "check_access"),
        ("Download im WLAN", "check_wifi_share"),
    )

    def run(self) -> List[Check]:
        out = []
        for name, attr in self.CHECKS:
            try:
                out.append(getattr(self, attr)())
            except Exception as e:                    # eine kaputte Prüfung kippt nicht den Rest
                out.append(Check(name, FAIL, f"Prüfung fehlgeschlagen: {e}",
                                 "Das ist ein Fehler im Programm, nicht an deiner Box — "
                                 "die Meldung steht im Protokoll."))
        return out

    # ── Datensparsamer Betrieb ────────────────────────────
    def check_privacy(self) -> Check:
        """Im Kita-Modus darf nichts konfiguriert sein, das Daten aufbewahrt."""
        privacy = self.b.profile.get("privacy") or {}
        if str(privacy.get("mode", "normal")) != "print_only":
            return Check("Datensparsamer Betrieb", SKIP, "Aus — normaler Betrieb mit Galerie und QR.")
        problems = []
        local = self.b.store.local
        if local["server"].get("url"):
            problems.append("eine Galerie ist eingetragen")
        storage = local.get("storage") or {}
        if str(storage.get("folder") or "").strip():
            problems.append("eine zweite Ablage ist eingerichtet")
        if (storage.get("usb") or {}).get("enabled"):
            problems.append("die Kopie auf den Stick ist an")
        if local.get("targets"):
            problems.append(f"{len(local['targets'])} weitere Ziele sind eingetragen")
        if not self.b.profile["print"].get("enabled"):
            problems.append("Drucken ist aus — dann bekommt niemand etwas")
        dropped = [m for m in self.b.profile.get("modes", []) if m not in self.b.PRINTABLE_MODES]
        if dropped:
            problems.append(f"{', '.join(dropped)} ist aktiv, lässt sich aber nicht drucken "
                            f"(wird den Gästen nicht angeboten)")
        detail = {"delete_after": privacy.get("delete_after", "print"),
                  "delete_seconds": privacy.get("delete_seconds", 120)}
        if problems:
            return Check("Datensparsamer Betrieb", WARN,
                         "An, aber " + " und ".join(problems) + ".",
                         "Die Box überträgt in diesem Modus nichts — die Einträge sind wirkungslos, "
                         "sehen aber so aus, als würde etwas gespeichert. Besser austragen.", detail)
        return Check("Datensparsamer Betrieb", OK,
                     f"An: drucken, dann löschen (spätestens nach {detail['delete_seconds']} s).",
                     "", detail)

    # ── Grundlage ─────────────────────────────────────────
    def check_data_dir(self) -> Check:
        probe = self.b.data / ".doctor"
        try:
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            return Check("Datenordner", FAIL, f"{self.b.data} ist nicht beschreibbar ({e}).",
                         "Anderen Ordner wählen oder Schreibrechte geben — sonst geht kein Foto verloren, "
                         "aber auch keins durch.")
        return Check("Datenordner", OK, f"{self.b.data} ist beschreibbar.")

    def check_disk(self) -> Check:
        du = shutil.disk_usage(self.b.data)
        free = du.free / 1e9
        detail = {"free_gb": round(free, 1), "total_gb": round(du.total / 1e9, 1)}
        if free < DISK_FAIL_GB:
            return Check("Speicherplatz", FAIL, f"Nur noch {_gb(free)} frei.",
                         "Alte Events aus dem Sitzungsordner löschen oder auf eine größere Platte umziehen.",
                         detail)
        if free < DISK_WARN_GB:
            return Check("Speicherplatz", WARN, f"{_gb(free)} frei — für einen langen Abend knapp.",
                         "Vor dem Event aufräumen; ein Video frisst schnell ein paar hundert Megabyte.", detail)
        return Check("Speicherplatz", OK, f"{_gb(free)} frei.", "", detail)

    # ── Hardware ──────────────────────────────────────────
    def check_camera(self) -> Check:
        info = self.b.camera.info()
        backend = info.get("backend") or self.b.store.local["camera"].get("backend", "auto")
        detail = {"backend": backend, "state": info.get("state"), "errors": info.get("errors")}
        if info.get("backend") == "mock":
            # The mock camera delivers a test picture. That is fine when
            # somebody asked for it -- and the worst case when "auto" fell
            # back to it because no camera was found: the booth looks healthy
            # and nobody notices until the photos are looked at.
            import os
            chosen = (os.environ.get("BOOTH_CAMERA", "").lower() == "mock"
                      or (self.b.store.local["camera"].get("backend") or "").lower() == "mock")
            if not chosen:
                return Check(
                    "Kamera", FAIL,
                    "Keine Kamera gefunden — die Box zeigt nur ein Testbild.",
                    "Kamera einschalten und Kabel prüfen, dann die Box neu starten. "
                    "In der Verwaltung lässt sich die Anbindung fest einstellen, "
                    "damit die Box nicht mehr selbst raten muss.",
                    {**detail, "fallback": True})
            return Check("Kamera", OK, "Testbild statt Kamera — so eingestellt.", "", detail)
        if info.get("state") == "ready":
            return Check("Kamera", OK, f"{backend} ist verbunden.", "", detail)
        hint = {
            "edsdk": "EDSDK.dll und EdsImage.dll in den EDSDK-Ordner legen und den Pfad in der Verwaltung eintragen. "
                     "Ohne Canon-SDK gehen auch digiCamControl oder der USB-Webcam-Modus der Kamera.",
            "digicamcontrol": "digiCamControl installieren und den Webserver in den Einstellungen anhaken — "
                              "die Box startet das Programm dann selbst.",
            "folder": "Tethering-Software starten und ihren Zielordner in der Verwaltung eintragen.",
            "webcam": "Kamera einschalten, USB-Kabel prüfen; im Webcam-Modus meldet sich die Kamera als Webcam.",
        }.get(backend, "Kamera einschalten, Kabel prüfen, in der Verwaltung ein Testfoto auslösen.")
        return Check("Kamera", FAIL, f"{backend}: {info.get('error') or 'nicht verbunden'}.", hint, detail)

    def check_edsdk(self) -> Check:
        """Nur wenn die Box auf Canon eingestellt ist: liegt ein brauchbares SDK bereit?

        Häufigster Fehler ist nicht die fehlende Datei, sondern die falsche:
        im Canon-Archiv liegen 32- und 64-Bit-Fassungen nebeneinander, und die
        32-Bit-DLL scheitert erst beim Laden mit einer Windows-Meldung, die
        nichts mit Fotos zu tun hat.
        """
        backend = self.b.store.local["camera"].get("backend", "auto")
        configured = (self.b.store.local["camera"].get("edsdk_dll") or "").strip()
        loaded = _loaded_edsdk()
        if backend not in ("edsdk", "auto") or (backend == "auto" and not configured and not loaded):
            return Check("Canon-SDK", SKIP, "Diese Box nutzt kein Canon-SDK.", "",
                         {"backend": backend})
        from .edsdk_setup import has_sdk_platform

        if not has_sdk_platform():
            return Check("Canon-SDK", SKIP, "Für dieses System gibt es kein Canon-SDK.",
                         "Auf Linux läuft die Box mit gphoto2 — Backend in der Verwaltung umstellen.",
                         {"platform": os.name})
        check = self._edsdk_files(configured)
        if loaded:
            # Several copies can lie on one PC; name the one that runs.
            check.detail["loaded"] = loaded
            check.message = f"{check.message} Geladen: {loaded}"
            if not configured and check.state == WARN:
                check.state, check.hint = OK, ""
        return check

    def _edsdk_files(self, configured: str) -> Check:
        from .edsdk_setup import pe_machine, setup_hint, status, target_dir

        if configured:
            dll = Path(configured)
            if not dll.exists():
                return Check("Canon-SDK", FAIL, f"Eingetragen, aber nicht vorhanden: {dll}",
                             f"Pfad in der Verwaltung prüfen oder das SDK neu einrichten: {setup_hint()}",
                             {"dll": str(dll)})
            machine = pe_machine(dll)
            if machine is not None and machine != 0x8664 and os.name == "nt":
                return Check("Canon-SDK", FAIL, "Die eingetragene EDSDK.dll ist 32 Bit.",
                             "Die Box läuft mit 64 Bit. Im entpackten Canon-Archiv die 64-Bit-Fassung nehmen: "
                             f"{setup_hint()} sucht sie selbst.",
                             {"dll": str(dll), "machine": machine})
            if not (dll.parent / "EdsImage.dll").exists() and os.name == "nt":
                return Check("Canon-SDK", WARN, "EdsImage.dll fehlt neben der EDSDK.dll.",
                             "Ohne sie lädt das SDK, kann die Aufnahme aber nicht entgegennehmen. "
                             "Beide Dateien gehören in denselben Ordner.",
                             {"dll": str(dll)})
            return Check("Canon-SDK", OK, f"Eingerichtet: {dll}", "", {"dll": str(dll)})

        ok, detail = status()
        if ok:
            return Check("Canon-SDK", OK, detail, "", {"folder": str(target_dir())})
        return Check("Canon-SDK", WARN, detail,
                     "Das SDK kommt aus dem Canon Developer Programme und liegt nicht bei. "
                     f"Nach dem Entpacken: {setup_hint()}",
                     {"folder": str(target_dir())})

    def check_ccapi(self) -> Check:
        """Kamera am Netz: erreichbar, und antwortet sie wie eine Canon?"""
        cam = self.b.store.local["camera"]
        url = (cam.get("ccapi_url") or "").strip()
        if cam.get("backend") not in ("ccapi", "auto") or not url:
            return Check("Canon über WLAN", SKIP, "Diese Box holt ihre Bilder nicht über WLAN.", "",
                         {"backend": cam.get("backend")})
        import httpx
        try:
            r = httpx.get(url.rstrip("/") + "/ccapi", timeout=4.0)
            versions = list(r.json().keys()) if r.status_code == 200 else []
        except Exception as e:                       # noqa: BLE001
            return Check("Canon über WLAN", FAIL, f"{url} antwortet nicht ({e.__class__.__name__}).",
                         "Kamera an? Im selben Netz wie die Box? Die Adresse steht am Ende des "
                         "WLAN-Menüs der Kamera und ändert sich, wenn sie sich neu verbindet — "
                         "eine feste Adresse im Router erspart das Nachtragen.",
                         {"url": url})
        if not versions:
            return Check("Canon über WLAN", FAIL, f"{url} antwortet, aber nicht wie eine Canon.",
                         "Ist das wirklich die Kamera? Die CCAPI muss außerdem einmal mit Canons "
                         "Aktivierungswerkzeug freigeschaltet worden sein.", {"url": url})
        return Check("Canon über WLAN", OK, f"Kamera erreichbar ({', '.join(versions[:3])}).", "",
                     {"url": url, "versions": versions})

    def check_flash(self) -> Check:
        """Blitzbetrieb: Ladezeit gegen Serienaufnahmen, und was am Rad steht.

        Die beiden Fehler, die man erst auf dem Druck sieht: das zweite Bild
        einer Collage wird dunkel, weil der Blitz noch lud. Und die Kamera steht
        auf einer Betriebsart, die der Betreiber nicht wollte.
        """
        flash = self.b.profile.get("flash") or {}
        # Nicht direkt am Backend vorbei: Die Selbstdiagnose läuft je nach
        # Aufruf im HTTP-Thread, im Auftragsweg oder auf dem Hauptthread —
        # das EDSDK verlangt alle Aufrufe auf dem Kamera-Thread.
        mode = self.b.camera.frage("shooting_mode") or ""
        detail = {"flash": flash, "mode": mode or "nicht auslesbar"}

        if not flash.get("enabled"):
            return Check("Blitz und Belichtung", SKIP, "Kein Aufsteckblitz eingetragen.",
                         "Mit Blitz gehört er in die Verwaltung — die Box hält dann die Ladezeit ein.",
                         detail)
        # The live view's own exposure, in one line: what is on the camera for
        # the live view, what goes back for the shot, how bright the frame is.
        # Without it nobody can tell "too dark a room" from "never switched".
        preview = self.b.camera.preview_line()
        detail["preview"] = self.b.camera.preview_status()
        tail = f" {preview}." if preview else ""
        dunkel = self._dark_live_view_hint(detail["preview"])

        recycle = float(flash.get("recycle_seconds") or 0)
        gif = self.b.profile.get("gif") or {}
        interval = float(gif.get("interval") or 0)
        problems = []
        if "gif" in (self.b.profile.get("modes") or []) and interval and interval < recycle:
            problems.append(f"GIF nimmt alle {interval:g} s auf, der Blitz braucht {recycle:g} s")
        if any(m in (self.b.profile.get("modes") or []) for m in ("boomerang", "video")):
            problems.append("Boomerang und Video sind Serien — dafür ist ein Aufsteckblitz zu langsam")

        want = str(flash.get("expected_mode") or "any")
        if want != "any" and mode and mode != want:
            hint = {"M": "In M bleibt die Helligkeit den ganzen Abend gleich; der Blitz macht das Licht.",
                    "Av": "In Av bleibt der Raum im Bild sichtbar, dafür wandert die Belichtungszeit."}
            return Check("Blitz und Belichtung", WARN,
                         f"Kamera steht auf {mode}, erwartet war {want}.{tail}",
                         _join(hint.get(want, "") + " Am Moduswahlrad der Kamera umstellen, oder die Erwartung "
                               "in der Verwaltung ändern.", dunkel), detail)

        if problems:
            return Check("Blitz und Belichtung", WARN, "; ".join(problems) + tail,
                         _join("Diese Modi abschalten oder den Abstand vergrößern. Sonst wird jede zweite "
                               "Aufnahme dunkel, und das sieht man erst auf dem Druck.", dunkel), detail)

        message = f"Blitz eingetragen, {recycle:g} s Pause zwischen Aufnahmen."
        if mode:
            message += f" Kamera steht auf {mode}."
        elif self.b.camera.backend:
            message += " Betriebsart der Kamera nicht auslesbar."
        if dunkel:
            return Check("Blitz und Belichtung", WARN, message + tail, dunkel, detail)
        return Check("Blitz und Belichtung", OK, message + tail, "", detail)

    @staticmethod
    def _dark_live_view_hint(preview: Dict[str, Any]) -> str:
        """The likely cause when ISO and shutter are used up and the live view is still dark.

        Seen on the booth PC (EOS M50, M, flash): the camera shows the
        exposure for the flash in live view, so no ISO makes the room visible.
        Only a hint to check — the setting's name and place differ by body.
        """
        from .camera import exposure
        mean = (preview or {}).get("mean")
        if not (preview or {}).get("at_end") or mean is None or float(mean) >= exposure.TARGET_BAND[0]:
            return ""
        return ("Das Live-Bild bleibt dunkel, obwohl ISO und Belichtungszeit schon am Ende sind. "
                "Viele Canon-Kameras zeigen im Live-Bild die Belichtung für den Blitz. Im Kameramenü "
                "lässt sich dafür oft die „Belichtungssimulation“ ausschalten; Name und Ort hängen "
                "vom Modell ab.")

    def check_printer(self) -> Check:
        status = self.b.printer.status()
        enabled = bool(self.b.profile["print"].get("enabled"))
        if not enabled:
            return Check("Drucker", SKIP, "Drucken ist im Profil ausgeschaltet.")
        if not self.b.printer.backend:
            return Check("Drucker", WARN, "Kein Drucker eingerichtet, Drucken ist aber eingeschaltet.",
                         "Entweder Drucker in der Verwaltung eintragen oder Drucken ausschalten — "
                         "sonst sehen Gäste einen Knopf, der nicht funktioniert.")
        state = status.get("state")
        if state in ("offline", "error"):
            return Check("Drucker", FAIL, f"{status.get('name') or status.get('backend')}: {state}.",
                         "Papier, Farbband, Kabel, eingeschaltet? Danach in der Verwaltung einen Testdruck.",
                         {"printer": status})
        return Check("Drucker", OK, f"{status.get('name') or status.get('backend')} bereit "
                                    f"({status.get('printed', 0)} gedruckt).", "", {"printer": status})

    # ── Druckmodul (Windows) ──────────────────────────────
    #: What printing on Windows imports. pywin32 itself imports win32timezone
    #: the moment EnumJobs hands back a job with a time; the build of
    #: 15.11.2026 lacked it, and the booth printed but could not read its
    #: queue. An empty queue never touches it, hence the explicit import.
    WINDOWS_PRINT_MODULES = ("win32print", "win32gui", "win32timezone")

    def _windows_printer_names(self) -> List[str]:
        names = []
        for st in getattr(self.b.printer, "stations", []) or []:
            backend = getattr(st, "backend", None)
            if getattr(backend, "name", "") != "windows":
                continue
            try:
                name = backend._name()
            except Exception:                         # noqa: BLE001 — no default printer: OpenPrinter says so
                name = getattr(backend, "printer", "") or "(Standarddrucker)"
            if name not in names:
                names.append(name)
        return names

    def check_print_module(self) -> Check:
        if not _is_windows():
            return Check("Druckmodul", SKIP, "Nur unter Windows nötig.")
        import importlib
        hint = ("Die Box ist unvollständig gebaut. Mit packaging\\windows\\alles-einrichten.ps1 neu bauen; "
                "bis dahin druckt sie, ohne den Druck verfolgen zu können.")
        for module in self.WINDOWS_PRINT_MODULES:
            try:
                importlib.import_module(module)
            except Exception as e:                    # noqa: BLE001 — the text is the finding
                return Check("Druckmodul", FAIL, f"{module} lässt sich nicht laden: {type(e).__name__}: {e}",
                             hint, {"module": module, "error": f"{type(e).__name__}: {e}"})
        names = self._windows_printer_names()
        if not names:
            return Check("Druckmodul", OK, "Druckmodul geladen; kein Windows-Drucker eingerichtet.")
        import win32print
        drivers: Dict[str, str] = {}
        for name in names:
            try:
                handle = win32print.OpenPrinter(name)
                try:
                    win32print.EnumJobs(handle, 0, 256, 1)
                    drivers[name] = self._driver_name(win32print, handle)
                finally:
                    win32print.ClosePrinter(handle)
            except Exception as e:                    # noqa: BLE001 — the text is the finding
                return Check("Druckmodul", FAIL,
                             f"Warteschlange von „{name}“ nicht lesbar: {type(e).__name__}: {e}",
                             "Druckername in der Verwaltung prüfen; ist er richtig, die Box neu bauen.",
                             {"printer": name, "error": f"{type(e).__name__}: {e}"})
        generic = [n for n, d in drivers.items() if d.strip().lower() == self.GENERIC_DRIVER.lower()]
        if generic:
            return Check("Druckmodul", WARN,
                         f"„{generic[0]}“ läuft mit dem Treiber „{self.GENERIC_DRIVER}“ – damit nahm der "
                         "SELPHY CP1300 keinen Auftrag an.",
                         "Den Druckertreiber von canon.de installieren und den Drucker damit neu einrichten "
                         "(nicht den, den Windows 11 von selbst wählt).",
                         {"printers": names, "drivers": drivers})
        return Check("Druckmodul", OK, f"Druckmodul geladen, Warteschlange lesbar ({', '.join(names)}).",
                     "", {"printers": names, "drivers": drivers})

    #: The driver Windows 11 sets up by itself for a SELPHY on USB. On the
    #: booth PC (15.11.2026) the CP1300 took no job with it; the Canon driver
    #: from canon.de printed at once.
    GENERIC_DRIVER = "Microsoft IPP Class Driver"

    @staticmethod
    def _driver_name(win32print, handle) -> str:
        """The queue's driver (PRINTER_INFO_2.pDriverName); "" when Windows will not say."""
        try:
            return str((win32print.GetPrinter(handle, 2) or {}).get("pDriverName") or "")
        except Exception:                             # noqa: BLE001 — a name is a hint, not a requirement
            return ""

    # ── Galerie und Zeit ──────────────────────────────────
    @staticmethod
    def _media_sentence(entry: dict, named: bool) -> str:
        """One printer's counted supply; tray and ribbon get their own numbers."""
        label = f"{entry.get('label') or 'Drucker'}: " if named else ""
        fach, band = entry.get("fach"), entry.get("farbband")
        if not (fach and band):
            return f"{label}Noch etwa {int(entry.get('rest') or 0)} Blatt (mitgezählt)."
        f, b = int(fach["rest"]), int(band["rest"])
        first = ("Papier und Farbband sind gleichzeitig leer" if f == b
                 else "Das Papier ist zuerst leer" if f < b else "Das Farbband ist zuerst leer")
        return (f"{label}Papierfach {f} von {fach['gesamt']}, Farbband {b} von {band['gesamt']} (gezählt). "
                f"{first}, noch {min(f, b)} Blatt.")

    def check_media(self) -> Check:
        """Papier reicht noch? Gezählt, nicht gemessen — mehr geht bei diesen Druckern nicht."""
        cfg = self.b.profile["print"]
        per_cassette = int(cfg.get("sheets_per_cassette") or 0)
        gezaehlt = [s for s in self.b.sheets() if s.get("quelle") == "gezählt"]
        if cfg.get("enabled") and gezaehlt and not per_cassette:
            # Model or cassette size per printer: the booth counts per printer.
            rest = sum(int(s.get("rest") or 0) for s in gezaehlt)
            detail = {"left": rest, "printers": gezaehlt}
            if any(s.get("fach") and s.get("farbband") for s in gezaehlt):
                # Tray and ribbon are counted apart: say which one runs out first.
                message = " ".join(self._media_sentence(s, len(gezaehlt) > 1) for s in gezaehlt)
                return Check("Papiervorrat", WARN if rest <= 3 else OK, message,
                             "Papier und Farbband bereitlegen, nach dem Wechsel „Papier nachgelegt“ bzw. „Farbband gewechselt“."
                             if rest <= 3 else "", detail)
            if rest <= 3:
                return Check("Papiervorrat", WARN, f"Noch {rest} Blatt bis zum Kassettenwechsel.",
                             "Papier und Farbband bereitlegen, nach dem Wechsel „Papier nachgelegt“ bzw. „Farbband gewechselt“.", detail)
            return Check("Papiervorrat", OK, f"Noch etwa {rest} Blatt (mitgezählt).", "", detail)
        if not cfg.get("enabled") or not per_cassette:
            # Unknown is not empty: a calm note, never a warning.
            return Check("Papiervorrat", SKIP, "Papiervorrat nicht eingetragen – die Box kann nicht mitzählen.",
                         "Druckermodell oder Blatt je Kassette eintragen, dann warnt die Box rechtzeitig.",
                         {"per_cassette": per_cassette})
        left = self.b.media_left().get("sheets_left")
        detail = {"left": left, "per_cassette": per_cassette, "printed": self.b.printer.printed}
        per_print = float(cfg.get("seconds_per_print") or 0)
        if left is not None and left <= 3:
            return Check("Papiervorrat", WARN, f"Noch {left} Blatt bis zum Kassettenwechsel.",
                         "Papier und Farbband bereitlegen. Nach dem Wechsel den Zähler in der "
                         "Verwaltung zurücksetzen, sonst zählt die Box weiter durch.", detail)
        message = f"Noch {left} von {per_cassette} Blatt."
        if per_print:
            message += f" Ein Druck dauert rund {per_print:g} s."
        return Check("Papiervorrat", OK, message, "", detail)

    def check_gallery(self) -> Check:
        server = self.b.store.local["server"]
        if not server.get("url"):
            return Check("Galerie", SKIP, "Diese Box läuft ohne Galerie (standalone).")
        if not server.get("api_key"):
            return Check("Galerie", FAIL, "Adresse eingetragen, aber kein Geräte-Schlüssel.",
                         "Schlüssel im Galerie-Admin unter der Box erzeugen und hier eintragen.")
        if self.b.agent.online:
            return Check("Galerie", OK, f"Verbunden mit {server['url']}.")
        return Check("Galerie", WARN, f"Zurzeit offline ({self.b.agent.last_error or 'kein Kontakt'}).",
                     "Aufnahmen gehen nicht verloren — sie warten in der Warteschlange. "
                     "Für den QR-Code am Bildschirm reicht das, die Bilder kommen später an.")

    def check_event(self) -> Check:
        local = self.b.store.local
        server = local.get("server") or {}
        if not (server.get("url") and server.get("api_key")):
            return Check("Event", SKIP, "Ohne Galerie gibt es kein zugewiesenes Event zu prüfen.")
        problems = event_problems(self.b.profile, local)
        warnings = [text for state, text in problems if state == WARN]
        notes = [text for state, text in problems if state != WARN]
        if warnings:
            hint = ("In der Galerie unter „Boxen“ diese Box öffnen, ihr ein Event zuweisen und "
                    "das Event veröffentlichen. Die Box holt die Änderung beim nächsten Abgleich ab.")
            return Check("Event", WARN, " ".join(warnings + notes), hint)
        ev = self.b.profile.get("event") or {}
        message = f"Event „{ev.get('name') or ev.get('slug')}“, QR-Code zeigt auf {ev.get('base_url')}."
        return Check("Event", OK, " ".join([message] + notes))

    def check_clock(self) -> Check:
        """Eine falsch gehende Uhr macht Zeitstempel und Ablaufdaten unbrauchbar."""
        if not self.b.store.local["server"].get("url"):
            return Check("Uhrzeit", SKIP, "Ohne Galerie gibt es nichts zu vergleichen.")
        try:
            import httpx
            r = httpx.get(self.b.store.local["server"]["url"].rstrip("/") + "/healthz", timeout=5.0)
            served = r.headers.get("Date", "")
            if not served:
                return Check("Uhrzeit", SKIP, "Server nennt keine Zeit.")
            from email.utils import parsedate_to_datetime
            drift = abs(time.time() - parsedate_to_datetime(served).timestamp())
        except Exception as e:
            return Check("Uhrzeit", SKIP, f"Nicht prüfbar: {e}")
        if drift > CLOCK_WARN_SECONDS:
            return Check("Uhrzeit", WARN, f"Die Uhr der Box weicht um {int(drift)} s ab.",
                         "In den Windows-Einstellungen die Zeit automatisch stellen lassen — sonst stimmen "
                         "Zeitstempel und Ablaufdaten nicht.", {"drift_seconds": int(drift)})
        return Check("Uhrzeit", OK, f"Weicht um {int(drift)} s ab.")

    # ── Ablage ────────────────────────────────────────────
    def check_queue(self) -> Check:
        counts = self.b.outbox.counts()
        if counts.get("failed"):
            # Why, in plain words and counted, so the operator sees it in the
            # gallery without the log (17.11.2026: "8 Uploads endgültig
            # fehlgeschlagen" and nothing else). No file names: this travels.
            from photobox_sync import reasons
            try:
                fehler = [f.get("error") or "" for f in self.b.outbox.failed_list(500)]
            except Exception:                          # noqa: BLE001 — the count alone still says something
                fehler = []
            warum = reasons.short(fehler)
            haupt = reasons.summary(fehler, 1)[0]["reason"] if fehler else ""
            rat = {"kein Event zugewiesen": "In der Galerie der Box ein Event zuweisen, dann „Fehlgeschlagene erneut“.",
                   "Datei fehlt": "Die Dateien sind auf der Box nicht mehr da; erneut versuchen hilft nicht — verwerfen.",
                   "API-Schlüssel ungültig": "Die Box neu mit der Galerie koppeln, dann „Fehlgeschlagene erneut“.",
                   }.get(haupt, "In der Verwaltung „Fehlgeschlagene erneut“ drücken; bleibt es dabei, stimmt ein Ziel nicht.")
            return Check("Warteschlange", WARN,
                         f"{counts['failed']} Uploads endgültig fehlgeschlagen ({warum}), {counts.get('pending', 0)} offen."
                         if warum else
                         f"{counts['failed']} Uploads endgültig fehlgeschlagen, {counts.get('pending', 0)} offen.",
                         rat, counts)
        if counts.get("pending", 0) > QUEUE_WARN:
            return Check("Warteschlange", WARN, f"{counts['pending']} Uploads stauen sich.",
                         "Netz langsam oder Ziel offline — die Box arbeitet das ab, sobald es wieder geht.", counts)
        return Check("Warteschlange", OK, f"{counts.get('pending', 0)} offen, {counts.get('done', 0)} erledigt.",
                     "", counts)

    def check_storage_targets(self) -> Check:
        storage = self.b.store.local.get("storage") or {}
        parts, worst = [], OK
        folder = str(storage.get("folder") or "").strip()
        if folder:
            if Path(folder).is_dir():
                parts.append(f"zweiter Ordner {folder} da")
            else:
                worst, _ = FAIL, parts.append(f"zweiter Ordner {folder} fehlt")
        usb = storage.get("usb") or {}
        if usb.get("enabled"):
            path = str(usb.get("path") or "").strip()
            from . import usb_backup
            found = usb_backup.drives()
            if path and Path(path).is_dir():
                parts.append(f"Stick {path} steckt")
            elif not path and found:
                parts.append(f"Stick {found[0]['label']} steckt, laufende Sicherung an")
            else:
                worst = WARN if worst == OK else worst
                parts.append(f"laufende Sicherung an, aber kein Stick da{' (' + path + ')' if path else ''}")
        if not parts:
            return Check("Zweite Ablage", SKIP, "Nur der Arbeitsordner der Box, keine Kopien eingerichtet.")
        hint = ("Fehlt der Stick, wartet nur seine Warteschlange — die Aufnahmen sind trotzdem sicher. "
                "Laufwerksbuchstabe in der Verwaltung prüfen." if worst != OK else "")
        return Check("Zweite Ablage", worst, " · ".join(parts), hint)

    def check_mail(self) -> Check:
        cfg = self.b.profile.get("mail") or {}
        if not cfg.get("enabled"):
            return Check("E-Mail", SKIP, "E-Mail-Versand ist aus.")
        # Über die Galerie braucht die Box kein Postfach — das ist der Sinn
        # dieses Weges. Die Prüfung verlangte trotzdem Server und Absender und
        # schickte den Betreiber damit in eine SMTP-Einrichtung, die er nicht
        # braucht. Gefunden bei der Durchsicht am 19.09.2026.
        ueber_galerie = str(cfg.get("transport") or "gallery").lower() == "gallery"
        ueber_relay = ueber_galerie and getattr(self.b.mail, "relay", None) is not None
        eigenes_postfach = bool(cfg.get("host") and cfg.get("from_addr"))
        status = self.b.mail.status()
        if not ueber_relay and not eigenes_postfach:
            fehlt = ("Versand über die Galerie eingestellt, aber keine Galerie verbunden."
                     if ueber_galerie else "Eingeschaltet, aber Server oder Absender fehlt.")
            return Check("E-Mail", FAIL, fehlt,
                         "Box koppeln — oder SMTP-Server, Absender und Passwort eintragen. "
                         "Sonst sammeln sich unsendbare Mails.")
        if status.get("failed"):
            return Check("E-Mail", WARN, f"{status['failed']} Mails konnten nicht zugestellt werden.",
                         f"Letzter Fehler: {status.get('last_error') or 'unbekannt'}", status)
        wohin = "über die Galerie" if ueber_relay else f"{cfg.get('host')}"
        return Check("E-Mail", OK, f"{wohin} eingerichtet, {status.get('pending', 0)} in der Schlange.",
                     "", status)

    # ── Werkzeuge ─────────────────────────────────────────
    def check_ffmpeg(self) -> Check:
        modes = set(self.b.profile.get("modes") or [])
        needs = modes & {"video", "boomerang", "gif"}
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as e:
            if not needs:
                return Check("Video-Werkzeug", SKIP, "Kein Modus braucht es.")
            return Check("Video-Werkzeug", FAIL, f"ffmpeg fehlt ({e}).",
                         "Ohne ffmpeg gibt es keine Videos, Boomerangs und GIFs. Programm neu installieren.")
        return Check("Video-Werkzeug", OK, f"ffmpeg gefunden ({Path(exe).name}).")

    def check_luts(self) -> Check:
        catalog = self.b.lut_catalog()
        baked = (self.b.profile.get("luts") or {}).get("baked") or ""
        wanted = luts.canonical_key(str(baked).removeprefix("lut-"))
        if baked and not any(c["id"].removeprefix("lut-") == wanted for c in catalog):
            return Check("LUT-Filter", FAIL, f"Der Hauslook „{baked}“ liegt nicht auf dieser Box.",
                         "LUT in der Galerie hochladen oder in den Ordner <Daten>/luts legen — sonst kommt "
                         "jedes Bild ungefiltert heraus.")
        return Check("LUT-Filter", OK, f"{len(catalog)} LUTs verfügbar.")

    def check_profile_media(self) -> Check:
        """A file the profile names but the booth does not have is shown as nothing.

        No logo, no smile picture, a silent shutter: the kiosk skips a missing
        file without a word, so the guest sees a gap and the operator sees no
        error. The self-check names the file instead (usually it has not been
        downloaded yet, or was deleted in the gallery after it was chosen).
        """
        from .config import media_files
        wanted = media_files(self.b.profile)
        missing = {name: field for name, field in wanted.items()
                   if "/" in name or "\\" in name or not (self.b.assets_dir / name).is_file()}
        if missing:
            names = ", ".join(sorted(missing)[:8]) + (" …" if len(missing) > 8 else "")
            return Check("Medien aus dem Profil", WARN,
                         f"{len(missing)} Datei(en) aus dem Profil fehlen auf der Box: {names}.",
                         "Box mit der Galerie verbinden, damit sie die Dateien lädt — oder im Box-Profil "
                         "eine andere Datei wählen. Bis dahin bleibt die Stelle leer.",
                         {"missing": missing})
        return Check("Medien aus dem Profil", OK, f"{len(wanted)} Datei(en) aus dem Profil vorhanden.")

    def check_port(self) -> Check:
        """Belegt ein fremdes Programm den Port, startet die Box später nicht."""
        port = int(self.b.store.local.get("port", 8765))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            occupied = s.connect_ex(("127.0.0.1", port)) == 0
        if not occupied:
            return Check("Weboberfläche", SKIP, f"Port {port} ist frei (die Box läuft gerade nicht).")
        try:
            import httpx
            ours = httpx.get(f"http://127.0.0.1:{port}/api/about", timeout=2.0).json().get("name", "")
        except Exception:
            ours = ""
        if ours.startswith("Photobox"):
            return Check("Weboberfläche", OK, f"Port {port} antwortet — das ist diese Box.")
        return Check("Weboberfläche", FAIL, f"Auf Port {port} antwortet ein anderes Programm.",
                     "Entweder das Programm beenden oder in der Verwaltung einen anderen Port wählen — "
                     "sonst startet die Box nicht.")

    def check_access(self) -> Check:
        """The factory PIN fails, wherever the admin is reachable from.

        2580 is in every manual. Loopback is no excuse: the PIN field sits on
        the touchscreen, and a guest who taps four digits is in the settings.
        On the network it is worse — anyone in the same Wi-Fi.
        """
        from .config import FACTORY_PIN, MIN_PIN_LENGTH
        host = str(getattr(self.b, "listen_host", "127.0.0.1") or "127.0.0.1")
        loopback = host in ("localhost", "::1") or host.startswith("127.")
        if self.b.factory_pin_active():
            where = ("jeder am Touchscreen" if loopback else
                     f"jeder am Touchscreen und jeder im selben WLAN (Adresse {host})")
            return Check("Zugang", FAIL,
                         f"Die PIN ist noch die Werks-PIN {FACTORY_PIN}. Sie steht in jeder Anleitung; "
                         f"{where} kommt damit in die Verwaltung.",
                         f"Verwaltung der Box öffnen und mit {FACTORY_PIN} anmelden: Die Box verlangt dann "
                         f"sofort eine eigene PIN (mindestens {MIN_PIN_LENGTH} Ziffern). Oder in der Galerie "
                         "im Box-Profil unter „Pause und Zugang“ eine PIN setzen.")
        if loopback:
            return Check("Zugang", OK, "Eigene PIN; die Verwaltung ist nur an diesem Rechner erreichbar.")
        return Check("Zugang", OK, f"Im Netzwerk erreichbar (Adresse {host}), mit eigener PIN.")

    def check_wifi_share(self) -> Check:
        """Download without internet: is there an address, and does the guest port answer?

        The booth sets up no Wi-Fi itself. What it can check: that this machine
        has an address in a local network, and that the second listener (the
        guest page, its own port) is bound — in the booth process itself, or,
        for ``--doctor`` run beside a running booth, by knocking on the port.
        """
        from . import guest_share
        name = "Download im WLAN"
        cfg = self.b.profile.get("wifi_share") or {}
        if not cfg.get("enabled"):
            return Check(name, SKIP, "Ausgeschaltet.")
        if not (self.b.store.local.get("guest_share") or {}).get("allowed"):
            return Check(name, SKIP, "Im Profil eingeschaltet, aber an dieser Box gesperrt — es wird nichts geöffnet.",
                         "Nur wer an der Box selbst sitzt, kann das freigeben (Verwaltung der Box, „Download im WLAN erlauben“).")
        if self.b.ephemeral:
            return Check(name, SKIP, "Datensparsamer Betrieb: kein Link, also auch kein lokaler.")
        if not str(cfg.get("ssid") or "").strip():
            return Check(name, WARN, "Eingeschaltet, aber ohne WLAN-Namen — die Box zeigt deshalb keinen WLAN-Code.",
                         "Im Box-Profil unter „Download ohne Internet“ den Namen (SSID) des WLANs eintragen.")
        local = self.b.store.local
        port = guest_share.guest_port(local)
        addr = guest_share.guest_address(local, guest_share.local_addresses)
        if not addr:
            return Check(name, WARN, "Keine Adresse in einem lokalen Netz gefunden — Handys im WLAN erreichen "
                                     "die Box so nicht, der Ergebnisbildschirm zeigt den gewohnten QR-Code.",
                         "Den Rechner mit dem WLAN-Router oder Hotspot verbinden (Kabel oder WLAN) oder in den "
                         "lokalen Einstellungen unter guest_share.address die Adresse eintragen.")
        gs = guest_share.for_booth(self.b)
        if gs.bound:
            return Check(name, OK, f"Gäste-Seite unter http://{addr}:{gs.port or port} im WLAN „{cfg.get('ssid')}“.")
        if gs.error:
            return Check(name, WARN, f"Die Gäste-Seite lauscht nicht: {gs.error}",
                         "Das Programm auf dem Port beenden oder unter guest_share.port einen anderen wählen, "
                         "dann die Box neu starten.")
        main_port = int(local.get("port", 8765))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            booth_up = s.connect_ex(("127.0.0.1", main_port)) == 0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            guest_up = s.connect_ex(("127.0.0.1", port)) == 0
        if guest_up:
            return Check(name, OK, f"Gäste-Seite unter http://{addr}:{port} im WLAN „{cfg.get('ssid')}“.")
        if booth_up:
            return Check(name, WARN, f"Die Box läuft, aber auf Port {port} lauscht keine Gäste-Seite.",
                         "Box neu starten. Bleibt es so, ist der Port belegt — unter guest_share.port einen "
                         "anderen wählen.")
        return Check(name, SKIP, f"Die Box läuft gerade nicht; die Gäste-Seite kommt mit ihr auf Port {port}.")


def summary(checks: List[Check]) -> Dict[str, Any]:
    counts = {state: sum(1 for c in checks if c.state == state) for state in (OK, WARN, FAIL, SKIP)}
    worst = FAIL if counts[FAIL] else (WARN if counts[WARN] else OK)
    return {"worst": worst, "counts": counts}


def report(checks: List[Check], width: int = 78) -> str:
    """Text für die Konsole — das, was am Box-PC in der Eingabeaufforderung steht."""
    marks = {OK: "OK  ", WARN: "!   ", FAIL: "X   ", SKIP: "–   "}
    lines = []
    for c in checks:
        lines.append(f"{marks.get(c.state, '?   ')}{c.name}: {c.message}")
        if c.hint and c.state in (WARN, FAIL):
            for chunk in _wrap(c.hint, width - 6):
                lines.append(f"      {chunk}")
    s = summary(checks)
    lines.append("")
    lines.append({OK: "Alles in Ordnung.", WARN: "Läuft, aber sieh dir die Punkte mit ! an.",
                  FAIL: "So geht die Box nicht auf ein Event."}[s["worst"]])
    return "\n".join(lines)


def _wrap(text: str, width: int) -> List[str]:
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out
