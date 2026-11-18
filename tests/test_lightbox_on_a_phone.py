"""The guest's large view on a phone (18.11.2026, reported from an iPhone).

Three things went wrong at 390 px: the previous arrow sat almost entirely
outside the screen and the next arrow half outside; share and download ended
in the same iOS share sheet; and the download was a small variant (about
23 KB) although the event allowed the original. The geometry is checked in
headless Chrome with mobile emulation (tests/chrome_cdp.py); it is not
Safari, so the safe-area insets are zero here.
"""
from __future__ import annotations

import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest

from tests.chrome_cdp import CHROME, Browser
from tests.test_download_sizes import _event_with_photo

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app" / "templates" / "public" / "gallery.html"
CSS = ROOT / "app" / "static" / "css" / "public.css"

SPY = ("window.__dl = []; HTMLAnchorElement.prototype.click = function () {"
       " window.__dl.push({href: this.href, download: this.getAttribute('download')}); };")
IPHONE = ("Object.defineProperty(navigator, 'userAgent', {get: () => 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X)"
          " AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1'});")
# An Android phone: on a Mac, touch emulation plus "MacIntel" reads as an iPad.
ANDROID = "Object.defineProperty(navigator, 'platform', {get: () => 'Linux armv8l'});"
SHARE_SPY = ("window.__shared = []; navigator.canShare = () => true;"
             " navigator.share = async (d) => { window.__shared.push({url: d.url || '', files: (d.files || []).map(f => f.size)}); };"
             " window.__fetched = []; const _f = window.fetch; window.fetch = function (u, o) { window.__fetched.push(String(u)); return _f(u, o); };")


def _second_photo(app, slug, eid):
    import secrets

    from PIL import Image

    from app import db, media
    with app.app_context():
        rel = f"orig/{slug}-2.jpg"
        full = media.data_dir() / rel
        Image.new("RGB", (1800, 1200), (20, 20, 30)).save(full, "JPEG", quality=90)
        db.add_media(event_id=eid, kind="photo", filename="foto_2.jpg",
                     path_original=rel, path_webp=rel, path_thumb=rel,
                     size_bytes=full.stat().st_size, sha256=secrets.token_hex(8),
                     share_token=secrets.token_hex(6))


@pytest.fixture(scope="module")
def gallery():
    from werkzeug.serving import make_server

    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    app.test_client().get("/healthz")
    slug, eid, mid = _event_with_photo(app, w=1800, h=1200)
    _second_photo(app, slug, eid)                # the arrows only show with two pictures
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {"base": f"http://127.0.0.1:{port}", "slug": slug, "mid": mid}
    finally:
        server.shutdown()


def _open_phone(b, g, before=""):
    b.viewport(390, 844, mobile=True, scale=3)
    b.before_load(SPY + before)
    b.goto(f"{g['base']}/gallery/{g['slug']}", settle=1.2)
    b.eval("document.querySelector('.photo-item .photo-open')"
           ".dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))")
    time.sleep(0.6)


def _rect(b, el_id):
    return b.eval(f"(() => {{ const r = document.getElementById('{el_id}').getBoundingClientRect();"
                  " return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height,"
                  " vw: document.documentElement.clientWidth}; })()")


needs_chrome = pytest.mark.skipif(CHROME is None, reason="no Chrome on this machine")


# ── 1. Both arrows inside the screen, same inset, tappable ──────────────────

@needs_chrome
def test_both_arrows_sit_fully_inside_a_390px_screen_with_the_same_inset(gallery):
    with Browser() as b:
        _open_phone(b, gallery)
        if os.environ.get("PB_SHOT"):
            b.screenshot(Path(os.environ["PB_SHOT"]))
        prev, nxt = _rect(b, "lbPrev"), _rect(b, "lbNext")
        assert b.eval("getComputedStyle(document.getElementById('lbPrev')).visibility") == "visible"
        # The arrow is what a guest sees at its edge: nothing else lies on top of it.
        top = b.eval("(() => { const r = document.getElementById('lbPrev').getBoundingClientRect();"
                     " const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
                     " return e && e.closest('#lbPrev') ? 'lbPrev' : (e ? e.id || e.className : ''); })()")
        assert top == "lbPrev", top
        vw = prev["vw"]
        assert vw == 390, prev
        assert prev["l"] >= 0 and prev["r"] <= vw, ("previous arrow leaves the screen", prev)
        assert nxt["l"] >= 0 and nxt["r"] <= vw, ("next arrow leaves the screen", nxt)
        assert abs(prev["l"] - (vw - nxt["r"])) <= 1, ("arrows not mirrored", prev, nxt)
        assert 4 <= prev["l"] <= 16, ("inset too large or none", prev)
        for r in (prev, nxt):
            assert r["w"] >= 44 and r["h"] >= 44, r
            assert r["w"] <= 52, ("arrow covers more picture than needed", r)
        # Both vertically centred on the same line.
        assert abs(prev["t"] - nxt["t"]) <= 1, (prev, nxt)


def test_arrows_respect_the_safe_area_on_a_phone():
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"\.lb-prev\s*\{[^}]*left:\s*max\(\s*8px,\s*env\(safe-area-inset-left", css)
    assert re.search(r"\.lb-next\s*\{[^}]*right:\s*max\(\s*8px,\s*env\(safe-area-inset-right", css)


# ── 3. Without a choice the guest gets the best the event allows ───────────

def test_a_download_without_a_size_is_the_original_when_the_event_allows_it(app, client):
    from tests.test_download_sizes import _longest_edge
    slug, _, mid = _event_with_photo(app, w=3000, h=2000)
    r = client.get(f"/gallery/{slug}/photo/{mid}/download")
    assert r.status_code == 200
    assert _longest_edge(r.get_data()) == 3000


def test_a_download_without_a_size_is_the_largest_size_when_the_original_is_off(app, client):
    import json

    from app import db, gallery_settings as gsm
    from tests.test_download_sizes import _longest_edge
    slug, eid, mid = _event_with_photo(app, w=3000, h=2000)
    with app.app_context():
        db.update_event(eid, {"gallery_json": json.dumps(gsm.clean({"download_original": False}))})
    r = client.get(f"/gallery/{slug}/photo/{mid}/download")
    assert r.status_code == 200
    assert _longest_edge(r.get_data()) == 2048


def _last(b):
    return b.eval("(window.__dl[window.__dl.length - 1] || {}).href || ''")


@needs_chrome
def test_the_first_download_asks_nothing_and_takes_the_original(gallery):
    with Browser() as b:
        _open_phone(b, gallery, before=ANDROID)
        b.eval("localStorage.removeItem('pb-dl-size')")
        b.eval("document.getElementById('lbDownload').click()")
        time.sleep(0.4)
        assert b.eval("document.getElementById('dlSheet').hidden") is True, "the size sheet asked first"
        assert "size=original" in _last(b), _last(b)
        assert "/data/" not in _last(b) and "thumb" not in _last(b)


@needs_chrome
def test_a_remembered_size_counts_only_while_the_event_offers_it(gallery):
    with Browser() as b:
        _open_phone(b, gallery, before=ANDROID)
        b.eval("localStorage.setItem('pb-dl-size', 'web')")
        b.eval("document.getElementById('lbDownload').click()")
        time.sleep(0.4)
        assert "size=web" in _last(b)
        b.eval("localStorage.setItem('pb-dl-size', 'tiny')")      # a size this event does not offer
        b.eval("document.getElementById('lbDownload').click()")
        time.sleep(0.4)
        assert "size=original" in _last(b)


@needs_chrome
def test_changing_the_size_is_secondary_and_marks_the_size_in_use(gallery):
    with Browser() as b:
        _open_phone(b, gallery)
        b.eval("localStorage.removeItem('pb-dl-size')")
        b.eval("window.pbLightboxClose()")
        time.sleep(0.3)
        state = b.eval("(() => { const c = document.getElementById('dlChange');"
                       " return {hidden: c.hidden, text: c.textContent}; })()")
        assert state["hidden"] is False and "Original" in state["text"], state
        b.eval("document.getElementById('dlChange').click()")
        time.sleep(0.3)
        assert b.eval("document.activeElement.dataset.size") == "original"
        assert b.eval("document.querySelector('#dlSheet .dl-opt[aria-current]').dataset.size") == "original"


# ── 2. Share and download do different things on an iPhone ─────────────────

@needs_chrome
def test_on_an_iphone_download_offers_the_file_and_does_not_open_the_share_sheet(gallery):
    with Browser() as b:
        _open_phone(b, gallery, before=IPHONE + SHARE_SPY)
        b.eval("localStorage.removeItem('pb-dl-size')")
        b.eval("document.getElementById('lbDownload').click()")
        time.sleep(0.5)
        assert b.eval("document.getElementById('dlSave').hidden") is False
        link = b.eval("(() => { const a = document.getElementById('dlSaveFile');"
                      " return {href: a.href, dl: a.hasAttribute('download'), text: a.textContent.trim()}; })()")
        assert "/download?size=original" in link["href"] and link["dl"] and link["text"] == "Als Datei laden", link
        # The long-press picture ("Zu Fotos hinzufügen") is the share file: the
        # download route's JPEG at up to 2048 px, never the display/WebP variant.
        # The route answers with an attachment header; an <img> shows it anyway.
        shown = b.eval("document.getElementById('dlSaveImg').src")
        assert re.search(r"/photo/\d+/download\?size=large&share=1$", shown), shown
        time.sleep(0.8)
        img = b.eval("(() => { const i = document.getElementById('dlSaveImg');"
                     " return {ok: i.complete, w: i.naturalWidth, h: i.naturalHeight}; })()")
        assert img["ok"] and img["w"] == 1800 and img["h"] == 1200, img     # full picture, not upscaled
        assert b.eval("document.getElementById('dlSaveShare')") is None
        assert b.eval("window.__shared.length") == 0, "download opened the share sheet"


@needs_chrome
def test_share_sends_the_picture_file_at_the_download_quality_not_the_display_variant(gallery):
    with Browser() as b:
        _open_phone(b, gallery, before=IPHONE + SHARE_SPY)
        b.eval("localStorage.removeItem('pb-dl-size')")
        b.eval("document.getElementById('lbShare').click()")
        time.sleep(1.0)
        fetched = b.eval("window.__fetched")
        # The download route's file at the best size up to 2048 px (iOS share sheet), not the original.
        assert any(re.search(r"/photo/\d+/download\?size=large&share=1$", u) for u in fetched), fetched
        shared = b.eval("window.__shared")
        assert len(shared) == 1 and shared[0]["files"] and shared[0]["files"][0] > 0, shared
        assert b.eval("document.getElementById('dlSave').hidden") is True


def test_share_and_download_have_their_own_names_in_both_languages():
    from app.i18n import de, en
    for lang in (de, en):
        strings = next(v for v in vars(lang).values() if isinstance(v, dict) and "gallery.share" in v)
        assert strings["gallery.share"] and strings["gallery.download_hint"] and strings["dl.sheet_file"]
        assert strings["gallery.share"] != strings["gallery.download_hint"]
    src = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r'id="lbShare"[^>]*title="\{\{ t\(\'gallery.share\'\) \}\}" aria-label="\{\{ t\(\'gallery.share\'\) \}\}"', src)
    assert re.search(r'id="lbDownload"[^>]*title="\{\{ t\(\'gallery.download_hint\'\) \}\}"', src)
    assert "dlSaveShare" not in src


# ── Guard for the class: every control of the large view on every phone width ──

@needs_chrome
@pytest.mark.parametrize("width", [320, 360, 375, 390, 414, 430])
def test_every_lightbox_control_is_inside_the_screen_and_on_top(gallery, width):
    with Browser() as b:
        b.viewport(width, 740, mobile=True, scale=3)
        b.before_load(SPY)
        b.goto(f"{gallery['base']}/gallery/{gallery['slug']}", settle=1.0)
        b.eval("document.querySelector('.photo-item .photo-open')"
               ".dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))")
        time.sleep(0.5)
        controls = b.eval("""(() => Array.from(document.querySelectorAll('#lightbox button, #lightbox a[href]'))
            .filter(el => !el.hidden && el.offsetParent !== null && getComputedStyle(el).visibility !== 'hidden')
            .map(el => { const r = el.getBoundingClientRect();
                         const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                         return {id: el.id || el.className, l: r.left, r: r.right, t: r.top, b: r.bottom,
                                 w: r.width, h: r.height, top: !!(hit && (hit === el || el.contains(hit))),
                                 name: el.getAttribute('aria-label') || el.textContent.trim()}; }))()""")
        vw, vh = width, 740
        assert len(controls) >= 5, controls
        for c in controls:
            assert c["l"] >= 0 and c["t"] >= 0 and c["r"] <= vw and c["b"] <= vh, ("off screen", width, c)
            assert c["w"] >= 44 and c["h"] >= 44, ("too small to tap", width, c)
            assert c["top"], ("covered by something else", width, c)
            assert c["name"], ("no accessible name", c)
        names = [c["name"] for c in controls]
        assert len(names) == len(set(names)), ("two controls share a name", names)


def test_lightbox_css_pushes_no_control_off_screen_and_uses_the_safe_area():
    css = CSS.read_text(encoding="utf-8")
    for m in re.finditer(r"([^{}]*\.lb[-\w]*[^{}]*)\{([^}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        assert not re.search(r"\b(left|right|top|bottom)\s*:\s*-\d", body), (sel, body)
        assert not re.search(r"translate[XY]?\(\s*-\d{2,}(px|%)", body.replace("translateY(-50%)", "")), (sel, body)
    for sel in (r"\.lb-prev", r"\.lb-next", r"\.lb-tools #lbClose", r"\.lb-tools \{\s*position: fixed"):
        rule = re.search(sel + r"[^{]*\{[^}]*\}" if "position" not in sel else sel + r"[^}]*\}", css)
        assert rule and "safe-area-inset" in rule.group(0), sel


def test_share_and_download_have_distinct_handlers():
    src = TEMPLATE.read_text(encoding="utf-8")
    share = re.search(r"getElementById\('lbShare'\)\.addEventListener\('click', \(\) => \{(.*?)\}\);", src, re.S).group(1)
    dl = re.search(r"getElementById\('lbDownload'\)\.addEventListener\('click', \(\) => \{(.*?)\}\);", src, re.S).group(1)
    assert "sharePhoto(" in share and "downloadPhoto(" not in share
    assert "downloadPhoto(" in dl and "sharePhoto(" not in dl
    # The iOS download sheet hands out a file and never calls the share sheet.
    sheet = re.search(r"function dlSaveSheet\(.*?\n\}", src, re.S).group(0)
    assert "navigator.share" not in sheet and "file.href = dlUrl" in sheet
