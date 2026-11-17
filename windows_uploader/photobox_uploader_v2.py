"""
Photobox Uploader v2 — robuster Sync-Client für den Fotobox-PC (Windows/macOS/Linux)

Überwacht einen Ordner und lädt neue Fotos in eine Photobox-Gallery-Event.
Robust für den Echtbetrieb:
  • Stable-File-Guard  — lädt eine Datei erst hoch, wenn ihre Größe stabil ist
                          (verhindert das Hochladen halb geschriebener Fotos).
  • Resiliente Retries — Netzwerkfehler/Server-5xx werden unbegrenzt mit Backoff
                          wiederholt; echte Ablehnungen (4xx) werden nach
                          `max_attempts` als „failed" markiert, die Datei bleibt liegen.
  • Lokale Warteschlange (SQLite) mit Status je Datei und Zielspur.
  • Optionales zweites Ziel: Datei-Backup per SFTP/FTPS auf NAS/Kunden-FTP
                          (Struktur <base>/JJJJ-MM/Datei) — Muster aus Klarbild.
  • Auto-Event: legt das Event bei Bedarf über /api/events/ensure an.
  • Lesbares, rotierendes Log.

Usage:
  python photobox_uploader_v2.py --config config.json
  python photobox_uploader_v2.py --config config.json --once   # ein Durchlauf (Test/Cron)
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import posixpath
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:  # pragma: no cover
    print("Fehler: 'requests' fehlt.  Installieren mit:  pip install requests")
    sys.exit(1)


# ───────────────────────── Konfiguration ─────────────────────────

@dataclass
class BackupTarget:
    """Zweites Ziel: Datei-Sicherung per SFTP/FTPS (optional)."""
    enabled: bool = False
    protocol: str = "sftp"          # sftp | ftps
    host: str = ""
    port: int = 22
    username: str = ""
    password: str = ""
    base_path: str = "/"            # Zielordner, darunter JJJJ-MM/Datei

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "BackupTarget":
        d = d or {}
        proto = str(d.get("protocol", "sftp")).lower()
        default_port = 22 if proto == "sftp" else 21
        return cls(
            enabled=bool(d.get("enabled", False)),
            protocol=proto,
            host=str(d.get("host", "")).strip(),
            port=int(d.get("port", default_port)),
            username=str(d.get("username", "")).strip(),
            password=str(d.get("password", "")),
            base_path=str(d.get("base_path", "/")).strip() or "/",
        )


@dataclass
class Config:
    server_url: str
    api_key: str
    watch_folder: Path
    event_slug: Optional[str] = None
    auto_event: bool = False
    event_name_template: str = "Event {date}"
    upload_interval: float = 3.0
    min_stable_seconds: float = 3.0     # Datei muss so lange unverändert sein
    max_attempts: int = 5               # für NICHT-Netzwerkfehler (4xx)
    backoff_base: float = 2.0
    backoff_max: float = 60.0
    supported_extensions: set = field(default_factory=lambda: {".jpg", ".jpeg", ".png", ".gif", ".mp4"})
    delete_after_upload: bool = False
    log_file: Path = Path("uploader.log")
    backup: BackupTarget = field(default_factory=BackupTarget)


def load_config(path: Path) -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    server_url = str(cfg.get("server_url", "")).strip().rstrip("/")
    api_key = str(cfg.get("api_key", "")).strip()
    event_slug = str(cfg.get("event_slug", "")).strip() or None
    auto_event = bool(cfg.get("auto_event", False))
    watch_folder = Path(cfg.get("watch_folder", "."))
    exts = cfg.get("supported_extensions", [".jpg", ".jpeg", ".png", ".gif", ".mp4"])
    log_file = Path(cfg.get("log_file", "uploader.log"))

    if not server_url.startswith("http"):
        raise ValueError("server_url ist ungültig oder fehlt")
    if not api_key:
        raise ValueError("api_key fehlt")
    if not auto_event and not event_slug:
        raise ValueError("event_slug ist Pflicht (oder auto_event aktivieren)")
    if not watch_folder.exists():
        raise ValueError(f"watch_folder existiert nicht: {watch_folder}")
    if not log_file.is_absolute():
        log_file = Path(path).parent / log_file

    return Config(
        server_url=server_url,
        api_key=api_key,
        watch_folder=watch_folder,
        event_slug=event_slug,
        auto_event=auto_event,
        event_name_template=str(cfg.get("event_name_template", "Event {date}")).strip(),
        upload_interval=float(cfg.get("upload_interval", 3)),
        min_stable_seconds=float(cfg.get("min_stable_seconds", 3)),
        max_attempts=int(cfg.get("max_attempts", 5)),
        supported_extensions={str(e).lower() for e in exts},
        delete_after_upload=bool(cfg.get("delete_after_upload", False)),
        log_file=log_file,
        backup=BackupTarget.from_dict(cfg.get("backup")),
    )


# ───────────────────────── Logging ─────────────────────────

def setup_logging(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("photobox_uploader")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ───────────────────────── Zustands-DB ─────────────────────────

class StateDB:
    """Lokale Warteschlange: eine Zeile je Datei, Status je Zielspur."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    path         TEXT PRIMARY KEY,
                    size         INTEGER NOT NULL,
                    mtime        REAL NOT NULL,
                    gallery_done INTEGER NOT NULL DEFAULT 0,
                    backup_done  INTEGER NOT NULL DEFAULT 0,
                    attempts     INTEGER NOT NULL DEFAULT 0,
                    status       TEXT NOT NULL DEFAULT 'pending',  -- pending|done|failed
                    last_error   TEXT,
                    updated_at   TEXT
                )
                """
            )

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def get(self, path: str) -> Optional[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()

    def seen(self, path: str, size: int, mtime: float) -> None:
        """Datei registrieren/aktualisieren. Ändert sich size/mtime, Status zurücksetzen."""
        with self._conn() as c:
            row = c.execute("SELECT size, mtime FROM files WHERE path=?", (path,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO files (path,size,mtime,updated_at) VALUES (?,?,?,?)",
                    (path, size, mtime, _now()),
                )
            elif int(row["size"]) != int(size) or float(row["mtime"]) != float(mtime):
                # Datei wurde neu/weiter geschrieben → als frisch behandeln
                c.execute(
                    "UPDATE files SET size=?,mtime=?,gallery_done=0,backup_done=0,"
                    "attempts=0,status='pending',last_error=NULL,updated_at=? WHERE path=?",
                    (size, mtime, _now(), path),
                )

    def mark(self, path: str, *, gallery_done=None, backup_done=None,
             status=None, last_error=None, bump_attempt=False) -> None:
        sets, vals = [], []
        if gallery_done is not None:
            sets.append("gallery_done=?"); vals.append(1 if gallery_done else 0)
        if backup_done is not None:
            sets.append("backup_done=?"); vals.append(1 if backup_done else 0)
        if status is not None:
            sets.append("status=?"); vals.append(status)
        if last_error is not None:
            sets.append("last_error=?"); vals.append(last_error[:500])
        if bump_attempt:
            sets.append("attempts=attempts+1")
        sets.append("updated_at=?"); vals.append(_now())
        vals.append(path)
        with self._conn() as c:
            c.execute(f"UPDATE files SET {', '.join(sets)} WHERE path=?", vals)

    def counts(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM files GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ───────────────────────── Hilfslogik (rein, testbar) ─────────────────────────

def is_stable(path: Path, min_age: float, now: Optional[float] = None) -> bool:
    """True, wenn die Datei seit mindestens `min_age` Sekunden nicht mehr verändert wurde.

    Verhindert das Hochladen einer Datei, die die Booth-Software noch schreibt.
    """
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size == 0:
        return False
    now = time.time() if now is None else now
    return (now - st.st_mtime) >= min_age


def is_transient_status(status_code: int) -> bool:
    """Netzwerknahe/temporäre Fehler → unbegrenzt wiederholen. 4xx (außer 429) → begrenzt."""
    if status_code == 429:
        return True
    return status_code >= 500


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponentielles Backoff mit Deckel (ohne Jitter, deterministisch testbar)."""
    return min(cap, base * (2 ** max(0, attempt - 1)))


def remote_backup_path(base_path: str, filename: str, when: Optional[datetime] = None) -> str:
    """<base>/JJJJ-MM/Datei — wie Klarbilds NAS-Spiegelung."""
    when = when or datetime.now()
    month = when.strftime("%Y-%m")
    base = "/" + base_path.strip("/") if base_path.strip("/") else ""
    return posixpath.join(base, month, filename)


# ───────────────────────── Ziel 1: Galerie-API ─────────────────────────

class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def ensure_event(logger: logging.Logger, config: Config, session: "requests.Session") -> bool:
    """Auto-Event: Event serverseitig anlegen/auflösen und Slug merken."""
    if config.event_slug:
        return True
    if not config.auto_event:
        return False
    name = config.event_name_template.format(date=datetime.now().strftime("%Y-%m-%d"))
    try:
        r = session.post(f"{config.server_url}/api/events/ensure", json={"name": name},
                         headers={"X-API-Key": config.api_key}, timeout=30)
        if r.status_code == 200 and r.json().get("ok") and r.json().get("slug"):
            config.event_slug = r.json()["slug"]
            logger.info("Auto-Event bereit: '%s' → slug '%s' (%s)",
                        name, config.event_slug,
                        "neu" if r.json().get("created") else "vorhanden")
            return True
        logger.error("events/ensure fehlgeschlagen: HTTP %s %s", r.status_code, r.text[:200])
    except requests.RequestException as e:
        logger.warning("events/ensure nicht erreichbar: %s", e)
    return False


def upload_to_gallery(config: Config, session: "requests.Session", file_path: Path) -> None:
    """Ein Foto in die Galerie laden. Wirft TransientError/PermanentError."""
    try:
        with open(file_path, "rb") as fh:
            r = session.post(
                f"{config.server_url}/api/upload",
                files={"file": (file_path.name, fh)},
                data={"event_slug": config.event_slug},
                headers={"X-API-Key": config.api_key},
                timeout=60,
            )
    except requests.RequestException as e:
        raise TransientError(f"Netzwerkfehler: {e}")
    if r.status_code == 200 and (r.json() or {}).get("ok"):
        return
    if is_transient_status(r.status_code):
        raise TransientError(f"HTTP {r.status_code}: {r.text[:200]}")
    raise PermanentError(f"HTTP {r.status_code}: {r.text[:200]}")


# ───────────────────────── Ziel 2: Datei-Backup (SFTP/FTPS) ─────────────────────────

def backup_file(config: Config, file_path: Path) -> None:
    """Datei aufs NAS/FTP spiegeln (<base>/JJJJ-MM/Datei). Wirft TransientError."""
    t = config.backup
    remote = remote_backup_path(t.base_path, file_path.name)
    try:
        if t.protocol == "sftp":
            _sftp_put(t, file_path, remote)
        elif t.protocol == "ftps":
            _ftps_put(t, file_path, remote)
        else:
            raise PermanentError(f"Unbekanntes Backup-Protokoll: {t.protocol}")
    except (PermanentError,):
        raise
    except Exception as e:  # Verbindungs-/IO-Fehler → transient
        raise TransientError(f"Backup fehlgeschlagen: {e}")


def _sftp_put(t: BackupTarget, local: Path, remote: str) -> None:
    import paramiko  # lazy import, nur wenn Backup genutzt wird
    transport = paramiko.Transport((t.host, t.port))
    transport.banner_timeout = 30
    try:
        transport.connect(username=t.username, password=t.password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.get_channel().settimeout(60)
        _sftp_mkdirs(sftp, posixpath.dirname(remote))
        sftp.put(str(local), remote)
    finally:
        transport.close()


def _sftp_mkdirs(sftp, directory: str) -> None:
    parts, cur = directory.strip("/").split("/"), ""
    for p in parts:
        if not p:
            continue
        cur += "/" + p
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)


def _ftps_put(t: BackupTarget, local: Path, remote: str) -> None:
    from ftplib import FTP_TLS  # lazy import
    ftp = FTP_TLS()
    ftp.connect(t.host, t.port, timeout=60)
    try:
        ftp.login(t.username, t.password)
        ftp.prot_p()
        _ftp_mkdirs(ftp, posixpath.dirname(remote))
        with open(local, "rb") as fh:
            ftp.storbinary(f"STOR {remote}", fh)
    finally:
        ftp.quit()


def _ftp_mkdirs(ftp, directory: str) -> None:
    parts, cur = directory.strip("/").split("/"), ""
    for p in parts:
        if not p:
            continue
        cur += "/" + p
        try:
            ftp.mkd(cur)
        except Exception:
            pass  # existiert bereits


# ───────────────────────── Verarbeitung ─────────────────────────

def process_file(logger, config, session, state: StateDB, file_path: Path) -> None:
    path = str(file_path)
    try:
        st = file_path.stat()
    except OSError:
        return
    state.seen(path, st.st_size, st.st_mtime)
    row = state.get(path)
    if row is None:
        return

    # Fertig?
    need_backup = config.backup.enabled
    if row["gallery_done"] and (row["backup_done"] or not need_backup):
        return
    if row["status"] == "failed":
        return  # dauerhaft fehlgeschlagen, nicht endlos spammen

    # Stable-File-Guard
    if not is_stable(file_path, config.min_stable_seconds):
        logger.debug("Warte auf stabile Datei: %s", file_path.name)
        return

    # Ziel 1: Galerie
    if not row["gallery_done"]:
        try:
            upload_to_gallery(config, session, file_path)
            state.mark(path, gallery_done=True, status="pending")
            logger.info("✓ Galerie: %s", file_path.name)
        except TransientError as e:
            state.mark(path, last_error=str(e), bump_attempt=True)
            logger.warning("↻ Galerie später erneut (%s): %s", file_path.name, e)
            return  # Backup erst nach erfolgreichem Galerie-Upload
        except PermanentError as e:
            state.mark(path, last_error=str(e), bump_attempt=True)
            attempts = (row["attempts"] or 0) + 1
            if attempts >= config.max_attempts:
                state.mark(path, status="failed")
                logger.error("✗ Galerie endgültig fehlgeschlagen (%s): %s", file_path.name, e)
            return

    # Ziel 2: Backup (optional)
    if need_backup and not state.get(path)["backup_done"]:
        try:
            backup_file(config, file_path)
            state.mark(path, backup_done=True)
            logger.info("✓ Backup: %s", file_path.name)
        except TransientError as e:
            state.mark(path, last_error=str(e), bump_attempt=True)
            logger.warning("↻ Backup später erneut (%s): %s", file_path.name, e)
            return
        except PermanentError as e:
            state.mark(path, last_error=str(e), status="failed")
            logger.error("✗ Backup-Konfiguration fehlerhaft: %s", e)
            return

    # Alles erledigt
    fresh = state.get(path)
    if fresh["gallery_done"] and (fresh["backup_done"] or not need_backup):
        state.mark(path, status="done")
        if config.delete_after_upload:
            try:
                file_path.unlink()
                logger.info("gelöscht: %s", file_path.name)
            except OSError as e:
                logger.warning("konnte %s nicht löschen: %s", file_path.name, e)


def scan_once(logger, config, session, state: StateDB) -> None:
    if not config.watch_folder.exists():
        logger.warning("Watch-Ordner fehlt: %s", config.watch_folder)
        return
    for fp in sorted(config.watch_folder.rglob("*")):
        if fp.is_file() and fp.suffix.lower() in config.supported_extensions:
            try:
                process_file(logger, config, session, state, fp)
            except Exception as e:  # pragma: no cover - Schutznetz
                logger.error("Unerwarteter Fehler bei %s: %s", fp.name, e, exc_info=True)


def run(config: Config, once: bool = False) -> None:
    logger = setup_logging(config.log_file)
    state = StateDB(config.log_file.parent / "uploader_state.db")
    session = requests.Session()

    logger.info("=" * 56)
    logger.info("Photobox Uploader v2 gestartet")
    logger.info("Server:   %s", config.server_url)
    logger.info("Event:    %s", config.event_slug or f"(auto: {config.event_name_template})")
    logger.info("Ordner:   %s", config.watch_folder)
    logger.info("Backup:   %s", f"{config.backup.protocol}://{config.backup.host}" if config.backup.enabled else "aus")
    logger.info("=" * 56)

    if config.auto_event and not config.event_slug:
        while not ensure_event(logger, config, session):
            if once:
                logger.error("Auto-Event nicht bereit — Abbruch (--once)")
                return
            logger.warning("Auto-Event noch nicht bereit, neuer Versuch in 10s …")
            time.sleep(10)

    if once:
        scan_once(logger, config, session, state)
        logger.info("Einzeldurchlauf fertig. Status: %s", state.counts())
        return

    consecutive_idle = 0
    while True:
        try:
            scan_once(logger, config, session, state)
        except KeyboardInterrupt:
            logger.info("Beendet durch Benutzer")
            return
        c = state.counts()
        if c != {"done": c.get("done", 0)} and (c.get("pending") or c.get("failed")):
            logger.debug("Status: %s", c)
        time.sleep(config.upload_interval)


def main():
    ap = argparse.ArgumentParser(description="Photobox Gallery – Uploader v2")
    ap.add_argument("--config", default="config.json", help="Pfad zur Konfigurationsdatei")
    ap.add_argument("--once", action="store_true", help="Nur ein Durchlauf, dann beenden")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Konfig nicht gefunden: {cfg_path}")
        print("Beispiel kopieren:  cp config.auto.example.json config.json")
        sys.exit(1)
    try:
        config = load_config(cfg_path)
    except Exception as e:
        print(f"Konfig-Fehler: {e}")
        sys.exit(1)
    try:
        run(config, once=args.once)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
