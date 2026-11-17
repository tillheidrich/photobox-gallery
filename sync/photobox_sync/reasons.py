"""Why an upload gave up, in a few plain words - for the operator, on the spot and remotely.

The queue stores the raw error a target raised ("422: {"ok": false, "error":
"no event assigned"}", "Datei fehlt: C:\\...\\<name>.jpg", "PermissionError:
[Errno 13] ..."). The live booth showed "8 Uploads endgültig fehlgeschlagen"
and nobody could tell why without opening the log (17.11.2026). ``explain``
turns a raw error into one short German reason that names no path, no file
and no guest; ``summary`` counts them for the heartbeat.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List

#: HTTP answers of the gallery that have a plain meaning.
_HTTP = {
    400: "Galerie lehnt ab: 400 (Anfrage ungültig)",
    403: "Galerie lehnt ab: 403 (Event gehört nicht zu dieser Box)",
    404: "Galerie lehnt ab: 404 (nicht gefunden)",
    409: "Galerie lehnt ab: 409 (Aufnahme gehört zu einem anderen Event)",
    410: "Galerie lehnt ab: 410 (Event abgelaufen)",
    413: "Galerie lehnt ab: 413 (Datei zu groß)",
    415: "Galerie lehnt ab: 415 (Dateityp)",
    422: "kein Event zugewiesen",
}

#: (pattern in the raw error, reason), first match wins. Lower case.
_RULES = [
    ("no event assigned", "kein Event zugewiesen"),
    ("event not assigned to this device", "Event gehört nicht zu dieser Box"),
    ("uid belongs to another event", "Aufnahme gehört zu einem anderen Event"),
    ("file type", "Dateityp nicht erlaubt"),
    ("datei fehlt", "Datei fehlt"),
    ("filenotfounderror", "Datei fehlt"),
    ("no such file", "Datei fehlt"),
    ("api-schlüssel ungültig", "API-Schlüssel ungültig"),
    ("invalid api key", "API-Schlüssel ungültig"),
    ("anderen schlüssel als beim einrichten", "Server-Schlüssel geändert — nichts gesendet"),
    ("anmeldung abgelehnt", "Anmeldung am Ziel abgelehnt"),
    ("ziel nicht konfiguriert", "Ziel nicht eingerichtet"),
    ("kein ordner eingetragen", "Zielordner nicht eingetragen"),
    ("ordner nicht da", "Zielordner fehlt"),
    ("permissionerror", "keine Schreibrechte am Ziel"),
    ("errno 13", "keine Schreibrechte am Ziel"),
    ("permission denied", "keine Schreibrechte am Ziel"),
    ("errno 28", "Ziel ist voll"),
    ("no space left", "Ziel ist voll"),
    ("prüfsumme", "Datei kam beschädigt an"),
    ("bytes angekommen", "Datei kam unvollständig an"),
    ("wlan-anmeldeseite", "WLAN verlangt eine Anmeldung"),
    ("nicht von der galerie", "Antwort kam nicht von der Galerie"),
    ("timeout", "Zeitüberschreitung"),
    ("timed out", "Zeitüberschreitung"),
    ("netz:", "kein Netz"),
]

_STATUS = re.compile(r"^\s*(?:http\s*)?(\d{3})\b", re.IGNORECASE)


def explain(error: str) -> str:
    """One short German reason for a raw upload error; never a path or a file name."""
    raw = str(error or "").strip()
    if not raw:
        return "unbekannt"
    low = raw.lower()
    for pattern, reason in _RULES:
        if pattern in low:
            return reason
    m = _STATUS.match(raw)
    if m:
        code = int(m.group(1))
        if code in _HTTP:
            return _HTTP[code]
        if 400 <= code < 600:
            return f"Galerie lehnt ab: {code}"
    m = re.match(r"^([A-Za-z]+(?:Error|Exception))\b", raw)
    if m:
        return f"Fehler an der Box ({m.group(1)})"
    return "Fehler am Ziel"


def summary(errors: Iterable[str], limit: int = 5) -> List[Dict[str, object]]:
    """[{"reason": ..., "count": n}], most frequent first, at most ``limit`` reasons."""
    counts = Counter(explain(e) for e in errors)
    return [{"reason": r, "count": n} for r, n in counts.most_common(max(1, int(limit)))]


def short(errors: Iterable[str], limit: int = 3) -> str:
    """"6× kein Event zugewiesen, 2× Datei fehlt" - for one line of text."""
    return ", ".join(f"{s['count']}× {s['reason']}" for s in summary(errors, limit))
