"""The template library that ships with the gallery ("Vorlagen-Bibliothek").

Built by ``tools/kollektion`` (templates as checked JSON, artwork, fonts,
previews, ``katalog.json``). This module only reads it and copies a chosen
template into the gallery's own templates — the same place an imported
``.abzug`` package goes, so it reaches the booths on the path every own
template takes. Nothing the installation already has is overwritten.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HIER = Path(__file__).resolve().parent
VORLAGEN = HIER / "vorlagen"
MEDIEN = HIER / "medien"
VORSCHAU = HIER / "vorschau"


@lru_cache(maxsize=1)
def katalog() -> Dict[str, Any]:
    try:
        return json.loads((HIER / "katalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"format": 1, "familien": []}


def _bekannt(datei: str) -> bool:
    return any(v["datei"] == datei for f in katalog()["familien"] for v in f["vorlagen"])


def vorlage(datei: str) -> Dict[str, Any]:
    """A library template by its file name — only names the catalogue lists."""
    if not _bekannt(str(datei)):
        raise KeyError(datei)
    return json.loads((VORLAGEN / str(datei)).read_text(encoding="utf-8"))


def vorschau_pfad(name: str) -> Optional[Path]:
    """The preview picture, only for names the catalogue lists (never a path)."""
    name = str(name or "")
    if not any(v["vorschau"] == name for f in katalog()["familien"] for v in f["vorlagen"]):
        return None
    p = VORSCHAU / name
    return p if p.is_file() else None


def suche(q: str = "", kategorie: str = "") -> List[Dict[str, Any]]:
    """Families matching a search word (family, category or layout) and a category."""
    q = str(q or "").strip().lower()
    kategorie = str(kategorie or "").strip()
    aus = []
    for fam in katalog()["familien"]:
        if kategorie and fam["kategorie"] != kategorie:
            continue
        text = " ".join([fam["name"], fam["kategorie"], fam["id"]] +
                        [v["layout_name"] for v in fam["vorlagen"]]).lower()
        if q and q not in text:
            continue
        aus.append(fam)
    return aus


def kategorien() -> List[str]:
    gesehen: List[str] = []
    for fam in katalog()["familien"]:
        if fam["kategorie"] not in gesehen:
            gesehen.append(fam["kategorie"])
    return gesehen


def uebernehmen(datei: str, assets_dir: Path) -> Tuple[str, List[str]]:
    """Copy one library template with its pictures and fonts into the gallery.

    Returns the file name the template now has and notes for the operator.
    An identical template already there is left alone ("schon übernommen");
    an own template with the same name stays untouched and the library's
    copy is saved under a new name. A picture or font of the same name that
    differs is kept, and said so.
    """
    from app import ecosystem, vorlagen as vorlagen_mod, vorlagen_kern, vorlagen_paket

    inhalt = vorlage(datei)
    hinweise: List[str] = []
    assets_dir.mkdir(parents=True, exist_ok=True)
    vorhanden = {a["file"]: a for a in ecosystem.list_assets()}
    for name in vorlagen_paket.medien_von(inhalt):
        quelle = MEDIEN / name
        if not quelle.is_file():
            hinweise.append(f"{name}: fehlt in der Bibliothek.")
            continue
        daten = quelle.read_bytes()
        ziel = assets_dir / name
        if ziel.exists() and ziel.read_bytes() != daten:
            hinweise.append(f"{name}: eine andere Datei dieses Namens war schon da — behalten.")
            continue
        if not ziel.exists():
            ziel.write_bytes(daten)
        if name not in vorhanden:
            art = "font" if name.lower().endswith((".ttf", ".otf")) else "overlay"
            ecosystem.add_asset(art, name, name, vorlagen_paket.pruefsumme(daten), len(daten))

    geprueft, _ = vorlagen_kern.annehmen(inhalt)
    geprueft["updated_at"] = inhalt.get("updated_at", 0)
    ziel = assets_dir / vorlagen_kern.dateiname(geprueft)
    if ziel.exists():
        try:
            gleich = vorlagen_kern.pruefe(json.loads(ziel.read_text(encoding="utf-8")))[0]
        except (OSError, ValueError):
            gleich = None
        if gleich is not None and {**gleich, "updated_at": 0} == {**geprueft, "updated_at": 0}:
            return ziel.name, hinweise + ["Diese Vorlage ist schon übernommen."]
        # An own template carries this name: it stays, the copy gets a name of its own.
        n = 2
        while (assets_dir / f"{geprueft['id']}-{n}.json").exists():
            n += 1
        geprueft = dict(geprueft, id=f"{geprueft['id']}-{n}", name=f"{geprueft['name']} ({n})")
        hinweise.append("Eine eigene Vorlage hatte denselben Namen — sie bleibt, die Kopie heißt "
                        f"„{geprueft['name']}“.")
    eintrag = vorlagen_mod.speichern(geprueft, assets_dir)
    return eintrag["file"], hinweise
