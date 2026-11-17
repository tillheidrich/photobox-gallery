"""The time a picture was taken, as the booth knows it — written into EXIF.

The camera's clock cannot be trusted: an EOS M50 on a mains adapter (dummy
battery) forgets it and dates every picture 2017 (seen 15.11.2026). The PC's
clock is what the booth goes by everywhere else (run folders, library, the
``taken_at`` of every upload), so the pictures say the same.

Two cases, two rules:

* Files the booth renders itself get fresh EXIF with the time of the run
  (``exif_for``). They are new files anyway.
* The camera's original is never re-encoded: it is the one file that is
  exactly what the camera saw. When its EXIF date is off by more than a few
  minutes, ``stamp_original`` overwrites the date strings where they are.
  EXIF dates have a fixed length, so nothing moves: same size, same maker
  notes, same thumbnail, the picture data byte for byte the same. When that
  is not possible (no EXIF, a layout we do not understand), the file stays
  as it is — the booth's own records still carry the right time.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterator, Optional, Tuple

from PIL import Image

DATETIME = 0x0132
EXIF_IFD = 0x8769
ORIGINAL, DIGITIZED = 0x9003, 0x9004
OFFSET, OFFSET_ORIGINAL, OFFSET_DIGITIZED = 0x9010, 0x9011, 0x9012
#: Within this many seconds the camera counts as right and is left alone.
TOLERANCE = 300
_FORMAT = "%Y:%m:%d %H:%M:%S"


def date_text(when: float) -> str:
    return time.strftime(_FORMAT, time.localtime(when))


def offset_text(when: float) -> str:
    """The local UTC offset at that moment, as EXIF writes it: "+01:00"."""
    secs = time.localtime(when).tm_gmtoff or 0
    sign = "-" if secs < 0 else "+"
    secs = abs(int(secs))
    return f"{sign}{secs // 3600:02d}:{secs % 3600 // 60:02d}"


def exif_for(when: float) -> bytes:
    """EXIF for a file the booth rendered: when the run was taken, nothing else."""
    exif = Image.Exif()
    exif[DATETIME] = date_text(when)
    sub = exif.get_ifd(EXIF_IFD)
    sub[ORIGINAL] = sub[DIGITIZED] = date_text(when)
    sub[OFFSET] = sub[OFFSET_ORIGINAL] = sub[OFFSET_DIGITIZED] = offset_text(when)
    return exif.tobytes()


# ── the camera's original, patched in place ──────────────────────────

def _exif_payload(data: bytes) -> Optional[int]:
    """Where the TIFF header of the EXIF block starts, or None."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xDA, 0xD9):                  # picture data from here on: no EXIF before it
            return None
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\x00\x00":
            return i + 10
        i += 2 + length
    return None


def _entries(data: bytes, tiff: int, ifd: int, order: str) -> Iterator[Tuple[int, int, int, int]]:
    """(tag, type, count, absolute position of the value) of one directory."""
    start = tiff + ifd
    if ifd <= 0 or start + 2 > len(data):
        raise ValueError("directory outside the file")
    n = int.from_bytes(data[start:start + 2], order)
    if start + 2 + 12 * n > len(data):
        raise ValueError("directory runs past the end")
    for k in range(n):
        e = start + 2 + 12 * k
        tag = int.from_bytes(data[e:e + 2], order)
        typ = int.from_bytes(data[e + 2:e + 4], order)
        count = int.from_bytes(data[e + 4:e + 8], order)
        if typ == 2 and count <= 4:
            where = e + 8                            # short strings sit in the entry itself
        else:
            where = tiff + int.from_bytes(data[e + 8:e + 12], order)
        yield tag, typ, count, where


def _date_fields(data: bytes):
    """Every date/offset string in the EXIF: {tag: (position, length)}."""
    tiff = _exif_payload(data)
    if tiff is None or tiff + 8 > len(data):
        return {}
    order = {b"II": "little", b"MM": "big"}.get(bytes(data[tiff:tiff + 2]))
    if order is None or int.from_bytes(data[tiff + 2:tiff + 4], order) != 42:
        return {}
    found = {}
    sub = None
    for tag, typ, count, where in _entries(data, tiff, int.from_bytes(data[tiff + 4:tiff + 8], order), order):
        if tag == DATETIME and typ == 2:
            found[tag] = (where, count)
        elif tag == EXIF_IFD and count == 1:
            sub = where - tiff                       # a LONG in the entry: the value is the offset
    if sub:
        for tag, typ, count, where in _entries(data, tiff, sub, order):
            if typ == 2 and tag in (ORIGINAL, DIGITIZED, OFFSET, OFFSET_ORIGINAL, OFFSET_DIGITIZED):
                found[tag] = (where, count)
    return {t: (w, c) for t, (w, c) in found.items() if 0 < w and w + c <= len(data)}


def _read(data: bytes, field) -> str:
    where, count = field
    return bytes(data[where:where + count]).split(b"\x00")[0].decode("ascii", "replace")


def stamp_original(path: Path, when: float, tolerance: float = TOLERANCE) -> bool:
    """Give the camera's file the PC's time, if its own is wrong. True if patched.

    Only the bytes of the date strings change, and only when the new text has
    exactly the length of the old one (it always does for a well-formed
    camera file). Anything unexpected: the file is left as it is.
    """
    path = Path(path)
    try:
        data = bytearray(path.read_bytes())
        fields = _date_fields(bytes(data))
    except (OSError, ValueError):
        return False
    stamp = fields.get(ORIGINAL) or fields.get(DATETIME)
    if not stamp:
        return False
    try:
        camera = time.mktime(time.strptime(_read(data, stamp), _FORMAT))
        if abs(camera - when) <= tolerance:
            return False
    except (ValueError, OverflowError):
        pass                                         # "0000:00:00 00:00:00" and the like: wrong
    texts = {DATETIME: date_text(when), ORIGINAL: date_text(when), DIGITIZED: date_text(when),
             OFFSET: offset_text(when), OFFSET_ORIGINAL: offset_text(when),
             OFFSET_DIGITIZED: offset_text(when)}
    changed = False
    for tag, (where, count) in fields.items():
        new = texts[tag].encode("ascii") + b"\x00"
        if len(new) != count:
            continue                                 # an unusual length: leave this one
        data[where:where + count] = new
        changed = True
    if not changed:
        return False
    tmp = path.with_name(path.name + ".zeit")
    tmp.write_bytes(bytes(data))
    os.replace(tmp, path)
    return True
