"""Pairing code: one string instead of an address and a 40-character key.

The event PC and the gallery do not find each other by themselves — on purpose.
There is no broadcast, no discovery, no way in from outside: the device key is
the only door. But handing that key over by hand is the step people get wrong,
usually at a venue, usually with a phone keyboard.

So the gallery hands out one code that carries both halves:

    PBX1-<base64url of {"u": address, "k": key, "n": name}>-<4 characters check>

Copy in the gallery, paste on the PC, done. The check characters catch the
classic broken paste — a code cut in half is refused instead of silently
writing a wrong key that only fails hours later.

This is not encryption and not meant to be: the code carries the key in plain
sight, exactly like the key field it replaces. It is treated as a secret, shown
only to a logged-in admin, and never logged.

``app/pairing.py`` in the gallery mirrors this format; a test keeps both sides
speaking the same language.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Dict

PREFIX = "PBX1"

# Kurzcode: acht Zeichen, keine Verwechslungen (kein I, O, 0, 1). Der lange Code
# trägt alles in sich, der kurze holt sich den Schlüssel beim ersten Kontakt ab —
# dafür kann man ihn vorlesen und abtippen.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PRODUCT_DOMAIN = "photobox-gallery.com"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _check(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:4]


def make_code(url: str, api_key: str, name: str = "") -> str:
    """Build the code the gallery shows. Empty address or key is refused."""
    url = (url or "").strip().rstrip("/")
    api_key = (api_key or "").strip()
    if not url or not api_key:
        raise ValueError("Adresse und Schlüssel werden beide gebraucht")
    payload = {"u": url, "k": api_key}
    if name:
        payload["n"] = name[:60]
    body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{PREFIX}-{body}-{_check(body)}"


def read_code(code: str) -> Dict[str, str]:
    """Take the code apart. Raises ValueError with a sentence a human can act on."""
    text = "".join((code or "").split())          # Zeilenumbrüche aus dem Kopieren
    parts = text.split("-")
    if len(parts) != 3 or parts[0].upper() != PREFIX:
        raise ValueError("Das sieht nicht nach einem Kopplungscode aus")
    body, given = parts[1], parts[2].lower()
    if _check(body) != given:
        raise ValueError("Der Code ist unvollständig oder verändert — bitte noch einmal kopieren")
    try:
        data = json.loads(_unb64(body).decode("utf-8"))
        url, key = str(data["u"]), str(data["k"])
    except Exception as e:
        raise ValueError("Der Code lässt sich nicht lesen") from e
    if not url.startswith(("http://", "https://")) or not key:
        raise ValueError("Der Code enthält keine gültige Galerie-Adresse")
    return {"url": url.rstrip("/"), "api_key": key, "name": str(data.get("n") or "")}


def _clean_code(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch in ALPHABET)


def parse_input(text: str, known_url: str = "") -> Dict[str, str]:
    """Verstehen, was da eingefügt wurde.

    Vier Schreibweisen, weil vor Ort keine davon erklärt werden kann:

    * ``PBX1-…``                     — der lange Code, trägt alles in sich
    * ``demo-ABCD-EFGH``             — Kurzcode einer Galerie unter photobox-gallery.com
    * ``meine-galerie.de ABCD-EFGH`` — Adresse und Kurzcode, durch Leerzeichen oder /
    * ``ABCD-EFGH``                  — nur der Code, wenn die Adresse schon eingetragen ist
      (der Fall nach „Schlüssel erneuern")

    Rückgabe: ``{"mode": "self"}`` mit Adresse und Schlüssel, oder
    ``{"mode": "fetch"}`` mit Adresse und Code — den holt der Aufrufer ab.
    """
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("Bitte den Kopplungscode aus der Galerie einfügen")
    if text.upper().startswith(PREFIX + "-"):
        return {"mode": "self", **read_code(text)}

    # Das „//" in https:// darf beim Trennen nicht mitzählen
    parts = [p.replace("\x00", "://") for p in re.split(r"[\s/]+", text.replace("://", "\x00")) if p]
    host_part = parts[0] if len(parts) > 1 else ""
    rest = "".join(parts[1:]) if len(parts) > 1 else parts[0]

    # Von rechts einsammeln, bis acht Zeichen zusammen sind — was davor steht,
    # ist der Name der Galerie und nicht Teil des Codes.
    segments = [seg for seg in rest.split("-") if seg]
    code = ""
    while segments and len(code) < 8:
        last = segments[-1].upper()
        if len(last) + len(code) > 8 or any(ch not in ALPHABET for ch in last):
            break
        code = last + code
        segments.pop()
    if len(code) != 8:
        raise ValueError("Ein Kopplungscode hat acht Zeichen — zum Beispiel ABCD-EFGH")

    tenant = "-".join(segments).strip(" .-")
    if host_part:
        host = host_part.rstrip("/")
        url = host if host.startswith(("http://", "https://")) else "https://" + host
    elif tenant:
        url = f"https://{tenant.lower()}.{PRODUCT_DOMAIN}"
    else:
        url = (known_url or "").rstrip("/")
    if not url:
        raise ValueError("Zu diesem Code fehlt die Galerie-Adresse — "
                         "bitte den langen Code nehmen oder die Adresse dazuschreiben")
    return {"mode": "fetch", "url": url, "code": code}


class PairingError(ValueError):
    """A refused pairing, with a sentence for a human and a machine-readable reason.

    ``reason``: ``malformed`` (not a code), ``expired``, ``used``, ``unknown``
    (the gallery knows no such code), ``unreachable``, ``too_many``, ``refused``.
    Still a ValueError, so callers that only catch that keep working.
    """

    def __init__(self, message: str, reason: str = "refused"):
        super().__init__(message)
        self.reason = reason


def fetch_key(url: str, code: str, verify_tls: bool = True, user_agent: str = "photobox-sync",
              timeout: float = 15) -> Dict[str, str]:
    """Trade a short code for the device key at ``url``/api/v2/pair.

    Used by the stand-alone sync program and by the booth, so both say the
    same sentences. Never logs or returns the code. Returns ``api_key``,
    ``name`` (the device's name in the gallery), ``gallery_name`` and, when
    the gallery states one, its own ``url``.
    """
    import httpx

    try:
        r = httpx.post(f"{url.rstrip('/')}/api/v2/pair", json={"code": code}, timeout=timeout,
                       headers={"User-Agent": user_agent}, verify=verify_tls)
    except httpx.HTTPError as e:
        raise PairingError(f"{url} ist nicht erreichbar ({e.__class__.__name__}) — "
                           "Netz prüfen oder den langen Code nehmen", "unreachable")
    if r.status_code == 429:
        raise PairingError("Zu viele Versuche — bitte ein paar Minuten warten", "too_many")
    if r.status_code == 404:
        try:
            reason = str((r.json() or {}).get("reason") or "")
        except ValueError:
            reason = ""
        if reason == "used":
            raise PairingError("Dieser Code wurde schon eingelöst — ein Code gilt nur einmal. "
                               "In der Galerie einen neuen erzeugen", "used")
        if reason == "expired":
            raise PairingError("Dieser Code ist abgelaufen (er gilt 15 Minuten) — "
                               "in der Galerie einen neuen erzeugen", "expired")
        raise PairingError("Code ungültig oder abgelaufen — Tippfehler prüfen oder "
                           "in der Galerie einen neuen erzeugen", "unknown")
    if r.status_code >= 400:
        raise PairingError(f"Galerie antwortet mit {r.status_code}", "refused")
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict) or not data.get("api_key"):
        raise PairingError("Die Galerie hat keinen Schlüssel geschickt", "refused")
    out = {"api_key": str(data["api_key"]), "name": str(data.get("device_name") or ""),
           "gallery_name": str(data.get("gallery_name") or "")[:80]}
    if str(data.get("url") or "").startswith(("http://", "https://")):
        out["url"] = str(data["url"]).rstrip("/")
    return out


FILE_NAMES = ("kopplung.txt", "pairing.txt")


def redeem_file(folders, pair, log) -> bool:
    """Pairing without a keyboard: a ``kopplung.txt`` (or ``pairing.txt``) in one of ``folders``.

    ``pair`` takes the file's text and raises ValueError with a sentence when
    the code is refused. On success the file is first overwritten and then
    deleted — renaming was not enough, the long code carries the device key in
    plain text and lay on under a new name (found in the review of
    19.09.2026); a note says when it was redeemed. On failure the file stays
    and the reason is logged, never the code. Only the first file found is
    tried. Returns True when a file was redeemed.
    """
    import time
    from pathlib import Path

    for folder in folders:
        for name in FILE_NAMES:
            f = Path(folder) / name
            if not f.is_file():
                continue
            try:
                pair(f.read_text(encoding="utf-8"))
            except (ValueError, OSError) as e:
                log.warning("%s nicht eingelöst: %s", f.name, e)
                return False
            try:
                f.with_suffix(".eingeloest.txt").write_text(
                    "Kopplung eingelöst am " + time.strftime("%d.%m.%Y %H:%M") + ".\n"
                    "Der Code stand hier und ist entfernt — er trägt den Schlüssel im Klartext.\n",
                    encoding="utf-8")
                try:
                    f.write_text("eingelöst\n", encoding="utf-8")   # erst überschreiben,
                finally:
                    f.unlink(missing_ok=True)                        # dann weg
            except OSError as e:
                log.warning("%s eingelöst, aber nicht gelöscht: %s — bitte von Hand löschen", f.name, e)
            log.info("Gekoppelt über %s — die Datei wurde entfernt", f.name)
            return True
    return False
