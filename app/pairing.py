"""Pairing code for a device — the gallery's half of the handshake.

The format is mirrored in ``sync/photobox_sync/pairing.py``; the sync program
must be able to read what this writes, so the format lives in both places and
``tests/test_pairing.py`` keeps them honest. It is small and stable enough that
a shared import across two separately installable packages would cost more than
it saves.

The code carries the device key in plain sight, exactly like the key field next
to it. It is shown to a logged-in admin and nowhere else — never in a log, never
in a page that guests can reach.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Dict

PREFIX = "PBX1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _check(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:4]


def make_code(url: str, api_key: str, name: str = "") -> str:
    url = (url or "").strip().rstrip("/")
    api_key = (api_key or "").strip()
    if not url or not api_key:
        raise ValueError("address and key are both required")
    payload = {"u": url, "k": api_key}
    if name:
        payload["n"] = name[:60]
    body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{PREFIX}-{body}-{_check(body)}"


def read_code(code: str) -> Dict[str, str]:
    text = "".join((code or "").split())
    parts = text.split("-")
    if len(parts) != 3 or parts[0].upper() != PREFIX:
        raise ValueError("not a pairing code")
    body, given = parts[1], parts[2].lower()
    if _check(body) != given:
        raise ValueError("pairing code is incomplete or altered")
    try:
        data = json.loads(_unb64(body).decode("utf-8"))
        url, key = str(data["u"]), str(data["k"])
    except Exception as e:
        raise ValueError("pairing code is unreadable") from e
    if not url.startswith(("http://", "https://")) or not key:
        raise ValueError("pairing code carries no valid gallery address")
    return {"url": url.rstrip("/"), "api_key": key, "name": str(data.get("n") or "")}
