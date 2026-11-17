"""The booth's pages on its own 14" touch screen (1920×1200, WebView2).

Seen on the device on 16.11.2026: links in the browser's blue and purple on the
dark admin, a GitHub address and the data folder running out of the "Über"
card, "Einstellungen speichern" on the very bottom edge, and no keyboard at
all for a text field, because Windows opens none in the kiosk window.

The first tests read the files and run everywhere. The browser tests render
the admin page with long words in it and type with the on-screen keyboard;
they need Playwright and skip without it, like test_oberflaechen_laufen.py.
"""
from __future__ import annotations

import http.server
import re
import socket
import socketserver
import threading
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
ADMIN = (WEB / "admin.html").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")


def _admin_css() -> str:
    return ADMIN.split("<style>", 1)[1].split("</style>", 1)[0]


def test_links_use_the_theme_not_the_browser_blue():
    assert ":where(a){color:var(--text)" in CSS and ":where(a:visited){color:var(--text)}" in CSS
    css = _admin_css()
    assert "  a{color:var(--text)" in css and "a:visited{color:var(--text)}" in css
    assert "a:focus-visible" in css and "a):focus-visible" in CSS


def test_long_words_break_inside_their_card():
    css = _admin_css()
    assert "overflow-wrap:anywhere" in css
    assert re.search(r"\.card[^{]*\{[^}]*min-width:0", css) or ".card *" in css
    assert "minmax(0,1fr)" in css.split("dl{", 1)[1].split("}", 1)[0]


def test_no_text_on_the_admin_page_is_smaller_than_14px():
    sizes = [int(n) for n in re.findall(r"font-size:(\d+)px", ADMIN)]
    assert sizes and min(sizes) >= 14, sorted(sizes)[:5]


def test_the_save_bar_keeps_away_from_the_bottom_edge():
    rule = re.search(r"\.ps-actions\{([^}]*)\}", _admin_css())
    assert rule and "sticky" in rule.group(1)
    assert "bottom:0" not in rule.group(1).replace(" ", "")
    assert "--admin-safe-bottom" in rule.group(1), "the profile's safe margin counts"


@pytest.mark.parametrize("page", ["admin.html", "index.html", "vorlagen.html"])
def test_every_page_with_fields_loads_the_on_screen_keyboard(page):
    assert '<script src="/static/osk.js"></script>' in (WEB / page).read_text(encoding="utf-8")


def test_the_keyboard_has_the_german_letters_and_a_number_pad():
    js = (WEB / "static" / "osk.js").read_text(encoding="utf-8")
    for key in ('"ä"', '"ö"', '"ü"', '"ß"', '"@"', '"__shift"', '"__back"', '"__enter"', '"__close"',
                "NUMBERS", "onscreen_keyboard", "isTrusted"):
        assert key in js, key
    assert "innerHTML" not in js, "built with DOM calls, nothing to escape"


# ── in a browser ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def base():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB), **kw)

        def log_message(self, *a):
            pass

    server = socketserver.TCPServer(("127.0.0.1", port), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api", reason="Playwright nicht installiert")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            br = p.chromium.launch()
        except Exception as e:                       # pragma: no cover
            pytest.skip(f"Chromium nicht startbar: {e}")
        yield br
        br.close()


LONG = ("C:\\\\Users\\\\Betreiberkonto-mit-langem-Namen\\\\AppData\\\\Local\\\\PhotoboxBooth\\\\daten-ordner"
        "-ohne-ein-einziges-leerzeichen")


@pytest.mark.parametrize("size", [(1920, 1200), (1280, 800)])
def test_nothing_runs_out_of_a_card(browser, base, size):
    pg = browser.new_page(viewport={"width": size[0], "height": size[1]})
    try:
        pg.goto(f"{base}/admin.html")
        pg.evaluate(f"""() => {{
          document.getElementById('pin').hidden = true;
          document.getElementById('main').hidden = false;
          document.getElementById('about').innerHTML =
            '<dt>Quelltext</dt><dd><a href="#">github.com/beispiel-organisation/photobox-gallery-mit-langem-namen</a> · AGPL-3.0-or-later</dd>'
            + '<dt>Datenordner</dt><dd><code>{LONG}</code></dd>';
        }}""")
        pg.wait_for_timeout(200)
        bad = pg.evaluate("""() => [...document.querySelectorAll('#main .card')].flatMap((card) => {
            const c = card.getBoundingClientRect();
            if (!c.width) return [];
            return [...card.querySelectorAll('*')].filter((e) => {
              if (e.closest('pre,textarea,select,.jump')) return false;
              const r = e.getBoundingClientRect();
              return r.width && r.right > c.right + 1;
            }).map((e) => e.tagName + ' ' + (e.textContent || '').slice(0, 40));
          })""")
        wide = pg.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
    finally:
        pg.close()
    assert bad == [] and not wide, bad[:5]


def test_the_keyboard_types_and_keeps_the_field_in_view(browser, base):
    pg = browser.new_page(viewport={"width": 1920, "height": 1200}, has_touch=True)
    try:
        pg.goto(f"{base}/admin.html")
        pg.evaluate("() => { document.getElementById('pin').hidden = true; document.getElementById('main').hidden = false; }")
        field = pg.locator("#booth_name")
        field.scroll_into_view_if_needed()
        pg.evaluate("() => { const f = document.getElementById('booth_name'); f.value = ''; f.focus(); }")
        pg.wait_for_timeout(200)
        assert pg.is_visible("#osk")
        for k in ("__shift", "f", "o", "t", "o", "__back", "ö"):
            pg.dispatch_event(f'#osk [data-k="{k}"]', "pointerdown")
        assert field.input_value() == "Fotö"
        gap = pg.evaluate("""() => document.getElementById('osk').getBoundingClientRect().top
                                   - document.getElementById('booth_name').getBoundingClientRect().bottom""")
        assert gap > 0, "the field sits under the keyboard"
        pg.keyboard.press("x")                      # a real keyboard: ours steps aside
        assert not pg.is_visible("#osk")
    finally:
        pg.close()
