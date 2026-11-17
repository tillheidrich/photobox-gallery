"""Wie die Datei heißt, die der Gast bekommt.

Aus der Box kommt ``abcd2345xy_1.jpg``. Das ist ein guter Name für eine
Datenbank und ein schlechter für einen Ordner: Wer zwanzig Aufnahmen von drei
Veranstaltungen im Download-Ordner liegen hat, sieht zwanzigmal dasselbe
Muster und weiß bei keiner, wozu sie gehört.

Deshalb ein Muster je Event — oder eines für die ganze Instanz, wenn alle
gleich heißen sollen::

    {event}-{datum}-{nr}        →  sommerfest-2026-07-20-0007.jpg
    {event_name} {nr}           →  Sommerfest 2026 0007.jpg
    {datum}_{zeit}_{art}        →  2026-07-20_21-14-03_print.jpg

**Was das nicht tut:** Dateien auf der Platte umbenennen. Dort hängen die
Namen an der Datenbank, an Vorschaubildern und an Prüfsummen; ein Umbenennen
dort wäre ein Umbau mit Ausfallrisiko für einen Namen, den ohnehin niemand
sieht. Geändert wird der Name, unter dem die Datei **ausgeliefert** wird —
beim einzelnen Download, in „Meine Auswahl" und im ZIP der ganzen Galerie.

Leeres Muster heißt: alles bleibt, wie es war — wenn „Original" ausdrücklich
gewählt ist. Ist nirgends etwas gewählt, bekommt der Gast statt der Kennung der
Box (``9rq3r2txww_web.jpg``) den lesbaren Namen ``<event>-<nr>``
(``STANDARDMUSTER``), im Download genauso wie in dem, was die Galerie anzeigt.

Numbering ``{nr}``: the place of a visible picture in the event, by capture
time then id (``nummern``). Known and accepted: when a picture before it is
deleted or hidden, the numbers after it move up by one, so a name handed out
yesterday can differ from today's. A picture outside the numbering (hidden,
shown to an admin; added after the page read the numbers) gets a number of
its own from its id (``nummer_fuer``), never one another picture carries.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional

#: Was im Muster stehen darf. Alles andere bleibt stehen, wie es ist —
#: ein unbekannter Platzhalter wird nicht heimlich verschluckt.
PLATZHALTER = [
    ("{event}", "Kürzel des Events", "sommerfest"),
    ("{event_name}", "Name des Events", "Sommerfest 2026"),
    ("{datum}", "Aufnahmedatum", "2026-07-20"),
    ("{jahr}", "Jahr", "2026"),
    ("{monat}", "Monat", "07"),
    ("{tag}", "Tag", "20"),
    ("{zeit}", "Uhrzeit", "21-14-03"),
    ("{stunde}", "Stunde", "21"),
    ("{minute}", "Minute", "14"),
    ("{nr}", "Laufende Nummer im Event, vierstellig", "0007"),
    ("{nr2}", "Laufende Nummer, zweistellig", "07"),
    ("{nr6}", "Laufende Nummer, sechsstellig", "000007"),
    ("{art}", "Foto, Druck, GIF, Video …", "print"),
    ("{groesse}", "Gewählte Größe", "web"),
    ("{original}", "Ursprünglicher Name ohne Endung", "abcd2345xy_1"),
    ("{id}", "Kennung in der Galerie", "4711"),
]

#: Ein Muster, das man gefahrlos vorschlagen kann.
BEISPIEL = "{event}-{datum}-{nr}"

#: What a guest gets when the event has no rule at all: the storage id of the
#: box ("9rq3r2txww_web.jpg") means nothing to a person, so the gallery shows
#: and delivers "<event>-<number>" instead (17.11.2026). A pattern or a fixed
#: rule that someone chose is never replaced by this.
STANDARDMUSTER = "{event}-{nr}"

MAX_LAENGE = 120


def _entschaerfe(text: str) -> str:
    """Aus einem Stück Text einen Teil eines Dateinamens machen.

    Umlaute bleiben lesbar (ä → ae), Pfadtrenner verschwinden, und ein Name
    fängt nie mit einem Punkt an: Eine Datei namens ``.jpg`` ist auf jedem
    System unsichtbar und auf keinem gewollt.
    """
    wert = str(text or "")
    for zeichen, ersatz in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"),
                            ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        wert = wert.replace(zeichen, ersatz)
    wert = unicodedata.normalize("NFKD", wert).encode("ascii", "ignore").decode()
    wert = re.sub(r"[^A-Za-z0-9 ._-]+", "-", wert)
    # „Müllers Fest & Co." wird sonst zu „Muellers Fest - Co" — mit Leerzeichen
    # um den Strich herum, die nur davon stammen, dass dort ein & stand.
    wert = re.sub(r"\s*-\s*", "-", wert)
    wert = re.sub(r"-{2,}", "-", wert)
    wert = re.sub(r"\s{2,}", " ", wert)
    return wert.strip(" .-")


def gueltig(muster: str) -> bool:
    """Taugt dieses Muster als Dateiname?

    Ein Muster ohne Platzhalter ist erlaubt (dann heißen alle Dateien gleich
    und werden durchnummeriert), eines mit Pfadtrennern nicht.
    """
    wert = str(muster or "").strip()
    if not wert:
        return True
    if len(wert) > MAX_LAENGE:
        return False
    return not any(z in wert for z in ("/", "\\", "..", "\0"))


def werte(*, event: Dict[str, Any], medium: Dict[str, Any], nummer: int = 1,
          groesse: str = "") -> Dict[str, str]:
    """Die Werte für einen konkreten Abzug."""
    roh = str(medium.get("taken_at") or medium.get("created_at") or "")
    datum, _, zeit = roh.replace("T", " ").partition(" ")
    datum = datum[:10] or "0000-00-00"
    zeit = (zeit[:8] or "00-00-00").replace(":", "-")
    jahr, monat, tag = (datum.split("-") + ["", "", ""])[:3]
    original = str(medium.get("filename") or "")
    return {
        "{event}": _entschaerfe(event.get("slug") or ""),
        "{event_name}": _entschaerfe(event.get("name") or ""),
        "{datum}": datum,
        "{jahr}": jahr,
        "{monat}": monat,
        "{tag}": tag,
        "{zeit}": zeit,
        "{stunde}": zeit[:2],
        "{minute}": zeit[3:5],
        "{nr}": f"{int(nummer):04d}",
        "{nr2}": f"{int(nummer):02d}",
        "{nr6}": f"{int(nummer):06d}",
        "{art}": _entschaerfe(medium.get("kind") or "photo"),
        "{groesse}": _entschaerfe(groesse or ""),
        "{original}": _entschaerfe(original.rsplit(".", 1)[0] if "." in original else original),
        "{id}": str(medium.get("id") or ""),
    }


def baue(muster: str, *, event: Dict[str, Any], medium: Dict[str, Any], endung: str,
         nummer: int = 1, groesse: str = "", rueckfall: str = "") -> str:
    """Der Name, unter dem diese Datei ausgeliefert wird.

    Ohne Muster kommt ``rueckfall`` zurück — alles bleibt, wie es war.
    """
    if not gueltig(muster) or not str(muster or "").strip():
        return rueckfall
    name = str(muster)
    for schluessel, wert in werte(event=event, medium=medium, nummer=nummer,
                                  groesse=groesse).items():
        name = name.replace(schluessel, wert)
    name = _entschaerfe(name)[:MAX_LAENGE]
    if not name:
        return rueckfall
    endung = ("." + endung.lstrip(".").lower()) if endung else ""
    return f"{name}{endung}"


def stamm(medium: Dict[str, Any]) -> str:
    """The original file name without extension, exactly as it arrived.

    Unlike ``{original}`` this is not made safe for a file system: it is the
    name the photographer's catalogue knows (Lightroom filters by it), so a
    space or a bracket stays. Only a path in front is dropped.
    """
    name = str(medium.get("filename") or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name.lstrip(".") else name


def vorschau(muster: str) -> str:
    """Wie ein Name mit diesem Muster aussähe — für die Einstellungsseite."""
    if not gueltig(muster):
        return "— das geht so nicht —"
    beispiel_event = {"slug": "sommerfest", "name": "Sommerfest 2026"}
    beispiel_medium = {"created_at": "2026-07-20T21:14:03", "kind": "photo",
                       "filename": "abcd2345xy_1.jpg", "id": 4711}
    return baue(muster, event=beispiel_event, medium=beispiel_medium, endung="jpg",
                nummer=7, groesse="web", rueckfall="abcd2345xy_1.jpg")


def laufende_nummer(event_id: int, medium: Dict[str, Any]) -> int:
    """Die wievielte Aufnahme dieses Events ist das?

    Gezählt wird nach Aufnahmezeitpunkt, bei Gleichstand nach Kennung — damit
    dieselbe Datei morgen dieselbe Nummer trägt wie heute.
    """
    from app import db

    con = db.get_db()
    try:
        zeile = con.execute(
            "SELECT COUNT(*) AS n FROM media WHERE event_id=? AND COALESCE(hidden,0)=0 AND "
            "(datetime(COALESCE(created_at,'')) < datetime(COALESCE(?,'')) OR "
            " (datetime(COALESCE(created_at,'')) = datetime(COALESCE(?,'')) AND id <= ?))",
            (int(event_id), medium.get("created_at"), medium.get("created_at"),
             int(medium.get("id") or 0))).fetchone()
        return max(1, int(zeile["n"] or 1))
    except Exception:                   # noqa: BLE001 — ohne Nummer eben die Eins
        return 1
    finally:
        con.close()


def nummern(event_id: int) -> Dict[int, int]:
    """Alle laufenden Nummern eines Events auf einmal.

    Für ein ZIP mit fünfhundert Bildern wäre ``laufende_nummer`` je Datei eine
    Zählabfrage über alle Bilder — fünfhundert Abfragen, jede über fünfhundert
    Zeilen. Eine Abfrage reicht.
    """
    from app import db

    con = db.get_db()
    try:
        zeilen = con.execute(
            "SELECT id FROM media WHERE event_id=? AND COALESCE(hidden,0)=0 "
            "ORDER BY datetime(COALESCE(created_at,'')), id", (int(event_id),)).fetchall()
        return {int(z["id"]): i for i, z in enumerate(zeilen, 1)}
    except Exception:                   # noqa: BLE001
        return {}
    finally:
        con.close()


def nummer_fuer(nummern: Dict[int, int], medium: Dict[str, Any]) -> int:
    """The number of a picture from ``nummern``; one of its own for a picture outside it.

    Outside the numbering are hidden pictures an admin is shown and pictures
    added after the numbers were read. Until 17.11.2026 they all got
    ``len(nummern) + 1`` and so the same visible name. Now the number is
    derived from the id, above every number of the map: unique per picture.
    """
    mid = int(medium.get("id") or 0)
    nr = nummern.get(mid)
    return nr if nr is not None else len(nummern) + max(1, mid)


def muster_fuer(event: Optional[Dict[str, Any]], gs: Optional[Dict[str, Any]] = None) -> str:
    """Das Muster dieses Events — oder das der Instanz, wenn dort keines steht."""
    from app import db

    eigenes = str((gs or {}).get("download_pattern") or "").strip()
    if eigenes:
        return eigenes
    try:
        return str(db.get_setting("download_pattern") or "").strip()
    except Exception:                   # noqa: BLE001
        return ""


# ── The fixed rule "event name, date and time" (15.11.2026) ───────────
# Owner: "entweder Original oder halt Name des Events … und Datum und
# Uhrzeit". ``original`` is today's behaviour (the box's name, or the
# pattern above if one is set); ``event_datetime`` names every file
# ``<Event>_<YYYY-MM-DD>_<HH-MM-SS>.<ext>`` by its capture time, with
# ``_2``, ``_3`` … for shots of the same second. The booth's delivery
# targets name files with the same rule from a copy of these functions
# (sync/photobox_sync/targets.py); a test keeps both copies equal.

RULES = ("original", "event_datetime")
#: Room for the event part: the whole name stays within 100 characters with
#: "_YYYY-MM-DD_HH-MM-SS" (20), a suffix up to "_999" (4) and ".jpeg" (5).
EVENT_MAX = 64
NAME_MAX = 100
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def sicherer_eventname(name: str) -> str:
    """The event's name as part of a file name every system accepts.

    German umlauts are written out (ä → ae), other accents dropped, white
    space becomes "-", everything but ``A-Za-z0-9-_`` goes, runs of "-" or
    "_" shrink to one, nothing leads or trails. Never empty ("Event") and
    never a name Windows reserves (CON, PRN, AUX, NUL, COM1–9, LPT1–9).
    """
    wert = str(name or "")
    for zeichen, ersatz in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"),
                            ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ẞ", "SS")):
        wert = wert.replace(zeichen, ersatz)
    wert = unicodedata.normalize("NFKD", wert).encode("ascii", "ignore").decode()
    wert = re.sub(r"\s+", "-", wert)
    wert = re.sub(r"[^A-Za-z0-9_-]+", "-", wert)
    wert = re.sub(r"-{2,}", "-", wert)
    wert = re.sub(r"_{2,}", "_", wert)
    wert = re.sub(r"[-_]{2,}", lambda m: m.group(0)[0], wert)
    wert = wert.strip("-_")[:EVENT_MAX].strip("-_")
    if not wert:
        return "Event"
    if wert.upper() in _RESERVED:
        return f"{wert}-Event"
    return wert


def event_datetime_name(event_name: str, when, endung: str, n: int = 1) -> str:
    """``<Event>_<YYYY-MM-DD>_<HH-MM-SS>[_n].<ext>`` — ``n`` from 2 on is the
    place among shots of the same second."""
    stem = f"{sicherer_eventname(event_name)}_{when.strftime('%Y-%m-%d_%H-%M-%S')}"
    if int(n) > 1:
        stem += f"_{int(n)}"
    endung = re.sub(r"[^a-z0-9]", "", str(endung or "").lower())[:5]
    return f"{stem}.{endung}" if endung else stem


def regel_gewaehlt(event: Optional[Dict[str, Any]], gs: Optional[Dict[str, Any]] = None) -> str:
    """The rule somebody chose — the event's own, else the account's — or "" if nobody did."""
    from app import db

    eigene = str((gs or {}).get("file_naming") or "").strip()
    if eigene in RULES:
        return eigene
    try:
        vorgabe = str(db.get_setting("file_naming") or "").strip()
    except Exception:                   # noqa: BLE001
        vorgabe = ""
    return vorgabe if vorgabe in RULES else ""


def regel_fuer(event: Optional[Dict[str, Any]], gs: Optional[Dict[str, Any]] = None) -> str:
    """This event's rule — its own, else the account default, else ``original``."""
    return regel_gewaehlt(event, gs) or "original"


def aufnahmezeit(medium: Dict[str, Any]):
    """When the picture was taken, in local time; the upload time if unknown.

    ``taken_at`` comes from the booth in the booth's (the venue's) local time
    and stays as it is; ``created_at`` is the database's UTC and is shown in
    the server's time zone, like every other time the gallery shows.
    """
    import datetime as dt

    for key, utc in (("taken_at", False), ("created_at", True)):
        roh = str(medium.get(key) or "").strip()
        if not roh:
            continue
        try:
            t = dt.datetime.fromisoformat(roh.replace(" ", "T").replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is not None:
            return t.astimezone().replace(tzinfo=None)
        if utc:
            return t.replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
        return t
    return dt.datetime(1970, 1, 1)


def _gruppe(medium: Dict[str, Any]) -> str:
    """One name per picture: the web and the original version of a box upload share it."""
    uid = str(medium.get("uid") or "").strip()
    return f"u:{uid}" if uid else f"i:{int(medium.get('id') or 0)}"


def event_datetime_namen(event: Dict[str, Any]) -> Dict[int, str]:
    """``{media id: stem}`` for every picture of the event, hidden ones included.

    Ordered by capture time and then by the oldest id of the picture, so the
    same file gets the same name tomorrow, in a single download and in a ZIP.
    """
    from app import db

    con = db.get_db()
    try:
        zeilen = [dict(z) for z in con.execute(
            "SELECT id, uid, taken_at, created_at FROM media WHERE event_id=?", (int(event["id"]),))]
    finally:
        con.close()
    gruppen: Dict[str, Dict[str, Any]] = {}
    for z in zeilen:
        g = gruppen.setdefault(_gruppe(z), {"ids": [], "zeit": aufnahmezeit(z), "erste": int(z["id"])})
        g["ids"].append(int(z["id"]))
        g["erste"] = min(g["erste"], int(z["id"]))
    aus: Dict[int, str] = {}
    gezaehlt: Dict[str, int] = {}
    for g in sorted(gruppen.values(), key=lambda g: (g["zeit"], g["erste"])):
        sekunde = g["zeit"].strftime("%Y-%m-%d_%H-%M-%S")
        gezaehlt[sekunde] = gezaehlt.get(sekunde, 0) + 1
        stem = event_datetime_name(event.get("name") or "", g["zeit"], "", gezaehlt[sekunde])
        for mid in g["ids"]:
            aus[mid] = stem
    return aus


def auslieferungsname(event: Dict[str, Any], medium: Dict[str, Any], rueckfall: str, *,
                      gs: Optional[Dict[str, Any]] = None, namen: Optional[Dict[int, str]] = None) -> Optional[str]:
    """The ``event_datetime`` name for this file, or None when the rule is ``original``."""
    if regel_fuer(event, gs) != "event_datetime":
        return None
    return event_datetime_auslieferung(event, medium, rueckfall,
                                       namen if namen is not None else event_datetime_namen(event))


def event_datetime_auslieferung(event: Dict[str, Any], medium: Dict[str, Any], rueckfall: str,
                                namen: Dict[int, str]) -> str:
    """The ``event_datetime`` name of this file from the event's names, without reading any setting."""
    stem = namen.get(int(medium.get("id") or 0))
    if not stem:
        stem = event_datetime_name(event.get("name") or "", aufnahmezeit(medium), "")
    endung = rueckfall.rsplit(".", 1)[-1] if "." in rueckfall else "jpg"
    return f"{stem}.{re.sub(r'[^a-z0-9]', '', endung.lower())[:5] or 'jpg'}"


def content_disposition(name: str, disposition: str = "attachment") -> str:
    """A Content-Disposition header after RFC 6266 with the RFC 5987 form.

    The plain ``filename`` carries an ASCII fallback (quotes and backslashes
    removed), ``filename*`` the exact name percent-encoded in UTF-8.
    """
    from urllib.parse import quote

    ascii_name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r'["\\\r\n]', "", ascii_name) or "download"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(str(name), safe='')}"


__all__ = ["BEISPIEL", "EVENT_MAX", "MAX_LAENGE", "NAME_MAX", "PLATZHALTER", "RULES", "auslieferungsname",
           "aufnahmezeit", "baue", "content_disposition", "event_datetime_name", "event_datetime_namen",
           "gueltig", "laufende_nummer", "muster_fuer", "nummern", "regel_fuer", "regel_gewaehlt", "sicherer_eventname", "stamm",
           "vorschau", "werte"]
