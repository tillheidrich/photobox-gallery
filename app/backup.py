"""Backups of the gallery's data folder: one archive, checkable, restorable.

    python -m app.backup create [--out DIR] [--no-media]
    python -m app.backup verify ARCHIVE
    python -m app.backup list [--dir DIR]
    python -m app.backup prune --keep N [--dir DIR]
    python -m app.backup restore ARCHIVE --into DIR [--force]

One archive is a plain tar file with three kinds of members:

* ``data/gallery.sqlite3`` — the database, taken with SQLite's online backup
  API. Copying the file of a running gallery can catch a write halfway; the
  backup API hands over a consistent page set even while guests are liking
  photos.
* ``data/<everything else the gallery needs>`` — guest media (``storage/``),
  operator uploads (``assets/``, ``faq/``), the inbox's TLS and SSH host keys
  (``eingang/``) and ``.secret_key``, which signs sessions and share links
  when ``SECRET_KEY`` is not set: without it every share link handed out
  before the restore stops working.
* ``manifest.json`` (last) — version, commit, table list with row counts,
  and size plus SHA-256 of every file, so ``verify`` can prove an archive is
  complete without restoring it.

Left out, and named in the manifest: ``cache/`` (download sizes and
watermarked copies, rebuilt on demand) and earlier backups.

Plain tar, not gzip: photos and videos do not compress, gzip would cost the
small VPS minutes of CPU for a few per cent, and an uncompressed tar lets
``list``/``verify`` seek from header to header instead of inflating
gigabytes to find the manifest.

The archive is confidential. The database holds password hashes, device
keys, the mail account and OAuth tokens. It is written with mode 600 into a
folder with mode 700, and the gallery's ``/data`` route never serves it.

This module must work against the *old* production data folder before an
upgrade, so it never calls ``init_db`` and never writes to the database.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

FORMAT = 1
KIND = "photobox-gallery-backup"
DB_NAME = "gallery.sqlite3"
PREFIX = "photobox-backup-"
ROOT = "data"
MANIFEST = "manifest.json"
STAGING = ".restore-tmp"

#: Top-level folders that never go into an archive, with the reason.
SKIP_DIRS = {
    "cache": "derived files (download sizes, watermarked copies), rebuilt on demand",
    "backups": "earlier backups",
    STAGING: "leftover of an interrupted restore",
}
#: The database goes in through the backup API; its side files would be a
#: second, inconsistent copy of the same data.
DB_SIDE_FILES = (DB_NAME, DB_NAME + "-wal", DB_NAME + "-shm", DB_NAME + "-journal")
#: Guest photos and videos: what ``--no-media`` leaves out. Operator uploads
#: (assets/, faq/) are small and needed for a working restore, so they stay.
MEDIA_DIRS = ("storage",)
#: Never fill the disk to the last byte: the running gallery needs room too.
RESERVE_BYTES = 64 * 1024 * 1024
_CHUNK = 1024 * 1024


class BackupError(Exception):
    """A backup or restore that cannot go ahead, with a message for people."""


# ── Paths ───────────────────────────────────────────────────────────────────

def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data")).resolve()


_env_data_dir = data_dir                   # ``create`` has a parameter of the same name


def backup_dir(data: Optional[Path] = None) -> Path:
    env = os.environ.get("PHOTOBOX_BACKUP_DIR", "").strip()
    return Path(env).resolve() if env else (Path(data or data_dir()).resolve() / "backups")


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _stamp(now: Optional[dt.datetime] = None) -> str:
    return (now or _utcnow()).strftime("%Y%m%dT%H%M%SZ")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


# ── What goes in ────────────────────────────────────────────────────────────

def _collect(data: Path, *, include_media: bool, exclude: List[Path]):
    """Files to archive (relative POSIX path, absolute path, size) and what was left out."""
    files: List[tuple] = []
    skipped: List[Dict[str, str]] = []
    for name, reason in SKIP_DIRS.items():
        if (data / name).exists():
            skipped.append({"path": name, "reason": reason})
    if not include_media:
        for name in MEDIA_DIRS:
            if (data / name).exists():
                skipped.append({"path": name, "reason": "media left out (--no-media)"})
    for current, dirs, names in os.walk(data):
        here = Path(current)
        rel_here = here.relative_to(data)
        keep = []
        for d in sorted(dirs):
            full = here / d
            top = rel_here == Path(".")
            if top and (d in SKIP_DIRS or (not include_media and d in MEDIA_DIRS)):
                continue
            if any(_is_within(full, ex) for ex in exclude):
                skipped.append({"path": full.relative_to(data).as_posix(), "reason": "backup folder"})
                continue
            if full.is_symlink():
                skipped.append({"path": full.relative_to(data).as_posix(), "reason": "symbolic link"})
                continue
            keep.append(d)
        dirs[:] = keep
        for n in sorted(names):
            full = here / n
            rel = full.relative_to(data).as_posix()
            if rel_here == Path(".") and n in DB_SIDE_FILES:
                continue
            if n.endswith(".part"):
                continue
            if full.is_symlink() or not full.is_file():
                skipped.append({"path": rel, "reason": "not a regular file"})
                continue
            try:
                files.append((rel, full, full.stat().st_size))
            except FileNotFoundError:
                continue
    return files, skipped


def media_bytes(data: Optional[Path] = None) -> int:
    """Size of the guest media on this disk — what decides daily vs. weekly."""
    data = Path(data or data_dir())
    total = 0
    for name in MEDIA_DIRS:
        for current, _dirs, names in os.walk(data / name):
            for n in names:
                try:
                    total += (Path(current) / n).stat().st_size
                except OSError:
                    pass
    return total


def _snapshot_db(src: Path, dst: Path) -> None:
    """Consistent copy of a database that may be in use (SQLite backup API)."""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        out = sqlite3.connect(str(dst))
        try:
            con.backup(out, pages=4096, sleep=0.05)
        finally:
            out.close()
    finally:
        con.close()


def db_facts(path: Path, *, check: bool = True) -> Dict[str, Any]:
    """Tables with row counts, schema fingerprint and integrity of one file.

    Only reads. Works on the old schema as well as the new one.
    """
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        schema = "\n".join(r[0] or "" for r in con.execute(
            "SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"))
        facts: Dict[str, Any] = {
            "tables": counts,
            "user_version": con.execute("PRAGMA user_version").fetchone()[0],
            "schema_sha256": hashlib.sha256(schema.encode()).hexdigest(),
        }
        if check:
            rows = [r[0] for r in con.execute("PRAGMA integrity_check").fetchall()]
            facts["integrity"] = "ok" if rows == ["ok"] else "; ".join(rows[:5])
            fk: Dict[str, int] = {}
            for row in con.execute("PRAGMA foreign_key_check").fetchall():
                fk[row[0]] = fk.get(row[0], 0) + 1
            facts["foreign_key_violations"] = fk
        return facts
    finally:
        con.close()


def originals_info(db_file: Optional[Path]) -> Dict[str, Any]:
    """Where offloaded originals live — host and path only, never credentials."""
    from urllib.parse import urlsplit, urlunsplit

    backend = (os.environ.get("ORIGINALS_BACKEND") or "local").strip().lower()
    target = ""
    if backend == "sftp":
        host = os.environ.get("ORIGINALS_SFTP_HOST", "")
        port = os.environ.get("ORIGINALS_SFTP_PORT", "22")
        target = f"sftp://{host}:{port}{os.environ.get('ORIGINALS_SFTP_PATH', '/photobox')}"
    elif backend == "webdav":
        parts = urlsplit(os.environ.get("ORIGINALS_WEBDAV_URL", ""))
        netloc = parts.hostname or ""
        if parts.port:
            netloc += f":{parts.port}"
        target = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    remote = 0
    if db_file and db_file.exists():
        con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(media)")}
            if "storage" in cols:
                remote = con.execute("SELECT COUNT(*) FROM media WHERE storage='remote'").fetchone()[0]
        finally:
            con.close()
    info = {"backend": backend, "target": target, "remote_media": remote}
    if remote:
        info["note"] = ("Originals already moved to the offload target are not in this archive; "
                        "back that target up separately.")
    return info


def _app_identity() -> Dict[str, str]:
    try:
        from app import about
        return {"app_version": about.version(), "app_commit": about.commit() or ""}
    except Exception:                       # noqa: BLE001 — an unknown version does not stop a backup
        return {"app_version": "unknown", "app_commit": ""}


class _HashingReader:
    """Hashes exactly the bytes tarfile copies into the archive."""

    def __init__(self, fh):
        self.fh = fh
        self.h = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        block = self.fh.read(size)
        self.h.update(block)
        return block


# ── Create ──────────────────────────────────────────────────────────────────

def create(*, data_dir: Optional[Path] = None, out_dir: Optional[Path] = None,
           include_media: bool = True, stamp: Optional[str] = None) -> Dict[str, Any]:
    """Write one archive of ``data_dir`` into ``out_dir`` and return what it holds.

    Order matters for a full disk: the space check runs before anything is
    written, the archive grows as ``<name>.tar.part`` and only a complete,
    fsynced file is renamed to ``<name>.tar``. A crash leaves a ``.part``
    that ``list`` ignores, never a truncated archive that looks finished.
    """
    started = time.monotonic()
    data = Path(data_dir).resolve() if data_dir else _env_data_dir()
    if not data.is_dir():
        raise BackupError(f"Data folder {data} does not exist.")
    out = Path(out_dir).resolve() if out_dir else backup_dir(data)
    db_file = data / DB_NAME
    files, skipped = _collect(data, include_media=include_media, exclude=[out])

    db_size = db_file.stat().st_size if db_file.exists() else 0
    payload = db_size + sum(size for _rel, _full, size in files)
    # tar: 512-byte header (PAX adds one more) and padding per member; the
    # database snapshot exists twice for a moment (temp file and archive).
    need = payload + db_size + 1536 * (len(files) + 2) + 1024 * 1024
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(out, 0o700)
    except OSError:
        pass
    free = shutil.disk_usage(out).free
    if free - RESERVE_BYTES < need:
        raise BackupError(
            f"Not enough free space in {out}: the backup needs about {need / 1e6:.0f} MB "
            f"plus {RESERVE_BYTES // (1024 * 1024)} MB reserve, {free / 1e6:.0f} MB are free. "
            f"Nothing was written. Free space, choose another --out, or use --no-media.")

    name = f"{PREFIX}{stamp or _stamp()}{'' if include_media else '-db'}"
    final = out / f"{name}.tar"
    n = 2
    while final.exists():
        final = out / f"{name}-{n}.tar"
        n += 1
    part = final.with_name(final.name + ".part")
    snap = final.with_name(final.name + ".db.part")
    entries: List[Dict[str, Any]] = []
    database: Optional[Dict[str, Any]] = None
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            with tarfile.open(fileobj=fh, mode="w", format=tarfile.PAX_FORMAT) as tar:
                if db_file.exists():
                    _snapshot_db(db_file, snap)
                    database = db_facts(snap)
                    database["file"] = DB_NAME
                    entries.append(_add(tar, snap, DB_NAME))
                    snap.unlink()
                for rel, full, _size in files:
                    try:
                        entries.append(_add(tar, full, rel))
                    except FileNotFoundError:
                        # Retention or a guest's deletion removed it after the walk.
                        skipped.append({"path": rel, "reason": "deleted while the backup ran"})
                manifest = {
                    "kind": KIND, "format": FORMAT,
                    "created_at": _utcnow().isoformat(timespec="seconds") + "Z",
                    **_app_identity(),
                    "media_included": include_media,
                    "confidential": True,
                    "database": database,
                    "originals": originals_info(db_file),
                    "files": entries,
                    "skipped": skipped,
                }
                raw = json.dumps(manifest, indent=1, ensure_ascii=False).encode()
                info = tarfile.TarInfo(MANIFEST)
                info.size, info.mode, info.mtime = len(raw), 0o600, int(time.time())
                tar.addfile(info, io.BytesIO(raw))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(part, final)
    except OSError as e:
        _remove_quietly(part, snap)
        raise BackupError(f"Backup failed and was removed again: {e}") from e
    except BaseException:
        _remove_quietly(part, snap)
        raise
    return {
        "path": str(final), "name": final.name, "size": final.stat().st_size,
        "files": len(entries), "media_included": include_media, "confidential": True,
        "tables": (database or {}).get("tables", {}),
        "integrity": (database or {}).get("integrity"),
        "skipped": skipped, "seconds": round(time.monotonic() - started, 2),
    }


def _add(tar: tarfile.TarFile, full: Path, rel: str) -> Dict[str, Any]:
    with open(full, "rb") as fh:
        info = tar.gettarinfo(fileobj=fh, arcname=f"{ROOT}/{rel}")
        info.mode &= 0o777
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        reader = _HashingReader(fh)
        tar.addfile(info, reader)
    return {"path": rel, "size": info.size, "sha256": reader.h.hexdigest()}


def _remove_quietly(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink()
        except OSError:
            pass


# ── Verify ──────────────────────────────────────────────────────────────────

def read_manifest(archive: Path) -> Dict[str, Any]:
    with tarfile.open(archive) as tar:
        try:
            member = tar.getmember(MANIFEST)
        except KeyError as e:
            raise BackupError(f"{Path(archive).name} has no manifest — not a gallery backup.") from e
        manifest = json.load(tar.extractfile(member))
    if manifest.get("kind") != KIND:
        raise BackupError(f"{Path(archive).name} is not a gallery backup.")
    if int(manifest.get("format") or 0) > FORMAT:
        raise BackupError(f"{Path(archive).name} was written by a newer version (format "
                          f"{manifest.get('format')}); update before restoring it.")
    return manifest


def verify(archive) -> Dict[str, Any]:
    """Check an archive without restoring it: every file present, every
    checksum right, the database readable and intact."""
    archive = Path(archive)
    problems: List[str] = []
    try:
        manifest = read_manifest(archive)
    except (BackupError, tarfile.TarError, OSError, ValueError) as e:
        return {"ok": False, "problems": [str(e)], "files": 0, "bytes": 0}
    expected = {f"{ROOT}/{e['path']}": e for e in manifest.get("files") or []}
    seen = set()
    total = 0
    database = None
    with tarfile.open(archive) as tar:
        for member in tar:
            if member.name == MANIFEST:
                continue
            entry = expected.get(member.name)
            if entry is None:
                problems.append(f"unexpected member {member.name}")
                continue
            seen.add(member.name)
            if not member.isfile():
                problems.append(f"{entry['path']}: not a regular file")
                continue
            h = hashlib.sha256()
            fh = tar.extractfile(member)
            is_db = entry["path"] == DB_NAME
            tmp = tempfile.NamedTemporaryFile(prefix="photobox-verify-", suffix=".sqlite3",
                                              delete=False) if is_db else None
            try:
                for block in iter(lambda: fh.read(_CHUNK), b""):
                    h.update(block)
                    if tmp:
                        tmp.write(block)
                if tmp:
                    tmp.close()
                if member.size != entry["size"]:
                    problems.append(f"{entry['path']}: size {member.size} instead of {entry['size']}")
                elif h.hexdigest() != entry["sha256"]:
                    problems.append(f"{entry['path']}: checksum does not match")
                elif tmp:
                    database = _check_db(Path(tmp.name), manifest.get("database") or {}, problems)
            finally:
                if tmp:
                    tmp.close()
                    _remove_quietly(Path(tmp.name))
            total += member.size
    for name, entry in expected.items():
        if name not in seen:
            problems.append(f"{entry['path']}: missing from the archive")
    return {"ok": not problems, "problems": problems, "files": len(seen), "bytes": total,
            "media_included": manifest.get("media_included"), "created_at": manifest.get("created_at"),
            "app_version": manifest.get("app_version"), "database": database}


def _check_db(path: Path, recorded: Dict[str, Any], problems: List[str]) -> Dict[str, Any]:
    try:
        facts = db_facts(path)
    except sqlite3.Error as e:
        problems.append(f"{DB_NAME}: cannot be opened ({e})")
        return {}
    if facts["integrity"] != "ok":
        problems.append(f"{DB_NAME}: integrity_check reports {facts['integrity']}")
    if recorded.get("tables") and facts["tables"] != recorded["tables"]:
        problems.append(f"{DB_NAME}: row counts differ from the manifest")
    return facts


# ── List and prune ──────────────────────────────────────────────────────────

def list_backups(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Finished archives in ``directory``, newest first (the name carries the time)."""
    directory = Path(directory) if directory else backup_dir()
    if not directory.is_dir():
        return []
    now = _utcnow()
    found = []
    for p in directory.glob(f"{PREFIX}*.tar"):
        stem = p.name[len(PREFIX):-len(".tar")]
        try:
            created = dt.datetime.strptime(stem[:16], "%Y%m%dT%H%M%SZ")
        except ValueError:
            continue
        found.append({"name": p.name, "path": str(p), "size": p.stat().st_size,
                      "created_at": created.isoformat(timespec="seconds") + "Z",
                      "age_hours": round((now - created).total_seconds() / 3600, 1),
                      "media_included": "-db" not in stem[16:]})
    found.sort(key=lambda b: b["name"], reverse=True)
    return found


def prune(directory: Optional[Path] = None, keep: int = 7) -> List[Dict[str, Any]]:
    """Delete all but the ``keep`` newest archives — but never the newest one
    with media: database-only copies alone cannot bring the photos back."""
    keep = max(1, int(keep))
    folder = Path(directory) if directory else backup_dir()
    # A run killed mid-way (deploy, restart) leaves a .part behind; after
    # half a day it is certainly not being written any more.
    if folder.is_dir():
        for part in folder.glob(f"{PREFIX}*.part"):
            try:
                if time.time() - part.stat().st_mtime > 12 * 3600:
                    part.unlink()
            except OSError:
                pass
    backups = list_backups(directory)
    keep_names = {b["name"] for b in backups[:keep]}
    newest_full = next((b for b in backups if b["media_included"]), None)
    if newest_full:
        keep_names.add(newest_full["name"])
    removed = []
    for b in backups:
        if b["name"] in keep_names:
            continue
        side = Path(b["path"] + ".offsite.json")
        offsite = None
        if side.exists():
            try:
                offsite = json.loads(side.read_text()).get("remote")
            except (OSError, ValueError):
                offsite = None
        _remove_quietly(Path(b["path"]), side)
        removed.append({"name": b["name"], "offsite": offsite})
    return removed


# ── The daily run ───────────────────────────────────────────────────────────
#
# Defaults, from what the code says about this gallery's disk:
#
# * Keep 7: one week of daily copies. Retention empties a gallery after 30
#   days by default (app/retention.py); a week is enough to notice a wrong
#   deletion or a failed upgrade, and deleted guest data does not linger in
#   backups for months.
# * Media daily up to 500 MB: the VPS only holds previews and web versions
#   (< 1 MB per photo, app/storage.py) once originals are offloaded, so a
#   few events fit. Seven full copies of that are 3.5 GB, well inside the
#   15 % the dashboard keeps free (storage_warn_percent, 85). Above it, the
#   daily backup is database + settings + operator uploads, and the media go
#   in once a week: with keep 7 the folder then holds about one full copy.
# * The newest backup with media is never pruned (see ``prune``).
DEFAULT_KEEP = 7
DEFAULT_MEDIA_DAILY_MB = 500
DEFAULT_MEDIA_EVERY_DAYS = 7
STALE_HOURS = 48                          # two missed nights
LAST_REPORT_KEY = "backup_last_report"


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def enabled() -> bool:
    return os.environ.get("PHOTOBOX_BACKUP", "on").strip().lower() not in ("off", "0", "no")


def keep_count() -> int:
    return _env_int("PHOTOBOX_BACKUP_KEEP", DEFAULT_KEEP, 1)


def media_daily_max_bytes() -> int:
    return _env_int("PHOTOBOX_BACKUP_MEDIA_DAILY_MB", DEFAULT_MEDIA_DAILY_MB) * 1024 * 1024


def media_every_days() -> int:
    return _env_int("PHOTOBOX_BACKUP_MEDIA_EVERY_DAYS", DEFAULT_MEDIA_EVERY_DAYS, 1)


def offsite_wanted() -> bool:
    """``PHOTOBOX_BACKUP_OFFSITE``: ``auto`` (default) copies to the offload
    target when one is set up; ``off`` never does."""
    return os.environ.get("PHOTOBOX_BACKUP_OFFSITE", "auto").strip().lower() not in ("off", "0", "no")


def _needs_media(out: Path, size: int) -> bool:
    limit = media_daily_max_bytes()
    if limit and size <= limit:              # 0 means: media weekly, whatever their size
        return True
    newest_full = next((b for b in list_backups(out) if b["media_included"]), None)
    # Twelve hours of slack: the daily run drifts by up to ten minutes a day.
    return newest_full is None or newest_full["age_hours"] >= media_every_days() * 24 - 12


def scheduled() -> Dict[str, Any]:
    """One backup as the maintenance run makes it: create, prune, copy off-site.

    A failing copy off the server is reported, never fatal — the local
    backup is the one that counts tonight.
    """
    out = backup_dir()
    report: Dict[str, Any] = {}
    made = create(out_dir=out, include_media=_needs_media(out, media_bytes()))
    report.update(name=made["name"], path=made["path"], size=made["size"],
                  media_included=made["media_included"], integrity=made["integrity"])
    removed = prune(out, keep_count())
    report["removed"] = len(removed)

    from app import storage
    target = storage.store() if offsite_wanted() else None
    report["offsite"] = "off"
    if target is not None:
        for r in removed:
            if r["offsite"]:
                storage.queue_remote_delete(r["offsite"])
        try:
            remote = target.put(Path(made["path"]), f"backups/{made['name']}")
            if hasattr(target, "verify"):
                target.verify(Path(made["path"]), remote)
            side = Path(made["path"] + ".offsite.json")
            side.write_text(json.dumps({"remote": remote, "at": _utcnow().isoformat(timespec="seconds")}))
            os.chmod(side, 0o600)
            report["offsite"] = "ok"
        except Exception as e:              # noqa: BLE001 — NAS asleep, full, wrong key: report it
            report["offsite"] = "failed"
            report["offsite_error"] = str(e)[:200]
    return report


def status() -> Dict[str, Any]:
    """For the dashboard: the newest backup, its age, and the last run's errors."""
    items = list_backups()
    last = items[0] if items else None
    report: Dict[str, Any] = {}
    try:
        from app import db
        report = json.loads(db.get_setting(LAST_REPORT_KEY) or "{}")
    except Exception:                       # noqa: BLE001 — a status never breaks the dashboard
        report = {}
    return {"enabled": enabled(), "last": last, "count": len(items),
            "stale": last is None or last["age_hours"] > STALE_HOURS,
            "error": report.get("error"), "offsite": report.get("offsite"),
            "offsite_error": report.get("offsite_error"),
            "newest_with_media": next((b for b in items if b["media_included"]), None)}


def backed_up_files(directory: Optional[Path] = None) -> Dict[str, set]:
    """Path → checksums of every file in an archive with media.

    For the offload: an original is only deleted locally once a backup
    holds exactly these bytes.
    """
    index: Dict[str, set] = {}
    for b in list_backups(directory):
        if not b["media_included"]:
            continue
        try:
            manifest = read_manifest(Path(b["path"]))
        except (BackupError, tarfile.TarError, OSError, ValueError):
            continue
        for entry in manifest.get("files") or []:
            index.setdefault(entry["path"], set()).add(entry["sha256"])
    return index


# ── Restore ─────────────────────────────────────────────────────────────────

def _safe_rel(name: str) -> Optional[str]:
    """``data/x/y`` → ``x/y``; None for anything that could escape the target."""
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or p.parts[0] != ROOT or len(p.parts) < 2:
        return None
    rest = p.parts[1:]
    if any(part in ("..", "", ".") for part in rest):
        return None
    return "/".join(rest)


def restore(archive, into, *, force: bool = False) -> Dict[str, Any]:
    """Restore ``archive`` into ``into`` and prove the result.

    Refuses a target that holds anything, unless ``force``: then the target
    is backed up first (into its own ``backups/`` folder, which a restore
    never empties), and only then cleared. The archive is verified before a
    single file is written, and extracted into a staging folder that is
    moved into place at the end, so a broken archive leaves the target as
    it was.
    """
    archive = Path(archive).resolve()
    target = Path(into).resolve()
    report: Dict[str, Any] = {"ok": False, "problems": [], "warnings": [], "safety_backup": None}

    present = [p for p in target.iterdir()] if target.is_dir() else []
    if present and not force:
        raise BackupError(f"{target} is not empty. Restore into an empty folder, or pass --force: "
                          f"the folder is then backed up into {target / 'backups'} and replaced.")
    if _is_within(archive, target) and not _is_within(archive, target / "backups"):
        raise BackupError("The archive lies inside the target folder and would be deleted by the restore.")

    checked = verify(archive)
    if not checked["ok"]:
        raise BackupError("The archive failed verification, nothing was restored: "
                          + "; ".join(checked["problems"][:10]))
    manifest = read_manifest(archive)

    target.mkdir(parents=True, exist_ok=True)
    if present:
        real = [p for p in present if p.name != "backups"]
        if real:
            safety = create(data_dir=target, out_dir=target / "backups",
                            stamp=_stamp() + "-before-restore")
            report["safety_backup"] = safety["path"]
        for p in real:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()

    staging = target / STAGING
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    try:
        with tarfile.open(archive) as tar:
            for member in tar:
                if member.name == MANIFEST:
                    continue
                rel = _safe_rel(member.name)
                if rel is None or not member.isfile():
                    report["warnings"].append(f"skipped member {member.name!r}")
                    continue
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                with open(dest, "wb") as fh:
                    shutil.copyfileobj(src, fh, _CHUNK)
                os.chmod(dest, (member.mode & 0o777) or 0o600)
        for entry in staging.iterdir():
            os.replace(entry, target / entry.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    files = manifest.get("files") or []
    for entry in files:
        p = target / entry["path"]
        if not p.is_file():
            report["problems"].append(f"{entry['path']}: missing after restore")
        elif p.stat().st_size != entry["size"] or _sha256_file(p) != entry["sha256"]:
            report["problems"].append(f"{entry['path']}: differs from the manifest after restore")
    db_file = target / DB_NAME
    if db_file.exists():
        facts = db_facts(db_file)
        report["database"] = facts
        if facts["integrity"] != "ok":
            report["problems"].append(f"integrity_check: {facts['integrity']}")
        if facts["foreign_key_violations"]:
            # Present in the source as well (the checksum matched): reported,
            # not fatal — refusing the restore would not make them go away.
            report["warnings"].append("foreign_key_check: " + ", ".join(
                f"{t} {n}" for t, n in sorted(facts["foreign_key_violations"].items())))
    elif any(e["path"] == DB_NAME for e in files):
        report["problems"].append("database missing after restore")
    report.update(ok=not report["problems"], files=len(files), target=str(target),
                  media_included=manifest.get("media_included"),
                  originals=manifest.get("originals"))
    return report


# ── Command line ────────────────────────────────────────────────────────────

CONFIDENTIAL = ("This archive is confidential: it holds password hashes, device keys, the mail "
                "account and OAuth tokens. It was written with mode 600 — keep it that way, store "
                "it encrypted or on a disk only the operator can read, and never where a web "
                "server serves files.")


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.backup",
                                     description="Back up, verify and restore the gallery's data folder.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create", help="write a new archive")
    p.add_argument("--out", help="target folder (default: PHOTOBOX_BACKUP_DIR or DATA_DIR/backups)")
    p.add_argument("--no-media", action="store_true", help="database and settings only, no guest media")
    p.add_argument("--data-dir", help="data folder to back up (default: DATA_DIR)")
    p = sub.add_parser("verify", help="check manifest and checksums of an archive")
    p.add_argument("archive")
    p = sub.add_parser("list", help="show the archives in the backup folder")
    p.add_argument("--dir")
    p = sub.add_parser("prune", help="delete all but the newest N archives")
    p.add_argument("--keep", type=int, required=True)
    p.add_argument("--dir")
    p = sub.add_parser("restore", help="restore an archive into an empty folder")
    p.add_argument("archive")
    p.add_argument("--into", required=True)
    p.add_argument("--force", action="store_true",
                   help="replace a non-empty folder (it is backed up first)")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "create":
            r = create(data_dir=Path(args.data_dir) if args.data_dir else None,
                       out_dir=Path(args.out) if args.out else None, include_media=not args.no_media)
            print(f"Written: {r['path']}")
            print(f"{r['files']} files, {_mb(r['size'])}, media {'included' if r['media_included'] else 'NOT included'}, "
                  f"database integrity: {r['integrity'] or 'no database'}, {r['seconds']} s")
            print(CONFIDENTIAL)
            return 0 if (r["integrity"] in ("ok", None)) else 1
        if args.cmd == "verify":
            r = verify(args.archive)
            print(("OK" if r["ok"] else "FAILED") + f": {r['files']} files, {_mb(r['bytes'])}")
            for problem in r["problems"]:
                print(f"  - {problem}")
            return 0 if r["ok"] else 1
        if args.cmd == "list":
            items = list_backups(Path(args.dir) if args.dir else None)
            if not items:
                print("No backups found.")
            for b in items:
                print(f"{b['name']}  {_mb(b['size']):>10}  {b['age_hours']:>7} h  "
                      f"{'with media' if b['media_included'] else 'database only'}")
            return 0
        if args.cmd == "prune":
            removed = prune(Path(args.dir) if args.dir else None, keep=args.keep)
            print(f"Removed {len(removed)} archive(s)." + "".join(f"\n  - {r['name']}" for r in removed))
            return 0
        if args.cmd == "restore":
            r = restore(args.archive, args.into, force=args.force)
            if r["safety_backup"]:
                print(f"The previous contents were backed up to {r['safety_backup']}")
            print(("Restored" if r["ok"] else "Restore FAILED") + f": {r['files']} files into {r['target']}")
            for w in r["warnings"]:
                print(f"  ! {w}")
            for problem in r["problems"]:
                print(f"  - {problem}")
            if (r.get("originals") or {}).get("remote_media"):
                print(f"  ! {r['originals']['remote_media']} originals live on the offload target "
                      f"({r['originals']['target']}) and are not part of this archive.")
            return 0 if r["ok"] else 1
    except BackupError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":                 # pragma: no cover
    raise SystemExit(main())
