"""Die Box-Software verteilen — ohne App-Store, ohne Fremdserver.

Jede Box spricht ohnehin mit ihrer Galerie. Also lädt der Betreiber die neue
Fassung einmal hier hoch, und die Boxen holen sie sich beim nächsten Sync.
Kein zweiter Dienst, kein Konto, keine Signaturkette, die niemand pflegt —
aber auch nicht blind: Zu jeder Datei gehört eine Prüfsumme, und die Box
tauscht erst, wenn sie stimmt.

Ein Update ist damit: Datei hochladen, Haken bei „für alle Boxen freigeben",
fertig. Wer eine Box nicht mitnehmen will, lässt sie auf ihrer Fassung stehen
(Kanal ``halten``).

Was hier NICHT passiert: Die Galerie schickt nichts von sich aus los. Sie legt
die Datei bereit und sagt, dass es sie gibt. Die Box entscheidet, wann sie
tauscht — mitten im Abend soll sich kein Rechner selbst neu starten.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import db

#: Welche Programme verteilt werden. Der Name ist der Kanal.
PROGRAMME = {"booth": "Fotobox (PhotoboxBooth)", "sync": "Sync-Programm (PhotoboxSync)"}
ERLAUBTE_ENDUNGEN = (".exe", ".zip", ".dmg", ".tar.gz")
MAX_BYTES = 400 * 1024 * 1024

_VERSION = re.compile(r"^\d+(\.\d+){0,3}([-.][A-Za-z0-9]+)?$")


def init_schema() -> None:
    con = db.get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS software (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            program     TEXT NOT NULL,
            version     TEXT NOT NULL,
            file        TEXT NOT NULL,
            sha256      TEXT NOT NULL,
            size_bytes  INTEGER NOT NULL DEFAULT 0,
            notes       TEXT NOT NULL DEFAULT '',
            released    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_software_prog ON software(program, id);")
    con.commit()
    con.close()


def ordner() -> Path:
    from app import media
    d = media.assets_root() / "software"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pruefsumme(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


def gueltige_version(wert: str) -> str:
    wert = str(wert or "").strip()
    return wert if _VERSION.match(wert) else ""


def hinzufuegen(*, program: str, version: str, dateiname: str, daten: bytes,
                notes: str = "", released: bool = False) -> Dict[str, Any]:
    """Eine Fassung ablegen. Gleiche Version noch einmal = ersetzen."""
    init_schema()
    if program not in PROGRAMME:
        raise ValueError("Unbekanntes Programm")
    version = gueltige_version(version)
    if not version:
        raise ValueError("Version sieht nicht aus wie eine Version (z. B. 0.3.0)")
    if not dateiname.lower().endswith(ERLAUBTE_ENDUNGEN):
        raise ValueError("Dateityp passt nicht (.exe, .zip, .dmg, .tar.gz)")
    if len(daten) > MAX_BYTES:
        raise ValueError("Datei zu groß")

    endung = "".join(Path(dateiname).suffixes[-2:]) if dateiname.endswith(".tar.gz") else Path(dateiname).suffix
    ziel = f"{program}-{version}{endung}"
    (ordner() / ziel).write_bytes(daten)
    sha = pruefsumme(daten)

    con = db.get_db()
    con.execute("DELETE FROM software WHERE program=? AND version=?", (program, version))
    cur = con.execute("""INSERT INTO software(program, version, file, sha256, size_bytes, notes, released)
                         VALUES (?,?,?,?,?,?,?)""",
                      (program, version, ziel, sha, len(daten), str(notes or "")[:2000], 1 if released else 0))
    con.commit()
    neu = con.execute("SELECT * FROM software WHERE id=?", (cur.lastrowid,)).fetchone()
    con.close()
    return dict(neu)


def liste(program: Optional[str] = None) -> List[Dict[str, Any]]:
    init_schema()
    con = db.get_db()
    if program:
        rows = con.execute("SELECT * FROM software WHERE program=? ORDER BY id DESC", (program,)).fetchall()
    else:
        rows = con.execute("SELECT * FROM software ORDER BY program, id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def freigeben(software_id: int, released: bool) -> None:
    init_schema()
    con = db.get_db()
    con.execute("UPDATE software SET released=? WHERE id=?", (1 if released else 0, int(software_id)))
    con.commit()
    con.close()


def loeschen(software_id: int) -> Optional[Dict[str, Any]]:
    init_schema()
    con = db.get_db()
    row = con.execute("SELECT * FROM software WHERE id=?", (int(software_id),)).fetchone()
    if row:
        con.execute("DELETE FROM software WHERE id=?", (int(software_id),))
        con.commit()
    con.close()
    if row:
        (ordner() / row["file"]).unlink(missing_ok=True)
    return dict(row) if row else None


def _teile(version: str):
    """Version in vergleichbare Zahlen zerlegen; Unbekanntes zählt als 0."""
    zahlen = []
    for stueck in re.split(r"[.\-]", str(version or "")):
        zahlen.append(int(stueck) if stueck.isdigit() else 0)
    return tuple(zahlen + [0] * (4 - len(zahlen)))[:4]


def neuer_als(kandidat: str, laufend: str) -> bool:
    return _teile(kandidat) > _teile(laufend)


def angebot(program: str, laufende_version: str, base_url: str) -> Optional[Dict[str, Any]]:
    """Was diese Box beim nächsten Sync angeboten bekommt — oder nichts.

    Angeboten wird nur, was freigegeben ist und neuer als das Laufende. Nichts
    wird erzwungen: Die Box bekommt eine Adresse und eine Prüfsumme.
    """
    init_schema()
    con = db.get_db()
    rows = con.execute("SELECT * FROM software WHERE program=? AND released=1", (program,)).fetchall()
    con.close()
    if not rows:
        return None
    # Die höchste Version gewinnt, nicht die zuletzt hochgeladene: Wer eine
    # alte Fassung nachträglich ablegt, soll damit niemanden zurückwerfen.
    row = max(rows, key=lambda r: _teile(r["version"]))
    if not neuer_als(row["version"], laufende_version or "0"):
        return None
    return {"version": row["version"], "notes": row["notes"], "sha256": row["sha256"],
            "size": int(row["size_bytes"]), "file": row["file"],
            "url": f"{base_url.rstrip('/')}/api/v2/booth/software/{row['file']}"}
