"""Vorlagen aus- und einlesen — als Paket, mit allem, was dazugehört.

Eine Druckvorlage ist selten allein etwas wert: Sie nennt einen Hintergrund
und vielleicht eine Überlagerung, und beides sind Dateien. Wer eine Vorlage
weitergibt, den QR-Code-Rahmen aber nicht mitschickt, gibt eine Vorlage
weiter, die beim Empfänger anders aussieht.

Deshalb dieses Format. Ein ``.abzug`` ist ein ZIP-Archiv:

    paket.json          Was drin ist, von welchem Programm, wann gepackt
    vorlagen/<id>.json  Eine oder mehrere Vorlagen
    medien/<datei>      Bilder: Überlagerungen und Collage-Hintergründe

Ein ZIP, weil es sich überall öffnen lässt: Wer wissen will, was er da
weitergibt, packt es aus und sieht nach. Nichts ist verschlüsselt, nichts
binär zusammengepresst — die Vorlagen sind lesbares JSON.

Dasselbe Modul liegt an der Box (``boothcore/media/vorlagen_paket.py``).
Kopiert wird es von ``tools/sync_editor.py``, ein Test vergleicht beide
Fassungen byteweise. Es darf deshalb nur aus der Standardbibliothek und aus
``vorlagen_kern`` heraus arbeiten.

Beim Einlesen gilt dieselbe Haltung wie sonst: Ein Paket ist eine fremde
Datei. Was nicht passt, wird nicht repariert und nicht ausgeführt, sondern
abgelehnt oder zurechtgestutzt — und der Mensch erfährt, was geändert wurde.
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence

try:                                    # Galerie
    from app.vorlagen_kern import MAX_DATEI_BYTES as MAX_VORLAGE_BYTES
    from app.vorlagen_kern import VorlageAbgelehnt, annehmen, dateiname, pruefsumme, slug
except ImportError:                     # Box
    from .vorlagen_kern import MAX_DATEI_BYTES as MAX_VORLAGE_BYTES  # type: ignore
    from .vorlagen_kern import VorlageAbgelehnt, annehmen, dateiname, pruefsumme, slug  # type: ignore

#: Version des Formats. Steigt nur, wenn sich die Struktur ändert — ein
#: neueres Paket wird dann von einer älteren Box erkannt und nicht geraten.
FORMAT = 1

#: Die Dateiendung, die der Mensch sieht.
ENDUNG = ".abzug"

#: Was als Medium mitreisen darf. Keine Skripte, keine Archive — ein Paket
#: soll nichts mitbringen, was ausgeführt wird.
MEDIEN_ENDUNGEN = {".jpg", ".jpeg", ".png", ".webp"}

#: Fonts a template's texts use (since 12.11.2026). A font file is data for
#: the renderer, but a crafted one has been an attack path in font engines;
#: so only TTF/OTF, recognised by their first four bytes rather than by the
#: name, and with a size limit far below that of a picture.
SCHRIFT_ENDUNGEN = {".ttf", ".otf"}
SCHRIFT_KENNUNG = {".ttf": (b"\x00\x01\x00\x00", b"true"), ".otf": (b"OTTO",)}
MAX_SCHRIFT_BYTES = 4 * 1024 * 1024

#: Grenzen. Ein Paket, das sie reißt, ist kein Vorlagenpaket mehr.
MAX_VORLAGEN = 60
MAX_MEDIEN = 60
MAX_MEDIUM_BYTES = 12 * 1024 * 1024
MAX_GESAMT_BYTES = 120 * 1024 * 1024

_SICHER = re.compile(r"^[A-Za-z0-9._-]+$")


class Paket(NamedTuple):
    """Was in einem eingelesenen Paket steckt."""
    vorlagen: List[Dict[str, Any]]
    medien: Dict[str, bytes]
    hintergruende: List[str]
    hinweise: List[str]


def medien_von(vorlage: Dict[str, Any]) -> List[str]:
    """Welche Dateien diese Vorlage selbst nennt — in aller Regel die Überlagerung.

    Der Hintergrund einer Collage ist keine Eigenschaft der Vorlage: Er wird
    je Box oder je Event gewählt, die Vorlage trägt dort nur den Platzhalter
    ``@hintergrund``. Hintergrundbilder reisen deshalb als eigene Liste mit
    (siehe ``packen``), nicht über diesen Weg. Farben in ``#rrggbb`` und alle
    ``@``-Platzhalter sind ohnehin keine Dateien.
    """
    aus = []
    for wert in (vorlage.get("background"), vorlage.get("overlay")):
        name = str(wert or "").strip()
        if not name or name.startswith("@") or name.startswith("#"):
            continue
        aus.append(name)
    # Pictures placed in the template and uploaded fonts its texts use.
    for bild in vorlage.get("images") or []:
        if isinstance(bild, dict) and bild.get("file"):
            aus.append(str(bild["file"]))
    for text in vorlage.get("texts") or []:
        if not isinstance(text, dict):
            continue
        for schluessel in ("font", "amp_font"):
            schrift = str(text.get(schluessel) or "")
            if schrift.lower().endswith((".ttf", ".otf")) and schrift not in aus:
                aus.append(schrift)
    return aus


def _sicherer_name(name: str) -> Optional[str]:
    """Nur der nackte Dateiname, und nur aus harmlosen Zeichen.

    Ein ZIP kann ``../../etc/passwd`` enthalten. Wer das ungeprüft auspackt,
    schreibt dorthin. Deshalb wird hier nicht bereinigt, sondern abgelehnt.
    """
    roh = str(name or "").replace("\\", "/").strip()
    if not roh or "/" in roh or roh in (".", ".."):
        return None
    return roh if _SICHER.fullmatch(roh) else None


def packen(vorlagen: List[Dict[str, Any]], medium_lesen: Callable[[str], Optional[bytes]],
           *, hintergruende: Sequence[str] = (), programm: str = "Abzug") -> bytes:
    """Vorlagen samt ihrer Bilder zu einem Paket schnüren.

    ``medium_lesen`` bekommt einen Dateinamen und gibt die Datei zurück oder
    ``None``. Fehlt ein Bild, wird die Vorlage trotzdem gepackt — sie zeigt
    dann eben auf eine Datei, die beim Empfänger nicht dabei ist. Das steht
    in ``paket.json``, damit es niemanden später überrascht.

    ``hintergruende`` sind die Collage-Hintergründe, die mitgehen sollen. Sie
    hängen nicht an einer einzelnen Vorlage — deshalb werden sie eigens
    genannt und beim Einlesen auch wieder als Hintergründe eingeordnet.
    """
    puffer = io.BytesIO()
    namen: List[str] = []
    fehlend: List[str] = []
    gepackt: set = set()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        for v in vorlagen[:MAX_VORLAGEN]:
            tid = slug(v.get("id") or v.get("name") or "vorlage")
            pfad = f"vorlagen/{tid}.json"
            z.writestr(pfad, json.dumps(v, ensure_ascii=False, indent=2, sort_keys=True))
            namen.append(pfad)
            for datei in medien_von(v):
                sicher = _sicherer_name(datei)
                if not sicher or sicher in gepackt:
                    continue
                daten = None
                try:
                    daten = medium_lesen(sicher)
                except Exception:       # noqa: BLE001 — ein fehlendes Bild bricht kein Paket ab
                    daten = None
                if daten is None:
                    fehlend.append(sicher)
                    continue
                z.writestr(f"medien/{sicher}", daten)
                gepackt.add(sicher)
        hg: List[str] = []
        for datei in hintergruende:
            sicher = _sicherer_name(datei)
            if not sicher:
                continue
            if sicher not in gepackt:
                try:
                    daten = medium_lesen(sicher)
                except Exception:       # noqa: BLE001
                    daten = None
                if daten is None:
                    fehlend.append(sicher)
                    continue
                z.writestr(f"medien/{sicher}", daten)
                gepackt.add(sicher)
            hg.append(sicher)
        kopf = {
            "format": FORMAT,
            "programm": programm,
            "erzeugt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "vorlagen": namen,
            "medien": sorted(gepackt),
            "hintergruende": sorted(set(hg)),
        }
        if fehlend:
            kopf["fehlende_medien"] = sorted(set(fehlend))
        z.writestr("paket.json", json.dumps(kopf, ensure_ascii=False, indent=2))
    return puffer.getvalue()


def lesen(daten: bytes) -> Paket:
    """Ein Paket auspacken und prüfen.

    Zurück kommen die geprüften Vorlagen, die mitgelieferten Bilder, die
    Namen der Collage-Hintergründe darunter und eine Liste von Hinweisen — was zurechtgerückt oder weggelassen wurde. Ein
    Paket, das gar keine brauchbare Vorlage enthält, wirft ``ValueError``:
    Dann hat der Mensch die falsche Datei erwischt, und das soll er hören.
    """
    hinweise: List[str] = []
    try:
        z = zipfile.ZipFile(io.BytesIO(daten))
    except Exception as e:              # noqa: BLE001
        raise ValueError(f"Das ist kein Vorlagenpaket: {e}") from e

    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_GESAMT_BYTES:
            raise ValueError("Das Paket ist zu groß.")
        kopf: Dict[str, Any] = {}
        try:
            kopf = json.loads(z.read("paket.json").decode("utf-8"))
        except Exception:               # noqa: BLE001 — ohne Kopf lesen wir eben, was da ist
            hinweise.append("Ohne paket.json — es wird genommen, was sich finden lässt.")
        if int(kopf.get("format") or FORMAT) > FORMAT:
            hinweise.append("Das Paket kommt von einer neueren Fassung. "
                            "Was diese Fassung nicht kennt, bleibt weg.")

        vorlagen: List[Dict[str, Any]] = []
        for eintrag in z.infolist():
            name = eintrag.filename.replace("\\", "/")
            if not name.startswith("vorlagen/") or not name.endswith(".json"):
                continue
            if len(vorlagen) >= MAX_VORLAGEN:
                hinweise.append(f"Mehr als {MAX_VORLAGEN} Vorlagen — der Rest bleibt weg.")
                break
            if eintrag.file_size > MAX_VORLAGE_BYTES:
                hinweise.append(f"{name}: abgelehnt — größer als {MAX_VORLAGE_BYTES // 1024} KB.")
                continue
            try:
                roh = json.loads(z.read(eintrag).decode("utf-8"))
            except Exception:           # noqa: BLE001
                hinweise.append(f"{name}: nicht lesbar, übersprungen.")
                continue
            if not isinstance(roh, dict):
                hinweise.append(f"{name}: keine Vorlage, übersprungen.")
                continue
            # The same gate as an upload or MCP (vorlagen_kern.annehmen): a
            # template that is not of this format stays out, with the reasons.
            try:
                vorlage, eigene = annehmen(roh)
            except VorlageAbgelehnt as e:
                hinweise.append(f"{name}: abgelehnt — " + "; ".join(e.gruende[:6]))
                continue
            hinweise += [f"{vorlage['name']}: {h}" for h in eigene]
            vorlagen.append(vorlage)

        medien: Dict[str, bytes] = {}
        for eintrag in z.infolist():
            name = eintrag.filename.replace("\\", "/")
            if not name.startswith("medien/") or name.endswith("/"):
                continue
            sicher = _sicherer_name(name[len("medien/"):])
            if not sicher:
                hinweise.append(f"{name}: unzulässiger Dateiname, weggelassen.")
                continue
            endung = "." + sicher.rsplit(".", 1)[-1].lower()
            if endung in SCHRIFT_ENDUNGEN:
                if eintrag.file_size > MAX_SCHRIFT_BYTES:
                    hinweise.append(f"{sicher}: Schrift zu groß, weggelassen.")
                    continue
                roh = z.read(eintrag)
                if not roh[:4] in SCHRIFT_KENNUNG[endung]:
                    hinweise.append(f"{sicher}: keine TTF-/OTF-Schrift, weggelassen.")
                    continue
                if len(medien) >= MAX_MEDIEN:
                    hinweise.append(f"Mehr als {MAX_MEDIEN} Dateien — der Rest bleibt weg.")
                    break
                medien[sicher] = roh
                continue
            if endung not in MEDIEN_ENDUNGEN:
                hinweise.append(f"{sicher}: keine Bilddatei, weggelassen.")
                continue
            if eintrag.file_size > MAX_MEDIUM_BYTES:
                hinweise.append(f"{sicher}: zu groß, weggelassen.")
                continue
            if len(medien) >= MAX_MEDIEN:
                hinweise.append(f"Mehr als {MAX_MEDIEN} Bilder — der Rest bleibt weg.")
                break
            medien[sicher] = z.read(eintrag)

    if not vorlagen:
        raise ValueError("In dieser Datei steckt keine Vorlage.")

    # Zeigt eine Vorlage auf ein Bild, das nicht mitgekommen ist, sagen wir das
    # hier — nicht erst, wenn der erste Abzug anders aussieht als gedacht.
    for v in vorlagen:
        for datei in medien_von(v):
            if datei not in medien:
                hinweise.append(f"{v['name']}: Das Bild {datei} fehlt im Paket.")
    hintergruende = [n for n in (kopf.get("hintergruende") or []) if n in medien]
    fehlende_hg = [n for n in (kopf.get("hintergruende") or []) if n not in medien]
    if fehlende_hg:
        hinweise.append("Diese Hintergründe fehlen im Paket: " + ", ".join(sorted(fehlende_hg)))
    return Paket(vorlagen, medien, hintergruende, hinweise)


def paketname(vorlagen: List[Dict[str, Any]]) -> str:
    """Ein Dateiname, den man in einem Ordner wiedererkennt."""
    datum = time.strftime("%Y-%m-%d")
    if len(vorlagen) == 1:
        return f"{slug(vorlagen[0].get('id') or vorlagen[0].get('name') or 'vorlage')}-{datum}{ENDUNG}"
    return f"vorlagen-{len(vorlagen)}-{datum}{ENDUNG}"


__all__ = ["ENDUNG", "FORMAT", "MAX_SCHRIFT_BYTES", "MEDIEN_ENDUNGEN", "Paket", "SCHRIFT_ENDUNGEN", "dateiname", "lesen",
           "medien_von", "packen", "paketname", "pruefsumme"]
