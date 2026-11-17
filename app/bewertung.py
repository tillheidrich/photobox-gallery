"""Nach dem Event einmal um eine Bewertung bitten.

Wer eine Fotobox vermietet, lebt davon, dass jemand nachher etwas Gutes über
ihn schreibt. Der beste Augenblick dafür ist nicht die Rechnung drei Wochen
später, sondern die Minute, in der ein Gast seine Bilder in der Hand hat und
sich freut. Genau dort steht die Bitte: unten auf der Galerie und auf der
Seite des eigenen Durchgangs, als ruhige Karte, nicht als Fenster, das sich
vor die Bilder schiebt.

**Was hier bewusst nicht gebaut ist: die Vorsortierung.** Andere Programme
fragen erst intern nach Sternen und schicken nur die vier und fünf zu Google,
während die eins bis drei in einem stillen Formular landen. Das ist in
Deutschland nach dem UWG unlauter und in den Richtlinien der Plattformen
verboten — und es ist, ganz unabhängig davon, Betrug an dem, der die
Bewertungen liest. Hier führt ein Link an eine Stelle, und zwar für alle.

Wer auch Kritik hören will, die nicht öffentlich werden soll, stellt neben den
Link eine eigene Adresse — beides nebeneinander, nicht das eine als Filter vor
dem anderen.

**Gezählt wird nur der Klick**, und nur als Tagessumme je Event. Keine Zeile
je Person, keine Kennung, kein Zählpixel eines Dritten. Die Zahl, die zählt,
ist ohnehin „wie oft ist jemand dem Link gefolgt" — alles andere wäre eine
Aufzeichnung über Gäste, für die es keinen Grund gibt.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app import db

#: Der Vorschlag, wenn niemand etwas anderes hinschreibt. Kurz, ohne Ausrufe­zeichen
#: und ohne „bitte bitte" — eine Bitte, die man auch überlesen darf.
STANDARDTEXT = "Hat es euch gefallen? Eine kurze Bewertung hilft uns sehr."

#: Nur Web-Adressen. ``javascript:`` und Verwandtes haben in einem Feld, das
#: ein Gast anklickt, nichts verloren.
SCHEMATA = ("http", "https")

MAX_URL = 300
MAX_TEXT = 200


def init_schema(cur: sqlite3.Cursor) -> None:
    """Tagessummen je Event. Mehr braucht es nicht, und mehr gibt es nicht."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bewertung_tage (
            event_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            klicks INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (event_id, tag)
        );""")


# ── Adresse und Text ──────────────────────────────────────────

def gueltige_url(roh: str) -> str:
    """Die Adresse, wenn sie eine ist — sonst der leere Text.

    Ohne Schema wird ``https://`` angenommen: Wer aus dem Profil bei Google
    etwas herauskopiert, hat das Schema manchmal nicht mit dabei.
    """
    wert = str(roh or "").strip()
    if not wert:
        return ""
    teile = urlparse(wert)
    # „javascript:alert(1)" hat kein ``//`` und würde sonst mit einem
    # vorangestellten ``https://`` durchrutschen. Deshalb erst zerlegen.
    # Ein Punkt im Schema gibt es nicht — das ist dann ein Hostname mit Port.
    if teile.scheme and "." not in teile.scheme:
        if teile.scheme.lower() not in SCHEMATA or not teile.netloc:
            return ""
        return wert[:MAX_URL]
    wert = "https://" + wert.lstrip("/")
    if not urlparse(wert).netloc:
        return ""
    return wert[:MAX_URL]


def _einstellung(schluessel: str) -> str:
    try:
        return (db.get_setting(schluessel) or "").strip()
    except Exception:                       # noqa: BLE001 — ohne Einstellung eben ohne Bitte
        return ""


def fuer_event(event: Optional[Dict[str, Any]],
               gs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Was dieses Event zeigen soll — Event schlägt Instanz.

    ``review_mode`` kennt drei Werte: ``erben`` nimmt, was für die ganze
    Instanz eingestellt ist, ``an`` und ``aus`` entscheiden für dieses eine
    Event. Adresse und Text stehen für sich: Ist das Feld am Event leer, gilt
    der Wert der Instanz — so lässt sich für einen Kunden nur der Text
    austauschen, ohne die Adresse noch einmal einzutragen.
    """
    gs = gs or {}
    modus = str(gs.get("review_mode") or "erben")
    if modus == "aus":
        return {"an": False, "url": "", "text": "", "slug": ""}
    if modus == "erben" and _einstellung("review_enabled") != "1":
        return {"an": False, "url": "", "text": "", "slug": ""}

    url = gueltige_url(gs.get("review_url") or "") or gueltige_url(_einstellung("review_url"))
    if not url:
        return {"an": False, "url": "", "text": "", "slug": ""}
    text = (str(gs.get("review_text") or "").strip()
            or _einstellung("review_text") or STANDARDTEXT)[:MAX_TEXT]
    return {"an": True, "url": url, "text": text,
            "slug": str((event or {}).get("slug") or "")}


# ── Zählen ────────────────────────────────────────────────────

def zaehle(event_id: Optional[int]) -> None:
    """Einen Klick als Tagessumme verbuchen. Ein Fehler hier hält niemanden auf."""
    try:
        con = db.get_db()
    except Exception:                       # noqa: BLE001
        return
    try:
        con.execute(
            "INSERT INTO bewertung_tage (event_id, tag, klicks) VALUES (?, date('now'), 1) "
            "ON CONFLICT(event_id, tag) DO UPDATE SET klicks = klicks + 1",
            (int(event_id or 0),))
        con.commit()
    except sqlite3.Error:
        pass
    finally:
        con.close()


def zahlen(event_id: int) -> Dict[str, Any]:
    """Wie oft dem Link gefolgt wurde — insgesamt und in den letzten 30 Tagen."""
    con = db.get_db()
    try:
        zeile = con.execute(
            "SELECT COALESCE(SUM(klicks), 0) AS gesamt, "
            "COALESCE(SUM(CASE WHEN tag >= date('now', '-30 days') THEN klicks ELSE 0 END), 0) AS monat, "
            "MAX(tag) AS zuletzt FROM bewertung_tage WHERE event_id=?", (int(event_id),)).fetchone()
        return {"gesamt": int(zeile["gesamt"] or 0), "monat": int(zeile["monat"] or 0),
                "zuletzt": zeile["zuletzt"] or ""}
    except sqlite3.Error:
        return {"gesamt": 0, "monat": 0, "zuletzt": ""}
    finally:
        con.close()


def tage(event_id: int, anzahl: int = 30) -> List[Dict[str, Any]]:
    """Die Tagessummen, neueste zuerst — für eine kleine Kurve im Admin."""
    con = db.get_db()
    try:
        return [dict(z) for z in con.execute(
            "SELECT tag, klicks FROM bewertung_tage WHERE event_id=? ORDER BY tag DESC LIMIT ?",
            (int(event_id), int(anzahl)))]
    except sqlite3.Error:
        return []
    finally:
        con.close()


__all__ = ["MAX_TEXT", "MAX_URL", "STANDARDTEXT", "fuer_event", "gueltige_url",
           "init_schema", "tage", "zaehle", "zahlen"]
