"""The booth admin is one very long page: a chip for every card gets the owner there.

Seen on the booth's touch screen (17.11.2026): the owner could not find "Zweites
Ziel im Netz" among twenty cards. The jump bar existed, but as one row that
scrolled sideways with its scrollbar hidden and chips 38 px high, so the far
chips were never seen. Now the chips wrap, each is at least 48 px high, and a
"Nach oben" button leads back to them from anywhere on the page.

The first tests read the files. The last one drives headless Chrome over the
DevTools protocol (tests/chrome_cdp.py of the gallery, no Playwright) and taps
the chip with a mouse and with a finger; it skips without Chrome.
"""
from __future__ import annotations

import http.server
import importlib.util
import re
import socket
import socketserver
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "booth" / "boothcore" / "web"
ADMIN = (WEB / "admin.html").read_text(encoding="utf-8")
PAGE_JS = (WEB / "static" / "admin-page.js").read_text(encoding="utf-8")


def _css() -> str:
    return ADMIN.split("<style>", 1)[1].split("</style>", 1)[0]


def _rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", _css())
    assert m, selector
    return m.group(1)


def test_every_card_has_its_heading_as_first_child_so_it_gets_a_chip():
    """The chips are built from ``#main .card > h2``; a card whose heading sits deeper would have none."""
    cards = re.findall(r'<section class="card[^"]*"([^>]*)>\s*(<h2[^>]*>)?', ADMIN)
    # "alleEinstellungen" is filled by settings.js and gets its heading there.
    plain = [c for c in cards if 'id="alleEinstellungen"' not in c[0]]
    assert len(plain) >= 18
    assert all(h for _, h in plain), "a card without a direct <h2>"
    assert 'querySelectorAll("#main .card > h2")' in PAGE_JS


def test_ids_are_unique_and_the_chip_ids_cannot_collide_with_them():
    ids = re.findall(r'\bid="([^"]+)"', ADMIN)
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    assert not [i for i in ids if i.startswith("abschnitt-")], "reserved for cards without an id"
    assert 'card.id = "abschnitt-" + i' in PAGE_JS


def test_the_chips_are_finger_sized_and_wrap_instead_of_hiding_behind_a_sideways_scroll():
    bar = _rule(".jump")
    assert "flex-wrap:wrap" in bar and "overflow-x" not in bar
    chip = _rule(".jump a")
    assert int(re.search(r"min-height:(\d+)px", chip).group(1)) >= 48
    assert int(re.search(r"font-size:(\d+)px", chip).group(1)) >= 16


def test_long_headings_get_a_short_chip_label():
    labels = re.findall(r'data-jump="([^"]+)"', ADMIN)
    assert "Fernsteuerung" in labels and "Download ohne Internet" in labels
    assert all(len(x) <= 24 for x in labels)
    assert "card.dataset.jump" in PAGE_JS


def test_the_way_back_up_keeps_off_the_edges_and_off_the_save_bar():
    rule = _rule(".to-top")
    assert "position:fixed" in rule
    assert int(re.search(r"right:(\d+)px", rule).group(1)) >= 24
    bottom = re.search(r"bottom:([^;]*)", rule).group(1).replace(" ", "")
    # Above the settings save bar (max(72px,10vh) above the safe margin), never on the bottom edge.
    assert "max(72px,10vh)" in bottom and "--admin-safe-bottom" in bottom
    assert "body.osk-open .to-top{display:none}" in _css(), "the on-screen keyboard has the bottom"
    assert 'id="toTop"' in ADMIN and "hidden" in re.search(r'<button[^>]*id="toTop"[^>]*>', ADMIN).group(0)
    assert 'toTop.addEventListener("click"' in PAGE_JS


def test_scrolling_helpers_stay_in_place():
    """The jump bar must not replace the drag-scroll helper or the on-screen keyboard."""
    assert '<script src="/static/pan.js"></script>' in ADMIN
    assert '<script src="/static/osk.js"></script>' in ADMIN
    assert "scroll-margin-top" in _css()


# ── in a browser ────────────────────────────────────────────────────────────

def _chrome_cdp():
    spec = importlib.util.spec_from_file_location("gallery_chrome_cdp", ROOT / "tests" / "chrome_cdp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def base():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])

    class Handler(http.server.SimpleHTTPRequestHandler):
        """The static files of the booth's web folder; every API call answers an empty object."""

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB), **kw)

        def _empty(self):
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self._empty()

        def do_GET(self):
            if self.path.startswith("/api/"):
                return self._empty()
            return super().do_GET()

        def log_message(self, *a):
            pass

    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _signed_in(b, base, width, height):
    """The admin page as it is after the PIN: the booth's menu hands the sign-in over in sessionStorage."""
    b.viewport(width, height)
    b.before_load('try{sessionStorage.setItem("booth-admin", JSON.stringify({token:"t"}))}catch(e){}')
    b.goto(f"{base}/admin.html", settle=1.5)


def _tap(b, x, y, touch):
    if touch:
        b.send("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
        b.send("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
    else:
        b.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        b.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)


@pytest.mark.parametrize("size", [(1280, 800), (1920, 1200)])
@pytest.mark.parametrize("touch", [False, True])
def test_tapping_the_chip_brings_the_card_into_view_and_the_way_back_up_works(base, size, touch):
    cdp = _chrome_cdp()
    if not cdp.CHROME:
        pytest.skip("kein Chrome")
    import time
    with cdp.Browser() as b:
        _signed_in(b, base, *size)
        n = b.eval("document.querySelectorAll('#main .card > h2').length")
        chips = b.eval("[...document.querySelectorAll('#jump a')].map(a => a.textContent)")
        assert n >= 18 and len(chips) == n and "Zweites Ziel im Netz" in chips
        # Every chip is in view without sideways scrolling and finger-sized.
        geo = b.eval("""(() => { const j = document.getElementById('jump').getBoundingClientRect();
          const hs = [...document.querySelectorAll('#jump a')].map(a => a.getBoundingClientRect());
          return {minH: Math.min(...hs.map(r => r.height)), right: Math.max(...hs.map(r => r.right)),
                  w: innerWidth, jumpH: j.height, sideways: document.documentElement.scrollWidth > innerWidth + 1}; })()""")
        assert geo["minH"] >= 48 and geo["right"] <= geo["w"] and not geo["sideways"], geo
        assert geo["jumpH"] < size[1] * 0.4, "the bar must not eat the screen"
        pos = b.eval("""(() => { const a = [...document.querySelectorAll('#jump a')]
          .find(a => a.textContent === 'Zweites Ziel im Netz').getBoundingClientRect();
          return [a.left + a.width / 2, a.top + a.height / 2]; })()""")
        _tap(b, pos[0], pos[1], touch)
        time.sleep(1.6)                                  # smooth scroll
        seen = b.eval("""(() => { const h = [...document.querySelectorAll('#main .card > h2')]
          .find(h => h.textContent.trim() === 'Zweites Ziel im Netz').getBoundingClientRect();
          const bar = document.querySelector('.topbar').getBoundingClientRect();
          return {top: h.top, bottom: h.bottom, barBottom: bar.bottom, vh: innerHeight, y: scrollY}; })()""")
        assert seen["y"] > 500, seen
        assert seen["top"] >= seen["barBottom"] - 1 and seen["bottom"] <= seen["vh"], seen
        # "Nach oben" is there now, away from the edges, and leads back to the chips.
        up = b.eval("""(() => { const u = document.getElementById('toTop'); const r = u.getBoundingClientRect();
          return {hidden: u.hidden, shown: r.width > 0, right: innerWidth - r.right, bottom: innerHeight - r.bottom,
                  h: r.height, x: r.left + r.width / 2, y: r.top + r.height / 2}; })()""")
        assert up["shown"] and not up["hidden"] and up["right"] >= 24 and up["bottom"] >= 24 and up["h"] >= 48, up
        _tap(b, up["x"], up["y"], touch)
        time.sleep(1.6)
        assert b.eval("scrollY") < 5
        assert b.eval("document.getElementById('toTop').hidden") is True
