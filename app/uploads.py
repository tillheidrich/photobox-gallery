"""What the gallery accepts as a recording, decided by content, not by name.

Until 11.11.2026 every upload path trusted the file name: a page named
``foto.jpg`` (or ``x.html``) was stored as it came and served back from
``/data`` under the gallery's own origin. Anything that is not a picture
Pillow can open, or a video in a container the booth actually produces, is
refused here, and the stored name is chosen by the server.
"""
from __future__ import annotations

import datetime as _dt
import os
import secrets
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

#: Pictures: format Pillow reports -> extension we store. "MPO" is a JPEG with
#: a second picture (preview) appended: Canon bodies and some phones write
#: it, Pillow names it apart. It is a JPEG for us, stored as ``.jpg``; its
#: ``n_frames`` is 2 but it is not an animation (``strip_gps`` handles it on
#: the JPEG path, which cuts everything behind the first end marker).
IMAGE_FORMATS = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp"}

#: Videos the booth writes (``boothcore/media/boomerang.py``: H.264 in MP4,
#: ``medienablage.py`` also lists WebM) plus MOV, which the FTP intake takes
#: from third-party booth programs. Recognised by their container signature.
VIDEO_EXTS = {".mp4", ".mov", ".webm"}

#: The only types ``/data`` serves inline. Everything else is a download.
INLINE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".pdf": "application/pdf",
}


class UploadRejected(ValueError):
    """An upload the gallery does not take. ``status`` is the HTTP answer."""

    def __init__(self, message: str, status: int = 415):
        super().__init__(message)
        self.status = status


def _mb_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(1, value) * 1024 * 1024


def max_request_bytes() -> int:
    """Hard cap for any request body (Flask ``MAX_CONTENT_LENGTH``).

    The largest legitimate body is the operator's software upload
    (``app/software.py``: ``MAX_BYTES`` = 400 MB). A booth clip is far
    smaller: at most 900 frames (``boomerang.MAX_FRAMES``), at most 1080 px
    wide, H.264 CRF 20 — even at a pessimistic 20 Mbit/s that is 60 s, about
    150 MB. 450 MB covers both with room for multipart overhead.
    """
    return _mb_env("PHOTOBOX_MAX_UPLOAD_MB", 450)


def max_guest_bytes() -> int:
    """Guests upload phone pictures (3-12 MB); 25 MB leaves room, not more."""
    return _mb_env("PHOTOBOX_GUEST_UPLOAD_MB", 25)


#: Major brands of ISO base media files that are videos. HEIC and AVIF use
#: the same container ("heic", "mif1", "avif") and must not pass as video.
_VIDEO_BRANDS = {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
                 b"avc1", b"M4V ", b"mmp4", b"dash", b"qt  "}


def _video_ext(head: bytes) -> Optional[str]:
    # ISO base media (MP4/MOV): box size, then "ftyp" and the major brand.
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in _VIDEO_BRANDS:
        return ".mov" if head[8:12] == b"qt  " else ".mp4"
    # Matroska/WebM: EBML header.
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    return None


def sniff(path: Path) -> Tuple[str, str]:
    """Return ``("image"|"video", ext)`` for an acceptable file, else raise."""
    with Path(path).open("rb") as fh:
        head = fh.read(64)
    ext = _video_ext(head)
    if ext:
        return "video", ext
    try:
        with Image.open(path) as im:
            fmt = im.format
            im.verify()
    except Exception as exc:                 # noqa: BLE001 — anything Pillow refuses is refused
        raise UploadRejected("unsupported file type") from exc
    if fmt not in IMAGE_FORMATS:
        raise UploadRejected("unsupported file type")
    return "image", IMAGE_FORMATS[fmt]


_GPS_IFD = 0x8825
_EXIF_IFD = 0x8769
#: Fields of the EXIF sub-directory that name the camera's owner or carry a
#: serial number (final review V7): CameraOwnerName, BodySerialNumber,
#: LensSerialNumber, and the vendor's MakerNote, where serials hide too.
_OWNER_TAGS = (0xA430, 0xA431, 0xA435, 0x927C)
_XMP_PREFIXES = (b"http://ns.adobe.com/xap/1.0/\x00", b"http://ns.adobe.com/xmp/extension/\x00")
#: Where Pillow puts XMP of a PNG (iTXt "XML:com.adobe.xmp") and a WebP ("xmp").
_XMP_INFO_KEYS = ("XML:com.adobe.xmp", "xmp")


def _clean_exif(exif: "Image.Exif") -> bool:
    """Remove location, owner and serials from ``exif`` in place. True if anything went."""
    changed = False
    if _GPS_IFD in exif:
        del exif[_GPS_IFD]
        changed = True
    if _EXIF_IFD in exif:
        sub = exif.get_ifd(_EXIF_IFD)
        for tag in _OWNER_TAGS:
            if tag in sub:
                del sub[tag]
                changed = True
    return changed


def _exif_without_gps(payload: bytes) -> Optional[bytes]:
    """The EXIF block without location, owner and serials, or None if it had none of them."""
    exif = Image.Exif()
    exif.load(payload)
    if not _clean_exif(exif):
        return None
    return exif.tobytes()


#: Segments dropped whole (second review R2-U4): IPTC/Photoshop (APP13,
#: may carry by-line, contact, city), the comment segment (COM) and the
#: MPF index (APP2 "MPF\0") that points to the secondary images cut below.
_DROP_SEGMENTS = {0xED, 0xFE}


def _scan_end(data: bytes, i: int) -> int:
    """Index of the first marker after the entropy-coded data starting at ``i``.

    Inside scan data 0xFF is followed by 0x00 (a stuffed byte) or a restart
    marker RST0-7; anything else is the next marker.
    """
    n = len(data)
    while i < n - 1:
        if data[i] == 0xFF:
            nxt = data[i + 1]
            if nxt == 0x00 or 0xD0 <= nxt <= 0xD7 or nxt == 0xFF:
                i += 2 if nxt != 0xFF else 1
                continue
            return i
        i += 1
    return n


def _jpeg_without_gps(data: bytes) -> Optional[bytes]:
    """Rewrite the JPEG's metadata segments without touching the image data.

    Re-encoding would cost quality for nothing: the position sits in the EXIF
    GPS directory (and possibly in XMP), both in APP1 segments before the
    picture. Orientation and the rest of IFD0 stay. Since the second review
    (R2-U4) also: IPTC (APP13) and comments (COM) go, and everything after
    the primary image's end marker is cut — a phone's MPF/"Motion Photo"
    appends a second JPEG or a video there, with its own GPS and serials.
    """
    if data[:2] != b"\xff\xd8":
        return None
    out, i, changed = bytearray(b"\xff\xd8"), 2, False
    n = len(data)
    while i + 2 <= n:
        if data[i] != 0xFF:
            return None                          # not a segment boundary: leave the file alone
        marker = data[i + 1]
        if marker == 0xFF:                       # fill byte
            i += 1
            continue
        if marker == 0xD9:                       # end of the primary image
            out += b"\xff\xd9"
            if i + 2 < n:
                changed = True                   # an appended image or video: cut
            return bytes(out) if changed else None
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += data[i:i + 2]
            i += 2
            continue
        if i + 4 > n:
            return None
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 2 or i + 2 + length > n:
            return None
        segment, payload = data[i:i + 2 + length], data[i + 4:i + 2 + length]
        i += 2 + length
        if marker == 0xDA:                       # start of scan: header, then entropy-coded data
            end = _scan_end(data, i)
            out += segment + data[i:end]
            i = end
            continue
        if marker in _DROP_SEGMENTS or (marker == 0xE2 and payload.startswith(b"MPF\x00")):
            changed = True
            continue
        if marker == 0xE1 and payload.startswith(_XMP_PREFIXES):
            changed = True                       # XMP may repeat the position (exif:GPS*)
            continue
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            cleaned = _exif_without_gps(payload)
            if cleaned is not None:
                if not cleaned.startswith(b"Exif\x00\x00"):
                    cleaned = b"Exif\x00\x00" + cleaned
                segment = b"\xff\xe1" + (len(cleaned) + 2).to_bytes(2, "big") + cleaned
                changed = True
        out += segment
    # No end marker: a truncated file. What was cleaned so far stands; keep
    # the rest of the bytes as they were (there is no appended image to cut).
    if changed:
        out += data[i:]
        return bytes(out)
    return None


def _save_animated(im: "Image.Image", path: Path, fmt: str, params: dict) -> None:
    """Re-save every frame of an animated PNG/WebP, with durations and loop.

    Until the second review (R2-U4) the clean copy was the first frame only:
    a guest's animation turned into a still without a word. Pillow hands out
    each frame already composed onto the canvas, so the frames are written
    as full pictures (APNG: disposal none, blend source) - same look, no
    metadata.
    """
    frames, durations = [], []
    for k in range(getattr(im, "n_frames", 1)):
        im.seek(k)
        frames.append(im.convert("RGBA"))
        durations.append(int(im.info.get("duration") or 100))
    params = dict(params, save_all=True, append_images=frames[1:], duration=durations,
                  loop=int(im.info.get("loop", 0) or 0))
    if fmt == "PNG":
        params.update(disposal=0, blend=0, default_image=False)
    frames[0].save(path, fmt, **params)


def strip_gps(path: Path) -> bool:
    """Remove location, camera owner and serial numbers from a guest's picture.

    True if something was removed. Taken out: the EXIF GPS directory, XMP
    (which may repeat the position), and the EXIF owner and serial fields.
    JPEG (what phones upload) is rewritten without re-encoding; IPTC,
    comments and an image or video appended after the picture (MPF, Motion
    Photo) go too. PNG and WebP with any of it are re-saved by Pillow without
    it (PNG losslessly; colour profile and the rest of EXIF stay); an
    animated one keeps all its frames, durations and loop count.
    """
    path = Path(path)
    data = path.read_bytes()
    if data[:2] == b"\xff\xd8":
        cleaned = _jpeg_without_gps(data)
        if cleaned is None:
            return False
        path.write_bytes(cleaned)
        return True
    with Image.open(path) as im:
        if im.format not in ("PNG", "WEBP"):
            return False
        exif = im.getexif()
        exif_changed = _clean_exif(exif)
        has_xmp = any(im.info.get(k) for k in _XMP_INFO_KEYS)
        if not (exif_changed or has_xmp):
            return False
        im.load()
        # Pillow writes no text chunk and no XMP unless asked: re-saving drops them.
        params = {}
        if len(exif):
            params["exif"] = exif.tobytes()
        if im.info.get("icc_profile"):
            params["icc_profile"] = im.info["icc_profile"]
        fmt = im.format
        if fmt == "WEBP":
            params["quality"] = 95
            if im.info.get("lossless"):
                params["lossless"] = True
        if getattr(im, "n_frames", 1) > 1:
            tmp = path.with_name(path.name + ".clean")
            _save_animated(im, tmp, fmt, params)
            animated = True
        else:
            clean = im.copy()
            animated = False
    if animated:
        os.replace(tmp, path)
    else:
        clean.save(path, fmt, **params)
    return True


def server_name(ext: str) -> str:
    """A stored name nobody outside chose: time plus randomness."""
    return f"{_dt.datetime.now():%Y%m%d-%H%M%S}_{secrets.token_hex(5)}{ext}"


def serve_type(path: Path) -> Tuple[str, bool]:
    """Content type for ``/data`` and whether it must be a download.

    Nothing outside ``INLINE_TYPES`` is ever served inline — not HTML, not
    SVG, not a type the platform's mimetypes table happens to know.
    """
    ext = Path(path).suffix.lower()
    if ext in INLINE_TYPES:
        return INLINE_TYPES[ext], False
    return "application/octet-stream", True
