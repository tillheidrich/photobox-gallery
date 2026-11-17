#!/usr/bin/env python3
"""Web copies of the marketing screenshots: WebP and AVIF at a few widths.

    python tools/marketing/export.py <shots dir> <target dir> [640,1280,1920]

For every PNG/JPG in <shots dir> it writes <name>-<width>.webp and
<name>-<width>.avif into <target dir>, never wider than the source, and a
sizes.json with each file's pixel size (the page needs width and height on
every <img> so nothing jumps while loading). Pillow only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image


def main() -> None:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    widths = [int(w) for w in (sys.argv[3] if len(sys.argv) > 3 else "640,1280,1920").split(",")]
    dst.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for f in sorted(list(src.glob("*.png")) + list(src.glob("*.jpg"))):
        with Image.open(f) as im:
            im = im.convert("RGB")
            done = set()
            for w in widths:
                w = min(w, im.width)
                if w in done:
                    continue
                done.add(w)
                h = round(im.height * w / im.width)
                small = im.resize((w, h), Image.Resampling.LANCZOS)
                small.save(dst / f"{f.stem}-{w}.webp", "WEBP", quality=80, method=6)
                small.save(dst / f"{f.stem}-{w}.avif", "AVIF", quality=60, speed=6)
                sizes.setdefault(f.stem, []).append([w, h])
    (dst / "sizes.json").write_text(json.dumps(sizes, indent=1), encoding="utf-8")
    print("exported", len(sizes), "pictures to", dst)


if __name__ == "__main__":
    main()
