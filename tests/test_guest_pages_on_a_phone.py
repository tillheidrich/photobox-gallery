"""Guest pages on a phone - what the measurement of 15.11.2026 found.

Measured with tools/admin_phone_check.mjs --guest at 360x740, 390x844,
430x932 and 844x390 (docs/ADMIN-PHONE-CHECK.md, "Guest pages"). The browser
check is the gate; these tests pin the parts a server answer can show, so
they cannot quietly come undone.
"""
import io
import json
import re
import secrets
from pathlib import Path

from PIL import Image

from app import gallery_settings as gsm

CSS = Path(__file__).resolve().parent.parent / "app" / "static" / "css"


def _jpeg(size=(900, 1200)):
    buf = io.BytesIO()
    Image.new("RGB", size, (60, 90, 120)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _run_event(client, items, gallery=None):
    """An event with one run; ``items`` are (uid suffix, kind, filename)."""
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    slug = "tel-" + secrets.token_hex(3)
    uid = "tl" + secrets.token_hex(5)
    eid = db.create_event(name="Telefon", slug=slug, upload_token=secrets.token_hex(8))
    db.update_event(eid, published=1, gallery_json=json.dumps(gsm.clean(gallery or {})))
    device = db.create_device(name=f"Box {slug}")
    ecosystem.save_device_profile(device["id"], {}, eid)
    for suffix, kind, name in items:
        data = io.BytesIO(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64) if name.endswith(".mp4") else _jpeg()
        r = client.post("/api/v2/booth/media", headers={"X-API-Key": device["api_key"]},
                        data={"uid": uid + suffix, "session_uid": uid, "event_slug": slug, "kind": kind,
                              "variant": "web", "file": (data, name)},
                        content_type="multipart/form-data")
        assert r.status_code in (200, 201), r.data[:300]
    event = db.get_event_by_id(eid)
    return event, f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"


def _coarse_blocks(css):
    return " ".join(m.group(1) for m in re.finditer(r"@media \(pointer: coarse\)[^{]*\{(.*?)\n\}", css, re.S))


# ── M8 safe areas, M9 brand ─────────────────────────────────

def test_the_page_reaches_into_the_safe_areas(client):
    client.get("/healthz")
    html = client.get("/faq").get_data(as_text=True)
    assert re.search(r'name="viewport" content="[^"]*viewport-fit=cover', html)


def test_the_brand_stays_on_one_line_on_a_phone():
    css = (CSS / "public.css").read_text(encoding="utf-8")
    assert "-webkit-line-clamp: 2" not in css
    phone = css[css.index("Brand on a phone"):][:600]
    assert "white-space: nowrap" in phone and "text-overflow: ellipsis" in phone


# ── M1 no zoom, M3 thumbs ───────────────────────────────────

def test_fields_are_16px_on_touch():
    coarse = _coarse_blocks((CSS / "public.css").read_text(encoding="utf-8"))
    assert re.search(r"\.form-control[^{]*\{[^}]*font-size: 16px", coarse)
    assert "#subscribeEmail" in coarse or "input:not(" in coarse


def test_every_guest_target_is_44px_on_touch():
    coarse = _coarse_blocks((CSS / "public.css").read_text(encoding="utf-8"))
    for selector in (".btn", ".nav-ctl", ".footer-links a", ".nav-logo", ".form-control"):
        assert selector in coarse, selector
    assert "min-height: 44px" in coarse and "min-width: 44px" in coarse


# ── M2 the first screen of the gallery ──────────────────────

def test_filter_chips_are_one_row_and_let_go_in_landscape():
    css = (CSS / "public.css").read_text(encoding="utf-8")
    phone = css[css.index("Gallery head on a phone"):][:2000]
    assert "flex-wrap: nowrap" in phone and "overflow-x: auto" in phone
    assert re.search(r"@media \(max-height: 500px\)[^{]*\{[^@]*\.gallery-toolbar[^}]*position: static", phone)


# ── M4 sharp tiles ──────────────────────────────────────────

def test_gallery_tiles_offer_the_thumbnail_and_the_web_version(client):
    event, _code = _run_event(client, [("", "photo", "a.jpg")], gallery={"layout": "list"})
    html = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    tile = re.search(r'<a class="photo-open"[^>]*>\s*<img ([^>]*)>', html).group(1)
    srcset = re.search(r'srcset="([^"]*)"', tile).group(1)
    assert "/thumbs/" in srcset or "_thumb" in srcset or len(srcset.split(",")) == 2
    assert len(srcset.split(",")) == 2 and all(p.strip().endswith("w") for p in srcset.split(","))
    sizes = re.search(r'sizes="([^"]*)"', tile).group(1)
    assert "900px" in sizes                      # the list layout is one column of at most 900 px


# ── M5 the run page loads one thing at a time ───────────────

def test_the_run_page_plays_only_the_first_video(client):
    event, code = _run_event(client, [("", "boomerang", "b.mp4"), (".v", "video", "v.mp4"), (".p", "photo", "p.jpg")])
    html = client.get(f"/p/{code}").get_data(as_text=True)
    videos = re.findall(r"<video[^>]*>", html)
    assert len(videos) == 2
    assert "autoplay" in videos[0]
    assert "autoplay" not in videos[1]
    # With a poster nothing loads; without one only the metadata and the
    # first frame (#t=0.1), so the clip is a picture, not a black box.
    assert ('poster=' in videos[1] and 'preload="none"' in videos[1]) \
        or ('poster=' not in videos[1] and 'preload="metadata"' in videos[1] and "#t=0.1" in videos[1])
    img = re.search(r'<figure class="sess-item"[^>]*>\s*<img ([^>]*)>', html).group(1)
    assert "srcset=" in img and "sizes=" in img


# ── Anrede ──────────────────────────────────────────────────

def test_the_run_page_speaks_to_the_guests_as_a_group(client):
    event, code = _run_event(client, [("", "photo", "p.jpg")])
    html = client.get(f"/p/{code}").get_data(as_text=True)
    assert "Eure Aufnahmen" in html and "Deine Aufnahmen" not in html
    client.post("/lang/en")
    html = client.get(f"/p/{code}").get_data(as_text=True)
    assert "Your photos" in html or "Your pictures" in html


def test_no_du_on_the_guest_pages():
    from app.i18n import de
    # Guests are addressed as a group, like the kiosk does ("ihr", "euer").
    # Client and admin texts keep "du": one person reads those.
    guest = ("session.", "mine.", "gallery.", "notfound.", "password.", "dl.", "share.", "home.", "soon.",
             "access.", "device.", "kiosk_guests.", "photo.", "about.privacy", "draft.")
    du = re.compile(r"\b(du|dein|deine|deinen|deinem|deiner|dir|dich)\b", re.I)
    offenders = [k for k, v in de.STRINGS.items() if k.startswith(guest) and isinstance(v, str) and du.search(v)]
    assert offenders == [], offenders


# ── M6 the layout page ──────────────────────────────────────
# Measured in the browser (computed colours, opacity included): the hints of
# a submitted layout were 3.34:1 light and 4.44:1 dark, because the whole
# fieldset was faded to .62; the error text was 4.36:1 light.

def test_a_locked_layout_form_does_not_fade_its_text():
    css = (CSS / "layout-kunde.css").read_text(encoding="utf-8")
    assert not re.search(r"\.lk-steps\[disabled\]\s*\{[^}]*opacity:\s*\.", css)
    assert re.search(r"\.lk-error\s*\{[^}]*color:\s*oklch\(0\.4", css)


def test_the_layout_page_speaks_english_in_english(client):
    from app import db, layout_freigabe as lf
    client.get("/healthz")
    eid = db.create_event(name="Layout EN", slug="layout-en-" + secrets.token_hex(3), upload_token=secrets.token_hex(8))
    db.update_event(eid, published=1, gallery_json=json.dumps(gsm.clean({"layout_approval": True})))
    from app import vorlagen
    from app.blueprints.admin_booth import _assets_dir
    v, _ = vorlagen.pruefe({"name": "Hoch", "paper": "10x15", "slots": [{"x": 70, "y": 70, "w": 1060, "h": 1180, "shot": 0}],
                            "texts": [{"x": 600, "y": 1330, "size": 92, "align": "center", "value": "x", "color": "#222222"}]})
    tpl = vorlagen.speichern(v, _assets_dir())["file"]
    token = lf.create(eid, {"templates": [tpl], "fields": lf.DEFAULT_FIELDS, "palettes": ["hell"],
                            "fonts": ["serif"], "logo": True}, days=30, by="admin")
    de_html = client.get(f"/layout/{token}").get_data(as_text=True)
    assert "Layout wählen" in de_html
    client.post("/lang/en")
    html = client.get(f"/layout/{token}").get_data(as_text=True)
    for german in ("Layout wählen", "Texte eintragen", "Farben und Schrift", "Vorschau und abschicken",
                   "Abschicken", "Höchstens", "freiwillig", "Logo hochladen", "Die Bilder sind Beispiele"):
        assert german not in html, german
    assert "Choose a layout" in html and "Send" in html
