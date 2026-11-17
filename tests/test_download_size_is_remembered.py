"""The size a guest picks for a download is remembered, and can be changed again (17.11.2026).

The size sheet (#dlSheet) says "Eure Auswahl wird gemerkt." and the choice is
kept in localStorage under ``pb-dl-size``. Checked in headless Chrome against a
real gallery server (tests/chrome_cdp.py): the tile button, the lightbox button
and "Meine Auswahl" (a ZIP) all ask ``dlAskSize()``, so one pick serves all of
them; a visible button next to the photo count shows the pick and opens the
sheet again; and a browser that refuses to store anything (private mode) still
downloads.
"""
from __future__ import annotations

import re
import secrets
import socket
import threading
import time
from pathlib import Path

import pytest

from tests.chrome_cdp import CHROME, Browser
from tests.test_download_sizes import _event_with_photo

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "public" / "gallery.html"

# An anchor click is a download here: remember where it would have gone instead of leaving the page.
SPY = ("window.__dl = []; HTMLAnchorElement.prototype.click = function () { window.__dl.push(this.href); };")
NO_STORAGE = ("Storage.prototype.getItem = function () { throw new Error('private mode'); };"
              "Storage.prototype.setItem = function () { throw new Error('private mode'); };")


def test_selection_and_lightbox_ask_the_same_remembered_size():
    src = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"async function downloadSelected\(\) \{.*?const size = await dlAskSize\(\);.*?"
                     r"selection\.zip\?ids=.*?&size=\$\{encodeURIComponent\(size\)\}", src, re.S)
    assert "downloadPhoto(d.url, d.name, d.id)" in src          # the lightbox button
    assert "localStorage.setItem(DL_KEY, size); } catch" in src   # private mode must not break a download
    assert "localStorage.getItem(DL_KEY)" in src and "catch (e) { return ''; }" in src


@pytest.fixture(scope="module")
def gallery():
    from werkzeug.serving import make_server

    from app import create_app, db

    app = create_app()
    app.config.update(TESTING=True)
    app.test_client().get("/healthz")
    slug, _eid, mid = _event_with_photo(app)
    with app.app_context():
        db.set_setting("selection_enabled", "1")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {"base": f"http://127.0.0.1:{port}", "slug": slug, "mid": mid}
    finally:
        server.shutdown()


def _open(b, g, before=""):
    b.viewport(1280, 800)
    b.before_load(SPY + before)
    b.goto(f"{g['base']}/gallery/{g['slug']}", settle=1.2)


def _tile_download(b):
    # Since 17.11.2026 a tile carries no buttons (Picdrop-style); the download
    # of a picture is the large view's button. Same dlAskSize() path as before.
    b.eval("(() => { if (!document.getElementById('lightbox').classList.contains('open'))"
           " document.querySelector('.photo-item .photo-open')"
           ".dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); })()")
    time.sleep(0.5)
    b.eval("document.getElementById('lbDownload').click()")
    time.sleep(0.4)


def _pick(b, size):
    assert b.eval("document.getElementById('dlSheet').hidden") is False, "the sheet should be asking"
    b.eval(f"document.querySelector('#dlSheet .dl-opt[data-size=\"{size}\"]').click()")
    time.sleep(0.4)


def _last(b):
    return b.eval("window.__dl[window.__dl.length - 1] || ''")


pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome on this machine")


def test_the_first_download_asks_the_next_ones_do_not_and_a_reload_remembers(gallery):
    with Browser() as b:
        _open(b, gallery)
        b.eval("localStorage.removeItem('pb-dl-size')")
        _tile_download(b)
        _pick(b, "web")
        assert b.eval("localStorage.getItem('pb-dl-size')") == "web"
        assert "size=web" in _last(b) and f"/photo/{gallery['mid']}/download" in _last(b)
        b.eval("window.__dl.length = 0")
        _tile_download(b)
        assert b.eval("document.getElementById('dlSheet').hidden") is True, "asked again"
        assert "size=web" in _last(b)
        b.goto(f"{gallery['base']}/gallery/{gallery['slug']}", settle=1.2)       # the next visit, same device
        assert b.eval("dlGetSize()") == "web"


def test_the_lightbox_button_uses_the_remembered_size(gallery):
    with Browser() as b:
        _open(b, gallery)
        b.eval("localStorage.setItem('pb-dl-size', 'large')")
        b.eval("document.querySelector('.photo-item .photo-open')"
               ".dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))")      # not .click(): spied
        time.sleep(0.6)
        b.eval("document.getElementById('lbDownload').click()")
        time.sleep(0.4)
        assert b.eval("document.getElementById('dlSheet').hidden") is True
        assert "size=large" in _last(b)


def test_there_is_a_visible_way_to_change_the_choice(gallery):
    with Browser() as b:
        _open(b, gallery)
        b.eval("localStorage.removeItem('pb-dl-size')")
        assert b.eval("document.getElementById('dlChange').hidden") is True      # nothing chosen yet
        _tile_download(b)
        _pick(b, "web")
        shown = b.eval("(() => { const c = document.getElementById('dlChange'); const r = c.getBoundingClientRect();"
                       " return {hidden: c.hidden, text: c.textContent.trim(), h: r.height}; })()")
        assert shown["hidden"] is False and shown["h"] >= 40
        assert "Mittel" in shown["text"], shown
        b.eval("document.getElementById('dlChange').click()")
        time.sleep(0.3)
        _pick(b, "large")                                                        # asked although remembered
        assert b.eval("localStorage.getItem('pb-dl-size')") == "large"
        assert "Groß" in b.eval("document.getElementById('dlChange').textContent")
        b.eval("window.__dl.length = 0")
        _tile_download(b)
        assert "size=large" in _last(b)
        # Cancelling the change keeps the old choice.
        b.eval("document.getElementById('dlChange').click()")
        time.sleep(0.3)
        b.eval("document.getElementById('dlSheetCancel').click()")
        time.sleep(0.3)
        assert b.eval("localStorage.getItem('pb-dl-size')") == "large"


def test_a_browser_that_stores_nothing_still_downloads(gallery):
    with Browser() as b:
        _open(b, gallery, before=NO_STORAGE)
        _tile_download(b)
        _pick(b, "original")
        assert "size=original" in _last(b)
        b.eval("window.__dl.length = 0")
        _tile_download(b)                       # nothing remembered: it asks again, and works again
        _pick(b, "web")
        assert "size=web" in _last(b)
