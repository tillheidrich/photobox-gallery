"""Zahlen zum Abend: je Event und je Box, zum Zurücksetzen und zum Mitnehmen.

Drei Quellen, die es schon gibt, werden hier zusammengeführt:

* ``stats``            — was Gäste in der Galerie tun (Aufrufe, Downloads, Teilen)
* ``media``            — was hochgeladen wurde, mit Art, Event und Gerät
* ``device_days``      — was an der Box passiert ist (Durchgänge, Drucke)

Die Box meldet Gesamtzähler, keine Ereignisse: „bisher 412 Durchgänge". Daraus
macht diese Datei den Zuwachs pro Tag — die Zahl, die wirklich interessiert
(„gestern 180 Durchgänge, 96 Drucke"). Geht eine Box neu los oder wird ein
Zähler zurückgesetzt, fängt der Tag bei null an, statt eine negative Zahl zu
erfinden.

Zurücksetzen und Exportieren gehören zusammen: Wer die Zahlen eines Kunden
löscht, will sie vorher meistens herausgeben. Deshalb liegt beides hier.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional

from app import db

import re as _re

#: Was in der Galerie gezählt wird, in der Reihenfolge der Anzeige.
AKTIONEN = [
    ("view_gallery", "Galerie geöffnet"),
    ("view_photo", "Foto angesehen"),
    ("download", "Foto geladen"),
    ("zip_download", "Alles geladen"),
    ("download_selection", "Auswahl geladen"),
    ("share_photo", "Foto geteilt"),
    ("share_whatsapp", "WhatsApp"),
    ("share_link", "Link kopiert"),
    ("like", "Gefällt mir"),
    ("guest_upload", "Gast-Upload"),
]
BEKANNT = {a for a, _ in AKTIONEN}


def init_schema() -> None:
    con = db.get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS device_days (
            device_id  INTEGER NOT NULL,
            day        TEXT NOT NULL,
            sessions   INTEGER NOT NULL DEFAULT 0,
            prints     INTEGER NOT NULL DEFAULT 0,
            last_sessions INTEGER NOT NULL DEFAULT 0,
            last_prints   INTEGER NOT NULL DEFAULT 0,
            changed    TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (device_id, day)
        );""")
    con.commit()
    con.close()


# ── Abendzahlen der Box ───────────────────────────────────
# The booth counts per event and day (boothcore/abendzahlen.py) and sends the
# sums of the running day and event with each heartbeat. Stored here as they
# arrive — the newest report of a day replaces the older one — in two extra
# tables. Only numbers under known-looking keys; the booth never sends names,
# file names or the time of a single run, and this side would not keep them.

MODI = ("photo", "collage", "gif", "boomerang", "video")
_ZAEHLER_NAME = _re.compile(r"^[a-z][a-z0-9_]{0,15}(\.[a-z0-9_]{1,15})?$")
_TAG = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ZAEHLER = 80


def _zaehler(roh: Any) -> Dict[str, int]:
    if not isinstance(roh, dict):
        return {}
    aus: Dict[str, int] = {}
    for key, wert in list(roh.items())[:MAX_ZAEHLER]:
        if (isinstance(key, str) and _ZAEHLER_NAME.match(key) and isinstance(wert, int)
                and not isinstance(wert, bool) and 0 < wert < 10_000_000):
            aus[key] = wert
    return aus


def clean_booth_stats(roh: Any) -> Dict[str, Any]:
    """The heartbeat's ``booth_stats`` in the expected shape, or nothing."""
    if not isinstance(roh, dict):
        return {}
    tag = str(roh.get("day") or "")
    if not _TAG.match(tag):
        return {}
    return {"event_slug": str(roh.get("event_slug") or "")[:80], "day": tag,
            "today": _zaehler(roh.get("today")), "event_total": _zaehler(roh.get("event_total"))}


def _init_zaehler(con) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS booth_counter_days (
            device_id  INTEGER NOT NULL,
            event_slug TEXT NOT NULL,
            day        TEXT NOT NULL,
            counters   TEXT NOT NULL DEFAULT '{}',
            changed    TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (device_id, event_slug, day)
        );""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS booth_counter_events (
            device_id  INTEGER NOT NULL,
            event_slug TEXT NOT NULL,
            counters   TEXT NOT NULL DEFAULT '{}',
            changed    TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (device_id, event_slug)
        );""")


def note_booth_counters(device_id: int, bericht: Dict[str, Any]) -> None:
    bericht = clean_booth_stats(bericht)
    if not bericht:
        return
    con = db.get_db()
    try:
        _init_zaehler(con)
        con.execute("""INSERT INTO booth_counter_days(device_id, event_slug, day, counters)
                       VALUES (?,?,?,?)
                       ON CONFLICT(device_id, event_slug, day)
                       DO UPDATE SET counters=excluded.counters, changed=datetime('now')""",
                    (int(device_id), bericht["event_slug"], bericht["day"], json.dumps(bericht["today"])))
        con.execute("""INSERT INTO booth_counter_events(device_id, event_slug, counters) VALUES (?,?,?)
                       ON CONFLICT(device_id, event_slug)
                       DO UPDATE SET counters=excluded.counters, changed=datetime('now')""",
                    (int(device_id), bericht["event_slug"], json.dumps(bericht["event_total"])))
        con.commit()
    finally:
        con.close()


def zusammenfassung(z: Dict[str, int]) -> Dict[str, Any]:
    """Raw counters as people read them: runs per mode, cancelled runs, the rush hour."""
    g = lambda k: int(z.get(k) or 0)                      # noqa: E731
    stunden = {h: g(f"hour.{h:02d}") for h in range(24)}
    spitze = max(stunden, key=lambda h: stunden[h]) if any(stunden.values()) else None
    return {
        "runs": {m: g(f"runs.{m}") for m in MODI}, "runs_total": sum(g(f"runs.{m}") for m in MODI),
        "aborted": {"cancel": g("aborted.cancel"), "timeout": g("aborted.timeout"),
                    "total": g("aborted.cancel") + g("aborted.timeout")},
        "retakes": g("retakes"), "prints": g("prints"), "print_errors": g("print_errors"),
        "prints_backup": g("prints_backup"),
        "consent": {"gallery": g("consent.gallery"), "print": g("consent.print"), "notice": g("consent.notice")},
        "camera": {"errors": g("camera.errors"), "reconnects": g("camera.reconnects")},
        "qr_big": g("qr_big"),
        "hours": {h: n for h, n in stunden.items() if n},
        "peak_hour": spitze, "peak_runs": stunden[spitze] if spitze is not None else 0,
    }


def _summe(listen: List[Dict[str, int]]) -> Dict[str, int]:
    aus: Dict[str, int] = {}
    for z in listen:
        for k, v in z.items():
            aus[k] = aus.get(k, 0) + int(v)
    return aus


def booth_counters_raw(device_id: int) -> Dict[str, int]:
    """The counters of the latest day this booth reported."""
    con = db.get_db()
    try:
        _init_zaehler(con)
        row = con.execute("""SELECT counters FROM booth_counter_days WHERE device_id=?
                             ORDER BY day DESC, changed DESC LIMIT 1""", (int(device_id),)).fetchone()
    finally:
        con.close()
    return json.loads(row["counters"]) if row else {}


def papier_reicht(status: Dict[str, Any]) -> Optional[int]:
    """"Enough for about N prints" — only when the booth knows its stock (counted sheets)."""
    blaetter = [b for b in (status.get("sheets") or []) if isinstance(b, dict)]
    gezaehlt = [b for b in blaetter if b.get("quelle") == "gezählt"]
    if gezaehlt:
        return sum(int(b.get("rest") or 0) for b in gezaehlt)
    if not blaetter and int(status.get("sheets_left") or 0) > 0:
        return int(status["sheets_left"])
    return None


def booth_today(device_id: int) -> Optional[Dict[str, Any]]:
    con = db.get_db()
    try:
        _init_zaehler(con)
        row = con.execute("""SELECT event_slug, day, counters FROM booth_counter_days WHERE device_id=?
                             ORDER BY day DESC, changed DESC LIMIT 1""", (int(device_id),)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    status = db.get_device_status(int(device_id)) or {}
    return {"day": row["day"], "event_slug": row["event_slug"],
            **zusammenfassung(json.loads(row["counters"] or "{}")),
            "paper_prints_left": papier_reicht(status)}


def event_counters(slug: str) -> Dict[str, Any]:
    """All booths of one event: the sums, and runs and prints per day."""
    con = db.get_db()
    try:
        _init_zaehler(con)
        gesamt = [json.loads(r["counters"] or "{}") for r in con.execute(
            "SELECT counters FROM booth_counter_events WHERE event_slug=?", (str(slug),))]
        tage: Dict[str, List[Dict[str, int]]] = {}
        for r in con.execute("SELECT day, counters FROM booth_counter_days WHERE event_slug=? ORDER BY day",
                             (str(slug),)):
            tage.setdefault(r["day"], []).append(json.loads(r["counters"] or "{}"))
    finally:
        con.close()
    je_tag = []
    for tag, listen in sorted(tage.items()):
        z = zusammenfassung(_summe(listen))
        je_tag.append({"day": tag, "runs": z["runs_total"], "prints": z["prints"],
                       "peak_hour": z["peak_hour"]})
    return {"booth_counters": zusammenfassung(_summe(gesamt)) if gesamt else None,
            "booth_counter_days": je_tag}


# ── Aufzeichnen ───────────────────────────────────────────

def note_heartbeat(device_id: int, status: Dict[str, Any]) -> None:
    """Den Zuwachs dieses Tages fortschreiben.

    Die Box zählt seit ihrem Start; wir rechnen die Differenz zum letzten
    Stand. Ein kleinerer Wert heißt Neustart oder Zähler zurückgesetzt — dann
    zählt der neue Wert als Zuwachs, aber nie als Minus.
    """
    init_schema()
    if status.get("booth_stats"):
        note_booth_counters(device_id, status["booth_stats"])
    sessions = int(status.get("sessions_total") or 0)
    prints = int(status.get("prints_today") or 0)
    con = db.get_db()
    try:
        heute = con.execute("SELECT date('now') AS d").fetchone()["d"]
        row = con.execute("SELECT * FROM device_days WHERE device_id=? AND day=?",
                          (int(device_id), heute)).fetchone()
        if row is None:
            # Erster Kontakt heute: Wir kennen den Stand von gestern nicht —
            # deshalb beginnt der Tag bei null und zählt ab jetzt.
            con.execute("""INSERT INTO device_days(device_id, day, sessions, prints,
                                                   last_sessions, last_prints)
                           VALUES (?,?,0,0,?,?)""", (int(device_id), heute, sessions, prints))
        else:
            zuwachs_s = sessions - int(row["last_sessions"])
            zuwachs_p = prints - int(row["last_prints"])
            if zuwachs_s < 0:
                zuwachs_s = sessions
            if zuwachs_p < 0:
                zuwachs_p = prints
            con.execute("""UPDATE device_days
                              SET sessions=sessions+?, prints=prints+?,
                                  last_sessions=?, last_prints=?, changed=datetime('now')
                            WHERE device_id=? AND day=?""",
                        (zuwachs_s, zuwachs_p, sessions, prints, int(device_id), heute))
        con.commit()
    finally:
        con.close()


# ── Lesen ─────────────────────────────────────────────────

def event(event_id: int) -> Dict[str, Any]:
    """Alles zu einem Event: Medien, Gästeaktionen, beteiligte Boxen."""
    con = db.get_db()
    try:
        medien = {r["kind"] or "photo": int(r["n"]) for r in con.execute(
            "SELECT kind, COUNT(*) AS n FROM media WHERE event_id=? GROUP BY kind", (int(event_id),))}
        aktionen = {r["action"]: int(r["n"]) for r in con.execute(
            "SELECT action, COUNT(*) AS n FROM stats WHERE event_id=? GROUP BY action", (int(event_id),))}
        tage = [dict(r) for r in con.execute(
            """SELECT date(created_at) AS tag, COUNT(*) AS medien
                 FROM media WHERE event_id=? GROUP BY tag ORDER BY tag""", (int(event_id),))]
        boxen = [dict(r) for r in con.execute(
            """SELECT d.id, d.name, COUNT(m.id) AS medien
                 FROM media m JOIN devices d ON d.id = m.device_id
                WHERE m.event_id=? GROUP BY d.id ORDER BY medien DESC""", (int(event_id),))]
        durchgaenge = int((con.execute(
            "SELECT COUNT(DISTINCT session_uid) AS n FROM media WHERE event_id=? AND session_uid<>''",
            (int(event_id),)).fetchone() or {"n": 0})["n"])
    finally:
        con.close()
    ev = db.get_event_by_id(int(event_id)) or {}
    return {"media": medien, "media_total": sum(medien.values()), "sessions": durchgaenge,
            "actions": aktionen, "days": tage, "devices": boxen,
            **event_counters(str(ev.get("slug") or ""))}


def device(device_id: int, tage: int = 60) -> Dict[str, Any]:
    """Alles zu einer Box: Durchgänge und Drucke je Tag, Medien je Event."""
    init_schema()
    con = db.get_db()
    try:
        verlauf = [dict(r) for r in con.execute(
            """SELECT day, sessions, prints FROM device_days
                WHERE device_id=? ORDER BY day DESC LIMIT ?""", (int(device_id), int(tage)))]
        events = [dict(r) for r in con.execute(
            """SELECT e.id, e.name, e.slug, COUNT(m.id) AS medien, MIN(m.created_at) AS von,
                      MAX(m.created_at) AS bis
                 FROM media m JOIN events e ON e.id = m.event_id
                WHERE m.device_id=? GROUP BY e.id ORDER BY bis DESC""", (int(device_id),))]
        medien = {r["kind"] or "photo": int(r["n"]) for r in con.execute(
            "SELECT kind, COUNT(*) AS n FROM media WHERE device_id=? GROUP BY kind", (int(device_id),))}
    finally:
        con.close()
    return {"days": list(reversed(verlauf)), "events": events, "media": medien,
            "media_total": sum(medien.values()),
            "sessions_total": sum(int(t["sessions"]) for t in verlauf),
            "prints_total": sum(int(t["prints"]) for t in verlauf),
            "today": booth_today(int(device_id))}


def overview() -> Dict[str, Any]:
    """Für die Übersichtsseite: Summen über alles."""
    init_schema()
    con = db.get_db()
    try:
        medien = int((con.execute("SELECT COUNT(*) AS n FROM media").fetchone() or {"n": 0})["n"])
        aktionen = {r["action"]: int(r["n"]) for r in con.execute(
            "SELECT action, COUNT(*) AS n FROM stats GROUP BY action")}
        boxen = [dict(r) for r in con.execute(
            """SELECT d.id, d.name,
                      COALESCE(SUM(dd.sessions),0) AS sessions,
                      COALESCE(SUM(dd.prints),0) AS prints
                 FROM devices d LEFT JOIN device_days dd ON dd.device_id = d.id
                GROUP BY d.id ORDER BY sessions DESC""")]
    finally:
        con.close()
    return {"media_total": medien, "actions": aktionen, "devices": boxen}


# ── Zurücksetzen ──────────────────────────────────────────

def reset_event(event_id: int) -> int:
    """Die Gästezahlen eines Events löschen. Die Fotos bleiben, versteht sich."""
    ev = db.get_event_by_id(int(event_id)) or {}
    con = db.get_db()
    cur = con.execute("DELETE FROM stats WHERE event_id=?", (int(event_id),))
    if ev.get("slug"):
        # The booths' counters of this event belong to the same evening.
        _init_zaehler(con)
        con.execute("DELETE FROM booth_counter_days WHERE event_slug=?", (ev["slug"],))
        con.execute("DELETE FROM booth_counter_events WHERE event_slug=?", (ev["slug"],))
    con.commit()
    anzahl = cur.rowcount or 0
    con.close()
    return anzahl


def reset_device(device_id: int) -> int:
    """Den Verlauf einer Box löschen — Durchgänge und Drucke je Tag."""
    init_schema()
    con = db.get_db()
    cur = con.execute("DELETE FROM device_days WHERE device_id=?", (int(device_id),))
    _init_zaehler(con)
    con.execute("DELETE FROM booth_counter_days WHERE device_id=?", (int(device_id),))
    con.execute("DELETE FROM booth_counter_events WHERE device_id=?", (int(device_id),))
    con.commit()
    anzahl = cur.rowcount or 0
    con.close()
    return anzahl


# ── Mitnehmen ─────────────────────────────────────────────

def _csv(zeilen: List[List[Any]], kopf: List[str]) -> str:
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";")      # Semikolon: Excel auf Deutsch
    schreiber.writerow(kopf)
    schreiber.writerows(zeilen)
    return puffer.getvalue()


def event_csv(event_id: int) -> str:
    daten = event(event_id)
    zeilen: List[List[Any]] = [["Medien gesamt", daten["media_total"]], ["Durchgänge", daten["sessions"]]]
    for art, anzahl in sorted(daten["media"].items()):
        zeilen.append([f"Medien · {art}", anzahl])
    for key, label in AKTIONEN:
        zeilen.append([label, daten["actions"].get(key, 0)])
    for extra in sorted(set(daten["actions"]) - BEKANNT):
        zeilen.append([extra, daten["actions"][extra]])
    for box in daten["devices"]:
        zeilen.append([f"Box · {box['name']}", box["medien"]])
    zeilen.append([])
    zeilen.append(["Tag", "Medien"])
    for tag in daten["days"]:
        zeilen.append([tag["tag"], tag["medien"]])
    return _csv(zeilen, ["Kennzahl", "Wert"])


def device_csv(device_id: int) -> str:
    daten = device(device_id)
    zeilen: List[List[Any]] = [["Tag", "Durchgänge", "Drucke"]]
    for tag in daten["days"]:
        zeilen.append([tag["day"], tag["sessions"], tag["prints"]])
    zeilen.append([])
    zeilen.append(["Event", "Medien", "von", "bis"])
    for ev in daten["events"]:
        zeilen.append([ev["name"], ev["medien"], (ev["von"] or "")[:16], (ev["bis"] or "")[:16]])
    return _csv(zeilen, ["Box", ""])


def event_json(event_id: int) -> str:
    return json.dumps(event(event_id), ensure_ascii=False, indent=1)


def device_json(device_id: int) -> str:
    return json.dumps(device(device_id), ensure_ascii=False, indent=1)
