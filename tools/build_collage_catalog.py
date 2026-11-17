#!/usr/bin/env python3
"""Den Vorlagenkatalog der Box für die Verwaltung ablegen.

Die Quelle der Vorlagen ist ``boothcore.media.compose`` — dort stehen sie als
Code, weil die Box sie rendert. Die Verwaltung braucht dieselben Maße, um eine
Vorschau zu zeichnen, kann aber die Box nicht importieren (zwei getrennte
Programme, zwei Installationen). Deshalb legt dieses Werkzeug den Katalog als
JSON neben die Galerie. Ein Test vergleicht beide Seiten; läuft er rot, einmal
hier durchlaufen lassen:

    python tools/build_collage_catalog.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL / "booth"))

ZIEL = WURZEL / "app" / "static" / "data" / "collage_templates.json"


def katalog() -> list:
    from boothcore.media import compose
    aus = []
    for t in compose.BUILTIN_TEMPLATES:
        info = compose.template_info(t)
        aus.append({
            "id": t["id"], "name": info["name"], "shots": info["shots"],
            "orientation": info["orientation"], "paper": info["paper"],
            "canvas": info["canvas"],
            "slots": [dict({"x": s["x"], "y": s["y"], "w": s["w"], "h": s["h"],
                            "shot": int(s.get("shot", i))},
                           **({"radius": s["radius"]} if "radius" in s and s["radius"] != "@rund" else {}))
                      for i, s in enumerate(t.get("slots", []))],
            "texts": [{"x": t2.get("x", 0), "y": t2.get("y", 0), "size": t2.get("size", 48),
                       "align": t2.get("align", "left"), "value": t2.get("value", ""),
                       "color": t2.get("color", "@schrift"), "bold": bool(t2.get("bold", True))}
                      for t2 in t.get("texts", [])],
            "qr": t.get("qr") or None,
            # The plain layouts have a fixed paper tone and no frame line; the
            # gallery's preview renders them from this file (app/layout_wahl.py).
            **({"background": t["background"], "plain": True} if t.get("plain") else {}),
        })
    return aus


def presets() -> dict:
    from boothcore.media import compose
    return compose.STYLE_PRESETS


def inhalt() -> str:
    return json.dumps({"templates": katalog(), "presets": presets()},
                      ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main() -> int:
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    neu = inhalt()
    alt = ZIEL.read_text(encoding="utf-8") if ZIEL.exists() else ""
    ZIEL.write_text(neu, encoding="utf-8")
    print(f"{'unverändert' if alt == neu else 'geschrieben'}: {ZIEL.relative_to(WURZEL)} "
          f"({len(json.loads(neu)['templates'])} Vorlagen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
