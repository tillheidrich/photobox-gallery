"""Speicher für Originale: lokal auf dem VPS oder ausgelagert aufs NAS.

Idee: Der VPS hält nur, was Gäste sofort brauchen — Vorschaubilder und
Web-Fassungen (typisch < 1 MB je Foto). Originale wandern nach kurzer Zeit
auf ein NAS und werden bei einem Download durch den VPS gestreamt.

Backends (Env ``ORIGINALS_BACKEND``):
* ``local`` (Standard) — alles bleibt im Volume.
* ``sftp``  — z. B. Synology DS220+ über Tailscale/WireGuard.
  ``ORIGINALS_SFTP_HOST``, ``_PORT``, ``_USER``, ``_KEY`` (Pfad) oder ``_PASSWORD``, ``_PATH``.
* ``webdav`` — Synology WebDAV Server. ``ORIGINALS_WEBDAV_URL``, ``_USER``, ``_PASSWORD``.

Ist das NAS gerade nicht erreichbar, bleibt die Galerie voll benutzbar; nur
der Original-Download meldet „gerade nicht verfügbar".
"""
from __future__ import annotations

import logging
import os
import posixpath
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger(__name__)


class RemoteUnavailable(Exception):
    pass


class RemoteMismatch(Exception):
    """The copy on the target is not the file we sent: keep the local one."""


#: Bytes read back from each end of an uploaded file (reasoning below).
SAMPLE_BYTES = 64 * 1024

#: Counters of the last ``offload_pending`` run, for the maintenance report.
#: A module value rather than a return value: callers (and tests) treat
#: ``offload_pending`` as returning a plain count.
last_offload_stats: dict = {}


def require_backup() -> bool:
    """``PHOTOBOX_OFFLOAD_REQUIRE_BACKUP`` (default on): delete an original
    locally only once a backup holds exactly these bytes."""
    return os.environ.get("PHOTOBOX_OFFLOAD_REQUIRE_BACKUP", "on").strip().lower() not in ("off", "0", "no")


def _ends(local: Path) -> tuple:
    size = local.stat().st_size
    with local.open("rb") as f:
        head = f.read(SAMPLE_BYTES)
        f.seek(max(0, size - SAMPLE_BYTES))
        tail = f.read(SAMPLE_BYTES)
    return size, head, tail


# Why a sample and not a checksum: neither the Synology SFTP server nor
# WebDAV offers a hash of a stored file, and reading every original back over
# the NAS's home upload line would double the offload traffic. The transfer
# itself is protected (SSH and TLS authenticate every packet), so what is
# left to catch is a truncated or unreadable file on the target: the size
# catches truncation, and reading the first and the last 64 KiB proves the
# file is there, readable, and ends with what we sent. Over plain-HTTP
# WebDAV, or a server that ignores Range requests, the whole file is read
# back and hashed instead.


def backend_name() -> str:
    return (os.environ.get("ORIGINALS_BACKEND") or "local").strip().lower()


def offload_after_minutes() -> int:
    try:
        return int(os.environ.get("ORIGINALS_OFFLOAD_AFTER_MIN", "30"))
    except ValueError:
        return 30


class SftpStore:
    def __init__(self):
        self.host = os.environ.get("ORIGINALS_SFTP_HOST", "")
        self.port = int(os.environ.get("ORIGINALS_SFTP_PORT", "22"))
        self.user = os.environ.get("ORIGINALS_SFTP_USER", "")
        self.key = os.environ.get("ORIGINALS_SFTP_KEY", "")
        self.password = os.environ.get("ORIGINALS_SFTP_PASSWORD", "")
        self.base = os.environ.get("ORIGINALS_SFTP_PATH", "/photobox")

    def _connect(self):
        import paramiko

        try:
            t = paramiko.Transport((self.host, self.port))
            t.banner_timeout = 15
            pkey = None
            if self.key:
                for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
                    try:
                        pkey = cls.from_private_key_file(self.key)
                        break
                    except Exception:
                        continue
            t.connect(username=self.user, password=self.password or None, pkey=pkey)
            return t, paramiko.SFTPClient.from_transport(t)
        except Exception as e:
            raise RemoteUnavailable(str(e)) from e

    def put(self, local: Path, rel: str) -> str:
        remote = posixpath.join(self.base, rel)
        t, sftp = self._connect()
        try:
            cur = ""
            for part in remote.strip("/").split("/")[:-1]:
                cur += "/" + part
                try:
                    sftp.stat(cur)
                except OSError:
                    sftp.mkdir(cur)
            sftp.put(str(local), remote + ".part")
            if sftp.stat(remote + ".part").st_size != local.stat().st_size:
                raise RemoteUnavailable("Größe stimmt nach Upload nicht")
            try:
                sftp.remove(remote)
            except OSError:
                pass
            sftp.rename(remote + ".part", remote)
            return remote
        finally:
            t.close()

    def verify(self, local: Path, remote: str) -> None:
        """Size, then the first and last block read back from the NAS."""
        size, head, tail = _ends(local)
        t, sftp = self._connect()
        try:
            remote_size = sftp.stat(remote).st_size
            if remote_size != size:
                raise RemoteMismatch(f"size {remote_size} on the target, {size} here")
            f = sftp.open(remote, "rb")
            try:
                if f.read(SAMPLE_BYTES) != head:
                    raise RemoteMismatch("first block differs")
                f.seek(max(0, size - SAMPLE_BYTES))
                if f.read(SAMPLE_BYTES) != tail:
                    raise RemoteMismatch("last block differs")
            finally:
                f.close()
        except OSError as e:
            raise RemoteUnavailable(str(e)) from e
        finally:
            t.close()

    def delete(self, remote: str) -> None:
        t, sftp = self._connect()
        try:
            try:
                sftp.remove(remote)
            except FileNotFoundError:
                pass
        finally:
            t.close()

    def stream(self, remote: str) -> Iterator[bytes]:
        t, sftp = self._connect()
        try:
            with sftp.open(remote, "rb") as f:
                f.prefetch()
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            t.close()


class WebdavStore:
    def __init__(self):
        self.url = os.environ.get("ORIGINALS_WEBDAV_URL", "").rstrip("/")
        self.auth = (os.environ.get("ORIGINALS_WEBDAV_USER", ""), os.environ.get("ORIGINALS_WEBDAV_PASSWORD", ""))

    def put(self, local: Path, rel: str) -> str:
        import requests

        parts = rel.split("/")
        try:
            for i in range(1, len(parts)):
                requests.request("MKCOL", f"{self.url}/{'/'.join(parts[:i])}", auth=self.auth, timeout=15)
            with local.open("rb") as f:
                r = requests.put(f"{self.url}/{rel}", data=f, auth=self.auth, timeout=300)
            if r.status_code not in (200, 201, 204):
                raise RemoteUnavailable(f"WebDAV {r.status_code}")
        except requests.RequestException as e:
            raise RemoteUnavailable(str(e)) from e
        return rel

    def verify(self, local: Path, remote: str) -> None:
        """Size from HEAD, then both ends by Range; whole file if that is not possible."""
        import hashlib

        import requests

        url = f"{self.url}/{remote}"
        size, head, tail = _ends(local)
        try:
            r = requests.request("HEAD", url, auth=self.auth, timeout=30)
            length = r.headers.get("Content-Length")
            if r.status_code != 200:
                raise RemoteUnavailable(f"WebDAV HEAD {r.status_code}")
            if length is not None and int(length) != size:
                raise RemoteMismatch(f"size {length} on the target, {size} here")
            if size == 0:
                return
            if not self.url.startswith("http://"):
                ranges = [(0, min(size, SAMPLE_BYTES) - 1, head), (max(0, size - SAMPLE_BYTES), size - 1, tail)]
                for lo, hi, expected in ranges:
                    part = requests.request("GET", url, auth=self.auth, timeout=60,
                                            headers={"Range": f"bytes={lo}-{hi}"})
                    if part.status_code != 206:
                        break                       # Range ignored: compare the whole file below
                    if part.content != expected:
                        raise RemoteMismatch("first block differs" if lo == 0 else "last block differs")
                else:
                    return
            want = hashlib.sha256()
            with local.open("rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    want.update(block)
            got = hashlib.sha256()
            full = requests.request("GET", url, auth=self.auth, timeout=300, stream=True)
            if full.status_code != 200:
                raise RemoteUnavailable(f"WebDAV GET {full.status_code}")
            for block in full.iter_content(256 * 1024):
                got.update(block)
            if got.hexdigest() != want.hexdigest():
                raise RemoteMismatch("checksum of the copy on the target differs")
        except requests.RequestException as e:
            raise RemoteUnavailable(str(e)) from e

    def delete(self, remote: str) -> None:
        import requests

        try:
            r = requests.delete(f"{self.url}/{remote}", auth=self.auth, timeout=30)
        except requests.RequestException as e:
            raise RemoteUnavailable(str(e)) from e
        if r.status_code not in (200, 204, 404):
            raise RemoteUnavailable(f"WebDAV {r.status_code}")

    def stream(self, remote: str) -> Iterator[bytes]:
        import requests

        try:
            r = requests.get(f"{self.url}/{remote}", auth=self.auth, stream=True, timeout=30)
        except requests.RequestException as e:
            raise RemoteUnavailable(str(e)) from e
        if r.status_code != 200:
            raise RemoteUnavailable(f"WebDAV {r.status_code}")
        yield from r.iter_content(256 * 1024)


def store():
    name = backend_name()
    if name == "sftp":
        return SftpStore()
    if name == "webdav":
        return WebdavStore()
    return None


def queue_remote_delete(remote_path: str) -> None:
    """NAS-Kopie später löschen (der Aufruf darf nie an einem langsamen NAS hängen)."""
    from app import db
    con = db.get_db()
    con.execute("CREATE TABLE IF NOT EXISTS remote_deletions (path TEXT PRIMARY KEY, queued_at TEXT DEFAULT (datetime('now')))")
    con.execute("INSERT OR IGNORE INTO remote_deletions(path) VALUES (?)", (remote_path,))
    con.commit()
    con.close()


def process_remote_deletions(limit: int = 100) -> int:
    from app import db
    s = store()
    if not s:
        return 0
    con = db.get_db()
    con.execute("CREATE TABLE IF NOT EXISTS remote_deletions (path TEXT PRIMARY KEY, queued_at TEXT DEFAULT (datetime('now')))")
    paths = [r[0] for r in con.execute("SELECT path FROM remote_deletions LIMIT ?", (limit,)).fetchall()]
    done = 0
    for p in paths:
        try:
            s.delete(p)
        except RemoteUnavailable as e:
            log.warning("NAS nicht erreichbar, Löschen pausiert: %s", e)
            break
        con.execute("DELETE FROM remote_deletions WHERE path=?", (p,))
        con.commit()
        done += 1
    con.close()
    return done


def offload_pending(limit: int = 50) -> int:
    """Originale, die alt genug sind, aufs NAS schieben und lokal löschen.

    Since 12.11.2026 the local copy only goes after two checks: the copy on
    the target is read back (``verify``: size and both ends), and — unless
    ``PHOTOBOX_OFFLOAD_REQUIRE_BACKUP=off`` — a backup holds these exact
    bytes. Until then the original simply stays here and is tried again on
    the next run; a full disk is visible on the dashboard long before.
    """
    import hashlib

    from app import backup, db, ecosystem, media

    last_offload_stats.clear()
    last_offload_stats.update(moved=0, waiting_for_backup=0, verify_failed=0)
    s = store()
    if not s:
        return 0
    ecosystem.init_schema()
    con = db.get_db()
    rows = con.execute(
        """SELECT m.*, e.slug FROM media m JOIN events e ON e.id=m.event_id
           WHERE m.storage='local' AND m.variant='original'
             AND datetime(m.created_at) <= datetime('now', ?)
             AND m.path_original IS NOT NULL
             AND m.path_original != COALESCE(m.path_webp,'')
           ORDER BY m.id LIMIT ?""",
        (f"-{offload_after_minutes()} minutes", limit)).fetchall()
    con.close()
    process_remote_deletions()
    covered = backup.backed_up_files() if require_backup() else None
    moved = 0
    for r in rows:
        local = (media.data_dir() / r["path_original"])
        if not local.exists():
            continue
        if covered is not None:
            sha = hashlib.sha256(local.read_bytes()).hexdigest()
            if sha not in covered.get(r["path_original"], ()):
                last_offload_stats["waiting_for_backup"] += 1
                continue
        rel = f"{r['slug']}/{(r['created_at'] or '')[:10]}/{local.name}"
        try:
            remote = s.put(local, rel)
            if hasattr(s, "verify"):
                s.verify(local, remote)
        except RemoteMismatch as e:
            log.warning("Copy on the offload target does not match, original kept: %s", e)
            last_offload_stats["verify_failed"] += 1
            continue
        except RemoteUnavailable as e:
            log.warning("NAS nicht erreichbar, Auslagerung pausiert: %s", e)
            break
        ecosystem.mark_remote(r["id"], remote)
        try:
            local.unlink()
        except OSError:
            pass
        moved += 1
    last_offload_stats["moved"] = moved
    return moved
