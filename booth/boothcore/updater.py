"""Neue Fassung holen — geprüft, und nie mitten im Abend.

Die Galerie legt eine neue Fassung bereit und sagt beim Sync Bescheid. Diese
Datei holt sie, prüft die Prüfsumme und legt sie neben das laufende Programm.
Getauscht wird erst beim nächsten Start:

1. ``hole()`` lädt die Datei nach ``data/update/`` und prüft SHA-256.
2. ``bereit()`` sagt, dass etwas Fertiges daliegt — die Verwaltung zeigt es an.
3. ``einspielen()`` schreibt ein winziges Skript, das nach dem Beenden die alte
   Datei beiseitelegt, die neue an ihren Platz schiebt und das Programm wieder
   startet. Dann beendet sich die Box.

Warum so umständlich? Weil eine laufende ``.exe`` sich unter Windows nicht
selbst überschreiben kann, und weil ein Update, das den Abend kostet, kein
Update ist. Schlägt irgendetwas fehl, bleibt die alte Fassung liegen und
läuft weiter — das ist der wichtigste Satz in dieser Datei.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("booth.update")

ZETTEL = "update.json"

_HEX = set("0123456789abcdef")


def _teile(version: str):
    """Version into comparable numbers; anything unknown counts as 0 (same rule as app/software.py)."""
    import re
    zahlen = [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", str(version or "").split()[-1] if str(version or "").split() else "")]
    return tuple(zahlen + [0] * (4 - len(zahlen)))[:4]


def neuer_als(kandidat: str, laufend: str) -> bool:
    return _teile(kandidat) > _teile(laufend)


def _gueltige_pruefsumme(sha: Any) -> bool:
    sha = str(sha or "").lower()
    return len(sha) == 64 and set(sha) <= _HEX


def _gleicher_ort(url: str, galerie: str) -> bool:
    """Same scheme, host and port: only then may the device key travel along (S-4)."""
    from urllib.parse import urlsplit
    if not url or not galerie:
        return False
    a, b = urlsplit(url), urlsplit(galerie)
    port = lambda u: u.port or (443 if u.scheme == "https" else 80)  # noqa: E731
    return (a.scheme, (a.hostname or "").lower(), port(a)) == (b.scheme, (b.hostname or "").lower(), port(b))


class Updater:
    def __init__(self, data: Path, version: str):
        self.dir = Path(data) / "update"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.version = version

    # ── Zustand ──────────────────────────────────────────
    @property
    def _zettel(self) -> Path:
        return self.dir / ZETTEL

    def stand(self) -> Dict[str, Any]:
        """Was gerade bekannt ist: angeboten, geladen, bereit oder nichts."""
        try:
            daten = json.loads(self._zettel.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"state": "nichts", "version": self.version}
        datei = self.dir / str(daten.get("file") or "")
        daten["ready"] = bool(daten.get("sha256") and datei.is_file()
                              and daten.get("downloaded_sha") == daten.get("sha256"))
        daten["state"] = "bereit" if daten["ready"] else "angeboten"
        daten["running"] = self.version
        return daten

    def merke(self, angebot: Dict[str, Any]) -> None:
        """Ein Angebot notieren. Heruntergeladen wird erst auf Zuruf."""
        if not angebot or not angebot.get("version"):
            return
        # S-4 (15.11.2026): without a checksum there is nothing to check the
        # file against, and an older or equal version is a downgrade — both
        # are refused before anything is noted. (Signed updates: still open.)
        if not _gueltige_pruefsumme(angebot.get("sha256")):
            log.warning("Update-Angebot %s ohne gültige Prüfsumme — ignoriert", angebot.get("version"))
            return
        if not neuer_als(str(angebot["version"]), self.version):
            log.info("Update-Angebot %s ist nicht neuer als %s — ignoriert", angebot.get("version"), self.version)
            return
        alt = self.stand()
        if alt.get("version") == angebot.get("version") and alt.get("ready"):
            return                               # schon da, nichts zu tun
        self._zettel.write_text(json.dumps({
            "version": angebot["version"], "url": angebot.get("url", ""),
            "sha256": angebot.get("sha256", ""), "size": int(angebot.get("size") or 0),
            "notes": angebot.get("notes", ""), "file": Path(angebot.get("file") or "update.bin").name,
            "seen": time.time(),
        }, ensure_ascii=False), encoding="utf-8")

    # ── Holen ────────────────────────────────────────────
    def hole(self, api_key: str = "", verify_tls: bool = True, gallery_url: str = "") -> Dict[str, Any]:
        """Die notierte Fassung laden und prüfen. Gibt den neuen Stand zurück.

        ``gallery_url`` is the gallery this booth is paired with. The device
        key is sent only when the offer's address is on that same host (S-4);
        redirects are not followed, so a download cannot wander elsewhere.
        """
        stand = self.stand()
        if stand.get("state") == "nichts" or not stand.get("url"):
            return {"ok": False, "error": "Nichts angeboten"}
        if not _gueltige_pruefsumme(stand.get("sha256")) or not neuer_als(str(stand.get("version")), self.version):
            return {"ok": False, "error": "Angebot ohne Prüfsumme oder nicht neuer — verworfen"}
        if stand.get("ready"):
            return {"ok": True, "state": "bereit", "version": stand["version"]}
        import httpx

        ziel = self.dir / str(stand["file"])
        teil = ziel.with_suffix(ziel.suffix + ".part")
        h = hashlib.sha256()
        try:
            with httpx.stream("GET", stand["url"], timeout=httpx.Timeout(600.0, connect=10.0),
                              headers={"X-API-Key": api_key}
                              if api_key and _gleicher_ort(stand["url"], gallery_url) else {},
                              verify=verify_tls, follow_redirects=False) as antwort:
                antwort.raise_for_status()
                with teil.open("wb") as f:
                    for brocken in antwort.iter_bytes(256 * 1024):
                        f.write(brocken)
                        h.update(brocken)
        except Exception as exc:                  # noqa: BLE001 — ein Fehlschlag kostet nichts
            teil.unlink(missing_ok=True)
            log.warning("Update nicht geladen: %s", exc)
            return {"ok": False, "error": str(exc)[:200]}
        gefunden = h.hexdigest()
        if gefunden != str(stand["sha256"]).lower():
            teil.unlink(missing_ok=True)
            log.error("Update verworfen: Prüfsumme passt nicht")
            return {"ok": False, "error": "Prüfsumme passt nicht — Datei verworfen"}
        os.replace(teil, ziel)
        daten = json.loads(self._zettel.read_text(encoding="utf-8"))
        daten["downloaded_sha"] = gefunden
        daten["downloaded_at"] = time.time()
        self._zettel.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
        log.info("Update %s geladen und geprüft", daten.get("version"))
        return {"ok": True, "state": "bereit", "version": daten.get("version")}

    def verwerfen(self) -> None:
        for p in self.dir.glob("*"):
            if p.is_file():
                p.unlink(missing_ok=True)

    # ── Einspielen ───────────────────────────────────────
    def ziel_programm(self) -> Optional[Path]:
        """Die laufende Datei, die ersetzt werden soll — nur im gebauten Programm."""
        if getattr(sys, "frozen", False):
            return Path(sys.executable)
        return None

    def einspielen(self) -> Dict[str, Any]:
        """Tauschen und neu starten. Läuft die Box aus dem Quellcode, sagt sie das."""
        stand = self.stand()
        if not stand.get("ready"):
            return {"ok": False, "error": "Nichts Fertiges da"}
        if not neuer_als(str(stand.get("version")), self.version):
            return {"ok": False, "error": "Diese Fassung ist nicht neuer als die laufende"}
        neu = self.dir / str(stand["file"])
        ziel = self.ziel_programm()
        if ziel is None:
            return {"ok": False, "error": "Aus dem Quellcode gestartet — hier tauscht nichts automatisch. "
                                          f"Die geprüfte Datei liegt unter {neu}"}
        if os.name == "nt":
            skript = self._windows_skript(neu, ziel)
            from . import lifecycle as _lc
            subprocess.Popen(["cmd", "/c", str(skript)], **_lc.respawn_kwargs())
        else:
            skript = self._posix_skript(neu, ziel)
            subprocess.Popen(["/bin/sh", str(skript)], close_fds=True, start_new_session=True)
        log.info("Update %s wird eingespielt, Programm beendet sich", stand.get("version"))
        # Und es beendet sich auch wirklich. Vorher stand genau das im Log,
        # aber die Funktion kehrte nur zurueck: Das Skript schob nach drei
        # Sekunden die laufende .exe beiseite, legte die neue hin und
        # startete sie -- Port belegt, neue Fassung stirbt, alte laeuft
        # weiter. Nach aussen sah das aus wie "Update tut nichts".
        from . import lifecycle
        # Exit 0 ends the watcher, but the script starts the new version: the
        # gallery should expect the box back (its silence alarm, if on).
        lifecycle.announce("update", True)
        threading.Timer(1.5, lambda: lifecycle.leave(0)).start()
        return {"ok": True, "version": stand.get("version"), "script": str(skript)}

    def _windows_skript(self, neu: Path, ziel: Path) -> Path:
        skript = self.dir / "einspielen.bat"
        skript.write_text(
            "@echo off\r\n"
            "rem Wartet, bis die Box beendet ist, legt die alte Datei beiseite und startet neu.\r\n"
            f"ping 127.0.0.1 -n 4 >nul\r\n"
            f'if exist "{ziel}.alt" del "{ziel}.alt"\r\n'
            f'move /y "{ziel}" "{ziel}.alt" >nul\r\n'
            f'move /y "{neu}" "{ziel}" >nul\r\n'
            f'if not exist "{ziel}" move /y "{ziel}.alt" "{ziel}" >nul\r\n'
            f'start "" "{ziel}"\r\n', encoding="utf-8")
        return skript

    def _posix_skript(self, neu: Path, ziel: Path) -> Path:
        skript = self.dir / "einspielen.sh"
        skript.write_text(
            "#!/bin/sh\n"
            "sleep 3\n"
            f'cp -p "{ziel}" "{ziel}.alt" 2>/dev/null\n'
            f'mv -f "{neu}" "{ziel}" || mv -f "{ziel}.alt" "{ziel}"\n'
            f'chmod +x "{ziel}"\n'
            f'"{ziel}" &\n', encoding="utf-8")
        skript.chmod(0o755)
        return skript


def aufraeumen(data: Path) -> None:
    """Reste eines eingespielten Updates wegräumen (beim Start)."""
    d = Path(data) / "update"
    for name in ("einspielen.bat", "einspielen.sh"):
        (d / name).unlink(missing_ok=True)
    for rest in Path(sys.executable).parent.glob("*.alt") if getattr(sys, "frozen", False) else []:
        try:
            if time.time() - rest.stat().st_mtime > 86400:
                rest.unlink()
        except OSError:
            pass
    shutil.rmtree(d / "tmp", ignore_errors=True)
