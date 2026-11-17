"""Zweiter Faktor per Authenticator-App — optional, ohne fremden Dienst.

TOTP nach RFC 6238: sechs Ziffern, alle 30 Sekunden neu, berechnet aus einem
gemeinsamen Geheimnis und der Uhrzeit. Google Authenticator, Microsoft
Authenticator, Aegis, 1Password und alle anderen sprechen dasselbe.

Absichtlich hier statt als Abhängigkeit: Der ganze Algorithmus sind dreißig
Zeilen Standardbibliothek. Eine Bibliothek dafür wäre mehr Wartung als Nutzen,
und diese Galerie läuft auch selbst gehostet ohne Paketquelle.

Wichtig für den Betrieb: Der Code wird gegen ein Fenster von ±1 Schritt
geprüft (die Uhr des Telefons geht selten exakt), und ein einmal benutzter
Schritt gilt nicht noch einmal — sonst könnte jemand, der einen Code
mitgelesen hat, ihn innerhalb derselben halben Minute wiederverwenden.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP = 30
DIGITS = 6
WINDOW = 1          # ein Schritt vor und zurück — gegen ungenaue Uhren


def new_secret(length: int = 20) -> str:
    """Ein frisches Geheimnis als Base32, wie die Apps es erwarten."""
    return base64.b32encode(secrets.token_bytes(length)).decode("ascii").rstrip("=")


def normalize(secret: str) -> bytes:
    text = "".join(str(secret or "").split()).upper()
    return base64.b32decode(text + "=" * (-len(text) % 8), casefold=True)


def code_at(secret: str, when: Optional[float] = None, step_offset: int = 0) -> str:
    counter = int((when if when is not None else time.time()) // STEP) + step_offset
    digest = hmac.new(normalize(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def current_step(when: Optional[float] = None) -> int:
    return int((when if when is not None else time.time()) // STEP)


def verify(secret: str, code: str, when: Optional[float] = None,
           last_step: int = -1) -> tuple[bool, int]:
    """Prüfen. Rückgabe: (stimmt, benutzter Schritt).

    ``last_step`` ist der zuletzt akzeptierte Schritt; alles davor oder gleich
    wird abgelehnt, damit ein abgelesener Code nicht zweimal funktioniert.
    """
    clean = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(clean) != DIGITS or not secret:
        return False, -1
    base = current_step(when)
    for offset in range(-WINDOW, WINDOW + 1):
        step = base + offset
        if step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, when, offset), clean):
            return True, step
    return False, -1


def provisioning_uri(secret: str, account: str, issuer: str = "Photobox Gallery") -> str:
    """Die Adresse hinter dem QR-Code, den die Authenticator-App scannt."""
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")
