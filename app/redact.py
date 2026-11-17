"""Take guest addresses and secrets out of text that is shown again later.

Booth log lines travel to the gallery, sit in its database for 30 days and
are shown in the admin and handed to an assistant (``booth_diagnostics``).
A line like ``mail to anna.meier@example.org failed`` must not carry the
guest's address there (final review A4). This module is the gallery's one
place for that; the booth has a copy with the same rules in
``booth/boothcore/redact.py``, kept equal by a test.

What is changed:

* an e-mail address becomes ``a***@example.org`` (first character, domain);
* ``password=…``, ``pin: …``, ``token=…`` and the like become ``password=••••``;
* ``Bearer …`` and the long pairing code ``PBX1-…`` become ``••••``.
"""
from __future__ import annotations

import re
from typing import Any

MASK = "••••"

# A local part and a domain in any script (IDN: "jörg@müller.de", or its
# punycode "xn--mller-kva.de"), with "@" written as is or URL-encoded
# ("%40", "%2540"). The first character stays, the rest of the local part
# goes: "j***@müller.de".
_EMAIL = re.compile(r"(?<![\w.%+-])([\w%+-])[\w.%+-]*?(@|%(?:25)?40)"
                    r"((?:[\w-]+\.)+(?:[^\W\d_]{2,}|xn--[\w-]+))(?![\w-])")
_SECRET_PAIR = re.compile(
    r"(?i)\b(pin|admin_pin|passwor[dt]|passwd|pwd|kennwort|token|access_token|api_key|apikey|"
    r"secret|share_key|device_key|password_sealed|sealed_password|sealed|private_key|secret_key|key|k)(['\"]?\s*[=:]\s*)(['\"]?)[^\s'\",;&}]+")
# "Passwort ist X", "password is X", "PIN lautet 1234", "PIN 123456".
_SECRET_SAID = re.compile(r"(?i)\b(passwor[dt]|kennwort|pin|code|token|secret|geheimnis)(\s+(?:ist|is|lautet|war|was)\s+)"
                          r"(['\"„“]?)[^\s'\"“”]+")
_PIN_DIGITS = re.compile(r"(?i)\b(pin|tan|code)(\s+)(\d{4,10})\b")
# The Wi-Fi share QR text "WIFI:T:WPA;S:Name;P:<password>;;" (";" and "\\"
# escaped with a backslash inside a value).
_WIFI = re.compile(r"(?i)(WIFI:(?:[^\n]*?;)?P:)((?:\\.|[^;\\\n])*)")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
_PAIRING = re.compile(r"(?i)\bPBX1-[A-Za-z0-9_-]+")
# The short pairing code the gallery shows ("ABCD-EF23", PAIR_ALPHABET:
# capitals without I and O, digits 2-9), with or without the dash.
_PAIR_SHORT = re.compile(r"\b[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}\b")


def address(email: Any) -> str:
    """``anna@example.org`` → ``a***@example.org``; anything without a domain → ``***``."""
    local, _, domain = str(email or "").partition("@")
    return (local[:1] + "***@" + domain) if local and domain else "***"


def text(value: Any) -> str:
    """The same text without full e-mail addresses and without secrets."""
    out = str(value if value is not None else "")
    if not out:
        return out
    out = _EMAIL.sub(lambda m: f"{m.group(1)}***{m.group(2)}{m.group(3)}", out)
    out = _WIFI.sub(lambda m: f"{m.group(1)}{MASK}", out)
    out = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", out)
    out = _PAIRING.sub(MASK, out)
    out = _PAIR_SHORT.sub(MASK, out)
    out = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}", out)
    out = _SECRET_SAID.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}", out)
    out = _PIN_DIGITS.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    return out
