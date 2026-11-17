#!/usr/bin/env python3
"""Write the built-in frames for boomerang and video ("Rahmen") to both programs.

Four neutral frames, drawn from colours, gradients and lines only: no photo,
no third-party art. They use the event's name and date ({event_name},
{date}), so every event gets its own title without a file to upload.

    python tools/build_motion_frames.py

Writes app/static/data/motion_frames.json and
booth/boothcore/media/motion_frames.json (identical; a test compares them
with what this script would write). With ``--previews DIR`` it also writes a
preview picture of each frame there.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "app" / "static" / "data" / "motion_frames.json",
           ROOT / "booth" / "boothcore" / "media" / "motion_frames.json"]

TITLE = "{event_name}"
DATE = "{date}"

# Phone apps lay their buttons over the top and bottom of a 9:16 film; the
# built-ins keep their lines out of the bottom 320 px (see the frame docs).
FRAMES = [
    {"kind": "motion_frame", "id": "titel-unten", "name": "Titel unten", "format": "9:16",
     "background": "#1b1b21", "background_to": "#2e2e38",
     "window": {"x": 60, "y": 120, "w": 960, "h": 1290, "radius": 36, "shadow": 24},
     "texts": [{"value": TITLE, "x": 540, "y": 1446, "size": 80, "bold": True, "align": "center",
                "color": "#ffffff", "w": 960},
               {"value": DATE, "x": 540, "y": 1548, "size": 42, "bold": False, "align": "center",
                "color": "#c9c9d2"}]},
    {"kind": "motion_frame", "id": "rahmen-hell", "name": "Rahmen schlicht hell", "format": "9:16",
     "background": "#f3f0ea",
     "window": {"x": 72, "y": 72, "w": 936, "h": 1380, "radius": 20},
     "texts": [{"value": TITLE, "x": 540, "y": 1484, "size": 64, "bold": True, "align": "center",
                "color": "#26262a", "w": 936},
               {"value": DATE, "x": 540, "y": 1568, "size": 36, "bold": False, "align": "center",
                "color": "#6c6a66"}]},
    {"kind": "motion_frame", "id": "rahmen-dunkel", "name": "Rahmen schlicht dunkel", "format": "9:16",
     "background": "#111114",
     "window": {"x": 76, "y": 76, "w": 928, "h": 1372, "radius": 20,
                "border": {"width": 4, "color": "#f2f2f2"}},
     "texts": [{"value": TITLE, "x": 540, "y": 1484, "size": 64, "bold": True, "align": "center",
                "color": "#f2f2f2", "w": 928},
               {"value": DATE, "x": 540, "y": 1568, "size": 36, "bold": False, "align": "center",
                "color": "#a4a4ac"}]},
    {"kind": "motion_frame", "id": "titel-links", "name": "Titel links", "format": "16:9",
     "background": "#1a1c23", "background_to": "#272b35",
     "window": {"x": 660, "y": 60, "w": 1200, "h": 960, "radius": 28, "shadow": 20},
     "shapes": [{"kind": "rect", "x": 80, "y": 520, "w": 120, "h": 6, "fill": "#d9b26a"}],
     "texts": [{"value": TITLE, "x": 80, "y": 380, "size": 72, "bold": True, "align": "left",
                "color": "#ffffff", "w": 540},
               {"value": DATE, "x": 80, "y": 556, "size": 40, "bold": False, "align": "left",
                "color": "#c3c7cf"}]},
]


def built() -> bytes:
    return (json.dumps({"frames": FRAMES}, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--previews", help="folder for a preview picture of each frame")
    args = ap.parse_args(argv)
    data = built()
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        same = target.is_file() and target.read_bytes() == data
        if not same:
            target.write_bytes(data)
        print(f"{'unverändert' if same else 'geschrieben'}: {target.relative_to(ROOT)}")
    if args.previews:
        sys.path.insert(0, str(ROOT))
        from app import motion_frames
        out = Path(args.previews)
        out.mkdir(parents=True, exist_ok=True)
        for frame in motion_frames.builtins(TARGETS[0]):
            pic = motion_frames.preview(frame, None, {"event_name": "Sommerfest 2027", "date": "17.07.2027"},
                                        max_px=900)
            pic.save(out / f"rahmen-{frame['id']}.png")
            print(f"Vorschau: {out / ('rahmen-' + frame['id'] + '.png')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
