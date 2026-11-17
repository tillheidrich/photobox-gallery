"""The second upload target ("Zweites Ziel im Netz") set from the gallery.

The card for Picdrop, an FTP server or an SFTP NAS exists in the booth's own
admin and, since 17.11.2026, on the gallery's sync page of the booth and
through MCP (docs/SECOND-TARGET-FROM-THE-GALLERY.md). Since the owner's
decision of 17.11.2026 (evening) there is no switch at the booth any more:
a paired booth takes the setting from its gallery. What protects the
password instead:

* **One target, its fields only.** The jobs touch the entry named
  ``picdrop`` (or the older ``zweites-ziel``) of ``targets`` and nothing
  else: type, host, port, user, password, path, the flat-folder flag,
  enabled and the two upload choices. Everything is checked again here.
* **The password is sealed to this booth.** An X25519 key pair lives in
  ``<data>/keys/second_target.key``, outside local.json, so neither the
  admin page (``/api/admin/config``) nor a profile export nor a diagnostic
  carries it. The heartbeat carries the public key; the gallery seals the
  password to it (libsodium sealed box, PyNaCl) and keeps only that.
* **A password belongs to one server.** When type, host, port or user
  change and the same job brings no new password, the stored one is
  deleted, the old server's pinned SFTP key is forgotten and the target
  waits (``awaiting_password``) until a password for the new server is
  typed — in the gallery form or at the booth. The entry also carries
  ``password_for`` (photobox_sync.targets.endpoint_key); a target whose
  endpoint does not match it gets no password at all. A gallery that is
  wrong or hostile cannot make this booth send the old password elsewhere.
* **What goes back** is the state without the password: type, host, user,
  path, "password stored yes/no", "waits for a password", the last check.

SFTP takes a password only from here; a key file stays a booth-only
setting, and a target that has one keeps it. The connections themselves
("Prüfen") are opened through ``photobox_sync.targets``: paramiko for SFTP,
ftplib for FTP and FTPS.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from photobox_sync.targets import is_sftp, needs_password

log = logging.getLogger("boothcore.second_target")

#: The kinds of target, the fields and their limits. The gallery has the same
#: in app/second_target.py; a test in each suite compares them.
TYPES = ("ftps", "ftp", "sftp")
FIELDS = ("type", "host", "port", "user", "path", "flat", "enabled", "originals", "print_sheets")
LIMITS = {"host": 253, "user": 128, "password": 256, "path": 255}
#: Names the booth admin card uses for its one target ("zweites-ziel" before 09.2026).
NAMES = ("picdrop", "zweites-ziel")
#: A key file that cannot be read (held by a scanner, a lock) is tried this often, this far apart.
READ_ATTEMPTS = 5
READ_PAUSE_S = 0.2
#: Payload keys a set job may carry. Anything else refuses the whole job.
SET_KEYS = {"v", "action", "target", "password_sealed", "key_id"}

_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _text(value: Any, field: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field}: muss Text sein")
    if _CONTROL.search(value):
        raise ValueError(f"{field}: Steuerzeichen sind nicht erlaubt")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{field}: höchstens {limit} Zeichen")
    return value


def clean_host(value: Any) -> str:
    """A host name or an IP address — no scheme, no path, no port, no user."""
    host = _text(value, "Server", LIMITS["host"]).rstrip(".")
    if not host:
        raise ValueError("Server: bitte eintragen")
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        pass
    labels = host.split(".")
    if not all(_LABEL.match(part) for part in labels) or labels[-1].isdigit():
        raise ValueError("Server: nur der Name oder die IP-Adresse, ohne ftp://, Pfad oder Port")
    return host.lower()


def clean_path(value: Any) -> str:
    """The folder on the server (photobox_sync.zielordner: placeholders, no "..", slashes unified)."""
    from photobox_sync import zielordner
    return zielordner.check(_text(value, "Ordner", LIMITS["path"]))


def clean_fields(raw: Any) -> Dict[str, Any]:
    """The target as a job (or the gallery form) states it; refuses what is not a field."""
    if not isinstance(raw, dict):
        raise ValueError("Ziel fehlt")
    extra = sorted(set(map(str, raw)) - set(FIELDS))
    if extra:
        raise ValueError("Nicht änderbar über diesen Weg: " + ", ".join(extra))
    kind = str(raw.get("type") or "")
    if kind not in TYPES:
        raise ValueError("Art: ftps, ftp oder sftp")
    port = raw.get("port") or 0
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Port: 1 bis 65535 (leer = üblich)")
    out: Dict[str, Any] = {"type": kind, "host": clean_host(raw.get("host")),
                           "user": _text(raw.get("user"), "Benutzer", LIMITS["user"]),
                           "path": clean_path(raw.get("path"))}
    if port:
        out["port"] = port
    for flag, default in (("flat", True), ("enabled", True), ("originals", True), ("print_sheets", False)):
        value = raw.get(flag, default)
        if not isinstance(value, bool):
            raise ValueError(f"{flag}: ja oder nein")
        out[flag] = value
    return out


def clean_password(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Passwort: muss Text sein")
    if _CONTROL.search(value):
        raise ValueError("Passwort: Steuerzeichen sind nicht erlaubt")
    if len(value) > LIMITS["password"]:
        raise ValueError(f"Passwort: höchstens {LIMITS['password']} Zeichen")
    return value


def find(local: Dict[str, Any]) -> Tuple[int, Optional[Dict[str, Any]]]:
    """Index and entry of the card's target in ``targets`` (-1, None when there is none)."""
    for i, t in enumerate(local.get("targets") or []):
        if isinstance(t, dict) and t.get("name") in NAMES:
            return i, t
    return -1, None


def _port(t: Dict[str, Any]) -> int:
    try:
        return int(t.get("port") or 22)
    except (TypeError, ValueError):
        return 22


def endpoint_changed(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> bool:
    """Type, host, port or user differ (a new target counts as changed)."""
    from photobox_sync.targets import endpoint_key
    return not old or endpoint_key(old) != endpoint_key(new)


def bind_password(entry: Dict[str, Any], old: Optional[Dict[str, Any]], new_password: Optional[str],
                  host_keys=None, strict_new: bool = True) -> str:
    """Rule B2 for every source (gallery job, MCP job, booth form); returns a note for the log.

    ``new_password`` None = none given. With a new password it is bound to
    this endpoint. Without one an unchanged endpoint keeps the stored
    password; a changed endpoint loses it, forgets the old server's pinned
    key and waits for a password — unless ``strict_new`` is off and there
    was nothing to protect (the booth's own form, no password before).
    """
    from photobox_sync.targets import endpoint_key
    changed = endpoint_changed(old, entry)
    if host_keys is not None and old and changed:
        forget_endpoint(host_keys, old)
    if new_password:
        entry["password"] = new_password
        entry["password_for"] = endpoint_key(entry)
        entry.pop("awaiting_password", None)
        return "Passwort neu"
    if not changed:
        entry["password"] = str((old or {}).get("password") or "")
        if entry["password"] and not entry.get("password_for"):
            entry["password_for"] = endpoint_key(entry)
        if (old or {}).get("awaiting_password"):
            entry["awaiting_password"] = True
        return ("Passwort unverändert" if entry["password"] else "ohne Passwort") + \
            (", wartet weiter auf ein Passwort" if entry.get("awaiting_password") else "")
    had = bool((old or {}).get("password"))
    entry["password"] = ""
    entry.pop("password_for", None)
    if had or strict_new:
        entry["awaiting_password"] = True
        return ("Server geändert: altes Passwort gelöscht, wartet auf ein Passwort für den neuen Server"
                if had else "neuer Server ohne Passwort: wartet auf ein Passwort")
    entry.pop("awaiting_password", None)
    return "ohne Passwort"


def forget_endpoint(host_keys, old: Dict[str, Any]) -> None:
    """The old server's pinned SFTP key goes with its password."""
    if is_sftp(old.get("type")) and old.get("host"):
        host_keys.forget(str(old["host"]), _port(old))


def key_id(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:12]


class Keys:
    """This booth's key pair for sealed passwords. Made on first use, kept in one file."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "keys" / "second_target.key"
        self._private = None

    def private(self):
        """The private key: read from its file, made only when there is no file at all.

        Until 17.11.2026 any OSError (a lock, a virus scanner holding the file
        for a moment) made a new key and wrote it over the good one, so every
        password the gallery had sealed to it was lost. Now a file that cannot
        be read is tried again and then reported; a damaged one is reported
        too. Nothing ever overwrites an existing key file (same as
        photobox_sync/sealing.py).
        """
        from nacl.public import PrivateKey
        if self._private is None:
            if not self.path.exists():
                self._private = self._create(PrivateKey.generate())
            else:
                self._private = PrivateKey(self._read())
        return self._private

    def _read(self) -> bytes:
        last: Exception = OSError("key file not readable")
        for attempt in range(READ_ATTEMPTS):
            try:
                text = self.path.read_text(encoding="ascii").strip()
                break
            except FileNotFoundError:
                raise
            except OSError as e:
                last = e
                if attempt < READ_ATTEMPTS - 1:
                    time.sleep(READ_PAUSE_S)
        else:
            raise last
        try:
            raw = base64.b64decode(text, validate=True)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Schlüsseldatei {self.path} ist beschädigt") from e
        if len(raw) != 32:
            raise ValueError(f"Schlüsseldatei {self.path} ist beschädigt")
        return raw

    def _create(self, key):
        """Write a new key, 0600 from its first byte (``os.open`` with the mode), moved into place whole."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.unlink()                       # a leftover of a crash, never the key itself
        except FileNotFoundError:
            pass
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64encode(bytes(key)))
            f.flush()
            os.fsync(f.fileno())
        if self.path.exists():
            # Another process made one meanwhile: that one stays, ours goes.
            tmp.unlink()
            return key.__class__(self._read())
        os.replace(tmp, self.path)
        return key

    def public_b64(self) -> str:
        return base64.b64encode(bytes(self.private().public_key)).decode("ascii")

    def key_id(self) -> str:
        return key_id(bytes(self.private().public_key))

    def open(self, sealed_b64: str) -> str:
        from nacl.exceptions import CryptoError
        from nacl.public import SealedBox
        try:
            raw = SealedBox(self.private()).decrypt(base64.b64decode(str(sealed_b64), validate=True))
            return raw.decode("utf-8")
        except (CryptoError, ValueError, UnicodeDecodeError) as e:
            raise ValueError("Das Passwort ließ sich an der Box nicht öffnen — bitte in der Galerie "
                             "neu eingeben (die Box hat womöglich einen neuen Schlüssel).") from e


class SecondTarget:
    """The jobs and the report. ``booth`` gives store, agent, outbox, library, profile, logbuf."""

    def __init__(self, booth):
        self.b = booth
        self.keys = Keys(booth.data)

    # ── State ────────────────────────────────────────
    def report(self) -> Dict[str, Any]:
        """For the heartbeat: what is set, never the password."""
        local = self.b.store.local
        # "allowed" stays True for galleries from before 17.11.2026 (evening),
        # which still look for it; there is no switch at the booth any more.
        out: Dict[str, Any] = {"v": 2, "allowed": True, "configured": False}
        try:
            out["public_key"], out["key_id"] = self.keys.public_b64(), self.keys.key_id()
        except Exception as e:                     # noqa: BLE001 — no key, no remote change; the rest still reports
            log.warning("Schlüssel für das zweite Ziel nicht verfügbar: %s", type(e).__name__)
        _, t = find(local)
        if t:
            out.update({"configured": True, "enabled": t.get("enabled") is not False,
                        "type": str(t.get("type") or ""), "host": str(t.get("host") or ""),
                        "port": int(t.get("port") or 0) if str(t.get("port") or "0").isdigit() else 0,
                        "user": str(t.get("user") or ""), "path": str(t.get("path") or ""),
                        "flat": t.get("flat") is not False, "originals": t.get("originals") is not False,
                        "print_sheets": bool(t.get("print_sheets")),
                        "password_set": bool(t.get("password")), "key_file": bool(t.get("key_file")),
                        "awaiting_password": needs_password(t)})
            if is_sftp(t.get("type")) and t.get("host"):
                # Fingerprints only (photobox_sync.targets.HostKeys), never key material.
                out.update(self.b.host_keys.report(str(t["host"]), _port(t)))
        check = local.get("second_target_check") or {}
        if isinstance(check, dict) and check.get("at"):
            out["check"] = {"ok": bool(check.get("ok")), "message": str(check.get("message") or "")[:300],
                            "at": float(check.get("at") or 0)}
        return out

    def _audit(self, text: str) -> None:
        """One line in the booth log and in the gallery's device log; never a secret."""
        log.info(text)
        try:
            self.b.logbuf.add("info", text, source="second_target")
        except Exception:                        # noqa: BLE001 — the change itself is done
            pass

    # ── Jobs ─────────────────────────────────────────
    def handle_set(self, payload: Dict[str, Any]) -> str:
        payload = payload if isinstance(payload, dict) else {}
        extra = sorted(set(map(str, payload)) - SET_KEYS)
        if extra:
            raise ValueError("Nicht änderbar über diesen Weg: " + ", ".join(extra))
        local = self.b.store.local
        targets = [dict(t) for t in (local.get("targets") or []) if isinstance(t, dict)]
        i, old = find({"targets": targets})
        if payload.get("action") == "clear":
            if i >= 0:
                targets.pop(i)
            self.b.store.save_local({"targets": targets, "second_target_check": {}})
            self._audit("Zweites Ziel aus der Galerie entfernt (Zugang gelöscht)")
            return "Zweites Ziel entfernt; der Zugang ist an der Box gelöscht."
        if payload.get("action") not in (None, "set"):
            raise ValueError("unbekannte Aktion")
        fields = clean_fields(payload.get("target"))
        entry: Dict[str, Any] = {**(old or {}), **fields, "name": (old or {}).get("name") or "picdrop"}
        if "port" not in fields:
            entry.pop("port", None)
        if fields["type"] != "sftp":
            entry.pop("key_file", None)            # a key file only means something for SFTP
        new_password = None
        if payload.get("password_sealed"):
            if payload.get("key_id") and payload["key_id"] != self.keys.key_id():
                raise ValueError("Das Passwort ist für einen anderen Schlüssel dieser Box verschlossen — "
                                 "bitte in der Galerie neu eingeben.")
            new_password = clean_password(self.keys.open(payload["password_sealed"])) or None
        # Rule B2, decided here and not in the gallery: a changed server never gets the old password.
        pw_note = bind_password(entry, old, new_password, self.b.host_keys, strict_new=True)
        if i >= 0:
            targets[i] = entry
        else:
            targets.append(entry)
        self.b.store.save_local({"targets": targets})
        changed = [k for k in ("type", "host", "port", "user", "path", "flat", "enabled", "originals", "print_sheets")
                   if (old or {}).get(k) != entry.get(k)]
        self._audit(f"Zweites Ziel aus der Galerie geändert ({', '.join(changed) or 'nichts'}): {fields['type']} "
                    f"{fields['host']}, {'an' if fields['enabled'] else 'aus'}, {pw_note}")
        return (f"Zweites Ziel übernommen ({fields['type']}, {fields['host']}, "
                f"{'eingeschaltet' if fields['enabled'] else 'ausgeschaltet'}, {pw_note}). Gilt ab dem nächsten Upload.")

    def check(self) -> Tuple[bool, str]:
        """The booth admin's "Verbindung testen" for the stored target; the result is kept for the report."""
        from photobox_sync.targets import build_extra_targets

        from .app import ziel_fehler_text
        from . import redact
        _, t = find(self.b.store.local)
        if not t:
            ok, text = False, "Kein zweites Ziel eingerichtet."
        else:
            secret = str(t.get("password") or "")
            built = build_extra_targets([dict(t, enabled=True)], self.b.host_keys)
            if not built:
                ok, text = False, "Diese Art von Ziel kennt die Box nicht."
            else:
                try:
                    ok, text = True, "Verbindung steht. " + str(built[0].check() or "")
                except Exception as e:           # noqa: BLE001 — whatever went wrong is the answer
                    raw = str(e) or type(e).__name__
                    if secret:
                        raw = raw.replace(secret, "…")
                    ok, text = False, ziel_fehler_text(raw)
        text = redact.text(text.strip())[:300]
        self.b.store.save_local({"second_target_check": {"ok": ok, "message": text, "at": time.time()}})
        return ok, text

    def accept_host_key(self, fingerprint: str) -> Dict[str, Any]:
        """Pin the key the SFTP server showed last — exactly the fingerprint a person compared.

        Never implied by saving the target; a fingerprint other than the one
        the server presented at the last attempt is refused.
        """
        from photobox_sync.targets import is_fingerprint

        _, t = find(self.b.store.local)
        if not t or not is_sftp(t.get("type")) or not t.get("host"):
            raise ValueError("Das zweite Ziel ist kein SFTP-Server.")
        if not is_fingerprint(fingerprint) or not self.b.host_keys.accept(str(t["host"]), _port(t), fingerprint):
            raise ValueError("Dieser Schlüssel ist nicht der, den der Server zuletzt gezeigt hat — "
                             "bitte erst „Verbindung testen“, dann vergleichen.")
        self._audit(f"Neuer Server-Schlüssel für das zweite Ziel angenommen: {fingerprint}")
        return self.b.host_keys.report(str(t["host"]), _port(t))

    def handle_accept_host_key(self, payload: Dict[str, Any]) -> str:
        """Job ``accept_second_target_host_key`` {v, fingerprint}: from the gallery form only (never MCP)."""
        payload = payload if isinstance(payload, dict) else {}
        extra = sorted(set(map(str, payload)) - {"v", "fingerprint"})
        if extra:
            raise ValueError("Nicht änderbar über diesen Weg: " + ", ".join(extra))
        self.accept_host_key(str(payload.get("fingerprint") or ""))
        return "Neuer Server-Schlüssel angenommen; der nächste Upload meldet sich damit an."

    def handle_check(self, payload: Dict[str, Any]) -> str:
        ok, text = self.check()
        if not ok:
            raise RuntimeError(text)
        return text

    def backfill(self) -> int:
        """Queue the files of the current event the second target does not have yet. Explicit only."""
        b = self.b
        _, t = find(b.store.local)
        if not t or t.get("enabled") is False:
            raise ValueError("Das zweite Ziel ist nicht eingeschaltet.")
        if getattr(b, "ephemeral", False):
            raise ValueError("Datensparsamer Betrieb: von dieser Box geht nichts hinaus.")
        slug = str((b.profile.get("event") or {}).get("slug") or "")
        if not slug:
            raise ValueError("Der Box ist kein Event zugewiesen.")
        name = str(t.get("name"))
        keep = lambda uid: not b.library.withheld(uid)          # noqa: E731 — deleted or "Nur drucken" stays
        added = b.outbox.copy_event_to(name, slug, keep_session=keep)
        # Runs whose queue entries have aged out (older than 30 days and
        # beyond 2000 entries): the main file and its moving version from
        # the library, and the print sheet when that choice is on.
        ev = b.profile.get("event") or {}
        for row in b.library.on_the_booth([slug]):
            if not keep(row["uid"]):
                continue
            meta = {"taken_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(row["created"]))),
                    "event_name": str(ev.get("name") or "")}
            if str(ev.get("file_naming") or "") == "event_datetime":
                meta.update({"file_naming": "event_datetime", "event_name": str(ev.get("name") or "")})
            files = []
            if row.get("result"):
                video = str(row["mode"]) in ("boomerang", "video", "gif")
                files.append((row["uid"], str(row["mode"]), "web" if video else "original", row["result"]))
            if row.get("anim"):
                files.append((row["uid"] + (".boom" if str(row["anim"]).endswith(".mp4") else ".gif"),
                              "boomerang" if str(row["anim"]).endswith(".mp4") else "gif", "web", row["anim"]))
            if t.get("print_sheets") and row.get("print_file") and row.get("print_file") != row.get("result"):
                files.append((row["uid"] + ".print", "print", "print", row["print_file"]))
            for uid, kind, variant, path in files:
                if not Path(path).is_file():
                    continue                          # a file the target already has is left alone by add_new
                added += b.outbox.add_new(target=name, uid=uid, session_uid=row["uid"], event_slug=slug,
                                          kind=kind, variant=variant, path=Path(path), meta=meta, priority=9)
        self._audit(f"Bisherige Aufnahmen des Events an das zweite Ziel nachgesendet: {added} Dateien")
        return added

    def handle_backfill(self, payload: Dict[str, Any]) -> str:
        n = self.backfill()
        return f"{n} Dateien dieses Events in die Warteschlange des zweiten Ziels gelegt." if n else \
            "Nichts nachzusenden: Das zweite Ziel hat schon alle Dateien dieses Events (oder sie warten bereits)."
