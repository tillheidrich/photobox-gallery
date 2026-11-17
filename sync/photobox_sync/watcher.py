"""Ordner beobachten — für dslrBooth, LumaBooth und jede andere Aufnahme-Software.

* **Stabile Datei:** Eine Datei wird erst übernommen, wenn Größe und Änderungszeit
  über ``stable_seconds`` gleich bleiben — zwei Beobachtungen, immer. Halb
  geschriebene Fotos gehen nie raus.
* **Stabile Kennung:** Die Upload-Kennung entsteht aus Pfad, Größe und Zeit —
  ein Neustart lädt nichts doppelt, eine ersetzte Datei wird neu übertragen.
* **Art erkennen:** Regeln aus der Konfiguration (Ordner- oder Dateimuster), sonst
  Standard: ``*print*`` → Druck, GIF → gif, MP4/MOV → video, sonst Foto.
* Polling statt Dateisystem-Ereignissen: funktioniert auf Netzlaufwerken,
  USB-Sticks und unter jeder Windows-Version gleich.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

log = logging.getLogger("photobox.watch")

MEDIA_EXT = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".webm"}


def default_kind(path: Path) -> str:
    low = str(path).lower().replace("\\", "/")
    ext = path.suffix.lower()
    if ext == ".gif":
        return "gif"
    if ext in (".mp4", ".mov", ".webm"):
        return "boomerang" if "boomerang" in low else "video"
    if "print" in low or "collage" in low:
        return "print"
    if "original" in low:
        return "original"
    return "photo"


def file_uid(path: Path, size: int, mtime: float) -> str:
    return hashlib.sha1(f"{path}|{size}|{int(mtime)}".encode()).hexdigest()[:16]


class Watcher:
    def __init__(self, state_db: Path, folders: Callable[[], List[dict]], on_file: Callable[..., None],
                 stable_seconds: float = 3.0, interval: float = 2.0):
        self._seen = None
        self.state_db = state_db
        self.folders = folders
        self.on_file = on_file
        self.stable_seconds = stable_seconds
        self.interval = interval
        self._pending: Dict[str, tuple] = {}
        self._stop = threading.Event()
        self.seen_count = 0
        with self._con() as con:
            con.execute("CREATE TABLE IF NOT EXISTS seen (uid TEXT PRIMARY KEY, path TEXT, created REAL)")

    def _con(self):
        return sqlite3.connect(self.state_db, timeout=10)

    def _is_seen(self, uid: str) -> bool:
        if self._seen is None:                       # einmal laden, danach im Speicher prüfen
            with self._con() as con:
                self._seen = {r[0] for r in con.execute("SELECT uid FROM seen").fetchall()}
        return uid in self._seen

    def _mark(self, uid: str, path: Path) -> None:
        with self._con() as con:
            con.execute("INSERT OR IGNORE INTO seen(uid, path, created) VALUES (?,?,?)", (uid, str(path), time.time()))
        if self._seen is not None:
            self._seen.add(uid)

    def start(self) -> None:
        threading.Thread(target=self._loop, name="watch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def scan_once(self) -> int:
        found = 0
        wartend = 0
        now = time.time()
        for folder in self.folders():
            if folder.get("enabled") is False:      # Auftrag pausiert: Ordner bleibt stehen, nichts geht raus
                continue
            base = Path(folder.get("path") or "")
            if not base.is_dir():
                continue
            recursive = folder.get("recursive", True)
            since = float(folder.get("since") or 0)      # nur Dateien nach dem Einrichten
            it = base.rglob("*") if recursive else base.glob("*")
            for p in it:
                if not p.is_file() or p.suffix.lower() not in MEDIA_EXT or p.name.startswith("."):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime < since or not self.wanted(p, st.st_size, folder):
                    continue
                uid = file_uid(p, st.st_size, st.st_mtime)
                key = str(p)
                prev = self._pending.get(key)
                # Zwei Beobachtungen, immer. Bis zur Durchsicht am 19.09.2026
                # stand hier zusätzlich „oder beim ersten Blick, wenn die
                # Änderungszeit alt genug ist" — eine Abkürzung für den ersten
                # Durchlauf über einen vollen Ordner. Sie trifft aber jede
                # Datei, die ihre Zeitstempel von der Quelle behält (robocopy,
                # SD-Karte, Netzlaufwerk mit nachlaufender Uhr): Die wird noch
                # geschrieben, sieht aber alt aus und ging halb hinaus. Weil
                # die Kennung die Größe enthält, landete danach die fertige
                # Datei als zweiter Eintrag daneben.
                #
                # ``stable_seconds: 0`` schaltet die Prüfung weiterhin ganz ab —
                # wer weiß, dass seine Quelle fertig schreibt, bevor die Datei
                # sichtbar wird, soll nicht warten müssen.
                if self.stable_seconds <= 0:
                    bereit = True               # Prüfung ausdrücklich abgeschaltet
                else:
                    bereit = bool(prev and prev[0] == st.st_size and prev[1] == st.st_mtime
                                  and now - prev[2] >= self.stable_seconds)
                if bereit:
                    if not self._is_seen(uid):
                        kind = self.classify(p, folder)
                        genommen = False
                        try:
                            # Der Ordner wandert mit: nur so lässt sich eine Datei
                            # den Zielen zuordnen, die für genau diesen Job gelten.
                            genommen = self.on_file(p, uid, kind, folder) is not False
                        except Exception as e:
                            log.error("Datei %s nicht übernommen: %s", p.name, e)
                        if genommen:
                            self._mark(uid, p)
                            self.seen_count += 1
                            found += 1
                        else:
                            # Nicht abhaken: sonst ist die Datei für immer weg,
                            # auch nach dem Koppeln. Siehe on_file.
                            wartend += 1
                        self._pending.pop(key, None)
                elif not prev or prev[0] != st.st_size or prev[1] != st.st_mtime:
                    # Erster Blick oder die Datei wächst noch: merken und beim
                    # nächsten Durchgang vergleichen.
                    if not self._is_seen(uid):
                        self._pending[key] = (st.st_size, st.st_mtime, now)
        if wartend:
            log.warning("%s Datei(en) warten auf ein Ziel — ist die Box schon gekoppelt?", wartend)
        return found

    @staticmethod
    def wanted(path: Path, size: int, folder: dict) -> bool:
        """Filter je Auftrag: was gehört überhaupt hochgeladen?

        dslrBooth legt neben den Aufnahmen auch Vorschauen und Hilfsdateien ab,
        und Windows streut ``Thumbs.db`` dazwischen. Ohne Filter zahlt man dafür
        Hotspot-Volumen. ``include`` leer heißt: alles außer ``exclude``.
        """
        name = path.name.lower()
        rel = str(path).replace("\\", "/").lower()
        include = [str(x).lower() for x in (folder.get("include") or []) if str(x).strip()]
        exclude = [str(x).lower() for x in (folder.get("exclude") or []) if str(x).strip()]
        if include and not any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in include):
            return False
        if any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in exclude):
            return False
        min_kb = float(folder.get("min_kb") or 0)
        return not (min_kb and size < min_kb * 1024)

    @staticmethod
    def classify(path: Path, folder: dict) -> str:
        forced = folder.get("kind")
        if forced and forced != "auto":
            return forced
        rel = str(path).replace("\\", "/")
        for rule in folder.get("rules") or []:
            if fnmatch.fnmatch(rel.lower(), str(rule.get("match", "")).lower()):
                return rule.get("kind", "photo")
        return default_kind(path)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as e:
                log.error("Scan fehlgeschlagen: %s", e)
            self._stop.wait(self.interval)
