"""Ein Ort für alles, was der Ablauf an Bildern, Videos und Tönen braucht.

Bis zum 20.09.2026 gab es in der ganzen Box genau einen Upload — und der nahm
nur Vorlagenpakete. Ein Betreiber, der einen eigenen Auslöseton wollte, einen
Hintergrund für die Freistellung oder ein Video für den Startbildschirm, kam
an keiner Stelle der Verwaltung damit durch. Er hätte an die Dateiablage des
Kiosk-Rechners gemusst. Auf einem Gerät ohne Tastatur ist das keine Antwort.

Zwei Regeln, die hier mehr zählen als die Bequemlichkeit:

* **Der Name wird entschärft, nicht abgelehnt.** Wer ein Foto vom Telefon
  hochlädt, hat Leerzeichen, Umlaute und Klammern im Namen. Das ist kein
  Fehler des Betreibers, das ist der Normalfall.
* **Was in Gebrauch ist, wird nicht gelöscht.** Und die Absage sagt, wo es
  gebraucht wird — eine Fehlermeldung, die nur „geht nicht" sagt, kostet
  jemanden am Veranstaltungsabend zehn Minuten.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

#: Was eingelagert werden darf, nach Art.
ARTEN: Dict[str, Tuple[str, ...]] = {
    "bild": (".jpg", ".jpeg", ".png", ".gif", ".webp"),
    "video": (".mp4", ".webm"),
    "ton": (".mp3", ".ogg", ".wav", ".m4a"),
}

#: Alles zusammen — die Prüfung beim Hochladen.
ERLAUBT: Tuple[str, ...] = tuple(e for gruppe in ARTEN.values() for e in gruppe)

#: Obergrenze je Datei. Ein Startvideo darf groß sein, eine Box hat aber auch
#: nur eine Platte — und alles hier landet früher oder später im Profil, das
#: zwischen Box und Galerie hin und her reist.
MAX_BYTES = 64 * 1024 * 1024


def art(name: str) -> str:
    """``bild``, ``video``, ``ton`` — oder ``""`` für nichts davon."""
    endung = Path(str(name)).suffix.lower()
    for bezeichnung, endungen in ARTEN.items():
        if endung in endungen:
            return bezeichnung
    return ""


def sauberer_name(roh: str) -> str:
    """Ein Dateiname, der auf jedem Dateisystem und in jeder URL trägt.

    Umlaute werden umgeschrieben statt weggeworfen (``Grün.png`` wird
    ``gruen.png``, nicht ``gr n.png``), Pfadanteile fliegen raus, und übrig
    bleibt Kleinschreibung mit Bindestrichen.
    """
    name = Path(str(roh or "")).name                 # ../../etc/passwd -> passwd
    stamm, punkt, endung = name.rpartition(".")
    if not punkt:
        stamm, endung = name, ""
    ersatz = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss"}
    for a, b in ersatz.items():
        stamm = stamm.replace(a, b)
    stamm = unicodedata.normalize("NFKD", stamm).encode("ascii", "ignore").decode()
    stamm = re.sub(r"[^A-Za-z0-9._-]+", "-", stamm).strip("-._").lower()
    endung = re.sub(r"[^A-Za-z0-9]+", "", endung).lower()
    return f"{stamm or 'datei'}.{endung}" if endung else (stamm or "datei")


def freier_name(ordner: Path, name: str) -> str:
    """Ein Name, den es dort noch nicht gibt — ``ton.mp3`` wird ``ton-2.mp3``.

    Überschrieben wird nie ungefragt: Der Hintergrund, den jemand vor einer
    Stunde eingestellt hat, verschwindet nicht, weil eine zweite Datei
    zufällig gleich heißt.
    """
    ziel = ordner / name
    if not ziel.exists():
        return name
    stamm, punkt, endung = name.rpartition(".")
    if not punkt:
        stamm, endung = name, ""
    for n in range(2, 1000):
        kandidat = f"{stamm}-{n}.{endung}" if endung else f"{stamm}-{n}"
        if not (ordner / kandidat).exists():
            return kandidat
    return f"{stamm}-{Path(name).stem[:4]}x.{endung}" if endung else f"{stamm}-x"


# ── wer benutzt was ───────────────────────────────────────────────────

#: Wo im Profil ein Medienname stehen kann, und wie das auf Deutsch heißt.
#: Die Beschriftung geht wörtlich in die Absage beim Löschen — sie muss also
#: für jemanden taugen, der gerade am Abend vor der Box steht.
STELLEN: Tuple[Tuple[str, str, bool], ...] = (
    ("sounds.countdown", "Ton beim Countdown", False),
    ("sounds.shutter", "Ton beim Auslösen", False),
    ("sounds.done", "Ton, wenn das Bild fertig ist", False),
    ("sounds.print", "Ton beim Drucken", False),
    ("start.media", "Medien auf dem Startbildschirm", True),
    ("backgrounds", "Hintergrund für die Freistellung", True),
    ("collage_backgrounds", "Hintergrund für Collagen", True),
    ("branding.logo", "Logo", False),
    ("branding.watermark", "Wasserzeichen", False),
)


def _hol(profil: dict, pfad: str):
    wert = profil
    for teil in pfad.split("."):
        if not isinstance(wert, dict):
            return None
        wert = wert.get(teil)
    return wert


def verwendung(profil: dict, name: str) -> List[str]:
    """Alle Stellen, an denen dieses Medium eingestellt ist — auf Deutsch."""
    treffer: List[str] = []
    for pfad, beschriftung, ist_liste in STELLEN:
        wert = _hol(profil or {}, pfad)
        if ist_liste:
            if isinstance(wert, (list, tuple)) and name in [str(x) for x in wert]:
                treffer.append(beschriftung)
        elif wert and str(wert) == name:
            treffer.append(beschriftung)
    return treffer


def in_vorlagen(vorlagen: Iterable[dict], name: str) -> List[str]:
    """Vorlagen, die dieses Bild als Überlagerung oder Hintergrund nutzen."""
    treffer = []
    for v in vorlagen or []:
        if not isinstance(v, dict):
            continue
        if str(v.get("overlay") or "") == name or str(v.get("background") or "") == name:
            treffer.append(str(v.get("name") or v.get("id") or "eine Vorlage"))
    return treffer


def liste(ordner: Path, profil: dict, vorlagen: Optional[Iterable[dict]] = None) -> List[dict]:
    """Was liegt da, wie groß ist es, und wo wird es gebraucht."""
    if not ordner.exists():
        return []
    raus = []
    for p in sorted(ordner.iterdir()):
        if not p.is_file():
            continue
        art_ = art(p.name)
        if not art_:
            continue                                  # Vorlagen-JSON und Sonstiges
        benutzt = verwendung(profil, p.name) + [f"Vorlage „{v}“" for v in in_vorlagen(vorlagen, p.name)]
        raus.append({"name": p.name, "art": art_, "bytes": p.stat().st_size,
                     "verwendet_in": benutzt})
    return raus
