"""Eigene Druckvorlagen ablegen und ausliefern (Galerie-Seite).

Geprüft wird in :mod:`app.vorlagen_kern` — dieselbe Datei nutzt die Box.
Hier steht nur, was die Galerie damit tut: als Medium der Art „template"
ablegen (siehe ``ecosystem.ASSET_KINDS``). Damit reist die Vorlage auf dem
Weg, den es schon gibt: Die Box lädt ihre Medien beim Sync herunter, prüft die
Prüfsumme und hat die Vorlage beim nächsten Durchgang.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.vorlagen_kern import (  # noqa: F401 — die Galerie greift über dieses Modul darauf zu
    BILD_ENDUNGEN, MAX_SLOTS, MAX_TEXTE, PAPIERE, SCHRIFT_ENDUNGEN, VorlageAbgelehnt, annehmen, datei_name,
    dateiname, inhalt, lade, pruefe, pruefsumme, slug,
)


def beispielbilder(anzahl: int) -> List[Any]:
    """Stand-in photos for the preview: plainly samples, numbered like the shots.

    Real guests' photos have no place in a layout preview. They live in the
    shared renderer, because a test print at the booth uses the very same ones.
    """
    from app import vorlagen_render
    return vorlagen_render.beispielbilder(anzahl)


def speichern(vorlage: Dict[str, Any], assets_dir: Path) -> Dict[str, Any]:
    """Die Vorlage als Medium ablegen und den Eintrag zurückgeben.

    Gibt es sie schon, wird sie ersetzt — die Kennung ist der Name der Datei.
    Danach steigt die Profilversion aller Boxen, damit sie die neue Fassung
    beim nächsten Sync holen.
    """
    from app import ecosystem, db

    daten = inhalt(vorlage)
    datei = dateiname(vorlage)
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / datei).write_bytes(daten)

    vorhanden = next((a for a in ecosystem.list_assets("template") if a["file"] == datei), None)
    if vorhanden:
        con = db.get_db()
        con.execute("UPDATE booth_assets SET name=?, sha256=?, size_bytes=? WHERE id=?",
                    (vorlage["name"], pruefsumme(daten), len(daten), int(vorhanden["id"])))
        con.commit()
        con.close()
        eintrag = dict(vorhanden, name=vorlage["name"], sha256=pruefsumme(daten), size_bytes=len(daten))
    else:
        aid = ecosystem.add_asset("template", vorlage["name"], datei, pruefsumme(daten), len(daten))
        eintrag = {"id": aid, "kind": "template", "name": vorlage["name"], "file": datei,
                   "sha256": pruefsumme(daten), "size_bytes": len(daten)}
    for d in db.list_devices() or []:
        ecosystem.bump_profile(d["id"])
    return eintrag


def laden(datei: str, assets_dir: Path) -> Optional[Dict[str, Any]]:
    """A stored template through the gate, or None.

    Files can come back by other ways than the editor (a restored backup, a
    copy into the data folder); whatever does not pass is left out and
    logged once per file version, never shown or sent raw.
    """
    p = assets_dir / str(datei)
    if not p.is_file() or p.suffix != ".json":
        return None
    try:
        return lade(p)
    except OSError:
        return None
    except ValueError as e:
        _melde_einmal(p, e)
        return None


_GEMELDET: Dict[str, tuple] = {}


def _melde_einmal(p: Path, fehler: Exception) -> None:
    import logging
    try:
        st = p.stat()
        stand = (st.st_mtime, st.st_size)
    except OSError:
        stand = (0, 0)
    if _GEMELDET.get(str(p)) != stand:
        _GEMELDET[str(p)] = stand
        logging.getLogger(__name__).warning("Vorlage %s übersprungen: %s", p.name, fehler)


def eigene(assets_dir: Path) -> List[Dict[str, Any]]:
    """Alle eigenen Vorlagen mit ihrem Inhalt — für Liste und Editor."""
    from app import ecosystem
    aus = []
    for a in ecosystem.list_assets("template"):
        inhalt_ = laden(a["file"], assets_dir)
        if inhalt_:
            aus.append({"asset": a, "template": inhalt_})
    return aus


def testdruck(device_id: int, vorlage_kennung: str, assets_dir: Path, mark: bool = True) -> Dict[str, Any]:
    """Send one saved template to a booth as a test print (job ``test_print``).

    The same function behind the editor's button and the MCP tool. Only
    names the gallery itself lists are accepted: an own template by its file
    name, or a built-in one by its id — never a path. An own template travels
    with its saved content, so the booth prints the version just saved even
    before its next media sync.
    """
    from app import db, ecosystem

    kennung = str(vorlage_kennung or "").strip()
    if not db.get_device_by_id(int(device_id)):
        raise ValueError(f"Keine Box mit der Nummer {device_id}")
    payload: Dict[str, Any] = {"template": kennung, "mark": bool(mark)}
    name = ""
    eigen = next((e for e in eigene(assets_dir) if e["asset"]["file"] == kennung), None)
    if eigen:
        payload["content"], _ = pruefe(eigen["template"])
        name = str(eigen["template"].get("name") or kennung)
    elif kennung in ecosystem.BUILTIN_COLLAGE_IDS:
        name = next(c["name"] for c in ecosystem.BUILTIN_COLLAGES if c["id"] == kennung)
    else:
        raise ValueError(f"Keine bekannte Vorlage: {kennung[:80]}")
    job_id = ecosystem.add_job(int(device_id), "test_print", payload)
    return {"job_id": job_id, "booth_id": int(device_id), "template": kennung, "name": name,
            "mark": bool(mark)}
