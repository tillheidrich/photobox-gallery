"""Target passwords sent from the gallery to Photobox Sync, sealed to this PC's own key (17.11.2026).

Before, a ``set_sync_config`` job carried FTP/SFTP passwords in clear and
they stayed in the gallery's ``device_jobs.payload_json``. Now this program
makes an X25519 key pair on first use (``<data>/keys/sync_targets.key``,
mode 0600 where the OS has one), publishes only the public key and a short
key id in its report (``sealing``), and opens what the gallery sealed to it
(libsodium sealed box, PyNaCl — already installed as part of the SSH library). The private key
never leaves this PC; the gallery never holds the password readably.

The same scheme as the booth's second target (boothcore/second_target.py).
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any, Dict

LIMIT = 256
#: A key file that cannot be read (held by a scanner, a lock) is tried this often, this far apart.
READ_ATTEMPTS = 5
READ_PAUSE_S = 0.2
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def key_id(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:12]


class SealKeys:
    """This PC's key pair for sealed target passwords. Made on first use, kept in one file."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "keys" / "sync_targets.key"
        self._private = None

    def private(self):
        """The private key: read from its file, made only when there is no file at all.

        Until 17.11.2026 any OSError (a lock, a virus scanner holding the file
        for a moment) made a new key and wrote it over the good one, so every
        password the gallery had sealed to it was lost. Now a file that cannot
        be read is tried again and then reported; a damaged one is reported
        too. Nothing ever overwrites an existing key file.
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

    def report(self) -> Dict[str, Any]:
        """What the gallery needs to seal to this PC: the public half only."""
        return {"v": 1, "public_key": self.public_b64(), "key_id": self.key_id()}

    def open(self, sealed_b64: str, sealed_for: str = "") -> str:
        """The password the gallery sealed for this PC; a box for another key is refused."""
        from nacl.exceptions import CryptoError
        from nacl.public import SealedBox
        if sealed_for and sealed_for != self.key_id():
            raise ValueError("Das Passwort ist für einen anderen Schlüssel dieses PCs verschlossen — "
                             "bitte in der Galerie neu eingeben.")
        try:
            clear = SealedBox(self.private()).decrypt(base64.b64decode(str(sealed_b64), validate=True)).decode("utf-8")
        except (CryptoError, ValueError, TypeError) as e:
            raise ValueError("Das verschlossene Passwort lässt sich hier nicht öffnen — bitte in der Galerie "
                             "neu eingeben.") from e
        if len(clear) > LIMIT or _CONTROL.search(clear):
            raise ValueError("Passwort: höchstens 256 Zeichen, keine Steuerzeichen")
        return clear
