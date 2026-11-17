"""Sorting out near-duplicates and blurry photos by image statistics (no. 62).

No AI and no model (AGENTS.md, rule 3): two numbers per picture, both
computed with Pillow alone (numpy is not a gallery dependency).

* ``phash`` - a 64-bit difference hash (dHash): the picture shrunk to 9x8
  grey values, one bit per horizontal neighbour pair ("is the left one
  brighter?"). Two shots of the same moment differ in a few bits; two
  different scenes in about half of them. ``NEAR`` bits or fewer apart
  counts as a near-duplicate.
* ``sharpness`` - the variance of an edge image (``ImageFilter.FIND_EDGES``)
  of the picture scaled to ``SHARP_PX`` on its long edge, so a 24 MP
  original and a phone upload are measured alike. Blur flattens edges and
  the variance drops. The value has no unit; it is only compared within
  one event (``BLUR_FACTOR`` of the event's median).

Computed at upload (guest, admin, import, FTP) from the web version the
upload just made, and for everything else (booth uploads, older pictures) by ``backfill`` in the maintenance run,
a small batch at a time. The admin view proposes; nothing is hidden or
deleted unless the operator presses a button.
"""
from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageFilter, ImageOps, ImageStat

from app import db

#: Bits (of 64) two near-duplicates may differ in.
NEAR = 10
#: Long edge for the sharpness measure.
SHARP_PX = 512
#: "Blurry" = below this share of the event's median sharpness.
BLUR_FACTOR = {"strict": 0.25, "normal": 0.4, "loose": 0.55}
#: Pictures compared pairwise at most (the comparison is quadratic).
MAX_COMPARE = 3000
#: Kinds that are measured; videos and GIF animations are not.
KINDS = ("photo", "collage", "print")


def dhash(img: Image.Image) -> str:
    small = ImageOps.exif_transpose(img).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(small.tobytes())          # mode "L": one byte per pixel, row by row
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return f"{bits:016x}"


def distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def sharpness(img: Image.Image) -> float:
    grey = ImageOps.exif_transpose(img).convert("L")
    grey.thumbnail((SHARP_PX, SHARP_PX), Image.Resampling.LANCZOS)
    edges = grey.filter(ImageFilter.FIND_EDGES)
    # The filter draws a frame along the border; it is not in the picture.
    if edges.width > 8 and edges.height > 8:
        edges = edges.crop((2, 2, edges.width - 2, edges.height - 2))
    return round(float(ImageStat.Stat(edges).var[0]), 2)


def measure(path: Path) -> Optional[Tuple[str, float]]:
    try:
        with Image.open(path) as im:
            im.seek(0)
            im.load()
            return dhash(im), sharpness(im)
    except Exception:                       # noqa: BLE001 — an unreadable file is simply not measured
        return None


def _source_file(row: Dict[str, Any]) -> Optional[Path]:
    from app import media
    root = media.data_dir()
    # The web version first: big enough for both numbers, cheaper than the
    # original. The thumbnail is the last resort.
    for key in ("path_webp", "path_thumb", "path_original"):
        rel = row.get(key)
        if rel and not str(rel).lower().endswith((".mp4", ".webm", ".mov")):
            p = root / rel
            if p.is_file():
                return p
    return None


def store(media_id: int) -> bool:
    """Measure one picture and keep the numbers. False when it cannot be read."""
    row = db.get_media(int(media_id))
    if not row:
        return False
    path = _source_file(row)
    got = measure(path) if path else None
    con = db.get_db()
    try:
        # A file that cannot be read gets an empty hash, so the backfill
        # does not try it again every night.
        con.execute("UPDATE media SET phash=?, sharpness=? WHERE id=?",
                    (got[0] if got else "", got[1] if got else None, int(media_id)))
        con.commit()
    finally:
        con.close()
    return bool(got)


def backfill(limit: int = 200) -> Dict[str, int]:
    """For the maintenance run: measure pictures that have no numbers yet."""
    from app import ecosystem
    ecosystem.init_schema()
    marks = ",".join("?" * len(KINDS))
    con = db.get_db()
    try:
        ids = [int(r[0]) for r in con.execute(
            f"SELECT id FROM media WHERE phash IS NULL AND COALESCE(kind,'photo') IN ({marks}) "
            "AND COALESCE(variant,'') != 'original' ORDER BY id DESC LIMIT ?", (*KINDS, int(limit)))]
    finally:
        con.close()
    done = sum(1 for i in ids if store(i))
    return {"done": done, "tried": len(ids)}


def _rows(event_id: int) -> List[Dict[str, Any]]:
    marks = ",".join("?" * len(KINDS))
    con = db.get_db()
    try:
        return [dict(r) for r in con.execute(
            f"SELECT * FROM media WHERE event_id=? AND COALESCE(hidden,0)=0 AND COALESCE(variant,'') != 'original' "
            f"AND COALESCE(kind,'photo') IN ({marks}) ORDER BY datetime(created_at), id", (int(event_id), *KINDS))]
    finally:
        con.close()


def analyse(event_id: int, *, strictness: str = "normal", measure_missing: int = 60) -> Dict[str, Any]:
    """Groups of near-duplicates (sharpest first, marked ``keep``) and blurry pictures.

    Up to ``measure_missing`` pictures without numbers are measured on the
    way, so the view is useful before the first maintenance run.
    """
    rows = _rows(event_id)
    missing = [r for r in rows if r.get("phash") is None][:measure_missing]
    for r in missing:
        store(int(r["id"]))
    if missing:
        rows = _rows(event_id)
    measured = [r for r in rows if r.get("phash")]
    unmeasured = len(rows) - len(measured)

    # Union-find over every close pair.
    cand = measured[:MAX_COMPARE]
    parent = list(range(len(cand)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    hashes = [int(r["phash"], 16) for r in cand]
    for i in range(len(cand)):
        hi = hashes[i]
        for j in range(i + 1, len(cand)):
            if (hi ^ hashes[j]).bit_count() <= NEAR:
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a
    clusters: Dict[int, List[Dict[str, Any]]] = {}
    for i, r in enumerate(cand):
        clusters.setdefault(find(i), []).append(r)
    groups = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: -(r.get("sharpness") or 0))
        groups.append({"keep": int(members[0]["id"]), "items": members})
    groups.sort(key=lambda g: g["items"][0].get("created_at") or "")

    values = [float(r["sharpness"]) for r in measured if r.get("sharpness") is not None]
    mid = median(values) if values else 0.0
    limit = round(mid * BLUR_FACTOR.get(strictness, BLUR_FACTOR["normal"]), 2)
    blurry = sorted((r for r in measured if r.get("sharpness") is not None and float(r["sharpness"]) < limit),
                    key=lambda r: float(r["sharpness"]))
    return {"groups": groups, "blurry": blurry, "threshold": limit, "median": round(mid, 2),
            "total": len(rows), "unmeasured": unmeasured, "strictness": strictness}
