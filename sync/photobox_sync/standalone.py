"""Photobox Sync als eigenständiges Programm neben dslrBooth & Co.

* beobachtet einen oder mehrere Ordner (z. B. ``Bilder\\dslrBooth``)
* nimmt dslrBooth-Trigger entgegen: ``http://127.0.0.1:8766/trigger`` — damit
  gehören Dateien eines Durchgangs zusammen (Session) und landen sofort statt nach Polling
* lädt über die Outbox zur Photobox Gallery und optional zu NAS (SFTP) oder Picdrop (FTPS)
* meldet sich in der Galerie wie eine Box: Status, Event-Zuordnung, Aufträge
* lokale Oberfläche für Status und Einstellungen: ``http://127.0.0.1:8766``
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

import psutil
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import __version__
from .agent import Agent
from .diagnostics import LogBuffer
from .outbox import Outbox
from .pairing import parse_input
from .sealing import SealKeys
from .targets import HostKeys, is_fingerprint
from .watcher import Watcher
from .winsetup import autostart_state, enable_autostart, disable_autostart

log = logging.getLogger("photobox.sync")
WEB = Path(__file__).parent / "web"

DEFAULT_CONFIG: Dict[str, Any] = {
    "device_name": "Fotobox",
    "server": {"url": "", "api_key": "", "verify_tls": True},
    "targets": [],                 # [{"type":"ftps","name":"picdrop","host":"…","user":"…","password":"…","flat":true}]
    "upload_to_gallery": True,
    # Ein Eintrag je Sync-Auftrag. "targets" leer = an alle Ziele; sonst nur an die
    # genannten (Galerie heißt "gallery"). dslrBooth schreibt Originale, Drucke und
    # GIFs in getrennte Ordner — deshalb mehrere Aufträge mit eigenen Zielen.
    # "enabled": false hält einen Auftrag an, ohne ihn zu löschen.
    # "include"/"exclude" sind Muster (*.jpg, *thumb*), "min_kb" wirft Winzlinge raus.
    # [{"path": "C:/…/dslrBooth", "name": "Originale", "kind": "auto", "recursive": true,
    #   "enabled": true, "targets": ["picdrop"], "exclude": ["thumbs.db"], "min_kb": 20, "since": 0}]
    "folders": [],
    "paused": False,               # Upload anhalten, Warteschlange läuft weiter voll
    "stable_seconds": 3,
    "event_slug_override": "",     # leer = Event aus der Galerie-Zuordnung
    "port": 8766,
    "pin": "",                     # optional: Einstellungen nur mit PIN
}


def _merge_list(old: Optional[list], new: list, key: str) -> list:
    """Bekanntes behalten, Genanntes überschreiben.

    Die Ferne kennt nicht jedes Feld: Passwörter bekommt sie nie zu sehen, und
    eine ältere Oberfläche kennt vielleicht ``layout`` oder ``rules`` nicht.
    Würde sie den Eintrag einfach ersetzen, wäre beides weg. Deshalb wird je
    Eintrag zusammengeführt — leere Werte gelten als „nicht gesetzt", nicht als
    „löschen". Ein Eintrag verschwindet nur, wenn er in der neuen Liste fehlt.
    """
    known = {str(o.get(key)): o for o in (old or []) if o.get(key)}
    out = []
    for item in new or []:
        base = dict(known.get(str(item.get(key)), {}))
        base.update({k: v for k, v in item.items() if v not in ("", None)})
        for k, v in item.items():                 # Haken abwählen muss wirken
            if isinstance(v, bool):
                base[k] = v
        out.append(base)
    return out


def data_dir() -> Path:
    base = os.environ.get("PHOTOBOX_SYNC_DATA")
    if base:
        p = Path(base)
    elif os.name == "nt":
        p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PhotoboxSync"
    else:
        p = Path.home() / ".local" / "share" / "photobox-sync"
    p.mkdir(parents=True, exist_ok=True)
    return p


class SyncApp:
    def __init__(self, base: Optional[Path] = None, start_threads: bool = True):
        self.data = base or data_dir()
        (self.data / "logs").mkdir(parents=True, exist_ok=True)
        self.logbuf = LogBuffer()          # Warnungen und Fehler gehen mit dem Heartbeat in die Galerie
        self._setup_logging()
        self.config_path = self.data / "config.json"
        self.profile_path = self.data / "profile.json"
        self.config = self._load(self.config_path, DEFAULT_CONFIG)
        self.profile = self._load(self.profile_path, {"version": 0, "event": {}})
        self.outbox = Outbox(self.data / "outbox.sqlite")
        self.session: Optional[dict] = None
        self.last_files: list = []
        self.started = time.time()
        # SFTP host keys: trust on first use, then pinned (targets.HostKeys).
        self.host_keys = HostKeys(self.data / "sftp_host_keys.json")
        # Target passwords from the gallery arrive sealed to this key (sealing.py).
        self.seal_keys = SealKeys(self.data)
        self.agent = Agent(outbox=self.outbox, local_config=lambda: self.config, status=self.status,
                           host_keys=self.host_keys,
                           profile_version=lambda: int(self.profile.get("version") or 0),
                           apply_profile=self.apply_profile, report=self.report,
                           logs=lambda: self.logbuf.take(50))
        self.agent.job_handlers["restart_app"] = lambda p: threading.Timer(2, lambda: os._exit(3)).start() or "Neustart"
        # Fernwartung: dieselben Handgriffe wie auf der lokalen Seite
        self.agent.job_handlers["check_targets"] = lambda p: json.dumps(self.check_targets(), ensure_ascii=False)
        self.agent.job_handlers["rescan"] = lambda p: f"{self.watcher.scan_once()} neue Dateien"
        self.agent.job_handlers["set_sync_config"] = self.job_set_config
        self.agent.job_handlers["browse_remote"] = lambda p: json.dumps(
            self.browse(str(p.get("target") or ""), str(p.get("path") or ""), bool(p.get("create"))), ensure_ascii=False)
        self.watcher = Watcher(self.data / "watch.sqlite", folders=lambda: self.config.get("folders") or [],
                               on_file=self.on_file, stable_seconds=float(self.config.get("stable_seconds") or 3))
        if start_threads:
            self.agent.start()
            self.watcher.start()
            threading.Thread(target=self._housekeeping, name="housekeeping", daemon=True).start()

    def _housekeeping(self) -> None:
        """Stündlich: Erledigtes altern lassen, vor vollem Datenträger warnen."""
        while True:
            try:
                removed = self.outbox.prune()
                if removed:
                    log.info("Warteschlange aufgeräumt: %s alte Einträge entfernt", removed)
                free = shutil.disk_usage(self.data).free / 1e9
                if free < 2:
                    log.warning("Nur noch %.1f GB frei — Aufnahmen können verloren gehen", free)
            except Exception as e:
                log.warning("Aufräumen fehlgeschlagen: %s", e)
            time.sleep(3600)

    # ── Infrastruktur ─────────────────────────────────────
    def _setup_logging(self) -> None:
        root = logging.getLogger()
        if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            fh = RotatingFileHandler(self.data / "logs" / "sync.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(fh)
            root.setLevel(logging.INFO)
        if self.logbuf not in root.handlers:
            root.addHandler(self.logbuf)

    @staticmethod
    def _load(path: Path, defaults: dict) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        out = copy.deepcopy(defaults)
        for k, v in data.items():
            if isinstance(out.get(k), dict) and isinstance(v, dict):
                out[k].update(v)
            else:
                out[k] = v
        return out

    def save_config(self, patch: dict) -> dict:
        merged = copy.deepcopy(self.config)
        for k, v in patch.items():
            if k not in DEFAULT_CONFIG:
                continue
            if isinstance(merged.get(k), dict) and isinstance(v, dict):
                merged[k].update(v)
            else:
                merged[k] = v
        for f in merged.get("folders") or []:
            f.setdefault("since", time.time())     # alte Dateien nicht nachträglich hochladen
        self.config = merged
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.config_path)
        return merged

    def apply_profile(self, profile: dict) -> bool:
        if profile == self.profile:
            return False
        self.profile = profile
        self.profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        return True

    @property
    def event_slug(self) -> str:
        return self.config.get("event_slug_override") or (self.profile.get("event") or {}).get("slug", "")

    # ── Dateien & Sessions ────────────────────────────────
    def targets_for(self, folder: Optional[dict]) -> list:
        """Welche Ziele für diesen Ordner gelten.

        Leer heißt: alle. Das ist der einfache Fall und bleibt der Standard.
        Sobald ein Ordner eigene Ziele nennt, gehen seine Dateien nur dorthin —
        dslrBooth legt Originale, Drucke und GIFs in verschiedene Ordner, und die
        gehören selten alle an dieselbe Stelle.
        """
        available = self.agent.target_names()
        wanted = [str(x) for x in ((folder or {}).get("targets") or []) if str(x).strip()]
        if not wanted:
            return available
        unknown = [w for w in wanted if w not in available]
        if unknown:
            log.warning("Ordner %s nennt unbekannte Ziele: %s — die übrigen laufen weiter",
                        (folder or {}).get("path", "?"), ", ".join(unknown))
        return [t for t in available if t in wanted]

    def on_file(self, path: Path, uid: str, kind: str, folder: Optional[dict] = None) -> bool:
        """Eine neue Datei einreihen. ``False`` heißt: kein Ziel, nicht abhaken.

        Gibt es gerade kein Ziel — noch nicht gekoppelt, Galerie-Upload aus,
        Tippfehler im Ordner-Eintrag —, dann darf der Beobachter die Datei
        nicht als erledigt vermerken. Bis zur Durchsicht am 19.09.2026 tat er
        genau das: Die Datei landete in der ``seen``-Tabelle, die nie wieder
        geleert wird, und war nach dem Koppeln unwiederbringlich übersprungen.
        Im Protokoll stand dabei „Neue Datei: IMG_1.jpg" — es sah aus wie Erfolg.
        """
        sess = self.session
        active = sess and time.time() - sess["started"] < 900 and time.time() - sess.get("ended", time.time()) < 120
        session_uid = sess["uid"] if active else uid
        poster = make_poster(path, self.data / "posters") if path.suffix.lower() in (".mp4", ".mov", ".webm") else None
        meta = {"taken_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(path.stat().st_mtime)),
                "source": "watch", "original_name": path.name}
        eingereiht = 0
        for t in self.targets_for(folder):
            if t == "gallery" and not self.config.get("upload_to_gallery", True):
                continue
            self.outbox.add(target=t, uid=uid, session_uid=session_uid, event_slug=self.event_slug, kind=kind,
                            variant="original" if kind == "original" else "web", path=path, poster=poster,
                            meta=meta, priority=2 if kind != "original" else 6)
            eingereiht += 1
        if not eingereiht:
            return False
        self.last_files = ([{"name": path.name, "kind": kind, "at": time.time()}] + self.last_files)[:20]
        log.info("Neue Datei: %s (%s)", path.name, kind)
        return True

    def pair(self, code: str) -> dict:
        """Kopplungscode einlösen: Adresse und Schlüssel in einem Rutsch.

        Der einzige Schritt, der von Hand passieren muss — danach findet der PC
        die Galerie von allein. Der kurze Code holt den Schlüssel bei der
        Galerie ab (braucht also einmal Netz), der lange trägt ihn in sich
        (geht auch ohne). Ein bereits eingetragener Gerätename bleibt stehen;
        der Name aus dem Code ist nur ein Vorschlag für den Erstfall.
        """
        data = parse_input(code, known_url=(self.config.get("server") or {}).get("url", ""))
        if data["mode"] == "fetch":
            # Die Adresse aus der Antwort gilt: Die Galerie kennt ihre eigene
            # öffentliche Adresse besser als das, was jemand abgetippt hat.
            data = {"url": data["url"], **self._fetch_key(data["url"], data["code"])}
        patch = {"server": {**(self.config.get("server") or {}),
                            "url": data["url"], "api_key": data["api_key"]}}
        # „Fotobox" ist der Platzhalter aus der Voreinstellung, kein gewählter Name:
        # dann gewinnt der Name aus der Galerie. Ein eigener Name bleibt stehen.
        eigener = (self.config.get("device_name") or "").strip()
        if data.get("name") and eigener in ("", DEFAULT_CONFIG["device_name"]):
            patch["device_name"] = data["name"]
        self.save_config(patch)
        self.agent.sync_once()
        return {"url": data["url"], "name": self.config.get("device_name") or "",
                "online": self.agent.online, "error": self.agent.last_error}

    @staticmethod
    def _fetch_key(url: str, code: str) -> dict:
        """Kurzcode gegen Schlüssel tauschen — shared with the booth (pairing.fetch_key)."""
        from .pairing import fetch_key

        return fetch_key(url, code)

    # ── Fernwartung ───────────────────────────────────────
    def check_targets(self) -> list:
        """Jedes Ziel einmal anfassen: anmelden, Ordner ansehen, wieder auflegen.

        Vor Ort ist das die Frage, die zählt — nimmt der Server die Zugangsdaten
        an? Ohne diese Prüfung merkt man den Tippfehler erst, wenn die
        Warteschlange voll ist. Läuft lokal und aus der Ferne gleich.
        """
        out = []
        for t in self.agent.targets():
            if t.name == "gallery":
                out.append({"name": "gallery", "ok": self.agent.online,
                            "detail": "erreichbar" if self.agent.online else (self.agent.last_error or "noch kein Kontakt")})
                continue
            check = getattr(t, "check", None)
            if check is None:
                out.append({"name": t.name, "ok": None, "detail": "nicht prüfbar"})
                continue
            try:
                out.append({"name": t.name, "ok": True, "detail": check()})
            except Exception as e:
                out.append({"name": t.name, "ok": False, "detail": str(e) or e.__class__.__name__})
        return out

    def accept_host_key(self, target_name: str, fingerprint: str) -> dict:
        """Pin the key an SFTP target's server showed last, after the operator compared it.

        Only the fingerprint that server presented at the last attempt is
        taken; anything else is refused. Never implied by saving settings.
        """
        t = next((x for x in self.config.get("targets") or []
                  if (x.get("name") or x.get("host")) == target_name and
                  str(x.get("type") or "").lower() in ("sftp", "ssh", "scp")), None)
        if t is None:
            raise ValueError(f"Kein SFTP-Ziel {target_name!r}")
        if not is_fingerprint(fingerprint) or not self.host_keys.accept(t.get("host") or "", int(t.get("port") or 22),
                                                                      fingerprint):
            raise ValueError("Dieser Schlüssel ist nicht der, den der Server zuletzt gezeigt hat.")
        log.info("Neuer Server-Schlüssel für %s angenommen: %s", target_name, fingerprint)
        return {"ok": True, **self.host_keys.report(t.get("host") or "", int(t.get("port") or 22))}

    def browse(self, target_name: str, path: str = "", create: bool = False) -> dict:
        """Ordner am Ziel ansehen — damit niemand einen Pfad raten muss."""
        target = next((t for t in self.agent.targets() if t.name == target_name), None)
        if target is None:
            raise ValueError(f"Ziel {target_name!r} gibt es nicht")
        if create:
            if not hasattr(target, "make_dir"):
                raise ValueError(f"{target_name} kann keine Ordner anlegen")
            target.make_dir(path)
        lister = getattr(target, "listdir", None)
        if lister is None:
            raise ValueError(f"{target_name} lässt sich nicht durchsehen")
        return {"target": target_name, **lister("" if create else path)}

    def job_set_config(self, payload: dict) -> str:
        """Einstellungen aus der Ferne. Nur bekannte Felder, Passwörter bleiben stehen.

        Ohne das Zurückschreiben alter Passwörter würde jede Fernänderung die
        Zugangsdaten leeren — die Ferne bekommt sie ja nie zu sehen.
        """
        done = []
        if "autostart" in (payload or {}):
            ok, detail = enable_autostart() if payload["autostart"] else disable_autostart()
            done.append(("Autostart an" if payload["autostart"] else "Autostart aus") if ok else f"Autostart: {detail}")
        patch = {k: v for k, v in (payload or {}).items() if k in DEFAULT_CONFIG and k != "pin"}
        if not patch:
            return ", ".join(done) or "nichts zu ändern"
        if "targets" in patch:
            patch["targets"] = _merge_list(self.config.get("targets"), self._open_sealed(patch["targets"]), key="name")
        if "folders" in patch:
            patch["folders"] = _merge_list(self.config.get("folders"), patch["folders"], key="path")
        self.save_config(patch)
        return "übernommen: " + ", ".join(sorted(patch) + done)

    def _open_sealed(self, targets: Any) -> list:
        """Target entries from the gallery with their sealed passwords opened here.

        ``password_sealed`` + ``key_id`` become ``password``; an entry without
        either keeps the stored password (``_merge_list``). Nothing of the
        password is logged or answered.
        """
        out = []
        for t in targets if isinstance(targets, list) else []:
            if not isinstance(t, dict):
                continue
            t = dict(t)
            sealed = t.pop("password_sealed", None)
            kid = str(t.pop("key_id", "") or "")
            if sealed:
                t["password"] = self.seal_keys.open(str(sealed), kid)
            out.append(t)
        return out

    def report(self) -> Dict[str, Any]:
        """Alles, was die Fernwartung über dieses Sync-Programm zeigen soll.

        Ohne Zugangsdaten: Server und Benutzer stehen drin, Passwort und
        Schlüsseldatei nie. Wer die Galerie kompromittiert, bekommt hier keine
        Zugänge zu fremden Servern dazu.
        """
        c = self.outbox.counts()
        jobs = []
        for f in self.config.get("folders") or []:
            path = str(f.get("path") or "")
            jobs.append({"name": f.get("name") or "", "path": path, "kind": f.get("kind") or "auto",
                         "enabled": f.get("enabled") is not False, "targets": f.get("targets") or [],
                         "include": f.get("include") or [], "exclude": f.get("exclude") or [],
                         "min_kb": f.get("min_kb") or 0,
                         "exists": bool(path) and Path(path).is_dir()})
        targets = []
        for t in self.config.get("targets") or []:
            targets.append({"name": t.get("name") or t.get("host") or "", "type": t.get("type") or "ftps",
                            "host": t.get("host") or "", "port": t.get("port") or "", "user": t.get("user") or "",
                            "path": t.get("path") or "", "flat": t.get("flat") is not False,
                            "layout": t.get("layout") or "event/date", "removable": bool(t.get("removable")),
                            "only_web": bool(t.get("only_web")), "has_password": bool(t.get("password")),
                            "limit_kbps": t.get("limit_kbps") or 0, "paused": bool(t.get("paused")),
                            "key_file": Path(str(t["key_file"])).name if t.get("key_file") else "",
                            # Fingerprints only (targets.HostKeys), never key material.
                            **(self.host_keys.report(t.get("host") or "", int(t.get("port") or 22))
                               if str(t.get("type") or "").lower() in ("sftp", "ssh", "scp") and t.get("host")
                               else {})})
        du = shutil.disk_usage(self.data)
        return {"kind": "sync", "version": __version__, "device_name": self.config.get("device_name") or "",
                "queue": c, "jobs": jobs, "targets": targets, "target_names": self.agent.target_names(),
                "pending_by_target": self.outbox.pending_by_target(), "paused": bool(self.config.get("paused")),
                "speed_bps": round(self.agent.speed_bps()), "eta_seconds": self.agent.eta_seconds(),
                "history": self.outbox.history(25), "disk_free_gb": round(du.free / 1e9, 1),
                "log_counts": self.logbuf.counts(),
                "upload_to_gallery": self.config.get("upload_to_gallery", True),
                "last_files": self.last_files[:10], "last_upload": self.agent.last_upload,
                "booth_running": _booth_running(), "session": self.session,
                "autostart": autostart_state(), "port": int(self.config.get("port") or 8766),
                "log": self.log_lines(25), **self._sealing_report()}

    def _sealing_report(self) -> Dict[str, Any]:
        """The public key the gallery seals target passwords to; a PC without PyNaCl reports none."""
        try:
            return {"sealing": self.seal_keys.report()}
        except Exception as e:                         # noqa: BLE001 — the rest of the report still goes
            log.warning("Schlüssel für Zielpasswörter nicht verfügbar: %s", type(e).__name__)
            return {}

    def log_lines(self, count: int = 80) -> list:
        """Die letzten Meldungen — ohne die Zugriffszeilen des eigenen Webservers.

        Wer aus der Ferne ins Protokoll schaut, sucht Fehler, nicht jeden Aufruf
        der Status-Seite.
        """
        try:
            lines = (self.data / "logs" / "sync.log").read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return []
        return [ln for ln in lines if ' - - [' not in ln][-count:]

    def trigger(self, event_type: str, param1: str = "", param2: str = "") -> dict:
        """dslrBooth-Trigger. Dokumentierte Ereignisse: session_start, countdown_start, capture_start,
        file_download, processing_start, sharing_screen, printing, file_upload, session_end."""
        et = (event_type or "").lower()
        if et == "session_start":
            import secrets
            self.session = {"uid": secrets.token_hex(5), "started": time.time()}
        elif et == "session_end":
            if self.session:
                self.session["ended"] = time.time()     # späte Dateien (Druck, GIF) gehören noch dazu
        elif et in ("processing_start", "file_upload") and param1:
            p = Path(param1)
            if p.is_file():
                self.watcher.scan_once()          # sofort statt beim nächsten Polling
        return {"ok": True, "session": self.session}

    def status(self) -> Dict[str, Any]:
        du = shutil.disk_usage(self.data)
        return {
            "agent_version": f"photobox-sync {__version__}", "booth_running": _booth_running(),
            "booth_version": "", "disk_free_gb": round(du.free / 1e9, 2), "disk_total_gb": round(du.total / 1e9, 2),
            "last_photo_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.last_files[0]["at"])) if self.last_files else "",
            "cpu_percent": psutil.cpu_percent(interval=None), "ram_percent": psutil.virtual_memory().percent,
            "uptime_min": int((time.time() - psutil.boot_time()) / 60), "profile_version": int(self.profile.get("version") or 0),
        }


def _booth_running() -> bool:
    names = {"dslrbooth.exe", "lumabooth.exe", "dslrbooth", "photoboothsoftware.exe"}
    try:
        return any((p.info.get("name") or "").lower() in names for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def make_poster(video: Path, folder: Path) -> Optional[Path]:
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg  # type: ignore
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / (video.stem + "_poster.jpg")
    try:
        subprocess.run([exe, "-y", "-loglevel", "error", "-ss", "0.3", "-i", str(video), "-frames:v", "1", str(out)],
                       check=True, timeout=30, creationflags=0x08000000 if os.name == "nt" else 0)
        return out if out.exists() else None
    except Exception:
        return None


def create_app(sync: Optional[SyncApp] = None) -> FastAPI:
    s = sync or SyncApp()
    app = FastAPI(title="Photobox Sync", version=__version__, docs_url=None, redoc_url=None)
    app.state.sync = s

    import hmac

    def check_pin(request: Request):
        pin = str(s.config.get("pin") or "")
        if pin and not hmac.compare_digest(request.headers.get("X-Pin", ""), pin):
            time.sleep(0.5)
            raise HTTPException(401, "PIN erforderlich")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Schutz gegen DNS-Rebinding: nur Aufrufe an 127.0.0.1/localhost, keine fremden Seiten mit Schreibzugriff
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost", "::1", "[::1]", "testserver"):
            return JSONResponse({"detail": "nur lokal"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method != "GET" and origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
            return JSONResponse({"detail": "fremde Seite"}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    @app.api_route("/trigger", methods=["GET", "POST"])
    async def trigger(request: Request):
        q = dict(request.query_params)
        if request.method == "POST":
            try:
                q.update(await request.json())
            except Exception:
                pass
        return s.trigger(q.get("event_type", ""), q.get("param1", ""), q.get("param2", ""))

    @app.get("/api/status")
    def status():
        return {"version": __version__, "online": s.agent.online, "last_sync": s.agent.last_sync,
                "error": s.agent.last_error, "queue": s.outbox.counts(), "last_upload": s.agent.last_upload,
                "event": (s.profile.get("event") or {}).get("name") or s.event_slug, "event_slug": s.event_slug,
                "folders": [{"path": f.get("path"), "name": f.get("name") or "", "enabled": f.get("enabled") is not False,
                             "targets": f.get("targets") or [], "exists": Path(f.get("path") or "").is_dir()}
                            for f in s.config.get("folders") or []],
                "targets": s.agent.target_names(), "session": s.session, "last_files": s.last_files,
                "booth_running": _booth_running(), "pin_required": bool(s.config.get("pin")),
                "autostart": autostart_state(), "paused": bool(s.config.get("paused")),
                "pending_by_target": s.outbox.pending_by_target(),
                "speed_bps": round(s.agent.speed_bps()), "eta_seconds": s.agent.eta_seconds(),
                "disk_free_gb": round(shutil.disk_usage(s.data).free / 1e9, 1)}

    @app.get("/api/history")
    def history(request: Request, limit: int = 50):
        check_pin(request)
        return {"items": s.outbox.history(min(int(limit), 200))}

    @app.get("/api/config")
    def get_config(request: Request):
        check_pin(request)
        c = json.loads(json.dumps(s.config))
        if c["server"].get("api_key"):
            c["server"]["api_key"] = "••••" + c["server"]["api_key"][-4:]
        for t in c.get("targets") or []:
            if t.get("password"):
                t["password"] = "••••"
        return c

    @app.post("/api/config")
    def set_config(request: Request, body: dict = Body(...)):
        check_pin(request)
        srv = body.get("server")
        if isinstance(srv, dict) and str(srv.get("api_key", "")).startswith("••••"):
            srv.pop("api_key")
        old = {(t.get("name") or t.get("host")): t for t in s.config.get("targets") or []}
        for t in body.get("targets") or []:
            if t.get("password") == "••••":
                t["password"] = old.get(t.get("name") or t.get("host"), {}).get("password", "")
        s.save_config(body)
        threading.Thread(target=s.agent.sync_once, daemon=True).start()
        return {"ok": True}

    @app.get("/api/check_targets")
    def check_targets(request: Request):
        check_pin(request)
        return {"targets": s.check_targets()}

    @app.post("/api/targets/host-key")
    def accept_host_key(request: Request, body: dict = Body(...)):
        """Accept the new key an SFTP server showed — the exact fingerprint compared by a person."""
        check_pin(request)
        try:
            return s.accept_host_key(str(body.get("target") or ""), str(body.get("fingerprint") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/pair")
    def pair(request: Request, body: dict = Body(...)):
        check_pin(request)
        try:
            return s.pair(str(body.get("code") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/browse")
    def browse(request: Request, target: str, path: str = "", create: str = ""):
        check_pin(request)
        try:
            return s.browse(target, path, create == "1")
        except Exception as e:
            raise HTTPException(400, str(e) or e.__class__.__name__)

    @app.post("/api/action/{name}")
    def action(request: Request, name: str):
        check_pin(request)
        if name == "sync_now":
            s.outbox.reset_backoff()
            return {"ok": s.agent.sync_once()}
        if name == "retry_failed":
            return {"ok": True, "count": s.outbox.retry_failed()}
        if name == "rescan":
            return {"ok": True, "found": s.watcher.scan_once()}
        if name in ("pause", "resume"):
            s.save_config({"paused": name == "pause"})
            if name == "resume":
                s.outbox.reset_backoff()
            return {"ok": True, "paused": s.config.get("paused", False)}
        if name == "prune":
            return {"ok": True, "removed": s.outbox.prune()}
        if name in ("autostart_on", "autostart_off"):
            ok, detail = enable_autostart() if name.endswith("_on") else disable_autostart()
            return {"ok": ok, "detail": detail, "autostart": autostart_state()}
        raise HTTPException(404)

    @app.get("/api/log")
    def get_log(request: Request):
        check_pin(request)
        return {"lines": s.log_lines(80)}

    return app
