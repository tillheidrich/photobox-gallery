"""Eingang: Aufnahmen per FTP und SFTP annehmen — auch von fremden Programmen.

dslrBooth, LumaBooth, Darkroom und die meisten anderen Aufnahme-Programme
können eines von Haus aus: eine fertige Datei auf einen FTP- oder SFTP-Server
legen. Unser Sync-Programm braucht man dafür nicht, und auf manchen
Box-Rechnern darf man auch gar nichts installieren.

Deshalb macht die Galerie selbst einen Eingang auf. Jedes Event bekommt ein
eigenes Konto; wer sich damit anmeldet, landet in einem Ordner, der nur diesem
Event gehört, und sieht von der übrigen Instanz nichts. Sobald eine Datei
fertig geschrieben ist, wandert sie in die Galerie und verschwindet aus dem
Eingang — dort sammelt sich nichts an.

**Aus, bis jemand ihn einschaltet.** Ein offener Port ist eine Entscheidung,
keine Voreinstellung. Und wer ihn einschaltet, bekommt FTP im Klartext nur,
wenn er es ausdrücklich verlangt: Voreinstellung ist FTPS, daneben steht SFTP.
Ein Kennwort, das über ein fremdes Veranstaltungs-WLAN im Klartext geht, ist
ein verschenktes Kennwort.

**Was ein Konto darf:** hochladen, Ordner anlegen, eigene Dateien überschreiben.
Es darf nicht: aus dem eigenen Ordner heraus, fremde Events sehen, oder etwas
aus der Galerie löschen. Gelöscht wird in der Galerie, nicht über den Eingang.

Das Kennwort steht nur als Streuwert in der Datenbank. Beim Anlegen wird es
einmal angezeigt — danach nie wieder; wer es verliert, erzeugt ein neues.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash

from app import db

log = logging.getLogger("photobox.eingang")

#: Voreinstellungen. Die Ports liegen über 1024, damit der Dienst nicht als
#: root laufen muss; wer 21 oder 22 nach außen will, leitet davor um.
STANDARD = {
    "eingang_ftp": "0",          # aus
    "eingang_ftp_port": "2121",
    "eingang_ftp_tls": "1",      # FTPS verlangen
    "eingang_ftp_klartext": "0",  # unverschlüsseltes FTP ausdrücklich erlauben
    "eingang_ftp_passiv": "50000-50100",
    "eingang_sftp": "0",
    "eingang_sftp_port": "2222",
}

#: Was angenommen wird. Alles andere landet gar nicht erst in der Galerie.
ENDUNGEN = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm"}

#: Größte Datei, die der Eingang annimmt (ein 4K-Video von zehn Sekunden
#: liegt deutlich darunter).
MAX_BYTES = 512 * 1024 * 1024

#: So lange muss eine Datei unverändert sein, bevor sie eingelesen wird.
#: FTP meldet das Ende zwar selbst, aber nicht jeder Client schließt sauber,
#: und über SFTP kommt gar keine Meldung.
RUHE_SEKUNDEN = 3.0


def init_schema(cur: sqlite3.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS eingang_konten (
            event_id INTEGER PRIMARY KEY,
            benutzer TEXT NOT NULL UNIQUE,
            passwort_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_login TEXT,
            last_ip TEXT,
            dateien INTEGER NOT NULL DEFAULT 0
        );""")


# ── Einstellungen ─────────────────────────────────────────────

def einstellung(schluessel: str) -> str:
    wert = db.get_setting(schluessel)
    if wert is None or wert == "":
        return STANDARD.get(schluessel, "")
    return str(wert)


def an(dienst: str) -> bool:
    return einstellung(f"eingang_{dienst}") == "1"


def wurzel() -> Path:
    """Wo die Eingangsordner liegen — außerhalb von ``storage/``.

    Damit kann ein Konto nie in den öffentlichen Bereich schreiben, auch wenn
    an der Abschottung einmal etwas schiefgeht.
    """
    from app import media

    pfad = media.data_dir() / "eingang"
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


def ordner_fuer(slug: str) -> Path:
    pfad = wurzel() / re.sub(r"[^a-z0-9_-]", "", str(slug or "").lower())[:80]
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


# ── Konten ────────────────────────────────────────────────────

def konto(event_id: int) -> Optional[Dict[str, Any]]:
    con = db.get_db()
    try:
        zeile = con.execute("SELECT * FROM eingang_konten WHERE event_id=?",
                            (int(event_id),)).fetchone()
    finally:
        con.close()
    return dict(zeile) if zeile else None


def konten() -> List[Dict[str, Any]]:
    con = db.get_db()
    try:
        return [dict(z) for z in con.execute(
            "SELECT k.*, e.name AS event_name, e.slug AS event_slug FROM eingang_konten k "
            "JOIN events e ON e.id = k.event_id ORDER BY e.name")]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def neues_passwort(event_id: int) -> str:
    """Konto anlegen oder Kennwort erneuern. Gibt es genau einmal zurück."""
    event = db.get_event_by_id(int(event_id))
    if not event:
        raise ValueError("Dieses Event gibt es nicht.")
    benutzer = str(event["slug"])
    # Ohne mehrdeutige Zeichen: Das hier wird abgetippt, oft von einem Zettel.
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    passwort = "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(3))
    con = db.get_db()
    try:
        con.execute(
            "INSERT INTO eingang_konten (event_id, benutzer, passwort_hash) VALUES (?,?,?) "
            "ON CONFLICT(event_id) DO UPDATE SET benutzer=excluded.benutzer, "
            "passwort_hash=excluded.passwort_hash",
            (int(event_id), benutzer, generate_password_hash(passwort)))
        con.commit()
    finally:
        con.close()
    ordner_fuer(benutzer)
    return passwort


def konto_loeschen(event_id: int) -> None:
    con = db.get_db()
    try:
        con.execute("DELETE FROM eingang_konten WHERE event_id=?", (int(event_id),))
        con.commit()
    finally:
        con.close()


def pruefe(benutzer: str, passwort: str, ip: str = "") -> Optional[Dict[str, Any]]:
    """Anmeldung prüfen. ``None`` heißt: nein, und zwar ohne Auskunft darüber, warum."""
    name = str(benutzer or "").strip().lower()
    if not name:
        return None
    con = db.get_db()
    try:
        zeile = con.execute("SELECT * FROM eingang_konten WHERE benutzer=?", (name,)).fetchone()
    finally:
        con.close()
    if not zeile or not check_password_hash(zeile["passwort_hash"], passwort or ""):
        return None
    event = db.get_event_by_id(int(zeile["event_id"]))
    if not event:
        return None
    con = db.get_db()
    try:
        con.execute("UPDATE eingang_konten SET last_login=datetime('now'), last_ip=? WHERE event_id=?",
                    (str(ip or "")[:60], int(zeile["event_id"])))
        con.commit()
    finally:
        con.close()
    return {"event_id": int(zeile["event_id"]), "slug": event["slug"],
            "ordner": str(ordner_fuer(event["slug"]))}


# ── Einlesen ──────────────────────────────────────────────────

def art_aus_name(pfad: Path) -> str:
    """Foto, Druck, GIF oder Video — geraten wie beim Sync-Programm.

    dslrBooth legt Drucke unter ``*print*`` ab, Originale unter ``*original*``.
    Dieselben Regeln gelten hier, damit dieselbe Software dasselbe Ergebnis
    liefert, egal welchen Weg sie nimmt.
    """
    tief = str(pfad).lower().replace("\\", "/")
    endung = pfad.suffix.lower()
    if endung == ".gif":
        return "gif"
    if endung in (".mp4", ".mov", ".webm"):
        return "boomerang" if "boomerang" in tief else "video"
    if "print" in tief or "collage" in tief:
        return "print"
    if "original" in tief:
        return "original"
    return "photo"


class _AlsUpload:
    """Eine Datei auf der Platte so anbieten, wie ein Formular-Upload aussieht.

    So läuft der Eingang durch genau denselben Weg wie jeder andere Upload —
    inklusive Vorschaubildern, Prüfsumme und dem Moderations-Trigger. Zwei
    Wege in die Galerie, die sich unterschiedlich verhalten, wären zwei Wege,
    die man einzeln absichern müsste.
    """

    def __init__(self, pfad: Path, name: str):
        self.filename = name
        self._pfad = pfad

    def save(self, ziel) -> None:
        ziel = Path(ziel)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self._pfad), str(ziel))


def uebernimm(event_id: int, pfad: Path) -> Optional[int]:
    """Eine Datei aus dem Eingang in die Galerie holen. Gibt die Kennung zurück."""
    from app import media

    if pfad.suffix.lower() not in ENDUNGEN:
        log.info("Eingang: %s übergangen (keine Aufnahme)", pfad.name)
        pfad.unlink(missing_ok=True)
        return None
    if pfad.stat().st_size > MAX_BYTES:
        log.warning("Eingang: %s ist zu groß (%s Bytes)", pfad.name, pfad.stat().st_size)
        return None
    art = art_aus_name(pfad)
    ergebnis = media.save_upload(int(event_id), _AlsUpload(pfad, pfad.name), source="ftp")
    kennung = int(ergebnis.get("id") or 0)
    if kennung and art != "photo":
        con = db.get_db()
        try:
            con.execute("UPDATE media SET kind=? WHERE id=?", (art, kennung))
            con.commit()
        finally:
            con.close()
    con = db.get_db()
    try:
        con.execute("UPDATE eingang_konten SET dateien = dateien + 1 WHERE event_id=?",
                    (int(event_id),))
        con.commit()
    finally:
        con.close()
    log.info("Eingang: %s übernommen (%s, Event %s)", pfad.name, art, event_id)
    return kennung


def einlesen() -> int:
    """Alles, was fertig im Eingang liegt, in die Galerie holen.

    Läuft im Hintergrund und nach jedem gemeldeten Dateiende. Eine Datei gilt
    als fertig, wenn sie ``RUHE_SEKUNDEN`` lang unverändert ist — FTP meldet
    das Ende zwar, aber nicht jeder Client schließt sauber, und über SFTP
    kommt gar keine Meldung.
    """
    jetzt = time.time()
    genommen = 0
    for ordner in sorted(wurzel().iterdir()):
        if not ordner.is_dir():
            continue
        event = db.get_event_by_slug(ordner.name)
        if not event:
            continue
        for pfad in sorted(ordner.rglob("*")):
            if not pfad.is_file() or pfad.name.startswith("."):
                continue
            try:
                if jetzt - pfad.stat().st_mtime < RUHE_SEKUNDEN:
                    continue
                if uebernimm(int(event["id"]), pfad):
                    genommen += 1
            except FileNotFoundError:
                continue
            except Exception as e:              # noqa: BLE001 — eine kaputte Datei hält nichts auf
                log.error("Eingang: %s nicht übernommen: %s", pfad.name, e)
    return genommen


__all__ = ["ENDUNGEN", "MAX_BYTES", "RUHE_SEKUNDEN", "STANDARD", "an", "art_aus_name",
           "einlesen", "einstellung", "init_schema", "konten", "konto", "konto_loeschen",
           "neues_passwort", "ordner_fuer", "pruefe", "uebernimm", "wurzel"]
