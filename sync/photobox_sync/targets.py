"""Upload-Ziele.

* ``GalleryTarget`` — die eigene Photobox Gallery (API v2). Kennt Durchgänge,
  Posterbilder, Share-Kennungen. Das ist der Standard.
* ``SftpTarget``    — beliebiger SSH-Server, z. B. das NAS zuhause.
* ``FtpsTarget``    — FTP über TLS, z. B. Picdrop „Live Push".
* ``FolderTarget``  — ein Ordner auf diesem Rechner oder auf einem USB-Stick.
  Damit liegen die Daten dreifach: Arbeitsordner der Box, zweite Ablage, Stick —
  zusätzlich zum Sync mit Galerie/NAS/Picdrop. Fehlt der Stick, wartet die
  Warteschlange, bis er wieder steckt.

Jedes Ziel wirft ``TemporaryError`` (später erneut) oder ``PermanentError``
(Konfiguration falsch — nach mehreren Versuchen aufgeben).
"""
from __future__ import annotations

import ftplib
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import ssl
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx


class TemporaryError(Exception):
    pass


class PermanentError(Exception):
    pass


class ThrottledReader:
    """Datei lesen, aber nicht schneller als erlaubt.

    Am Eventabend hängt der Upload oft an einem Handy-Hotspot, über den
    gleichzeitig jemand Musik streamt. Eine Bremse je Ziel macht aus „alles
    steht" ein „läuft halt nebenher". Gebremst wird beim Lesen, deshalb wirkt
    es für FTP, SFTP und die Galerie gleichermaßen — kein Protokoll muss davon
    wissen.
    """

    def __init__(self, fp, kbps: float, chunk: int = 32 * 1024):
        self.fp = fp
        self.rate = max(1.0, float(kbps)) * 1024
        self.chunk = chunk
        self.started = time.monotonic()
        self.sent = 0

    def read(self, size: int = -1) -> bytes:
        want = self.chunk if size is None or size < 0 else min(size, self.chunk)
        data = self.fp.read(want)
        if not data:
            return data
        self.sent += len(data)
        due = self.started + self.sent / self.rate
        delay = due - time.monotonic()
        if delay > 0:
            time.sleep(min(delay, 2.0))
        return data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fp.close()
        return False

    def __getattr__(self, name):
        return getattr(self.fp, name)


def open_source(item: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None):
    """Die Datei zum Hochladen — mit Bremse, wenn das Ziel eine hat."""
    fp = open(item["path"], "rb")
    kbps = float((cfg or {}).get("limit_kbps") or 0)
    return ThrottledReader(fp, kbps) if kbps > 0 else fp


def verify_size(sent: int, remote: Optional[int], where: str) -> None:
    """Angekommen heißt: vollständig angekommen.

    Ein abgebrochener FTP-Upload hinterlässt eine halbe Datei und meldet keinen
    Fehler. Wenn der Server eine Größe nennt und sie nicht passt, gilt der
    Upload als nicht erfolgt und wird wiederholt.
    """
    if remote is None or remote == sent:
        return
    raise TemporaryError(f"{where}: {remote} statt {sent} Bytes angekommen")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _gallery_json(r: "httpx.Response") -> Dict[str, Any]:
    """The gallery's JSON answer — or TemporaryError when somebody else answered.

    Venue and hotel Wi-Fi with a login page (captive portal) answer every
    request themselves: a redirect, or their own page with 200. That is the
    network's trouble, not the file's; counted against the file, every
    picture queued while the portal stood was given up after eight tries.
    """
    if 300 <= r.status_code < 400:
        raise TemporaryError(f"Umleitung {r.status_code} statt Galerie (WLAN-Anmeldeseite?)")
    try:
        answer = r.json()
    except ValueError:
        raise TemporaryError("Antwort ist nicht von der Galerie (WLAN-Anmeldeseite?)") from None
    if not isinstance(answer, dict):
        raise TemporaryError("Antwort ist nicht von der Galerie")
    return answer


class GalleryTarget:
    name = "gallery"

    def __init__(self, url: str, api_key: str, verify_tls: bool = True, timeout: float = 60.0,
                 limit_kbps: float = 0):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.cfg = {"limit_kbps": limit_kbps}      # dieselbe Bremse wie bei den anderen Zielen
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=8.0), verify=verify_tls,
                                   headers={"X-API-Key": api_key, "User-Agent": "photobox-booth"})

    def upload(self, item: Dict[str, Any]) -> Dict[str, Any]:
        path = Path(item["path"])
        if not path.exists():
            raise PermanentError(f"Datei fehlt: {path}")
        data = {
            "uid": item["uid"], "session_uid": item["session_uid"], "event_slug": item["event_slug"],
            "kind": item["kind"], "variant": item["variant"], "sha256": sha256_file(path),
            "meta": item.get("meta") or "{}",
        }
        files = {"file": (path.name, open_source(item, getattr(self, "cfg", None)))}
        poster = item.get("poster")
        if poster and Path(poster).exists():
            files["poster"] = (Path(poster).name, Path(poster).open("rb"))
        try:
            r = self.client.post(f"{self.url}/api/v2/booth/media", data=data, files=files)
        except httpx.TransportError as e:
            raise TemporaryError(f"Netz: {e}") from e
        finally:
            for _, f in files.values():
                f.close()
        if r.status_code >= 500 or r.status_code in (401, 403, 408, 429):
            # 401/403: the key is wrong or was changed. That is the target's
            # trouble, not this file's — wait, do not write the file off.
            raise TemporaryError(f"Server {r.status_code}")
        if r.status_code >= 400:
            raise PermanentError(f"{r.status_code}: {r.text[:200]}")
        return _gallery_json(r)

    def sync(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            r = self.client.post(f"{self.url}/api/v2/booth/sync", json=payload, timeout=15)
        except httpx.TransportError as e:
            raise TemporaryError(str(e)) from e
        if r.status_code in (401, 403):
            raise PermanentError("API-Schlüssel ungültig")
        if r.status_code >= 400:
            raise TemporaryError(f"Server {r.status_code}")
        return _gallery_json(r)

    def jobs(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """The small job door during live remote control: results in, jobs out (see Agent._fast_loop)."""
        try:
            r = self.client.post(f"{self.url}/api/v2/booth/jobs", json=payload, timeout=10)
        except httpx.TransportError as e:
            raise TemporaryError(str(e)) from e
        if r.status_code in (401, 403):
            raise PermanentError("API-Schlüssel ungültig")
        if r.status_code >= 400:
            raise TemporaryError(f"Server {r.status_code}")
        return _gallery_json(r)

    def going_down(self, reason: str, expect_back: bool, timeout: float = 1.5) -> bool:
        """Tell the gallery this box ends on purpose. Short timeouts on every
        phase; the caller caps the whole call as well (``Agent.goodbye``)."""
        try:
            r = self.client.post(f"{self.url}/api/v2/booth/going-down",
                                 json={"reason": reason, "expect_back": bool(expect_back)},
                                 timeout=httpx.Timeout(timeout, connect=timeout))
        except httpx.HTTPError:
            return False
        return r.status_code == 200

    def download(self, url: str, target: Path) -> None:
        full = url if url.startswith("http") else f"{self.url}{url}"
        tmp = target.with_suffix(target.suffix + ".part")
        with self.client.stream("GET", full, timeout=60) as r:
            if r.status_code != 200:
                raise TemporaryError(f"Asset {r.status_code}")
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        tmp.replace(target)


# ── File names "event name, date and time" (15.11.2026) ─────────────
# A copy of app/dateinamen.py ``sicherer_eventname`` / ``event_datetime_name``:
# the booth names files offline and does not import the gallery. A gallery
# test (tests/test_file_naming_event_datetime.py) keeps both copies equal.
_EVENT_MAX = 64
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def sicherer_eventname(name: str) -> str:
    """The event name as a file-name part: umlauts written out, [A-Za-z0-9_-] only."""
    import re
    import unicodedata

    wert = str(name or "")
    for zeichen, ersatz in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"),
                            ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ẞ", "SS")):
        wert = wert.replace(zeichen, ersatz)
    wert = unicodedata.normalize("NFKD", wert).encode("ascii", "ignore").decode()
    wert = re.sub(r"\s+", "-", wert)
    wert = re.sub(r"[^A-Za-z0-9_-]+", "-", wert)
    wert = re.sub(r"-{2,}", "-", wert)
    wert = re.sub(r"_{2,}", "_", wert)
    wert = re.sub(r"[-_]{2,}", lambda m: m.group(0)[0], wert)
    wert = wert.strip("-_")[:_EVENT_MAX].strip("-_")
    if not wert:
        return "Event"
    if wert.upper() in _RESERVED:
        return f"{wert}-Event"
    return wert


def event_datetime_name(event_name: str, when, endung: str, n: int = 1) -> str:
    """``<Event>_<YYYY-MM-DD>_<HH-MM-SS>[_n].<ext>``."""
    import re

    stem = f"{sicherer_eventname(event_name)}_{when.strftime('%Y-%m-%d_%H-%M-%S')}"
    if int(n) > 1:
        stem += f"_{int(n)}"
    endung = re.sub(r"[^a-z0-9]", "", str(endung or "").lower())[:5]
    return f"{stem}.{endung}" if endung else stem


def _place_in_run(item: Dict[str, Any], meta: Dict[str, Any]) -> int:
    """Where this file stands among the files of its run that share its second.

    Fixed by the id alone, so a retry or a second target gives the same
    number: the run's result is 1, its single pictures ``.fN`` follow
    (N+1), then the camera originals ``.N`` (after the singles), the
    boomerang film after the GIF's film. GIF and MP4 have extensions of
    their own and need no number.
    """
    uid = str(item.get("uid") or "")
    _, dot, tail = uid.partition(".")
    if not dot or tail in ("gif", "mp4"):
        return 1
    if tail == "boom":
        return 2
    try:
        singles = max(0, int(meta.get("n_singles") or 0))
    except (TypeError, ValueError):
        singles = 0
    if tail.startswith("f") and tail[1:].isdigit():
        return int(tail[1:]) + 1
    if tail.isdigit():
        return singles + int(tail) + 1
    return 1


def file_name(item: Dict[str, Any], flat: bool = False) -> str:
    """Dateiname am Ziel. Die Kennung des Durchgangs steht vorn, sonst würden
    ``result.jpg`` aus verschiedenen Durchgängen einander überschreiben.

    With the event's rule ``event_datetime`` (meta ``file_naming``, set at
    the booth from the event data) the name is the event's name, the date
    and the time of the run. ``flat``: web and original copies land in one
    folder, so the web copy of a picture is marked ``-web``.
    """
    name = Path(item["path"]).name
    meta = _meta(item)
    if str(meta.get("file_naming") or "") == "event_datetime":
        import datetime as dt
        try:
            when = dt.datetime.fromisoformat(str(meta.get("taken_at") or "")[:19])
        except ValueError:
            when = None
        if when is not None:
            endung = Path(name).suffix.lstrip(".")
            neu = event_datetime_name(str(meta.get("event_name") or ""), when, endung,
                                      _place_in_run(item, meta))
            if flat and item.get("variant") == "web" and endung.lower() in ("jpg", "jpeg", "png", "webp"):
                stem, _, ext = neu.rpartition(".")
                neu = f"{stem}-web.{ext}"
            if str(item.get("uid") or "").endswith(".print"):
                # The print sheet shares the second and the extension of the
                # run's result; without its own mark it would replace it.
                stem, _, ext = neu.rpartition(".")
                neu = f"{stem}-druck.{ext}"
            return neu
    uid = str(item.get("uid") or "").strip()
    return f"{uid}_{name}" if uid and not name.startswith(uid) else name


#: The kinds of target the second target card ("Zweites Ziel im Netz") can be.
NETWORK_TYPES = ("ftps", "ftp", "sftp", "ssh", "scp")
#: Names that all mean SFTP. One set for building, pinning, reporting and
#: accepting a server key; forms save them as "sftp" (normal_type).
SFTP_ALIASES = frozenset({"sftp", "ssh", "scp"})


def is_sftp(kind: Any) -> bool:
    return str(kind or "").strip().lower() in SFTP_ALIASES


def normal_type(kind: Any) -> str:
    """The stored type: ssh/scp become sftp, the rest lower case."""
    k = str(kind or "").strip().lower()
    return "sftp" if k in SFTP_ALIASES else k


def skips_variant(cfg: Dict[str, Any], item: Dict[str, Any]) -> bool:
    """The second target's own two choices (17.11.2026), checked per file.

    ``originals`` (default on): the full-size result and the camera
    originals; off, only the web copies and films go. ``print_sheets``
    (default off): the sheet as it went to the printer. A file the booth
    queued for a choice that is off now is skipped, not failed.
    """
    variant = str(item.get("variant") or "")
    if variant == "original" and cfg.get("originals") is False:
        return True
    if variant == "print" and not cfg.get("print_sheets"):
        return True
    return False


def _meta(item: Dict[str, Any]) -> Dict[str, Any]:
    """Die Zusatzangaben einer Datei, egal ob als Text oder als Objekt übergeben.

    Aus der Outbox kommen sie als JSON-Text. Wer ein Ziel direkt aufruft (Test,
    einmaliger Nachschub von Hand), hat meist schon ein Objekt — das soll kein
    Absturz sein.
    """
    meta = item.get("meta") or {}
    if isinstance(meta, (str, bytes, bytearray)):
        try:
            return json.loads(meta)
        except ValueError:
            return {}
    return meta if isinstance(meta, dict) else {}


def remote_parts(cfg: Dict[str, Any], item: Dict[str, Any], default_path: str = "") -> tuple:
    """(absolute, folders, file name) of one upload on a network target (zielordner.py).

    The folder field with its placeholders filled in for this file; empty is
    the login's start folder, a leading ``/`` the top of the server. ``flat``
    puts every file into that folder; otherwise below it one folder per
    event and day, split into ``fotos`` and ``originale``.
    """
    from . import zielordner

    meta = _meta(item)
    flat = bool(cfg.get("flat", True))
    absolute, folders = zielordner.parts(cfg.get("path", default_path), flat,
                                         slug=item.get("event_slug") or "", event_name=meta.get("event_name") or "",
                                         when=meta.get("taken_at") or None,
                                         original=item.get("variant") == "original")
    return absolute, folders, file_name(item, flat=flat)


#: Written and deleted again by "Prüfen": proof that uploads can land in the folder.
CHECK_FILE = ".photobox-pruefung.txt"


def fixed_folder(cfg: Dict[str, Any]) -> tuple:
    """(absolute, folders) of the folder field up to its first placeholder — what a check can reach."""
    from . import zielordner

    raw = zielordner.check(cfg.get("path") or "")
    absolute = raw.startswith("/")
    out = []
    for p in [p for p in raw.split("/") if p]:
        if "{" in p:
            break
        out.append(p)
    return absolute, out


# ── Which password belongs to which server (17.11.2026) ──────────────
#
# A stored password is bound to the endpoint it was typed for: type, host,
# port and user. A target whose endpoint no longer matches gets no password
# and sends nothing until one is entered for the new endpoint — whoever
# changed the server (the gallery, an assistant, a hand-edited local.json)
# cannot make the booth hand the old password to another machine.

_DEFAULT_PORTS = {"ftps": 21, "ftp": 21, "sftp": 22, "ssh": 22, "scp": 22}


def endpoint_key(cfg: Dict[str, Any]) -> str:
    """``type|host|port|user`` with the usual port filled in; the identity a password is bound to."""
    kind = normal_type(cfg.get("type"))
    try:
        port = int(cfg.get("port") or 0) or _DEFAULT_PORTS.get(kind, 0)
    except (TypeError, ValueError):
        port = _DEFAULT_PORTS.get(kind, 0)
    return f"{kind}|{str(cfg.get('host') or '').strip().lower().rstrip('.')}|{port}|{str(cfg.get('user') or '').strip()}"


def needs_password(cfg: Dict[str, Any]) -> bool:
    """True when this network target must wait for a password typed for its current endpoint."""
    if str(cfg.get("type") or "").lower() not in NETWORK_TYPES:
        return False
    if cfg.get("awaiting_password"):
        return True
    bound = cfg.get("password_for")
    return bool(cfg.get("password") and bound and bound != endpoint_key(cfg))


AWAITING_PASSWORD_TEXT = ("Wartet auf das Passwort: Server, Port, Art oder Benutzer wurden geändert, das alte "
                          "Passwort ist deshalb gelöscht. Bitte das Passwort für den neuen Server in der Galerie "
                          "oder an der Box eintragen; bis dahin bleiben die Dateien in der Warteschlange.")


class AwaitingPasswordTarget:
    """Stands in for a network target whose password was dropped: connects nowhere, keeps the queue."""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = {k: v for k, v in cfg.items() if k != "password"}
        self.name = cfg.get("name") or str(cfg.get("type") or "ziel")

    def upload(self, item: Dict[str, Any]) -> Dict[str, Any]:
        raise TemporaryError(AWAITING_PASSWORD_TEXT)

    def check(self) -> str:
        raise PermanentError(AWAITING_PASSWORD_TEXT)

    def listdir(self, path: str = "") -> Dict[str, Any]:
        raise PermanentError(AWAITING_PASSWORD_TEXT)

    def make_dir(self, path: str) -> str:
        raise PermanentError(AWAITING_PASSWORD_TEXT)


def _remote_name(item: Dict[str, Any], base: str) -> str:
    meta = _meta(item)
    date = (meta.get("taken_at") or "")[:10] or "undatiert"
    slug = item.get("event_slug") or "ohne-event"
    folder = "originale" if item["variant"] == "original" else "fotos"
    return posixpath.join(base or "/", slug, date, folder, file_name(item))


def _listing(here: str, dirs: list, files: list, max_files: int = 60) -> Dict[str, Any]:
    """Ein Ordner, wie ihn die Auswahl anzeigt. Dateien nur zur Orientierung."""
    here = here if here.startswith("/") or ":" in here else "/" + here
    parent = here.rstrip("/").rsplit("/", 1)[0] or "/"
    return {"path": here, "parent": None if parent == here else parent,
            "dirs": dirs, "files": files[:max_files], "more_files": max(0, len(files) - max_files)}


def _local_name(item: Dict[str, Any], layout: str = "event/date") -> str:
    """Zielpfad innerhalb des Ordners — ``event/date``, ``event`` oder ``flat``."""
    meta = _meta(item)
    date = (meta.get("taken_at") or "")[:10] or "undatiert"
    slug = item.get("event_slug") or "ohne-event"
    folder = "originale" if item["variant"] == "original" else "fotos"
    if layout == "flat":
        return file_name(item, flat=True)
    name = file_name(item)
    if layout == "event":
        return posixpath.join(slug, folder, name)
    return posixpath.join(slug, date, folder, name)


class HostKeyChanged(TemporaryError):
    """The SFTP server shows a different host key than the one stored. Nothing was sent.

    Temporary on purpose: the files stay queued until the operator has
    compared the key and accepted it (or the right server answers again).
    """


def host_key_fingerprint(key) -> str:
    """OpenSSH's SHA-256 form of a server key, ``SHA256:<base64 without padding>``."""
    import base64

    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")


_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


def is_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and bool(_FINGERPRINT.match(value))


class HostKeys:
    """The SFTP servers' host keys this program has seen: trust on first use, then pinned.

    One small JSON file in the data folder (``sftp_host_keys.json``), keyed by
    ``host:port``: ``{"sha256", "type", "at"}`` for the pinned key, and
    ``seen`` for a different key a server showed since (so the operator can
    compare it and accept it explicitly). Only fingerprints, never key
    material, are stored or reported. A changed host or port is another key in
    the file, so it is a first use again: nothing of the old server's key
    carries over. ``path=None`` keeps it in memory (tests, a one-off check).
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._data: Dict[str, Any] = {}
        self._lock = threading.Lock()
        if self.path and self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError):
                self._data = {}

    @staticmethod
    def key(host: str, port: int) -> str:
        return f"{str(host).strip().lower()}:{int(port or 22)}"

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, host: str, port: int) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data.get(self.key(host, port)) or {})

    def pin(self, host: str, port: int, fingerprint: str, key_type: str = "") -> None:
        with self._lock:
            self._data[self.key(host, port)] = {"sha256": fingerprint, "type": str(key_type)[:40],
                                                "at": int(time.time())}
            self._save()

    def saw(self, host: str, port: int, fingerprint: str, key_type: str = "") -> None:
        with self._lock:
            entry = dict(self._data.get(self.key(host, port)) or {})
            entry["seen"] = {"sha256": fingerprint, "type": str(key_type)[:40], "at": int(time.time())}
            self._data[self.key(host, port)] = entry
            self._save()

    def accept(self, host: str, port: int, fingerprint: str) -> bool:
        """Pin the key the server showed last — only that exact one, and only when asked."""
        with self._lock:
            entry = dict(self._data.get(self.key(host, port)) or {})
            seen = entry.get("seen") or {}
            if not is_fingerprint(fingerprint) or seen.get("sha256") != fingerprint:
                return False
            self._data[self.key(host, port)] = {"sha256": fingerprint, "type": seen.get("type", ""),
                                                "at": int(time.time()), "accepted": True}
            self._save()
            return True

    def forget(self, host: str, port: int) -> None:
        with self._lock:
            if self._data.pop(self.key(host, port), None) is not None:
                self._save()

    def report(self, host: str, port: int) -> Dict[str, Any]:
        """Fingerprints only, for a status page or the heartbeat."""
        entry = self.get(host, port)
        out: Dict[str, Any] = {}
        if is_fingerprint(entry.get("sha256")):
            out["host_key"] = {"sha256": entry["sha256"], "type": str(entry.get("type") or ""),
                               "at": int(entry.get("at") or 0)}
        seen = entry.get("seen") or {}
        if is_fingerprint(seen.get("sha256")) and seen.get("sha256") != entry.get("sha256"):
            out["host_key_seen"] = {"sha256": seen["sha256"], "type": str(seen.get("type") or ""),
                                    "at": int(seen.get("at") or 0)}
        return out


#: Shared by every SFTP target built without a store of its own (in memory).
_MEMORY_KEYS = HostKeys(None)


def host_key_changed_text(stored: str, shown: str) -> str:
    return ("Der Server zeigt einen anderen Schlüssel als beim Einrichten "
            f"(gespeichert {stored}, jetzt {shown}). Die Box hat weder Passwort noch Dateien gesendet. "
            "Wurde der Server neu aufgesetzt, den Schlüssel beim Betreiber des Servers vergleichen und "
            "dann ausdrücklich „Neuen Schlüssel annehmen“.")


class SftpTarget:
    def __init__(self, cfg: Dict[str, Any], host_keys: Optional[HostKeys] = None):
        self.cfg = cfg
        self.name = cfg.get("name") or "sftp"
        self.host_keys = host_keys or _MEMORY_KEYS

    def _address(self):
        return str(self.cfg["host"]), int(self.cfg.get("port") or 22)

    def check(self) -> str:
        transport, sftp = self._open()
        try:
            # The folder up to its first placeholder: made if missing, then a
            # small file written and removed — proof that uploads can land.
            absolute, folders = fixed_folder(self.cfg)
            cur = "/" if absolute else ""
            try:
                for p in folders:
                    cur = (cur.rstrip("/") + "/" + p) if cur else p
                    try:
                        sftp.stat(cur)
                    except OSError:
                        sftp.mkdir(cur)
                probe = ((cur.rstrip("/") + "/") if cur else "") + CHECK_FILE
                with sftp.open(probe, "w") as f:
                    f.write(b"photobox")
                sftp.remove(probe)
            except OSError as e:
                raise PermanentError(f"Ordner {cur or '(Startordner)'} nicht beschreibbar: {e}") from e
            fp = self.host_keys.get(*self._address()).get("sha256", "")
            return ("angemeldet" + (" (Schlüsseldatei)" if self.cfg.get("key_file") else "")
                    + f", Ordner {cur or '(Startordner)'} beschreibbar"
                    + (f", Server-Schlüssel {fp}" if fp else ""))
        finally:
            transport.close()

    def _connect(self):
        """Key exchange, the host key compared with the stored one, only then the login.

        Until 17.11.2026 paramiko's ``Transport.connect`` took whatever key the
        server presented, so anyone between the booth and the NAS could read
        the password. Now: the first successful login stores the key's
        fingerprint (trust on first use); afterwards a different key ends the
        connection before user name, password or a file leaves the booth.
        """
        import paramiko

        host, port = self._address()
        transport = paramiko.Transport((host, port))
        try:
            transport.start_client(timeout=float(self.cfg.get("timeout") or 15))
            key = transport.get_remote_server_key()
            shown = host_key_fingerprint(key)
            stored = self.host_keys.get(host, port).get("sha256")
            if stored and stored != shown:
                self.host_keys.saw(host, port, shown, key.get_name())
                raise HostKeyChanged(host_key_changed_text(stored, shown))
            user = str(self.cfg.get("user") or "")
            if self.cfg.get("key_file"):
                transport.auth_publickey(user, paramiko.Ed25519Key.from_private_key_file(self.cfg["key_file"]))
            elif self.cfg.get("password"):
                transport.auth_password(user, str(self.cfg["password"]))
            else:
                transport.auth_none(user)
            if not transport.is_authenticated():
                raise paramiko.AuthenticationException("nicht angemeldet")
            if not stored:
                self.host_keys.pin(host, port, shown, key.get_name())
        except BaseException:
            transport.close()
            raise
        return transport

    def _open(self, auth_permanent: bool = True):
        """Verbindung plus SFTP-Kanal. Ruft auf, wer danach ``close()`` macht."""
        import paramiko

        try:
            transport = self._connect()
        except HostKeyChanged:
            raise
        except paramiko.AuthenticationException as e:
            if auth_permanent:
                raise PermanentError(f"Anmeldung abgelehnt: {e}") from e
            raise TemporaryError(f"Anmeldung abgelehnt: {e}") from e
        except (OSError, EOFError, paramiko.SSHException) as e:
            raise TemporaryError(str(e)) from e
        try:
            return transport, paramiko.SFTPClient.from_transport(transport)
        except (OSError, EOFError, paramiko.SSHException) as e:
            transport.close()
            raise TemporaryError(str(e)) from e

    def listdir(self, path: str = "") -> Dict[str, Any]:
        import stat as statmod

        transport, sftp = self._open()
        try:
            here = path.strip() or sftp.normalize(".")
            entries = sftp.listdir_attr(here)
            dirs = sorted(e.filename for e in entries if statmod.S_ISDIR(e.st_mode or 0))
            files = sorted(e.filename for e in entries if not statmod.S_ISDIR(e.st_mode or 0))
            return _listing(here, dirs, files)
        except OSError as e:
            raise PermanentError(f"{path or '.'}: {e}") from e
        finally:
            transport.close()

    def make_dir(self, path: str) -> str:
        transport, sftp = self._open()
        try:
            sftp.mkdir(path)
            return path
        except OSError as e:
            raise PermanentError(f"{path}: {e}") from e
        finally:
            transport.close()

    def upload(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if self.cfg.get("only_originals") and item["variant"] != "original":
            return {"skipped": True}
        if skips_variant(self.cfg, item):
            return {"skipped": True}
        # A refused login stays temporary for an upload, as before: the files
        # wait instead of being given up while someone fixes the password.
        transport, sftp = self._open(auth_permanent=False)
        try:
            # „Alles in einen Ordner" gilt hier genauso wie bei FTPS: Picdrop
            # ordnet die Bilder selbst einer Galerie zu und will sie im
            # Wurzelverzeichnis — ein Unterordner je Event und Tag käme dort
            # gar nicht erst an.
            # The folder (zielordner.py): empty = the login's start folder,
            # relative paths below it, a leading "/" from the top. Missing
            # folders are made one level at a time.
            absolute, folders, name = remote_parts(self.cfg, item, "/photobox")
            cur = "/" if absolute else ""
            for p in folders:
                cur = (cur.rstrip("/") + "/" + p) if cur else p
                try:
                    sftp.stat(cur)
                except OSError:
                    try:
                        sftp.mkdir(cur)
                    except OSError as e:
                        raise TemporaryError(f"Ordner {cur} lässt sich nicht anlegen (keine Schreibrechte?): {e}") from e
            remote = ((cur.rstrip("/") + "/") if cur else ("/" if absolute else "")) + name
            size = Path(item["path"]).stat().st_size
            with open_source(item, self.cfg) as f:
                sftp.putfo(f, remote + ".part")
            try:
                landed = sftp.stat(remote + ".part").st_size
            except OSError:
                landed = None
            verify_size(size, landed, self.name)
            try:
                sftp.remove(remote)
            except OSError:
                pass
            sftp.rename(remote + ".part", remote)
            return {"remote": remote, "bytes": size}
        except OSError as e:
            raise TemporaryError(str(e)) from e
        finally:
            transport.close()


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTPS on port 990: the control connection is TLS before the server says hello.

    Standard library only: the socket is wrapped right after connecting,
    with the same context, so certificate and host name are checked the
    same way as for explicit FTPS. ``login`` then must not send AUTH TLS.
    """

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        import socket as _socket
        raw = _socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self.context.wrap_socket(raw, server_hostname=self.host)
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def login(self, user="", passwd="", acct="", secure=True):
        return ftplib.FTP.login(self, user, passwd, acct)


class FtpsTarget:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.name = cfg.get("name") or "ftps"

    def timeout(self) -> float:
        """Wie lange auf den Server gewartet wird.

        Ohne Netz soll die Warteschlange nicht minutenlang an einer Datei
        hängen: Sie soll schnell merken, dass es nicht geht, und es später
        wieder versuchen. Am Eventabend ist ein kurzes Netzfenster mehr wert
        als ein geduldiger Verbindungsversuch.
        """
        return float(self.cfg.get("timeout") or 15)

    def _connect(self):
        port = int(self.cfg.get("port") or 21)
        # Port 990 is implicit FTPS (TLS from the first byte, an older way some
        # NAS still offer); every other port speaks explicit FTPS (AUTH TLS).
        cls = ImplicitFTP_TLS if port == 990 else ftplib.FTP_TLS
        ftp = cls(context=ssl.create_default_context(), timeout=self.timeout())
        ftp.connect(self.cfg["host"], port)
        ftp.login(self.cfg["user"], self.cfg.get("password", ""))
        ftp.prot_p()
        if self.cfg.get("passive", True):
            ftp.set_pasv(True)
        return ftp

    def check(self) -> str:
        try:
            ftp = self._connect()
        except ftplib.error_perm as e:
            raise PermanentError(f"Anmeldung abgelehnt: {e}") from e
        except (OSError, ftplib.Error) as e:
            raise TemporaryError(str(e)) from e
        try:
            # Login, then the folder (its part before the first placeholder):
            # made if missing, then a small file written and deleted again —
            # "angemeldet" alone said nothing about whether uploads can land.
            absolute, folders = fixed_folder(self.cfg)
            if absolute:
                ftp.cwd("/")
            for d in folders:
                try:
                    ftp.cwd(d)
                except ftplib.error_perm:
                    try:
                        ftp.mkd(d)
                        ftp.cwd(d)
                    except ftplib.error_perm as e:
                        raise PermanentError(f"Ordner {d} lässt sich nicht anlegen (keine Schreibrechte?): {e}") from e
            here = ftp.pwd()
            try:
                ftp.storbinary(f"STOR {CHECK_FILE}", io.BytesIO(b"photobox"))
                ftp.delete(CHECK_FILE)
            except (ftplib.error_perm, ftplib.error_temp) as e:
                raise PermanentError(f"Ordner {here} nicht beschreibbar: {e}") from e
            return f"angemeldet, Ordner {here} beschreibbar"
        except ftplib.error_perm as e:
            raise PermanentError(str(e)) from e
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    def listdir(self, path: str = "") -> Dict[str, Any]:
        try:
            ftp = self._connect()
        except ftplib.error_perm as e:
            raise PermanentError(f"Anmeldung abgelehnt: {e}") from e
        except (OSError, ftplib.Error) as e:
            raise TemporaryError(str(e)) from e
        try:
            if path.strip():
                ftp.cwd(path.strip())
            here = ftp.pwd()
            dirs, files = [], []
            try:
                for name, facts in ftp.mlsd():
                    if name in (".", ".."):
                        continue
                    (dirs if facts.get("type") == "dir" else files).append(name)
            except (ftplib.error_perm, ftplib.error_proto):
                # Ältere Server können kein MLSD: jeden Namen einmal anprobieren.
                for name in ftp.nlst():
                    name = name.rsplit("/", 1)[-1]
                    if name in (".", ".."):
                        continue
                    try:
                        ftp.cwd(name)
                        ftp.cwd(here)
                        dirs.append(name)
                    except ftplib.error_perm:
                        files.append(name)
            return _listing(here, sorted(dirs), sorted(files))
        except ftplib.error_perm as e:
            raise PermanentError(f"{path or '/'}: {e}") from e
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    def make_dir(self, path: str) -> str:
        try:
            ftp = self._connect()
        except (OSError, ftplib.Error) as e:
            raise TemporaryError(str(e)) from e
        try:
            ftp.mkd(path)
            return path
        except ftplib.error_perm as e:
            raise PermanentError(f"{path}: {e}") from e
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    def upload(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if self.cfg.get("only_web") and item["variant"] != "web":
            return {"skipped": True}
        if skips_variant(self.cfg, item):
            return {"skipped": True}
        try:
            ftp = self._connect()
        except ftplib.error_perm as e:
            raise PermanentError(str(e)) from e
        except (OSError, ftplib.Error) as e:
            raise TemporaryError(str(e)) from e
        try:
            # The folder (zielordner.py), also in flat mode since 17.11.2026
            # (before, flat ignored it; Picdrop's folder field stays empty).
            # Empty = the login's start folder; a leading "/" from the top.
            absolute, folders, remote = remote_parts(self.cfg, item)
            if absolute:
                ftp.cwd("/")
            for d in folders:
                try:
                    ftp.cwd(d)
                except ftplib.error_perm:
                    try:
                        ftp.mkd(d)
                        ftp.cwd(d)
                    except ftplib.error_perm as e:
                        raise TemporaryError(f"Ordner {d} lässt sich nicht anlegen (keine Schreibrechte?): {e}") from e
            size = Path(item["path"]).stat().st_size
            # Erst unter einem Zwischennamen, dann umbenennen — wie bei SFTP und
            # beim Ordner-Ziel. Bis zum 19.09.2026 schrieb FTP direkt unter den
            # Endnamen: Bricht die Datenverbindung ab, ohne dass der Server
            # einen Fehler meldet (Quota), stand ein halbes Bild unter dem
            # richtigen Namen bei Picdrop — und bei Servern ohne ``SIZE`` fiel
            # das auch der Größenprüfung nicht auf.
            zwischen = remote + ".part"
            with open_source(item, self.cfg) as f:
                ftp.storbinary(f"STOR {zwischen}", f)
            landed = None
            try:
                ftp.voidcmd("TYPE I")
                landed = ftp.size(zwischen)
            except (ftplib.Error, OSError):
                pass                 # Server ohne SIZE: dann eben ohne Nachweis
            verify_size(size, landed, self.name)
            try:
                ftp.delete(remote)
            except (ftplib.Error, OSError):
                pass                 # es gab noch keine
            ftp.rename(zwischen, remote)
            return {"remote": remote, "bytes": size}
        except (OSError, ftplib.Error) as e:
            raise TemporaryError(str(e)) from e
        finally:
            try:
                ftp.quit()
            except Exception:
                pass


class FtpTarget(FtpsTarget):
    """FTP ohne Verschlüsselung — für NAS und Webspace, die kein TLS können.

    Getrennt von FtpsTarget, weil beides früher denselben Weg nahm: Wer „ftp"
    eintrug, bekam eine TLS-Verbindung und einen Verbindungsfehler, wenn der
    Server kein TLS kann. Benutzername und Kennwort gehen hier im Klartext über
    die Leitung; im eigenen Netz ist das vertretbar, über das Internet nicht.
    """

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        self.name = cfg.get("name") or "ftp"

    def _connect(self):
        ftp = ftplib.FTP(timeout=self.timeout())
        ftp.connect(self.cfg["host"], int(self.cfg.get("port") or 21))
        ftp.login(self.cfg["user"], self.cfg.get("password", ""))
        if self.cfg.get("passive", True):
            ftp.set_pasv(True)
        return ftp


class FolderTarget:
    """Kopie in einen Ordner — zweite Ablage auf der Platte oder auf dem Stick.

    ``removable`` macht aus „Ordner fehlt" einen vorübergehenden Fehler: Der
    Stick darf abgezogen sein, die Aufnahmen gehen nicht verloren und werden
    nachgeholt, sobald er wieder steckt.
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.name = cfg.get("name") or "folder"

    def check(self) -> str:
        base = str(self.cfg.get("path") or "").strip()
        if not base:
            raise PermanentError("kein Ordner eingetragen")
        root = Path(base).expanduser()
        if root.is_dir():
            return "Ordner da"
        if root.parent.exists() and not self.cfg.get("removable"):
            return "Ordner wird beim ersten Upload angelegt"
        raise TemporaryError(f"Ordner nicht da: {root}")

    def listdir(self, path: str = "") -> Dict[str, Any]:
        root = Path(path.strip() or self.cfg.get("path") or Path.home()).expanduser()
        if not root.is_dir():
            raise PermanentError(f"Ordner nicht da: {root}")
        dirs, files = [], []
        try:
            for e in root.iterdir():
                (dirs if e.is_dir() else files).append(e.name)
        except OSError as e:
            raise PermanentError(str(e)) from e
        return _listing(str(root), sorted(dirs), sorted(files))

    def make_dir(self, path: str) -> str:
        try:
            Path(path).expanduser().mkdir(parents=True, exist_ok=False)
        except OSError as e:
            raise PermanentError(str(e)) from e
        return path

    def upload(self, item: Dict[str, Any]) -> Dict[str, Any]:
        base = str(self.cfg.get("path") or "").strip()
        if not base:
            return {"skipped": True}
        if item["variant"] == "original" and not self.cfg.get("originals", True):
            return {"skipped": True}
        root = Path(base).expanduser()
        removable = bool(self.cfg.get("removable"))
        if not root.exists():
            if removable or not root.parent.exists():
                raise TemporaryError(f"Ordner nicht da: {root}")
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise PermanentError(str(e)) from e
        source = Path(item["path"])
        if not source.exists():
            raise PermanentError(f"Datei fehlt: {source}")
        target = root / _local_name(item, self.cfg.get("layout", "event/date"))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".part")
            shutil.copy2(source, tmp)
            verify_size(source.stat().st_size, tmp.stat().st_size, self.name)
            os.replace(tmp, target)
            poster = item.get("poster")
            if poster and Path(poster).exists():
                shutil.copy2(poster, target.with_suffix(".jpg"))
        except OSError as e:                      # Stick abgezogen, voll, schreibgeschützt
            if removable or getattr(e, "errno", 0) in (28, 30):
                raise TemporaryError(str(e)) from e
            raise PermanentError(str(e)) from e
        return {"file": str(target)}


def build_extra_targets(cfgs, host_keys: Optional[HostKeys] = None):
    """The upload targets of a configuration. ``host_keys`` keeps the SFTP servers' keys (see HostKeys)."""
    out = []
    for c in cfgs or []:
        if c.get("enabled") is False:          # kept in the settings, switched off for now
            continue
        t = (c.get("type") or "").lower()
        if needs_password(c):                  # the server changed: no old password to a new machine
            out.append(AwaitingPasswordTarget(c))
            continue
        if is_sftp(t):                         # SSH-Dateiübertragung ist SFTP — ein Weg, mehrere Namen
            out.append(SftpTarget(c, host_keys))
        elif t == "ftps":
            out.append(FtpsTarget(c))
        elif t == "ftp":
            out.append(FtpTarget(c))
        elif t in ("folder", "usb", "stick"):
            out.append(FolderTarget(c))
    return out


def storage_targets(storage: Dict[str, Any] | None) -> list:
    """Ordner- und Stick-Ziele aus dem ``storage``-Block der lokalen Konfiguration."""
    storage = storage or {}
    out = []
    folder = str(storage.get("folder") or "").strip()
    if folder:
        out.append({"type": "folder", "name": "folder", "path": folder,
                    "layout": storage.get("layout", "event/date"),
                    "originals": bool(storage.get("originals", True))})
    usb = storage.get("usb") or {}
    if usb.get("enabled") and str(usb.get("path") or "").strip():
        out.append({"type": "folder", "name": "usb", "path": str(usb["path"]).strip(),
                    "layout": storage.get("layout", "event/date"), "removable": True,
                    "originals": bool(usb.get("originals", True))})
    return out
