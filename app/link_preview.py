"""Link previews (Open Graph) for galleries: what WhatsApp, iMessage, Signal,
Telegram, Facebook and Slack show when somebody pastes a gallery link.

Every gallery page names a 1200x630 JPEG under ``/gallery/<slug>/og.jpg``.
Which picture that is depends on what an *unauthenticated* fetcher may see -
a link-preview bot never has the password, the admin cookie or the client
code, and whatever it fetches lands in a chat that the operator does not
control. So the decision never looks at the session:

``full``     public, published, no password: the admin's own preview image,
             else the cover, else up to three visible photos, with the title.
``custom``   the admin uploaded a preview image. Used for a public gallery,
             and for a closed one only when "auch ohne Passwort zeigen" is
             ticked (``og_image_public``).
``brand``    password, link password, expired, watermark, or no picture
             yet: title and brand colour only, no guest photo, no
             description (it may name people).
``neutral``  silent mode, a draft, a gallery not live yet, or one that does
             not exist: "Fotogalerie" and nothing else - the same bytes and
             headers in every case, so the image is no way to learn whether
             such a gallery exists or what it is called.

The rules are listed in SECURITY.md ("Link previews").
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

W, H = 1200, 630
#: WhatsApp drops previews it cannot fetch quickly; 300 kB is a safe ceiling.
MAX_BYTES = 300_000
#: Bump when the drawing changes, so cached cards are made again.
VERSION = 3

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


# ── What may be shown ─────────────────────────────────────────

def _gs(event):
    from app import gallery_settings as gsm
    return gsm.for_event(event)


def _password_protected(event: Dict[str, Any]) -> bool:
    if event.get("password_hash"):
        return True
    from app import folders
    parent = folders.parent_of(event)
    return bool(parent and parent.get("password_hash"))


def _silent(event: Dict[str, Any]) -> bool:
    from app import silence
    return silence.is_silent(event)


def custom_image(event: Optional[Dict[str, Any]]) -> Optional[Path]:
    """The admin's uploaded preview image on disk, or None."""
    from app import media
    rel = ((event or {}).get("og_image_path") or "").strip()
    if not rel:
        return None
    p = media.data_dir() / rel
    return p if p.is_file() else None


def restricted(event: Dict[str, Any]) -> bool:
    """True when a stranger with the bare link would not see the photos."""
    from app import gallery_settings as gsm, schedule
    gs = _gs(event)
    if not event.get("published") or _password_protected(event):
        return True
    if gsm.is_expired(gs) or schedule.coming_soon(event):
        return True
    from app import folders
    parent = folders.parent_of(event)
    if parent and not parent.get("published"):
        return True
    return False


def visible_photos(event: Dict[str, Any], limit: int = 3) -> List[Dict[str, Any]]:
    """Pictures a guest sees in the gallery, newest first, still images only.

    ``db.list_media`` already leaves out hidden and pending ones (and a
    trashed picture is no longer in the table). Red-marked pictures are left
    out when the gallery hides them from guests, and an original that is the
    twin of a framed picture is skipped like in the grid.
    """
    from app import db
    gs = _gs(event)
    out = []
    for m in db.list_media(int(event["id"])) or []:
        if str(m.get("review_state") or "") == "pending" or int(m.get("hidden") or 0):
            continue
        if gs["hide_red_in_presentation"] and m.get("color_tag") == "red":
            continue
        if (m.get("kind") or "photo") in ("original", "video", "boomerang", "gif"):
            continue
        rel = m.get("path_webp") or m.get("path_thumb") or ""
        if not rel.lower().endswith(_IMAGE_EXT):
            continue
        out.append(m)
        if len(out) >= limit:
            break
    return out


def _cover(event: Dict[str, Any]) -> Optional[Path]:
    """The cover, if a guest may see it.

    A cover is either an uploaded file or one of the gallery's own pictures
    (``folders.admin_set_cover`` stores that picture's web path). A picture
    that was hidden, marked red or deleted after it became the cover must not
    reach a chat through the preview, so a cover that is a media path counts
    only while that picture is visible.
    """
    from app import db, media
    rel = (event.get("cover_path") or event.get("cover_original_path") or "").strip()
    if not rel:
        return None
    con = db.get_db()
    try:
        row = con.execute("SELECT id, event_id FROM media WHERE path_webp=? OR path_thumb=? OR path_original=? "
                          "LIMIT 1", (rel, rel, rel)).fetchone()
    finally:
        con.close()
    if row:
        ok = {int(m["id"]) for m in visible_photos(db.get_event_by_id(int(row["event_id"])) or event, 10_000)}
        if int(row["id"]) not in ok:
            return None
    p = media.data_dir() / rel
    return p if p.is_file() else None


def unknown_to_strangers(event: Dict[str, Any]) -> bool:
    """A gallery whose page a stranger cannot reach: a draft, a folder under a
    draft, or one whose start (``scheduled_live_at``) lies ahead. Its preview
    must be the unknown name's, byte for byte - the title on a brand card
    confirmed that a guessed name exists (18.11.2026)."""
    from app import folders, schedule
    if not event.get("published") or schedule.coming_soon(event):
        return True
    parent = folders.parent_of(event)
    return bool(parent and (not parent.get("published") or schedule.coming_soon(parent)))


def decide(event: Optional[Dict[str, Any]]) -> Tuple[str, List[Path]]:
    """(mode, source files) for this event; see the module docstring."""
    if not event or _silent(event) or unknown_to_strangers(event):
        return "neutral", []
    custom = custom_image(event)
    if restricted(event):
        if custom and int(event.get("og_image_public") or 0):
            return "custom", [custom]
        return "brand", []
    if custom:
        return "custom", [custom]
    if _gs(event)["watermark"]:
        return "brand", []
    cover = _cover(event)
    if cover:
        return "full", [cover]
    from app import media
    files = [media.data_dir() / (m.get("path_webp") or m.get("path_thumb")) for m in visible_photos(event)]
    files = [f for f in files if f.is_file()]
    return ("full", files) if files else ("brand", [])


# ── Text ──────────────────────────────────────────────────────

def _title(event: Optional[Dict[str, Any]], mode: str) -> str:
    from app.i18n import t
    if mode == "neutral" or not event:
        return t("og.neutral_title")
    return (_gs(event)["opener_title"] or event.get("name") or "").strip()[:90] or t("og.neutral_title")


def _description(event: Optional[Dict[str, Any]], mode: str) -> str:
    from app.i18n import t
    if mode == "neutral" or not event:
        return t("og.neutral_desc")
    if mode == "brand" or restricted(event):
        return t("og.protected_desc") if _password_protected(event) else t("og.closed_desc")
    text = " ".join(str(event.get("description") or "").split())
    if text:
        return text[:197] + "…" if len(text) > 200 else text
    n = guest_count(event)
    return t("og.count_desc", n=n) if n != 1 else t("og.count_desc_one")


def _tile_count(event: Dict[str, Any]) -> int:
    """Tiles a stranger sees in this gallery: the filter of ``public.gallery``
    for a visitor without a session (no manage, no ``?show=originals``)."""
    from app import db, ecosystem
    gs = _gs(event)
    rows = db.list_media(int(event["id"])) or []
    _partners, absorbed = ecosystem.unframed_partners(rows)
    rows = [m for m in rows if m["id"] not in absorbed]
    originals = sum(1 for m in rows if (m.get("kind") or "photo") == "original")
    proofing = gs["mode"] == "proofing"
    n = 0
    for m in rows:
        if (m.get("kind") or "photo") == "original" and originals and len(rows) > originals:
            continue
        if not proofing and gs["hide_red_in_presentation"] and m.get("color_tag") == "red":
            continue
        n += 1
    return n


def guest_count(event: Dict[str, Any]) -> int:
    """The count next to the photos on the gallery page for a stranger:
    its tiles plus the pictures of folders a stranger may open. The preview
    used to count every row of ``db.list_media`` (981 against 568 shown)."""
    from app import db, folders
    n = _tile_count(event)
    for child in folders.children_of(int(event["id"])):
        if not child.get("published") or _silent(child) or child.get("password_hash"):
            continue
        n += len(db.list_media(int(child["id"])) or [])
    return n


# ── Picture ───────────────────────────────────────────────────

def _colours(event: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    from app import branding
    bg, fg = "#1c1b19", "#ffffff"
    if event:
        try:
            b = branding.event_branding(event)
            if b.get("bg"):
                bg = b["bg"]
                fg = b.get("text") or branding.on_color(bg)
        except Exception:               # noqa: BLE001 - a broken design draws the house colours
            pass
    return bg, fg


def _hex(c: str) -> Tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _fit(path: Path, size: Tuple[int, int]) -> Image.Image:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        return ImageOps.fit(im, size, Image.Resampling.LANCZOS)


def _wrap(draw, text: str, font, width: int, lines: int = 2) -> List[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            out.append(cur)
            cur = w
            if len(out) == lines:
                break
    if cur and len(out) < lines:
        out.append(cur)
    if len(out) == lines and " ".join(out) != " ".join(words):
        last = out[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1]
        out[-1] = last.rstrip() + "…"
    return out


def _font(size: int, bold: bool = True):
    from app import brand
    return brand.schrift(size, bold=bold)


def _title_block(img: Image.Image, title: str, kicker: str, fg=(255, 255, 255), shade: bool = True) -> None:
    if shade:
        # A soft dark band under the text: a white title must stay readable on
        # a bright photo of a white wedding dress.
        grad = Image.new("L", (1, 280))
        for y in range(280):
            grad.putpixel((0, y), int(200 * (y / 279) ** 1.6))
        band = Image.new("RGB", (W, 280), (0, 0, 0))
        img.paste(band, (0, H - 280), grad.resize((W, 280)))
    draw = ImageDraw.Draw(img)
    f_title, f_kick = _font(64, True), _font(28, False)
    lines = _wrap(draw, title, f_title, W - 128)
    y = H - 64 - len(lines) * 76
    if kicker:
        draw.text((64, y - 46), kicker, font=f_kick, fill=fg)
    for line in lines:
        draw.text((64, y), line, font=f_title, fill=fg)
        y += 76


def render(event: Optional[Dict[str, Any]], mode: str, files: List[Path]) -> bytes:
    from app.i18n import t
    title = _title(event, mode)
    bg, fg = _colours(event if mode != "neutral" else None)
    if mode == "custom":
        img = _fit(files[0], (W, H))
    elif mode == "full" and files:
        img = Image.new("RGB", (W, H), _hex(bg))
        if len(files) == 1:
            img.paste(_fit(files[0], (W, H)), (0, 0))
        elif len(files) == 2:
            img.paste(_fit(files[0], (W // 2 - 2, H)), (0, 0))
            img.paste(_fit(files[1], (W // 2 - 2, H)), (W // 2 + 2, 0))
        else:
            img.paste(_fit(files[0], (716, H)), (0, 0))
            img.paste(_fit(files[1], (480, H // 2 - 2)), (720, 0))
            img.paste(_fit(files[2], (480, H // 2 - 2)), (720, H // 2 + 2))
        _title_block(img, title, t("og.kicker"))
    else:
        img = Image.new("RGB", (W, H), _hex(bg))
        draw = ImageDraw.Draw(img)
        fgc = _hex(fg)
        # The frame mark, outlined: the same shape as the favicon.
        draw.rounded_rectangle((64, 64, 64 + 54, 64 + 70), radius=6, outline=fgc, width=6)
        draw.rounded_rectangle((78, 78, 104, 104), radius=2, outline=fgc, width=4)
        if mode == "neutral":
            draw.text((64, H - 64 - 76), t("og.neutral_title"), font=_font(64, True), fill=fgc)
        else:
            kicker = t("og.kicker_protected") if (event and _password_protected(event)) else t("og.kicker")
            _title_block(img, title, kicker, fg=fgc, shade=False)
    return _encode(img)


def _encode(img: Image.Image) -> bytes:
    q = 84
    while True:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        data = buf.getvalue()
        if len(data) <= MAX_BYTES or q <= 44:
            return data
        q -= 8


# ── Cache ─────────────────────────────────────────────────────

def _cache_dir() -> Path:
    from app import media
    d = media.data_dir() / "cache" / "og"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(event: Optional[Dict[str, Any]], mode: str, files: List[Path]) -> str:
    parts: list = [VERSION, mode, _title(event, mode), _colours(event if mode != "neutral" else None)]
    if mode == "brand" and event:
        parts.append(_password_protected(event))
    for f in files:
        try:
            st = f.stat()
            parts.append([f.name, int(st.st_mtime), st.st_size])
        except OSError:
            parts.append([f.name])
    try:
        from app.i18n import t
        parts.append(t("og.kicker"))
    except Exception:                   # noqa: BLE001
        pass
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def card(event: Optional[Dict[str, Any]]) -> Tuple[bytes, str, str]:
    """(jpeg, cache key, mode). Made once per change of title, mode or pictures."""
    mode, files = decide(event)
    key = _key(event, mode, files)
    name = f"neutral-{key}.jpg" if mode == "neutral" else f"{int(event['id'])}-{key}.jpg"
    path = _cache_dir() / name
    if path.is_file():
        return path.read_bytes(), key, mode
    data = render(event, mode, files)
    prefix = "neutral-" if mode == "neutral" else f"{int(event['id'])}-"
    for old in _cache_dir().glob(prefix + "*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data, key, mode


def forget(event_id: int) -> None:
    """Drop the cached card of an event (after a change the key would miss)."""
    for old in _cache_dir().glob(f"{int(event_id)}-*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass


# ── Meta tags ─────────────────────────────────────────────────

def meta(event: Optional[Dict[str, Any]], base: str, *, force_brand: bool = False) -> Dict[str, Any]:
    """Everything the <meta> tags need, for a page about ``event``.

    ``force_brand``: pages that belong to one guest (a run page, a share
    link) never show the gallery's photos, whatever the gallery allows.
    """
    mode, files = decide(event)
    if force_brand and mode in ("full",):
        mode, files = "brand", []
    key = _key(event, mode, files)
    base = (base or "").rstrip("/")
    slug = (event or {}).get("slug") or "-"
    image = f"{base}/gallery/{slug}/og.jpg?v={key}"
    if force_brand and mode == "brand":
        image += "&b=1"
    return {
        "title": _title(event, mode),
        "description": _description(event, mode),
        "image": image,
        "width": W, "height": H,
        "url": f"{base}/gallery/{slug}" if event and mode != "neutral" else base + "/",
        "mode": mode,
    }


def card_for_request(event: Optional[Dict[str, Any]], brand_only: bool) -> Tuple[bytes, str, str]:
    if brand_only and event and decide(event)[0] == "full":
        mode = "brand"
        key = _key(event, mode, [])
        path = _cache_dir() / f"{int(event['id'])}-b-{key}.jpg"
        if not path.is_file():
            path.write_bytes(render(event, mode, []))
        return path.read_bytes(), key, mode
    return card(event)
