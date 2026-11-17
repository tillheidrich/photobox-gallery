"""Änderungsprotokoll: wer hat wann was umgestellt.

Eine Galerie, an der mehrere Menschen arbeiten, kennt irgendwann die Frage
„seit wann ist der Download aus?" — und niemand weiß es mehr. Das Löschprotokoll
(``app/retention.py``) beantwortet sie für Aufnahmen. Diese Datei beantwortet
sie für alles andere: Einstellungen, Events, Boxen, Box-Profile.

**Wie es aufgezeichnet wird.** Nicht, indem jede einzelne Formularroute einen
Aufruf einbaut — das wäre an dem Tag unvollständig, an dem jemand eine Route
hinzufügt und den Aufruf vergisst. Stattdessen wird vor jeder ändernden
Anfrage an die Verwaltung ein Schnappschuss der betroffenen Tabellen gezogen
und danach noch einer; was sich unterscheidet, steht im Protokoll. Neue Routen
sind damit vom ersten Tag an erfasst, ohne dass jemand daran denken muss.

Was das nicht kann, sagt es auch: Was nicht in diesen Tabellen steht — eine
gelöschte Aufnahme, eine hochgeladene Datei —, taucht nur als Vorgang auf
(„das wurde aufgerufen"), nicht als Vorher/Nachher. Für Aufnahmen gibt es das
Löschprotokoll, das genau dafür gebaut ist.

**Was nicht hineingeschrieben wird.** Kennwörter, Schlüssel, Token: Dass sich
ein Geheimnis geändert hat, steht drin, der Wert nie — weder der alte noch der
neue. Ein Protokoll, das Zugangsdaten sammelt, ist selbst das Leck.

Die IP-Adresse wird gekürzt gespeichert (letztes Feld auf 0, bei IPv6 die
hinteren Blöcke). Das genügt für „aus dem Büro oder von unterwegs" und macht
aus dem Protokoll keine Aufenthaltsaufzeichnung, die man begründen müsste.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from app import db

#: Wie lange Einträge stehen bleiben. Ein Protokoll, das nie aufräumt, wird zur
#: eigenen Altlast — und ein Jahr deckt jede Frage ab, die noch jemand stellt.
AUFBEWAHRUNG_TAGE = 365

#: Obergrenze für die Zahl der Vorgänge. Greift, bevor das Alter greift.
MAX_VORGAENGE = 20000

#: Ab wie vielen Werten ein Schnappschuss abgebrochen wird. Bei einer Galerie
#: mit sehr vielen Events wäre der Vergleich teurer als der Nutzen; dann wird
#: der Vorgang ohne Vorher/Nachher festgehalten, und das steht auch dabei.
MAX_WERTE = 20000

#: Wortteile, die einen Wert zum Geheimnis machen. Bewusst großzügig: lieber
#: eine harmlose Einstellung maskiert als ein Schlüssel im Klartext.
GEHEIM = ("passwor", "kennwort", "hash", "token", "secret", "geheim",
          "api_key", "apikey", "_key", "key_", "pin", "salt", "credential")

#: Spalten, die sich von selbst ändern. Sie im Protokoll zu führen hieße, jede
#: Seite mit Rauschen zu füllen, das niemand gefragt hat.
FLUECHTIG = {"updated_at", "created_at", "last_seen_at", "last_ip", "last_check",
             "last_result", "profile_version", "delivered_at", "done_at"}

#: Spalten, deren Inhalt JSON ist und deshalb aufgefaltet wird: Aus
#: ``gallery_json`` wird ``downloads``, ``moderation``, ``aussehen.akzent`` …
JSON_SPALTEN = {"gallery_json", "profile_json"}

#: Der maskierte Wert. Kein Sternchenmuster in Originallänge — auch die Länge
#: eines Kennworts ist eine Auskunft.
MASKE = "••••••"

#: Wie lang ein Wert im Protokoll werden darf.
MAX_WERT = 400


# ── Schema ────────────────────────────────────────────────────

def init_schema(cur: sqlite3.Cursor) -> None:
    """Wird aus ``ecosystem.init_schema`` heraus aufgerufen — eine Stelle."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_vorgaenge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL DEFAULT (datetime('now')),
            wer TEXT NOT NULL DEFAULT '',
            ip TEXT NOT NULL DEFAULT '',
            methode TEXT NOT NULL DEFAULT '',
            pfad TEXT NOT NULL DEFAULT '',
            was TEXT NOT NULL DEFAULT '',
            ergebnis TEXT NOT NULL DEFAULT ''
        );""")
    cur.execute("CREATE INDEX IF NOT EXISTS admin_vorgaenge_zeit ON admin_vorgaenge(at);")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_aenderungen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vorgang_id INTEGER NOT NULL REFERENCES admin_vorgaenge(id) ON DELETE CASCADE,
            gegenstand TEXT NOT NULL DEFAULT '',
            feld TEXT NOT NULL DEFAULT '',
            vorher TEXT,
            nachher TEXT
        );""")
    cur.execute("CREATE INDEX IF NOT EXISTS admin_aenderungen_vorgang ON admin_aenderungen(vorgang_id);")


# ── Werte aufbereiten ─────────────────────────────────────────

def ist_geheim(feld: str) -> bool:
    name = str(feld or "").lower()
    return any(teil in name for teil in GEHEIM)


def kuerze_ip(roh: str) -> str:
    """Letztes Feld auf 0 — aus 192.168.10.37 wird 192.168.10.0.

    Bei IPv6 fallen die hinteren Blöcke weg; übrig bleibt das Präfix, das den
    Anschluss grob verortet, nicht das Gerät.
    """
    wert = str(roh or "").strip()
    if not wert:
        return ""
    if ":" in wert:
        teile = [t for t in wert.split(":") if t != ""][:3]
        return ":".join(teile) + "::" if teile else ""
    teile = wert.split(".")
    if len(teile) == 4:
        return ".".join(teile[:3] + ["0"])
    return wert


def _text(wert: Any) -> Optional[str]:
    """Einen Datenbankwert in etwas verwandeln, das man lesen kann."""
    if wert is None:
        return None
    if isinstance(wert, bool):
        return "an" if wert else "aus"
    if isinstance(wert, (dict, list)):
        wert = json.dumps(wert, ensure_ascii=False, sort_keys=True)
    wert = str(wert)
    return wert if len(wert) <= MAX_WERT else wert[:MAX_WERT] + " …"


def _falte(ziel: Dict[Tuple[str, str], Optional[str]], gegenstand: str,
           praefix: str, wert: Any) -> None:
    """Verschachteltes JSON zu Punktnamen auffalten: ``aussehen.akzent``."""
    if isinstance(wert, dict):
        for schluessel, unterwert in wert.items():
            _falte(ziel, gegenstand, f"{praefix}.{schluessel}" if praefix else str(schluessel), unterwert)
        return
    ziel[(gegenstand, praefix)] = _text(wert)


# ── Schnappschuss ─────────────────────────────────────────────

def _zeile(ziel: Dict[Tuple[str, str], Optional[str]], gegenstand: str,
           zeile: Dict[str, Any]) -> None:
    for spalte, wert in zeile.items():
        if spalte in FLUECHTIG or spalte == "id":
            continue
        if spalte in JSON_SPALTEN:
            try:
                daten = json.loads(wert) if wert else {}
            except (ValueError, TypeError):
                daten = None
            if isinstance(daten, dict):
                _falte(ziel, gegenstand, "", daten)
                continue
        ziel[(gegenstand, spalte)] = _text(wert)


#: Welches Event, welches Gerät: In der Verwaltung steht die Kennung im Pfad.
_EVENT_IM_PFAD = re.compile(r"/events/(\d+)")
_GERAET_IM_PFAD = re.compile(r"/devices/(\d+)")


def bereich(pfad: str) -> Tuple[Optional[int], Optional[int]]:
    """Was diese Anfrage überhaupt berühren kann — Event, Gerät oder keins.

    Eine Galerie mit vierhundert Events zweimal je Klick vollständig zu lesen,
    wäre Aufwand für nichts: Ein Formular unter ``/admin/events/7/edit`` ändert
    Event 7 und sonst keines.
    """
    e = _EVENT_IM_PFAD.search(pfad or "")
    g = _GERAET_IM_PFAD.search(pfad or "")
    return (int(e.group(1)) if e else None, int(g.group(1)) if g else None)


def schnappschuss(event_id: Optional[int] = None,
                  device_id: Optional[int] = None) -> Optional[Dict[Tuple[str, str], Optional[str]]]:
    """Der Stand der Dinge, die sich über die Verwaltung ändern lassen.

    Die Einstellungen kommen vollständig — es sind wenige, und fast jede
    Umstellung landet dort. Von Events und Geräten kommt nur, was verrät,
    dass eines dazugekommen oder verschwunden ist; alle Spalten liefert nur
    das eine, das im Pfad steht. Damit bleibt der Vergleich gleich schnell,
    ob eine Galerie drei Events hat oder vierhundert.

    Was das kostet: Eine Umstellung, die viele Events auf einmal betrifft,
    ohne eines zu nennen, erscheint als Vorgang ohne Vorher/Nachher. Das steht
    dann auch so da, statt stillschweigend zu fehlen.

    ``None`` heißt: nicht lesbar oder zu viel — der Vorgang wird trotzdem
    festgehalten, nur ohne Vergleich.
    """
    aus: Dict[Tuple[str, str], Optional[str]] = {}
    try:
        con = db.get_db()
    except Exception:                       # noqa: BLE001 — ohne Datenbank kein Protokoll, aber auch kein Fehler
        return None
    try:
        for zeile in con.execute("SELECT key, value FROM settings"):
            aus[("Einstellungen", str(zeile["key"]))] = _text(zeile["value"])
        for zeile in con.execute("SELECT id, name FROM events ORDER BY id"):
            aus[(f"Event „{zeile['name']}“ (#{zeile['id']})", "name")] = _text(zeile["name"])
        for zeile in con.execute("SELECT id, name FROM devices ORDER BY id"):
            aus[(f"Gerät „{zeile['name']}“ (#{zeile['id']})", "name")] = _text(zeile["name"])
        if event_id:
            zeile = con.execute("SELECT * FROM events WHERE id=?", (int(event_id),)).fetchone()
            if zeile:
                _zeile(aus, f"Event „{zeile['name']}“ (#{zeile['id']})", dict(zeile))
        if device_id:
            zeile = con.execute("SELECT * FROM devices WHERE id=?", (int(device_id),)).fetchone()
            if zeile:
                _zeile(aus, f"Gerät „{zeile['name']}“ (#{zeile['id']})", dict(zeile))
        if len(aus) > MAX_WERTE:
            return None
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return aus


def vergleiche(vorher: Dict[Tuple[str, str], Optional[str]],
               nachher: Dict[Tuple[str, str], Optional[str]]) -> List[Dict[str, Any]]:
    """Was sich unterscheidet — ein Eintrag je Wert.

    Ist ein ganzer Gegenstand neu oder weg, steht das als eine Zeile da und
    nicht als dreißig. Wer ein Event anlegt, will „angelegt" lesen, nicht jede
    einzelne Spalte.
    """
    alte_gegenstaende = {g for g, _ in vorher}
    neue_gegenstaende = {g for g, _ in nachher}
    aus: List[Dict[str, Any]] = []

    for gegenstand in sorted(neue_gegenstaende - alte_gegenstaende):
        aus.append({"gegenstand": gegenstand, "feld": "", "vorher": None, "nachher": "angelegt"})
    for gegenstand in sorted(alte_gegenstaende - neue_gegenstaende):
        aus.append({"gegenstand": gegenstand, "feld": "", "vorher": "vorhanden", "nachher": "entfernt"})

    gemeinsam = alte_gegenstaende & neue_gegenstaende
    for schluessel in sorted(set(vorher) | set(nachher)):
        gegenstand, feld = schluessel
        if gegenstand not in gemeinsam:
            continue
        alt, neu = vorher.get(schluessel), nachher.get(schluessel)
        if alt == neu or (not alt and not neu):
            continue                    # „nie gesetzt" und „leer" sind dasselbe Nichts
        if ist_geheim(feld):
            alt, neu = (MASKE if alt is not None else None), (MASKE if neu is not None else None)
            if alt == neu:              # ein geändertes Geheimnis bleibt sichtbar als Änderung
                neu = MASKE + " (neu)"
        aus.append({"gegenstand": gegenstand, "feld": feld, "vorher": alt, "nachher": neu})
    return aus


# ── Schreiben ─────────────────────────────────────────────────

def notiere(*, wer: str, ip: str, methode: str, pfad: str, was: str,
            ergebnis: str = "", aenderungen: Optional[List[Dict[str, Any]]] = None) -> int:
    """Einen Vorgang samt seiner Änderungen festhalten. Gibt die Kennung zurück."""
    con = db.get_db()
    try:
        cur = con.execute(
            "INSERT INTO admin_vorgaenge (wer, ip, methode, pfad, was, ergebnis) VALUES (?,?,?,?,?,?)",
            (str(wer or "")[:120], kuerze_ip(ip), str(methode or "")[:10],
             str(pfad or "")[:300], str(was or "")[:200], str(ergebnis or "")[:80]))
        vorgang = int(cur.lastrowid)
        for eintrag in (aenderungen or []):
            con.execute(
                "INSERT INTO admin_aenderungen (vorgang_id, gegenstand, feld, vorher, nachher) "
                "VALUES (?,?,?,?,?)",
                (vorgang, str(eintrag.get("gegenstand") or "")[:200],
                 str(eintrag.get("feld") or "")[:200],
                 eintrag.get("vorher"), eintrag.get("nachher")))
        con.commit()
    finally:
        con.close()
    if vorgang % 200 == 0:
        aufraeumen()
    return vorgang


def aufraeumen() -> int:
    """Alte Einträge entfernen. Gibt zurück, wie viele Vorgänge gegangen sind."""
    con = db.get_db()
    try:
        cur = con.execute("DELETE FROM admin_vorgaenge WHERE at < datetime('now', ?)",
                          (f"-{int(AUFBEWAHRUNG_TAGE)} days",))
        weg = cur.rowcount or 0
        cur = con.execute(
            "DELETE FROM admin_vorgaenge WHERE id NOT IN "
            "(SELECT id FROM admin_vorgaenge ORDER BY id DESC LIMIT ?)", (int(MAX_VORGAENGE),))
        weg += cur.rowcount or 0
        # Foreign keys sind in get_db eingeschaltet; der Nachlauf schadet trotzdem nicht.
        con.execute("DELETE FROM admin_aenderungen WHERE vorgang_id NOT IN "
                    "(SELECT id FROM admin_vorgaenge)")
        con.commit()
    finally:
        con.close()
    return weg


# ── Lesen ─────────────────────────────────────────────────────

def liste(*, limit: int = 100, offset: int = 0, suche: str = "",
          wer: str = "") -> List[Dict[str, Any]]:
    """Vorgänge, neueste zuerst, jeder mit seinen Änderungen."""
    bedingungen, werte = [], []
    if suche:
        bedingungen.append("(v.pfad LIKE ? OR v.was LIKE ? OR EXISTS "
                           "(SELECT 1 FROM admin_aenderungen a WHERE a.vorgang_id=v.id "
                           "AND (a.gegenstand LIKE ? OR a.feld LIKE ?)))")
        werte += [f"%{suche}%"] * 4
    if wer:
        bedingungen.append("v.wer = ?")
        werte.append(wer)
    wo = (" WHERE " + " AND ".join(bedingungen)) if bedingungen else ""
    con = db.get_db()
    try:
        zeilen = con.execute(
            f"SELECT * FROM admin_vorgaenge v{wo} ORDER BY v.id DESC LIMIT ? OFFSET ?",
            werte + [int(limit), int(offset)]).fetchall()
        aus = []
        for zeile in zeilen:
            eintrag = dict(zeile)
            eintrag["aenderungen"] = [dict(a) for a in con.execute(
                "SELECT gegenstand, feld, vorher, nachher FROM admin_aenderungen "
                "WHERE vorgang_id=? ORDER BY gegenstand, feld", (eintrag["id"],)).fetchall()]
            aus.append(eintrag)
    finally:
        con.close()
    return aus


def anzahl(*, suche: str = "", wer: str = "") -> int:
    con = db.get_db()
    try:
        if suche or wer:
            return len(liste(limit=MAX_VORGAENGE, suche=suche, wer=wer))
        zeile = con.execute("SELECT COUNT(*) AS n FROM admin_vorgaenge").fetchone()
        return int(zeile["n"] if zeile else 0)
    finally:
        con.close()


def leute() -> List[str]:
    """Wer im Protokoll vorkommt — für den Filter."""
    con = db.get_db()
    try:
        return [str(z["wer"]) for z in con.execute(
            "SELECT DISTINCT wer FROM admin_vorgaenge WHERE wer <> '' ORDER BY wer")]
    finally:
        con.close()


# ── Anschluss an die Verwaltung ───────────────────────────────
#
# Zwei Haken an der Anwendung genügen: einer vor der Anfrage, einer danach.
# Welche Route gerade dran ist, spielt keine Rolle — genau das ist der Punkt.

#: Pfade, an denen jede Anfrage protokolliert wird. Die Box meldet sich über
#: ``/api`` — das sind Uploads und Herzschläge, keine Umstellungen, und sie
#: gehören deshalb nicht hierher.
PFADE = ("/admin", "/mcp", "/oauth/authorize")

#: Was nicht ins Protokoll muss: Anfragen, die nichts umstellen, aber oft
#: kommen. Ohne sie bleibt die Seite lesbar.
AUSGENOMMEN = ("/admin/protokoll",)

#: Aus einem Pfad eine Überschrift machen. Trifft nichts zu, steht der Pfad da
#: — das ist immer noch eine Antwort, nur eine technische.
_BESCHRIFTUNG = [
    (r"^/admin/login$", "Anmeldung"),
    (r"^/admin/settings", "Einstellungen"),
    (r"^/admin/events/new$", "Event angelegt"),
    (r"^/admin/events/\d+/delete", "Event gelöscht"),
    (r"^/admin/events/\d+/layout", "Layout-Freigabe"),
    (r"^/admin/events/\d+", "Event bearbeitet"),
    (r"^/admin/events", "Events"),
    (r"^/admin/booth/\d+/profile", "Box-Profil"),
    (r"^/admin/booth", "Box"),
    (r"^/admin/booth-assets", "Box-Medien"),
    (r"^/admin/devices", "Geräte"),
    (r"^/admin/domains", "Eigene Domains"),
    (r"^/admin/vorlagen", "Druckvorlagen"),
    (r"^/admin/deletions", "Löschprotokoll"),
    (r"^/admin/software", "Software"),
    (r"^/mcp", "Zugriff über die Schnittstelle"),
    (r"^/oauth/authorize", "Zustimmung für einen Assistenten"),
]


def beschrifte(pfad: str) -> str:
    for muster, name in _BESCHRIFTUNG:
        if re.match(muster, pfad or ""):
            return name
    return pfad or ""


def soll_protokollieren(methode: str, pfad: str) -> bool:
    if str(methode or "").upper() in ("GET", "HEAD", "OPTIONS"):
        return False
    if any(pfad.startswith(aus) for aus in AUSGENOMMEN):
        return False
    return any(pfad.startswith(an) for an in PFADE)


def vorher_merken() -> None:
    """Vor der Anfrage: den Stand festhalten, wenn es sich lohnt."""
    from flask import g, request

    if not soll_protokollieren(request.method, request.path):
        return
    try:
        g._protokoll_bereich = bereich(request.path)
        g._protokoll_vorher = schnappschuss(*g._protokoll_bereich)
        g._protokoll_aktiv = True
    except Exception:               # noqa: BLE001 — ein Protokoll darf nie eine Anfrage kippen
        g._protokoll_vorher = None
        g._protokoll_aktiv = False


def nachher_festhalten(response: Any) -> Any:
    """Nach der Anfrage: vergleichen und schreiben. Fehler bleiben hier."""
    from flask import g, request, session

    if not getattr(g, "_protokoll_aktiv", False):
        return response
    g._protokoll_aktiv = False
    try:
        vorher = getattr(g, "_protokoll_vorher", None)
        nachher = schnappschuss(*getattr(g, "_protokoll_bereich", (None, None))) \
            if vorher is not None else None
        aenderungen = vergleiche(vorher, nachher) if (vorher is not None and nachher is not None) else []
        aufruf = getattr(g, "_protokoll_aufruf", "")
        if aufruf:
            # What an assistant asked for, already shortened and masked by app/mcp.py.
            aenderungen = [{"gegenstand": "Aufruf", "feld": "Argumente", "vorher": None,
                            "nachher": aufruf}] + aenderungen
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            ergebnis = f"abgelehnt ({status})"
        elif vorher is None:
            ergebnis = "ohne Vergleich"
        elif not aenderungen:
            ergebnis = "ohne Änderung an Einstellungen, Events oder Geräten"
        else:
            anzahl_werte = len(aenderungen)
            ergebnis = "1 Wert geändert" if anzahl_werte == 1 else f"{anzahl_werte} Werte geändert"
        if status < 400 and getattr(g, "_protokoll_ergebnis", ""):
            ergebnis = g._protokoll_ergebnis
        wer = getattr(g, "_protokoll_wer", "")
        try:
            from app import auth
            if not wer:
                wer = auth.admin_user() if session.get("admin_logged_in") else ""
        except Exception:               # noqa: BLE001
            pass
        if not wer:
            wer = "Schnittstelle" if request.path.startswith("/mcp") else "nicht angemeldet"
        notiere(wer=wer, ip=request.remote_addr or "", methode=request.method,
                pfad=request.path, was=getattr(g, "_protokoll_was", "") or beschrifte(request.path),
                ergebnis=ergebnis,
                aenderungen=aenderungen)
    except Exception:                   # noqa: BLE001 — ein Protokoll darf nie eine Anfrage kippen
        pass
    return response


__all__ = ["AUFBEWAHRUNG_TAGE", "anzahl", "aufraeumen", "bereich", "init_schema", "ist_geheim",
           "kuerze_ip", "leute", "liste", "nachher_festhalten", "notiere",
           "schnappschuss", "soll_protokollieren", "vergleiche", "vorher_merken"]
