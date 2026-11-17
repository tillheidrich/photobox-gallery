"""Build the template collection: artwork and checked template files.

    python -m tools.kollektion.bauen            # everything
    python -m tools.kollektion.bauen gruen      # one family
    python -m tools.kollektion.bauen --neu      # redraw the artwork too

Writes ``app/vorlagen_bibliothek/medien/*.png`` (artwork) and the templates as
``app/vorlagen_bibliothek/vorlagen/<id>.json`` — each one passed through
``vorlagen_kern.pruefe`` exactly as the editor would save it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

WURZEL = Path(__file__).resolve().parents[2]
if str(WURZEL) not in sys.path:
    sys.path.insert(0, str(WURZEL))

from PIL import Image  # noqa: E402

from app import vorlagen_kern, vorlagen_render  # noqa: E402
from tools.kollektion import bau, familien  # noqa: E402

BIBLIOTHEK = WURZEL / "app" / "vorlagen_bibliothek"
VORLAGEN = BIBLIOTHEK / "vorlagen"


def alle_familien():
    from tools.kollektion import familien_feier
    return list(familien.FAMILIEN) + list(familien_feier.FAMILIEN_FEIER)


def bauen(nur: Optional[List[str]] = None, neu_zeichnen: bool = False, layouts=None
          ) -> List[Tuple[Dict, str, Dict, List[str]]]:
    """(family, layout id, checked template, notes from the check) for every template."""
    VORLAGEN.mkdir(parents=True, exist_ok=True)
    bau.MEDIEN.mkdir(parents=True, exist_ok=True)
    gezeichnet = set()
    aus = []
    for fam, macher in alle_familien():
        if nur and fam["id"] not in nur:
            continue
        for lid in layouts or fam.get("layouts") or familien.ALLE_LAYOUTS:
            roh = macher(lid)
            for datei, fn in roh.pop("_kunst").items():
                ziel = bau.MEDIEN / datei
                if datei in gezeichnet or (ziel.exists() and not neu_zeichnen):
                    continue
                fn().save(ziel, "PNG", optimize=True)
                gezeichnet.add(datei)
            vorlage, hinweise = vorlagen_kern.pruefe(roh)
            vorlage["updated_at"] = 0          # reproducible files
            (VORLAGEN / vorlagen_kern.dateiname(vorlage)).write_bytes(vorlagen_kern.inhalt(vorlage))
            aus.append((fam, lid, vorlage, hinweise))
    return aus


def rendern(vorlage: Dict, texte: Optional[Dict[str, str]] = None) -> Image.Image:
    """The template as the booth prints it, with the sample pictures."""
    werte = {"event_name": "Mia & Tom", "date": "18.11.2026"}
    werte.update(texte or {})
    return vorlagen_render.render_template(
        vorlage, vorlagen_render.beispielbilder(vorlage.get("shots") or 1), assets_dir=bau.MEDIEN,
        texts=werte, style_cfg={"preset": "clean"})


VORSCHAU = BIBLIOTHEK / "vorschau"
KATALOG = BIBLIOTHEK / "katalog.json"


def katalog_schreiben(gebaut: List[Tuple[Dict, str, Dict, List[str]]]) -> None:
    """The library's index and one small preview per template, for the admin page.

    The gallery cannot import this tool, so what its library page shows is
    written down here: families with number, name and category, and for each
    template its file, layout, paper and number of photos.
    """
    import json
    VORSCHAU.mkdir(parents=True, exist_ok=True)
    familien_aus: Dict[str, Dict] = {}
    for fam, lid, v, _ in gebaut:
        eintrag = familien_aus.setdefault(fam["id"], {
            "id": fam["id"], "nummer": fam["nummer"], "name": fam["name"], "kategorie": fam["kategorie"],
            "vorlagen": []})
        datei = vorlagen_kern.dateiname(v)
        eintrag["vorlagen"].append({"datei": datei, "id": v["id"], "name": v["name"], "layout": lid,
                                    "layout_name": familien.GEOMETRIE[lid]["name"], "paper": v["paper"],
                                    "shots": v["shots"], "vorschau": f"{v['id']}.jpg"})
        bild = rendern(v)
        bild.thumbnail((420, 420), Image.Resampling.LANCZOS)
        bild.convert("RGB").save(VORSCHAU / f"{v['id']}.jpg", "JPEG", quality=82, optimize=True)
    alle = sorted(familien_aus.values(), key=lambda f: f["nummer"])
    KATALOG.write_text(json.dumps({"format": 1, "familien": alle}, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")


def main(argv: List[str]) -> int:
    neu = "--neu" in argv
    nur = [a for a in argv if not a.startswith("--")] or None
    gebaut = bauen(nur, neu_zeichnen=neu)
    if not nur:
        katalog_schreiben(gebaut)
    for fam, lid, v, hinweise in gebaut:
        print(f"{v['id']}: {len(v['slots'])} Fotos, {len(v['texts'])} Texte, "
              f"{len(v.get('shapes') or [])} Formen, {len(v.get('images') or [])} Bilder"
              + (f" — {hinweise}" if hinweise else ""))
    print(f"{len(gebaut)} Vorlagen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
