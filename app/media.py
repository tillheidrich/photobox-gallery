import hashlib
import io
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# Pillow kann WebP nur, wenn libwebp verfügbar ist (Dockerfile installiert libwebp-dev)


# -----------------------------
# Paths
# -----------------------------

def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data")).resolve()


def storage_root() -> Path:
    root = data_dir() / "storage"
    root.mkdir(parents=True, exist_ok=True)
    return root


def assets_root() -> Path:
    root = data_dir() / "assets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def branding_dir() -> Path:
    d = assets_root() / "branding"
    d.mkdir(parents=True, exist_ok=True)
    return d


def event_assets_dir(slug: str) -> Path:
    d = assets_root() / "events" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def event_storage_dir(slug: str) -> Path:
    d = storage_root() / slug
    (d / "orig").mkdir(parents=True, exist_ok=True)
    (d / "webp").mkdir(parents=True, exist_ok=True)
    (d / "thumb").mkdir(parents=True, exist_ok=True)
    return d


# -----------------------------
# Files / processing
# -----------------------------

_filename_re = re.compile(r"[^a-zA-Z0-9._-]+")


def sanitize_filename(name: str) -> str:
    # Entfernt problematische Zeichen, hält aber Dateiendungen
    name = name.strip().replace(" ", "_")
    name = _filename_re.sub("_", name)
    return name[:180] if len(name) > 180 else name


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save_upload_to_event(slug: str, filename: str, file_bytes: bytes) -> Path:
    d = event_storage_dir(slug) / "orig"
    safe = sanitize_filename(filename)
    out = d / safe
    # Falls Datei schon existiert: suffix anhängen
    if out.exists():
        stem = out.stem
        suf = out.suffix
        i = 2
        while True:
            cand = d / f"{stem}_{i}{suf}"
            if not cand.exists():
                out = cand
                break
            i += 1
    out.write_bytes(file_bytes)
    return out


def save_upload_stream_to_event(slug: str, filename: str, file_storage) -> Path:
    """Speichert eine Upload-Datei (Werkzeug FileStorage) direkt auf Disk.

    Vorteil: Keine großen Videos in RAM laden.
    """
    d = event_storage_dir(slug) / "orig"
    safe = sanitize_filename(filename)
    out = d / safe
    # Falls Datei schon existiert: suffix anhängen
    if out.exists():
        stem = out.stem
        suf = out.suffix
        i = 2
        while True:
            cand = d / f"{stem}_{i}{suf}"
            if not cand.exists():
                out = cand
                break
            i += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    file_storage.save(out)
    return out


def process_image_to_webp_and_thumb(
    *,
    original_path: Path,
    webp_path: Path,
    thumb_path: Path,
    max_full_px: int = 2400,
    thumb_px: int = 600,
) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Erzeugt:
      - optimiertes WebP (für Anzeige)
      - Thumbnail WebP (für Grid)

    Gibt die Pfade zurück (oder None bei Fehler).
    """
    try:
        with Image.open(original_path) as img:
            img = ImageOps.exif_transpose(img)
            # GIF: erstes Frame verwenden
            try:
                img.seek(0)
            except Exception:
                pass

            # Vollbild WebP
            img_full = img.copy()
            img_full.thumbnail((max_full_px, max_full_px), Image.Resampling.LANCZOS)
            if img_full.mode in ("RGBA", "P"):
                img_full = img_full.convert("RGB")
            webp_path.parent.mkdir(parents=True, exist_ok=True)
            img_full.save(webp_path, "WEBP", quality=86, method=6)

            # Thumb WebP
            img_thumb = img.copy()
            img_thumb.thumbnail((thumb_px, thumb_px), Image.Resampling.LANCZOS)
            if img_thumb.mode in ("RGBA", "P"):
                img_thumb = img_thumb.convert("RGB")
            thumb_path.parent.mkdir(parents=True, exist_ok=True)
            img_thumb.save(thumb_path, "WEBP", quality=80, method=6)

            return webp_path, thumb_path
    except Exception:
        return None, None


# ── Download-Größen ──────────────────────────────────────────────
# Gäste laden meist auf dem Handy und wollen ein Bild für WhatsApp,
# kein 8-MB-Original. Wir erzeugen skalierte JPEGs und legen sie im
# Cache ab — JPEG bewusst statt WebP, weil es überall (iOS-Fotos,
# Windows, Mail) ohne Nachfragen landet.

DOWNLOAD_SIZES = {
    "small": 640,    # WhatsApp, schnell verschickt
    "web":   1024,   # Messenger, Social
    "large": 2048,   # Ansehen am Rechner, kleiner Druck
    # "original" wird nicht skaliert
}
DEFAULT_DOWNLOAD_SIZE = "large"

# Reihenfolge im Auswahl-Blatt (klein zuerst — mobil zuerst) plus das Original.
DOWNLOAD_SIZE_ORDER = ["small", "web", "large"]

# Pro Event abschaltbar. Das Original hat seinen eigenen, älteren Schalter
# (``download_original``) und bleibt dort maßgeblich.
DOWNLOAD_SIZE_SETTINGS = {
    "small": "download_640",
    "web":   "download_1024",
    "large": "download_2048",
}

# Grobe Schätzung für ein progressives JPEG bei Qualität 88. Nur für die
# Anzeige („ca. 0,4 MB") — nichts hängt an der Zahl.
JPEG_BYTES_PER_PIXEL = 0.18


def download_cache_dir() -> Path:
    p = data_dir() / "cache" / "downloads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def normalize_download_size(size: Optional[str]) -> str:
    size = (size or "").strip().lower()
    if size in DOWNLOAD_SIZES or size == "original":
        return size
    return DEFAULT_DOWNLOAD_SIZE


def build_download_variant(media_id: int, source_rel: str, size: str) -> Optional[Path]:
    """Liefert den Pfad zu einer skalierten Fassung (mit Cache).

    Gibt None zurück, wenn die Quelle fehlt oder nicht als Bild lesbar ist —
    der Aufrufer liefert dann das Original aus.
    """
    size = normalize_download_size(size)
    if size == "original":
        return None

    src = (data_dir() / source_rel).resolve()
    if not str(src).startswith(str(data_dir())) or not src.exists():
        return None

    target = download_cache_dir() / f"{int(media_id)}_{size}.jpg"
    # Cache nur nutzen, wenn er jünger ist als die Quelle
    try:
        if target.exists() and target.stat().st_mtime >= src.stat().st_mtime:
            return target
    except OSError:
        pass

    max_px = DOWNLOAD_SIZES[size]
    try:
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            try:
                img.seek(0)          # GIF: erstes Frame
            except Exception:
                pass
            img = img.copy()
            img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(target, "JPEG", quality=88, optimize=True, progressive=True)
        return target
    except Exception:
        return None


def scaled_size(width: int, height: int, max_px: int) -> Tuple[int, int]:
    """Kantenlängen nach dem Verkleinern in eine ``max_px``-Box (nie vergrößert)."""
    w, h = max(1, int(width or 0)), max(1, int(height or 0))
    longest = max(w, h)
    if longest <= max_px:
        return w, h
    f = max_px / longest
    return max(1, round(w * f)), max(1, round(h * f))


def estimate_download_bytes(width: int, height: int, max_px: int) -> int:
    """Geschätzte Dateigröße der skalierten Fassung in Bytes."""
    w, h = scaled_size(width, height, max_px)
    return int(w * h * JPEG_BYTES_PER_PIXEL)


def allowed_download_sizes(cfg: Optional[dict] = None) -> list:
    """Erlaubte Größen-Kennungen für dieses Event, klein zuerst, Original zuletzt."""
    cfg = cfg or {}
    out = [s for s in DOWNLOAD_SIZE_ORDER
           if cfg.get(DOWNLOAD_SIZE_SETTINGS[s], True)]
    if cfg.get("download_original", True):
        out.append("original")
    return out or ["original"]


def download_options(cfg: Optional[dict] = None, rows: Optional[list] = None) -> list:
    """Auswahl-Blatt für den Gast: Kennung, Kantenlänge und geschätzte Größe.

    Die Schätzung kommt aus den Maßen, die schon in der Datenbank stehen —
    kein Bild wird dafür geöffnet. Fehlen die Maße, bleibt sie leer.
    """
    rows = rows or []
    widths = [int(r.get("width") or 0) for r in rows if r.get("width")]
    heights = [int(r.get("height") or 0) for r in rows if r.get("height")]
    sizes = sorted(int(r.get("size_bytes") or 0) for r in rows if r.get("size_bytes"))
    median_w = sorted(widths)[len(widths) // 2] if widths else 0
    median_h = sorted(heights)[len(heights) // 2] if heights else 0
    median_bytes = sizes[len(sizes) // 2] if sizes else 0

    options = []
    for sid in allowed_download_sizes(cfg):
        px = DOWNLOAD_SIZES.get(sid)
        if px and median_w and median_h:
            est = estimate_download_bytes(median_w, median_h, px)
            if median_bytes:
                est = min(est, median_bytes)     # eine Verkleinerung wird nie größer
        elif sid == "original":
            est = median_bytes
        else:
            est = 0
        options.append({
            "id": sid,
            "px": px or 0,
            "bytes": est,
            "size_h": human_bytes(est) if est else "",
        })
    return options


def clear_download_cache(media_id: int) -> None:
    """Cache-Dateien eines Fotos entfernen (beim Löschen des Mediums)."""
    try:
        for size in DOWNLOAD_SIZES:
            f = download_cache_dir() / f"{int(media_id)}_{size}.jpg"
            if f.exists():
                f.unlink()
    except Exception:
        return


def save_event_cover(slug: str, file_storage) -> str:
    """Speichert/optimiert ein Coverbild pro Event.

    Rückgabe: relativer Pfad (ab DATA_DIR) z.B. 'assets/events/<slug>/cover.jpg'
    """
    d = event_assets_dir(slug)
    out = d / "cover.jpg"

    with Image.open(file_storage.stream) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        # sane max size
        img.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "JPEG", quality=86, optimize=True, progressive=True)

    return out.relative_to(data_dir()).as_posix()


def save_og_image(slug: str, file_storage) -> str:
    """Store the admin's link-preview image (app/link_preview.py), cut to 1200x630.

    A new name per upload: chat apps cache an image by its address, and the
    preview's ``?v=`` key follows the file's mtime and size anyway.
    """
    import secrets as _secrets
    d = event_assets_dir(slug)
    for old in d.glob("og-*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass
    out = d / f"og-{_secrets.token_hex(4)}.jpg"
    with Image.open(file_storage.stream) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img = ImageOps.fit(img, (1200, 630), Image.Resampling.LANCZOS)
        img.save(out, "JPEG", quality=84, optimize=True, progressive=True)
    return out.relative_to(data_dir()).as_posix()


LOGO_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
LOGO_MAX_BYTES = 2 * 1024 * 1024


class LogoRefused(ValueError):
    """A logo upload that is not taken, with a sentence for the operator."""


def save_brand_logo(file_storage, variant: str = "light") -> str:
    """Store the installation's logo (``variant`` "dark": the one for dark surfaces).

    Only PNG, JPEG and WebP, at most 2 MB, checked by content, not by name.
    SVG is refused: an SVG is a document that can carry scripts, and this
    file is served on the gallery's own origin. What is stored is always a
    PNG made by Pillow (no foreign bytes are ever served); transparent
    padding around the mark is trimmed (alpha bounding box plus a small
    margin), so a wide logo on a square canvas is not shown tiny. The upload
    as it came is kept next to it (``…-original.<ext>``), never served.

    Returns the path relative to DATA_DIR, e.g. 'assets/branding/logo.png'.
    """
    raw = file_storage.stream.read(LOGO_MAX_BYTES + 1)
    if len(raw) > LOGO_MAX_BYTES:
        raise LogoRefused("Das Logo ist größer als 2 MB. Bitte eine kleinere Datei nehmen.")
    head = raw[:512].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        raise LogoRefused("SVG wird nicht angenommen. Bitte das Logo als PNG (am besten mit transparentem "
                          "Hintergrund), JPEG oder WebP hochladen.")
    try:
        img = Image.open(io.BytesIO(raw))
        fmt = img.format
        img.load()
    except Exception:
        raise LogoRefused("Diese Datei ist kein Bild, das die Galerie lesen kann. Bitte PNG, JPEG oder WebP.")
    if fmt not in LOGO_FORMATS:
        raise LogoRefused(f"{fmt or 'Dieses Format'} wird nicht angenommen. Bitte PNG, JPEG oder WebP.")
    stem = "logo-dark" if variant == "dark" else "logo"
    d = branding_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}-original{LOGO_FORMATS[fmt]}").write_bytes(raw)
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGBA", "LA"):
        img = img.convert("RGBA")
    img = trim_transparent(img.convert("RGBA"))
    img.thumbnail((800, 800), Image.Resampling.LANCZOS)
    out = d / f"{stem}.png"
    img.save(out, "PNG", optimize=True)
    return out.relative_to(data_dir()).as_posix()


def trim_transparent(img: "Image.Image", margin: float = 0.04) -> "Image.Image":
    """Crop to what is not transparent, keeping a margin of ``margin`` × the larger side."""
    box = img.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if not box:
        return img
    pad = int(round(max(box[2] - box[0], box[3] - box[1]) * margin))
    left, top = max(0, box[0] - pad), max(0, box[1] - pad)
    right, bottom = min(img.width, box[2] + pad), min(img.height, box[3] + pad)
    if (left, top, right, bottom) == (0, 0, img.width, img.height):
        return img
    return img.crop((left, top, right, bottom))


def save_event_logo(slug: str, file_storage) -> str:
    """Logo für ein einzelnes Event (überschreibt das globale Logo in dieser Galerie).

    Rückgabe: relativer Pfad ab DATA_DIR, z.B. 'assets/events/<slug>/logo.png'
    """
    out = event_assets_dir(slug) / "logo.png"
    with Image.open(file_storage.stream) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGBA", "LA"):
            img = img.convert("RGBA")     # Transparenz erhalten
        img.thumbnail((800, 800), Image.Resampling.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG", optimize=True)
    return out.relative_to(data_dir()).as_posix()


def _save_banner(out: Path, file_storage) -> str:
    """Bannerbild speichern — breit, beschnitten auf ein ruhiges Format."""
    with Image.open(file_storage.stream) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((2400, 1200), Image.Resampling.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "JPEG", quality=85, optimize=True, progressive=True)
    return out.relative_to(data_dir()).as_posix()


def save_event_banner(slug: str, file_storage) -> str:
    """Banner für ein einzelnes Event, z. B. 'assets/events/<slug>/banner.jpg'."""
    return _save_banner(event_assets_dir(slug) / "banner.jpg", file_storage)


def save_brand_banner(file_storage) -> str:
    """Banner der Installation — Rückfall für Events ohne eigenes Banner."""
    return _save_banner(branding_dir() / "banner.jpg", file_storage)


def delete_rel_path(rel: str) -> None:
    """Delete a file relative to DATA_DIR (best-effort)."""
    try:
        full = (data_dir() / rel).resolve()
        # safety: ensure under data_dir
        if not str(full).startswith(str(data_dir())):
            return
        if full.exists() and full.is_file():
            full.unlink()
    except Exception:
        return


def human_bytes(num: int) -> str:
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < step:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= step
    return f"{num:.2f} PB"


# ─────────────────────────────────────────────────────────────
# Upload helpers
# ─────────────────────────────────────────────────────────────

def save_upload(event_id: int, file_storage, *, max_bytes: Optional[int] = None,
                allow_video: bool = True, strip_gps: bool = False,
                source: Optional[str] = None) -> dict:
    """Save an uploaded file, create thumbnail, and return metadata.

    The file is first written to a ``.part`` name and judged by its content
    (``app/uploads.py``); only a picture Pillow can open or a video container
    the booth produces is kept, under a name the server chooses. Anything
    else raises ``UploadRejected`` and leaves nothing behind.

    Returns: dict with 'id', 'path_thumb', 'path_full', 'path_orig'
    """
    from app import db
    from app import uploads

    event = db.get_event_by_id(event_id)
    if not event:
        raise ValueError("Event not found")

    orig_dir = event_storage_dir(event["slug"]) / "orig"
    tmp = orig_dir / f".{uploads.server_name('')}.part"
    file_storage.save(tmp)
    try:
        if max_bytes is not None and tmp.stat().st_size > max_bytes:
            raise uploads.UploadRejected("file too large", 413)
        art, ext = uploads.sniff(tmp)
        if art == "video" and not allow_video:
            raise uploads.UploadRejected("unsupported file type")
        if art == "image" and strip_gps:
            uploads.strip_gps(tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    orig_path = orig_dir / uploads.server_name(ext)
    tmp.replace(orig_path)

    webp_path = event_storage_dir(event["slug"]) / "webp" / (orig_path.stem + ".webp")
    thumb_path = event_storage_dir(event["slug"]) / "thumb" / (orig_path.stem + ".webp")

    full = thumb = None
    if art == "image":
        full, thumb = process_image_to_webp_and_thumb(
            original_path=orig_path,
            webp_path=webp_path,
            thumb_path=thumb_path,
        )

    # Compute file size and hash
    import hashlib as _hashlib
    import secrets as _secrets
    file_bytes = orig_path.read_bytes()
    size_bytes = len(file_bytes)
    sha256 = _hashlib.sha256(file_bytes).hexdigest()
    share_token = _secrets.token_urlsafe(16)

    # Register in DB
    media_id = db.add_media(
        event_id=event_id,
        kind="video" if art == "video" else "photo",
        filename=orig_path.name,
        path_original=orig_path.relative_to(data_dir()).as_posix(),
        path_webp=full.relative_to(data_dir()).as_posix() if full else None,
        path_thumb=thumb.relative_to(data_dir()).as_posix() if thumb else None,
        size_bytes=size_bytes,
        sha256=sha256,
        share_token=share_token,
    )
    if source:
        # Where it came from (app/media_source.py). Set after the insert so
        # db.add_media keeps its signature for every other caller.
        from app import ecosystem
        ecosystem.init_schema()
        con = db.get_db()
        try:
            con.execute("UPDATE media SET source=? WHERE id=?", (str(source)[:40], int(media_id)))
            con.commit()
        finally:
            con.close()
    if art == "image":
        # Numbers for "Aussortieren" (app/sortout.py). Never fails an upload.
        try:
            from app import ecosystem, sortout
            ecosystem.init_schema()
            sortout.store(int(media_id))
        except Exception:                   # noqa: BLE001
            pass

    return {
        "id": media_id,
        "path_original": orig_path.relative_to(data_dir()).as_posix(),
        "path_webp": full.relative_to(data_dir()).as_posix() if full else None,
        "path_thumb": thumb.relative_to(data_dir()).as_posix() if thumb else None,
    }


def save_cover(event_id: int, file_storage) -> str:
    """Save event cover image.

    Returns: relative path (from DATA_DIR) e.g. 'assets/events/<slug>/cover.jpg'
    """
    from app import db

    event = db.get_event_by_id(event_id)
    if not event:
        raise ValueError("Event not found")

    return save_event_cover(event["slug"], file_storage)


def save_logo(file_storage, variant: str = "light") -> str:
    """Save global branding logo (see save_brand_logo).

    Returns: relative path (from DATA_DIR) e.g. 'assets/branding/logo.png'
    """
    return save_brand_logo(file_storage, variant)


def save_hero_image(file_storage) -> str:
    """Save homepage hero image.

    Returns: relative path (from DATA_DIR) e.g. 'assets/branding/hero.jpg'
    """
    d = branding_dir()
    out = d / "hero.jpg"

    with Image.open(file_storage.stream) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        # sane max size
        img.thumbnail((2400, 1600), Image.Resampling.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "JPEG", quality=86, optimize=True, progressive=True)

    return out.relative_to(data_dir()).as_posix()


def generate_qr_png(url: str) -> bytes:
    """Generate QR code as PNG bytes."""
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    out = BytesIO()
    qr_img.save(out, "PNG")
    return out.getvalue()


# ─────────────────────────────────────────────────────────────
# Sharepic generator
# ─────────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = False, display: bool = False):
    """Schrift der Marke. Vorher stand hier DejaVu — deshalb sahen gedruckte
    Karten und Sharepics nach Standardinstallation aus statt nach dem Produkt."""
    from app import brand

    return brand.schrift(size, bold=bold, display=display)


def _make_gradient(w: int, h: int) -> "Image.Image":
    """Dunkler Grün-zu-Schwarz Farbverlauf als Fallback-Hintergrund."""
    img = Image.new("RGBA", (w, h))
    draw = ImageDraw.Draw(img)
    top_color = (15, 40, 20)
    bot_color = (8, 8, 10)
    for y in range(h):
        t = y / h
        r = int(top_color[0] + (bot_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (bot_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (bot_color[2] - top_color[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
    return img


def _wrap_text(draw: "ImageDraw.ImageDraw", text: str, font, max_width: int) -> list:
    """Bricht Text auf mehrere Zeilen um, passend für max_width."""
    words = text.split()
    lines = []
    cur = ""
    for word in words:
        cand = (cur + " " + word).strip()
        if draw.textlength(cand, font=font) <= max_width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def generate_sharepic_png(
    *,
    title: str,
    qr_url: str,
    cover_full_path: Optional[Path] = None,
    logo_full_path: Optional[Path] = None,
    bg_color: str = "#111111",
) -> bytes:
    """Generiert ein Story-Sharepic (1080x1920) mit QR Code.

    Layout:
    - Hero (oben):  Event-Titel auf verblürtem Cover / Farbverlauf
    - Card (mitte): QR Code (420px) + URL + Tipp
    - Footer (unten): Logo oder App-Branding
    """
    import qrcode

    W, H = 1080, 1920

    # ─── Hintergrund ────────────────────────────────────────────────────────
    if cover_full_path and cover_full_path.exists():
        try:
            with Image.open(cover_full_path) as bg:
                bg = ImageOps.exif_transpose(bg).convert("RGB")
                bg = ImageOps.fit(bg, (W, H), method=Image.Resampling.LANCZOS)
                bg = bg.filter(ImageFilter.GaussianBlur(radius=24))
                base = bg.convert("RGBA")
        except Exception:
            base = _make_gradient(W, H)
    else:
        base = _make_gradient(W, H)

    # Gradient-Overlay: stark oben (Hero-Lesbarkeit), leicht in Mitte, stark unten (Footer)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    for y in range(H):
        if y < 700:
            alpha = int(190 - 130 * (y / 700))
        elif y > H - 320:
            alpha = int(170 * (y - (H - 320)) / 320)
        else:
            alpha = 60
        ov_draw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    base.alpha_composite(overlay)

    draw = ImageDraw.Draw(base)

    # ─── Fonts ──────────────────────────────────────────────────────────────
    font_eyebrow  = _load_font(30, bold=False)
    font_title    = _load_font(74, bold=True, display=True)
    font_subtitle = _load_font(36, bold=False)
    font_url      = _load_font(30, bold=True)
    font_tip      = _load_font(25, bold=False)

    # ─── Hero Section ────────────────────────────────────────────────────────
    eyebrow = "✦   Deine Fotos sind online   ✦"
    ew = draw.textlength(eyebrow, font=font_eyebrow)
    draw.text(((W - ew) // 2, 210), eyebrow, font=font_eyebrow,
              fill=(220, 220, 220, 210))

    title_display = f"„{title}“"
    title_lines = _wrap_text(draw, title_display, font_title, W - 100)
    ty = 290
    for line in title_lines[:3]:
        lw = draw.textlength(line, font=font_title)
        draw.text(((W - lw) // 2, ty), line, font=font_title,
                  fill=(255, 255, 255, 255))
        ty += 88

    # ─── QR Code aufbauen ───────────────────────────────────────────────────
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_size = 420
    qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST)

    # QR-Frame: weißes Quadrat mit abgerundeten Ecken als Rahmen
    qr_pad = 14
    qr_frame_size = qr_size + qr_pad * 2
    qr_frame = Image.new("RGBA", (qr_frame_size, qr_frame_size), (255, 255, 255, 255))
    qfm = Image.new("L", (qr_frame_size, qr_frame_size), 0)
    ImageDraw.Draw(qfm).rounded_rectangle(
        (0, 0, qr_frame_size, qr_frame_size), radius=18, fill=255
    )
    qr_frame.putalpha(qfm)
    qr_frame.paste(qr_img.convert("RGBA"), (qr_pad, qr_pad))

    # ─── Card-Höhe dynamisch berechnen ──────────────────────────────────────
    short_url = qr_url.replace("https://", "").replace("http://", "")
    if len(short_url) > 52:
        short_url = short_url[:49] + "…"

    tip = "Tipp: Link im Browser öffnen → Fotos teilen & weiterleiten"
    tip_lines = _wrap_text(draw, tip, font_tip, W - 100 - 120)[:2]

    pad_top   = 70
    sub_h     = 48
    gap1      = 28
    url_h     = 38
    gap2      = 20
    div_gap   = 36
    tip_h     = len(tip_lines) * 36
    pad_bot   = 52

    card_x = 50
    card_y = 700
    card_w = W - 100
    card_h = pad_top + sub_h + gap1 + qr_frame_size + gap2 + url_h + div_gap + tip_h + pad_bot

    # ─── Weißes Card ────────────────────────────────────────────────────────
    card = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 248))
    cmask = Image.new("L", (card_w, card_h), 0)
    ImageDraw.Draw(cmask).rounded_rectangle(
        (0, 0, card_w, card_h), radius=50, fill=255
    )
    card.putalpha(cmask)
    base.alpha_composite(card, (card_x, card_y))

    draw = ImageDraw.Draw(base)

    # Subtitle
    sub = "Jetzt alle Fotos ansehen:"
    sw = draw.textlength(sub, font=font_subtitle)
    draw.text(((W - sw) // 2, card_y + pad_top), sub, font=font_subtitle,
              fill=(50, 50, 50, 255))

    # QR Code platzieren
    qr_x = (W - qr_frame_size) // 2
    qr_y = card_y + pad_top + sub_h + gap1
    base.alpha_composite(qr_frame, (qr_x, qr_y))

    draw = ImageDraw.Draw(base)

    # URL unter QR
    url_y = qr_y + qr_frame_size + gap2
    uw = draw.textlength(short_url, font=font_url)
    draw.text(((W - uw) // 2, url_y), short_url, font=font_url,
              fill=(34, 34, 34, 255))

    # Trennlinie
    div_y = url_y + url_h + 10
    draw.line(
        [(card_x + 80, div_y), (card_x + card_w - 80, div_y)],
        fill=(210, 210, 210, 255), width=1
    )

    # Tipp-Text
    ty2 = div_y + 16
    for line in tip_lines:
        lw = draw.textlength(line, font=font_tip)
        draw.text(((W - lw) // 2, ty2), line, font=font_tip,
                  fill=(105, 105, 105, 255))
        ty2 += 36

    # ─── Footer ─────────────────────────────────────────────────────────────
    footer_y = min(card_y + card_h + 70, H - 120)

    if logo_full_path and logo_full_path.exists():
        try:
            with Image.open(logo_full_path) as lg:
                lg = ImageOps.exif_transpose(lg)
                if lg.mode not in ("RGBA", "LA"):
                    lg = lg.convert("RGBA")
                lg.thumbnail((200, 120), Image.Resampling.LANCZOS)
                lx = (W - lg.width) // 2
                base.alpha_composite(lg, (lx, footer_y))
                draw = ImageDraw.Draw(base)
        except Exception:
            pass
    else:
        font_brand = _load_font(32, bold=True)
        brand = "Photobox Gallery"
        bw = draw.textlength(brand, font=font_brand)
        draw.text(((W - bw) // 2, footer_y), brand, font=font_brand,
                  fill=(255, 255, 255, 170))

    # ─── Output ─────────────────────────────────────────────────────────────
    out = BytesIO()
    base.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()
