"""Photobox Uploader (Windows, Polling) – AUTO EVENT MODE

Neu:
- Optionaler Auto-Event-Modus: überwacht ein Root-Verzeichnis (z.B. C:\DSLRBooth\)
  und legt für jeden neu entdeckten Event-Ordner automatisch ein Event auf dem Server an
  (via /api/events/ensure + Admin-Key). Danach werden Dateien hochgeladen.

Start:
  python photobox_uploader.py --config config.json

Manueller Modus (legacy):
- Wenn auto_events=false, wird wie bisher "watch" aus der config genutzt und ein fester upload_token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


# -----------------------------
# Models / Config
# -----------------------------

@dataclass
class WatchItem:
    path: Path
    kind: str  # auto|photo|print|gif|video
    include_ext: Optional[set[str]] = None
    exclude_substrings: Optional[list[str]] = None


@dataclass
class AutoSubfolder:
    subdir: str
    kind: str
    include_ext: set[str]
    exclude_substrings: list[str]


@dataclass
class Config:
    server_url: str
    upload_token: str
    poll_interval_seconds: float
    stable_seconds: float
    recursive: bool
    allowed_ext: set[str]
    watch: list[WatchItem]
    state_db: Path
    log_file: Path

    # Auto-events
    auto_events: bool = False
    admin_api_key: str = ""
    ensure_endpoint: str = "/api/events/ensure"
    event_root: Optional[Path] = None
    event_folder_glob: str = "*"
    event_subfolders: Optional[list[AutoSubfolder]] = None


# -----------------------------
# Helpers
# -----------------------------

def slugify(text: str) -> str:
    import re
    t = text.lower().strip()
    t = re.sub(r"\s+", "-", t)
    t = re.sub(r"[^a-z0-9-]+", "", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t or "event"


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))

    server_url = str(raw.get("server_url", "")).strip().rstrip("/")
    upload_token = str(raw.get("upload_token", "")).strip()

    if not server_url or not server_url.startswith("http"):
        raise ValueError("config: server_url fehlt oder ist ungültig")

    poll_interval_seconds = float(raw.get("poll_interval_seconds", 2))
    stable_seconds = float(raw.get("stable_seconds", 3))
    recursive = bool(raw.get("recursive", True))

    allowed_ext_list = raw.get("allowed_ext") or [".jpg", ".jpeg", ".png", ".gif", ".mp4", ".webp"]
    allowed_ext = {str(x).lower() for x in allowed_ext_list}

    auto_events = bool(raw.get("auto_events", False))
    admin_api_key = str(raw.get("admin_api_key", "")).strip()
    ensure_endpoint = str(raw.get("ensure_endpoint", "/api/events/ensure")).strip() or "/api/events/ensure"
    event_root = raw.get("event_root")
    event_folder_glob = str(raw.get("event_folder_glob", "*")).strip() or "*"

    watch_items: list[WatchItem] = []
    if not auto_events:
        if not upload_token:
            raise ValueError("config: upload_token fehlt (für auto_events=false)")
        for w in raw.get("watch", []):
            p = Path(str(w.get("path", "")))
            kind = str(w.get("kind", "auto")).strip().lower() or "auto"
            if kind not in {"auto", "photo", "print", "gif", "video"}:
                raise ValueError(f"config: watch.kind ungültig: {kind}")

            include_ext = w.get("include_ext")
            include_ext_set = None
            if include_ext:
                include_ext_set = {str(x).lower() for x in include_ext}

            exclude_substrings = w.get("exclude_substrings") or []
            exclude_substrings_l = [str(x).lower() for x in exclude_substrings]

            watch_items.append(WatchItem(path=p, kind=kind, include_ext=include_ext_set, exclude_substrings=exclude_substrings_l))

        if not watch_items:
            raise ValueError("config: watch ist leer (auto_events=false)")

    # auto-events needs admin key + event_root
    auto_subfolders: list[AutoSubfolder] | None = None
    if auto_events:
        if not admin_api_key:
            raise ValueError("config: admin_api_key fehlt (für auto_events=true)")
        if not event_root:
            raise ValueError("config: event_root fehlt (für auto_events=true)")
        event_root_path = Path(str(event_root))

        # Default mapping: Originals + Prints (ohne thumb) + Animated nur mp4
        sub = raw.get("event_subfolders") or [
            {"subdir": "Originals", "kind": "photo", "include_ext": [".jpg", ".jpeg", ".png", ".webp"], "exclude_substrings": [r"\\thumb\\", "/thumb/"]},
            {"subdir": "Prints", "kind": "print", "include_ext": [".jpg", ".jpeg", ".png", ".webp"], "exclude_substrings": [r"\\thumb\\", "/thumb/"]},
            {"subdir": "Animated", "kind": "video", "include_ext": [".mp4"], "exclude_substrings": [r"_mp4.jpg", ".jpg"]},
        ]
        auto_subfolders = []
        for s in sub:
            auto_subfolders.append(
                AutoSubfolder(
                    subdir=str(s.get("subdir", "")).strip(),
                    kind=str(s.get("kind", "auto")).strip().lower(),
                    include_ext={str(x).lower() for x in (s.get("include_ext") or [])},
                    exclude_substrings=[str(x).lower() for x in (s.get("exclude_substrings") or [])],
                )
            )
        upload_token = ""  # not used in auto mode
        watch_items = []   # generated dynamically
    else:
        event_root_path = None

    state_db = Path(str(raw.get("state_db", "uploader_state.sqlite3")))
    log_file = Path(str(raw.get("log_file", "uploader.log")))

    base_dir = path.parent
    if not state_db.is_absolute():
        state_db = base_dir / state_db
    if not log_file.is_absolute():
        log_file = base_dir / log_file

    return Config(
        server_url=server_url,
        upload_token=upload_token,
        poll_interval_seconds=poll_interval_seconds,
        stable_seconds=stable_seconds,
        recursive=recursive,
        allowed_ext=allowed_ext,
        watch=watch_items,
        state_db=state_db,
        log_file=log_file,
        auto_events=auto_events,
        admin_api_key=admin_api_key,
        ensure_endpoint=ensure_endpoint,
        event_root=event_root_path,
        event_folder_glob=event_folder_glob,
        event_subfolders=auto_subfolders,
    )


# -----------------------------
# State DB
# -----------------------------

def init_state_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded (
              abs_path TEXT PRIMARY KEY,
              size_bytes INTEGER NOT NULL,
              mtime REAL NOT NULL,
              sha256 TEXT,
              media_id INTEGER,
              uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_uploaded_mtime ON uploaded(mtime);")

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS event_map (
              event_dir TEXT PRIMARY KEY,
              slug TEXT NOT NULL,
              upload_token TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        con.commit()
    finally:
        con.close()


def was_uploaded(db_path: Path, abs_path: str, size_bytes: int, mtime: float) -> bool:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute("SELECT size_bytes, mtime FROM uploaded WHERE abs_path=?", (abs_path,)).fetchone()
        if not row:
            return False
        return int(row[0]) == int(size_bytes) and float(row[1]) == float(mtime)
    finally:
        con.close()


def mark_uploaded(db_path: Path, abs_path: str, size_bytes: int, mtime: float, sha256: str | None, media_id: int | None) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            """
            INSERT INTO uploaded (abs_path, size_bytes, mtime, sha256, media_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(abs_path) DO UPDATE SET
              size_bytes=excluded.size_bytes,
              mtime=excluded.mtime,
              sha256=excluded.sha256,
              media_id=excluded.media_id,
              uploaded_at=datetime('now');
            """,
            (abs_path, int(size_bytes), float(mtime), sha256, media_id),
        )
        con.commit()
    finally:
        con.close()


def get_event_mapping(db_path: Path, event_dir: Path) -> Optional[tuple[str, str]]:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute("SELECT slug, upload_token FROM event_map WHERE event_dir=?", (str(event_dir),)).fetchone()
        if not row:
            return None
        return str(row[0]), str(row[1])
    finally:
        con.close()


def set_event_mapping(db_path: Path, event_dir: Path, slug: str, upload_token: str) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            """
            INSERT INTO event_map (event_dir, slug, upload_token)
            VALUES (?, ?, ?)
            ON CONFLICT(event_dir) DO UPDATE SET
              slug=excluded.slug,
              upload_token=excluded.upload_token,
              updated_at=datetime('now');
            """,
            (str(event_dir), slug, upload_token),
        )
        con.commit()
    finally:
        con.close()


# -----------------------------
# Upload logic
# -----------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_kind(path: Path, default_kind: str) -> str:
    # If user sets auto, use file extension to infer
    ext = path.suffix.lower()
    if ext in {".mp4"}:
        return "video"
    if ext in {".gif"}:
        return "gif"
    if default_kind in {"photo", "print", "gif", "video"}:
        return default_kind
    return "photo"


def ensure_event(server_url: str, admin_key: str, ensure_endpoint: str, event_name: str, event_slug: str) -> tuple[str, str]:
    url = f"{server_url}{ensure_endpoint}"
    headers = {"X-Admin-Key": admin_key, "Content-Type": "application/json"}
    payload = {"event_name": event_name, "event_slug": event_slug}
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code in (401, 403):
        raise RuntimeError(f"Admin-API auth failed ({r.status_code}). Prüfe admin_api_key und ggf. ADMIN_ALLOWED_IPS.")
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"ensure_event failed: {data}")
    return str(data["slug"]), str(data["upload_token"])


def upload_file(server_url: str, upload_token: str, kind: str, file_path: Path) -> int:
    url = f"{server_url}/api/upload"
    kind = detect_kind(file_path, kind)
    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f, "application/octet-stream")}
        data = {"token": upload_token, "kind": kind}
        r = requests.post(url, data=data, files=files, timeout=120)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(str(payload))
    return int(payload.get("media_id") or 0)


# -----------------------------
# Scanning
# -----------------------------

def is_stable(path: Path, stable_seconds: float) -> bool:
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    age = time.time() - st.st_mtime
    return age >= stable_seconds and st.st_size > 0


def should_include(path: Path, allowed_ext: set[str], include_ext: Optional[set[str]], exclude_substrings: Optional[list[str]]) -> bool:
    p = str(path).lower()
    if exclude_substrings:
        for s in exclude_substrings:
            if s and s in p:
                return False

    ext = path.suffix.lower()
    if ext not in allowed_ext:
        return False
    if include_ext is not None and ext not in include_ext:
        return False
    return True


def iter_files(base: Path, recursive: bool) -> list[Path]:
    if not base.exists():
        return []
    if recursive:
        return [p for p in base.rglob("*") if p.is_file()]
    return [p for p in base.glob("*") if p.is_file()]


def discover_event_dirs(event_root: Path, glob_pat: str) -> list[Path]:
    if not event_root.exists():
        return []
    out = []
    for p in event_root.glob(glob_pat):
        if p.is_dir():
            out.append(p)
    return sorted(out, key=lambda x: x.name.lower())


# -----------------------------
# Main loop
# -----------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Pfad zur config.json")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # logging
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(cfg.log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("uploader")

    init_state_db(cfg.state_db)

    log.info("Uploader startet. server_url=%s auto_events=%s", cfg.server_url, cfg.auto_events)

    while True:
        try:
            if cfg.auto_events:
                assert cfg.event_root is not None and cfg.event_subfolders is not None
                event_dirs = discover_event_dirs(cfg.event_root, cfg.event_folder_glob)
                for event_dir in event_dirs:
                    # Ignore system folders
                    if event_dir.name.lower() in {"thumbs", "thumb", "templates"}:
                        continue

                    mapping = get_event_mapping(cfg.state_db, event_dir)
                    if mapping:
                        slug, token = mapping
                    else:
                        event_name = event_dir.name
                        event_slug = slugify(event_name)
                        slug, token = ensure_event(cfg.server_url, cfg.admin_api_key, cfg.ensure_endpoint, event_name, event_slug)
                        set_event_mapping(cfg.state_db, event_dir, slug, token)
                        log.info("Event angelegt/gefunden: %s -> %s", event_name, slug)

                    # Generate dynamic watch set for this event
                    for sf in cfg.event_subfolders:
                        base = event_dir / sf.subdir
                        files = iter_files(base, cfg.recursive)
                        for fp in files:
                            if not should_include(fp, cfg.allowed_ext, sf.include_ext, sf.exclude_substrings):
                                continue
                            if not is_stable(fp, cfg.stable_seconds):
                                continue
                            st = fp.stat()
                            abs_path = str(fp.resolve())
                            if was_uploaded(cfg.state_db, abs_path, st.st_size, st.st_mtime):
                                continue
                            try:
                                media_id = upload_file(cfg.server_url, token, sf.kind, fp)
                                mark_uploaded(cfg.state_db, abs_path, st.st_size, st.st_mtime, None, media_id)
                                log.info("Uploaded (%s): %s", sf.kind, fp)
                            except Exception as e:
                                log.warning("Upload failed: %s | %s", fp, e)
                                continue

            else:
                # legacy manual mode
                for w in cfg.watch:
                    files = iter_files(w.path, cfg.recursive)
                    for fp in files:
                        if not should_include(fp, cfg.allowed_ext, w.include_ext, w.exclude_substrings):
                            continue
                        if not is_stable(fp, cfg.stable_seconds):
                            continue
                        st = fp.stat()
                        abs_path = str(fp.resolve())
                        if was_uploaded(cfg.state_db, abs_path, st.st_size, st.st_mtime):
                            continue
                        try:
                            media_id = upload_file(cfg.server_url, cfg.upload_token, w.kind, fp)
                            mark_uploaded(cfg.state_db, abs_path, st.st_size, st.st_mtime, None, media_id)
                            log.info("Uploaded (%s): %s", w.kind, fp)
                        except Exception as e:
                            log.warning("Upload failed: %s | %s", fp, e)
                            continue

        except Exception as e:
            log.exception("Loop error: %s", e)

        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
