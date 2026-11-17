"""One finger scrolls every booth page that is longer than the screen.

Reported 16.11.2026 from the booth PC (WebView2 own window, HID touch
screen, Windows 11): in "Verwaltung" and "Vorlagen" a finger did not scroll,
the mouse wheel did. A finger that arrives as a mouse (a touch screen in
mouse mode, or a digitizer that the web view did not see at start) drags
instead of panning, and a mouse drag never scrolls a page.

These checks hold the parts in this repository that decide it:

* nothing on an operator page or above a scroller says ``touch-action:none``
  (only the drawing canvas and the template editor's sheet, which draw and
  drag under the finger on purpose);
* the window's guard and pywebview's own blocker cancel two-finger touches
  only, never one;
* the web view is told there is a touch screen (``--touch-events=enabled``)
  and pinch and history swipes stay off;
* every operator page, and the kiosk, load ``pan.js``: a press that comes in
  as a mouse and is dragged pans the box under it like a finger would;
* the page tells the window which kind of pointer it sees, so the self-test
  read over ssh says whether the finger arrives as "touch" or as "mouse".

The browser half is ``tools/touch_pan_check.mjs`` (raw touch points and a
mouse drag through Chrome's DevTools protocol, against a running booth).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from boothcore import window as w

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
STATIC = WEB / "static"
OPERATOR_PAGES = ["admin.html", "vorlagen.html", "hilfe.html"]
#: Draws or drags under the finger by design; a finger there must not scroll.
NO_PAN_ALLOWED = {"#drawCanvas", ".vle-blatt"}


def _css_blocks(text: str):
    """(selector, body) pairs of every rule in plain CSS or a CSS-in-JS string."""
    flat = re.sub(r'"\s*\+\s*"', "", text)                    # the editor builds its CSS from "..." + "..."
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", flat):
        yield m.group(1).strip().split("\n")[-1].strip(' "\''), m.group(2)


def _sources():
    for p in [STATIC / "app.css", STATIC / "vorlagen-editor.js", *[WEB / n for n in OPERATOR_PAGES], WEB / "index.html"]:
        yield p, p.read_text(encoding="utf-8")


def test_only_the_drawing_canvas_and_the_editor_sheet_refuse_a_panning_finger():
    found = set()
    for path, text in _sources():
        for sel, body in _css_blocks(text):
            if re.search(r"touch-action\s*:\s*none", body):
                found.add(sel)
                assert sel in NO_PAN_ALLOWED, f"{path.name}: {sel} has touch-action:none"
    assert found == NO_PAN_ALLOWED


@pytest.mark.parametrize("page", OPERATOR_PAGES)
def test_an_operator_page_scrolls_as_a_whole_and_pans_both_ways(page):
    text = (WEB / page).read_text(encoding="utf-8")
    head = text.split("</style>")[0]
    # app.css locks the kiosk (html,body overflow:hidden); the page opens it again.
    assert re.search(r"html[^{]*\{[^}]*overflow:auto", head), page
    assert re.search(r"html[^{]*\{[^}]*touch-action:pan-x pan-y", head), page


@pytest.mark.parametrize("page", OPERATOR_PAGES + ["index.html"])
def test_every_page_loads_the_mouse_pan(page):
    assert '<script src="/static/pan.js"></script>' in (WEB / page).read_text(encoding="utf-8")


def test_the_mouse_pan_leaves_a_real_finger_and_the_editor_alone():
    js = (STATIC / "pan.js").read_text(encoding="utf-8")
    assert 'e.pointerType !== "mouse"' in js           # a finger pans natively, nothing to do
    for skip in ("input", "textarea", "select", "[draggable", "canvas", ".vle-blatt", ".osk", "[data-no-pan]"):
        assert skip in js, skip
    assert "preventDefault" in js and "click" in js     # the click that ends a pan is not a tap


def test_the_guards_cancel_two_fingers_only():
    guard = w.GUARD_JS
    m = re.search(r"addEventListener\('touchmove',\s*function \(e\) \{(.*?)\}, \{passive: false\}\)", guard)
    assert m and "touches.length > 1" in m.group(1)
    assert "touchstart" not in guard.replace("lastTouch", "")


def test_the_web_view_knows_the_touch_screen_and_keeps_pinch_and_swipes_off():
    args = w.BROWSER_ARGS
    assert "--touch-events=enabled" in args
    assert "--disable-pinch" in args and "--overscroll-history-navigation=0" in args
    assert args.count("--disable-features=") == 1


def test_the_page_reports_which_pointer_it_sees(tmp_path):
    assert "pointer:" in w.GUARD_JS and "maxTouchPoints" in w.GUARD_JS
    win = w.OwnWindow("http://127.0.0.1:1/", tmp_path, {})
    w._reset_status()
    win.heartbeat({"w": 1920, "h": 1200, "pointer": "mouse", "touch_points": 10, "evil": "x"})
    page = w.status()["page"]
    assert page["pointer"] == "mouse" and page["touch_points"] == 10 and "evil" not in page
