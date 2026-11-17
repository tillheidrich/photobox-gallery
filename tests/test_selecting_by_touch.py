"""Picking several photos with a finger ("Meine Auswahl" and the client's picks).

Reported from an iPhone on 15.11.2026: selecting several photos in the
guest gallery did not work properly. Measured with real touch input over
CDP (tools/admin_phone_check.mjs --select-touch, docs/ADMIN-PHONE-CHECK.md,
"Selecting by touch"); these tests pin what a server answer and the shipped
CSS and JS can show, the browser check is the gate for the behaviour.
"""
import json
import re
import secrets
from pathlib import Path

from app import gallery_settings as gsm

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app" / "static" / "css" / "public.css").read_text(encoding="utf-8")
PICKS_JS = (ROOT / "app" / "static" / "js" / "client-picks.js").read_text(encoding="utf-8")


def _gallery(app, client, n=3, **gallery):
    from app import db, media
    client.get("/healthz")
    slug = "tap-" + secrets.token_hex(3)
    with app.app_context():
        eid = db.create_event(slug=slug, name="Antippen", upload_token=secrets.token_hex(8))
        db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gallery))})
        for i in range(n):
            rel = f"orig/{slug}-{i}.jpg"
            full = media.data_dir() / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(b"\xff\xd8\xff\xe0JPEGDATA" + str(i).encode())
            db.add_media(event_id=eid, kind="photo", filename=f"Hochzeit_{i}.jpg", path_original=rel,
                         path_webp=None, path_thumb=None, size_bytes=full.stat().st_size,
                         sha256=secrets.token_hex(8), share_token=secrets.token_hex(6))
    return client.get(f"/gallery/{slug}").get_data(as_text=True)


def _rule(selector):
    """The declarations of every rule whose selector list names ``selector``."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
        if selector in [s.strip() for s in m.group(1).split(",")]:
            out.append(m.group(2))
    return " ".join(out)


def _media_block(query):
    return " ".join(m.group(1) for m in re.finditer(re.escape(query) + r"\s*\{(.*?)\n\}", CSS, re.S))


# ── 1. A tap on the check mark itself did nothing ───────────────────
# The tile's click handler called preventDefault() for every click, also the
# one on the check box - and a cancelled click on a check box is undone by the
# browser, with no change event. The 44 px box in the corner, the obvious
# place to tap, was the one spot that never selected.

def test_a_tap_on_the_check_box_is_not_undone_by_the_tile(app, client):
    html = _gallery(app, client)
    handler = html[html.index("items.forEach(el => {"):][:900]
    assert "selectable" in handler
    guard = handler.find(".photo-select-cb")
    prevent = handler.find("e.preventDefault()")
    assert guard != -1 and prevent != -1
    # the check box returns before anything cancels its click
    assert re.search(r"e\.target === cb\) return", handler), handler
    assert guard < prevent


def test_every_tile_has_a_named_check_box_for_selection_mode(app, client):
    html = _gallery(app, client, n=3)
    boxes = re.findall(r'<input type="checkbox" class="photo-select-cb"[^>]*>', html)
    assert len(boxes) == 3
    # "<event>-0001.jpg auswählen" (the name the download gets), not three times "Auswählen"
    names = [re.search(r'aria-label="([^"]+)"', b).group(1) for b in boxes]
    assert all(re.fullmatch(r"tap-[0-9a-f]+-000[123]\.jpg auswählen", n) for n in names), names
    assert len(set(names)) == 3 and not any("Hochzeit_" in n for n in names)
    assert all(" hidden" in b for b in boxes)      # shown only in selection mode
    assert html.count('class="photo-select-mark"') == 3


def test_no_check_boxes_without_the_selection_switch(app, client):
    html = _gallery(app, client, n=2, selection=False)
    assert 'class="photo-select-cb"' not in html


# ── 2. Tile buttons swallowed taps in selection mode ────────────────
# In proofing mode the colour flag sits on every tile, also on a phone; in
# selection mode a tap on it opened the flag menu instead of selecting.

def test_tile_buttons_are_gone_in_selection_mode():
    hidden = _rule(".photo-item.selectable .photo-item-overlay")
    assert re.search(r"display:\s*none", hidden), hidden


# ── 3. iOS: callout, text selection, double-tap zoom, sticky hover ──

def test_a_tile_in_selection_mode_shows_no_callout_and_selects_no_text():
    rule = _rule(".photo-item.selectable")
    for decl in ("-webkit-touch-callout: none", "user-select: none", "-webkit-user-select: none",
                 "touch-action: manipulation", "-webkit-tap-highlight-color: transparent"):
        assert decl in rule, decl


def test_the_hover_zoom_only_applies_with_a_real_pointer():
    # iOS keeps :hover on the last tapped element: the tile stayed zoomed and
    # darkened after a tap, which read as "selected" on a tile that was not.
    outside = re.sub(r"@media \((hover: hover|prefers-reduced-motion: reduce)\)\s*\{.*?\n\}", "", CSS, flags=re.S)
    assert ".photo-item:hover img" not in outside
    assert ".photo-item:hover img" in _media_block("@media (hover: hover)")


# ── 4. The mark: large, clear in light and dark ─────────────────────

def test_the_selected_mark_is_large_and_filled():
    mark = _rule(".photo-item .photo-select-mark")
    assert re.search(r"width:\s*(2[8-9]|3\d)px", mark), mark
    checked = _rule(".photo-select-cb:checked + .photo-select-mark")
    assert "var(--color-primary)" in checked and "svg" in checked


# ── 5. The bar: at the bottom, in reach of the thumb, clear of the home bar ──

def test_the_selection_bar_sits_at_the_bottom_on_a_phone():
    phone = _media_block("@media (max-width: 700px), (max-height: 500px)")
    bar = phone[phone.index(".selection-bar.is-active"):][:400]
    assert "position: fixed" in bar and "bottom: 0" in bar
    assert "env(safe-area-inset-bottom" in bar
    # the last row can scroll clear of it
    assert "body.is-selecting .photo-grid" in phone
    # held sideways it no longer scrolls away with the page
    assert ".selection-bar.is-active { position: static" not in CSS
    assert not re.search(r"\.gallery-toolbar, \.selection-bar\.is-active\s*\{\s*position: static", CSS)


# ── 6. The client's pick dot ────────────────────────────────────────

def test_the_pick_dot_is_a_full_finger_target():
    dot = _rule(".pick-dot")
    assert re.search(r"width:\s*44px", dot) and re.search(r"height:\s*44px", dot), dot


def test_picks_show_at_once_and_reach_the_server_in_tap_order():
    # Before, every tap waited for the server before the dot changed, two
    # quick taps both sent "on", and the first picks of a client with no
    # selection yet each created one.
    toggle = PICKS_JS[PICKS_JS.index("function toggle("):]
    body = toggle[:toggle.index("\n  }\n")]
    assert body.index("render()") < body.index("queue")      # drawn before anything is sent
    assert "queue.get(id)" in body                            # one photo's requests in tap order
    assert "if (!creating)" in PICKS_JS                        # one selection, however many taps
