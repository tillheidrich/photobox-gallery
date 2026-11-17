#!/usr/bin/env python3
"""Was Galerie und Box wörtlich teilen, an beide Stellen legen.

Zwei Programme, zwei Installationen — aber der Vorlageneditor und seine
Prüfung sollen an beiden Stellen dieselben sein. Ein Zweitexemplar von Hand
liefe irgendwann auseinander, deshalb kopiert dieses Werkzeug die Quelle aus
``app/`` in die Box, und ein Test vergleicht beide Seiten byteweise.

    python tools/sync_editor.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
PAARE = [
    (WURZEL / "app" / "static" / "js" / "vorlagen-editor.js",
     WURZEL / "booth" / "boothcore" / "web" / "static" / "vorlagen-editor.js"),
    (WURZEL / "app" / "vorlagen_kern.py",
     WURZEL / "booth" / "boothcore" / "media" / "vorlagen_kern.py"),
    (WURZEL / "app" / "vorlagen_paket.py",
     WURZEL / "booth" / "boothcore" / "media" / "vorlagen_paket.py"),
    (WURZEL / "app" / "vorlagen_render.py",
     WURZEL / "booth" / "boothcore" / "media" / "vorlagen_render.py"),
    # Frames for boomerang and video: checked and drawn the same at both ends.
    (WURZEL / "app" / "motion_frames.py",
     WURZEL / "booth" / "boothcore" / "media" / "motion_frames.py"),
    # The second upload target's folder rule and placeholders: the gallery
    # checks and shows it, the booth's sync package builds every upload with it.
    (WURZEL / "app" / "zielordner.py",
     WURZEL / "sync" / "photobox_sync" / "zielordner.py"),
]


def main() -> int:
    for quelle, ziel in PAARE:
        if not quelle.is_file():
            print(f"fehlt: {quelle}", file=sys.stderr)
            return 1
        ziel.parent.mkdir(parents=True, exist_ok=True)
        gleich = ziel.is_file() and ziel.read_bytes() == quelle.read_bytes()
        if not gleich:
            shutil.copyfile(quelle, ziel)
        print(f"{'unverändert' if gleich else 'kopiert'}: {ziel.relative_to(WURZEL)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
