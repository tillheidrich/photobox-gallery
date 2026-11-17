"""The event edit page on a phone stays where the operator scrolled it (17.11.2026).

Report from the owner: on ``/admin/events/<id>/edit#abschnitt-2``, on a
phone, the page jumped back to the top while scrolling down. Cause: the row
of section chips sat inside an empty slot ``<div>``, so ``position: sticky``
had nothing to stick in and the row scrolled away with the header; then the
observer that marks the chip of the section on screen called
``scrollIntoView({block: "nearest"})`` on that chip, which scrolls the page
too, back up to the row.

Checked in headless Chrome at 390x844 against a real gallery server, as a
logged-in admin (signed session cookie), over the DevTools protocol
(tests/chrome_cdp.py, standard library only: no Playwright on the machines
that run the suites). Without the fix the first test fails: the page is
pulled back up after each scroll.
"""
from __future__ import annotations

import secrets
import socket
import threading
import time

import pytest

from app import db, ecosystem
from tests.chrome_cdp import CHROME, Browser

pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome on this machine")

# Every section open, so the page is long enough to scroll (the stored fold
# state is read through localStorage).
ALL_OPEN = ("Storage.prototype.getItem = (function (orig) { return function (k) {"
            " return String(k).indexOf('pb-sections:') === 0 ? '1' : orig.call(this, k); }; })"
            "(Storage.prototype.getItem);")


@pytest.fixture(scope="module")
def gallery():
    """A real gallery behind a real server, an event, an admin session cookie."""
    from werkzeug.serving import make_server

    from app import accounts, create_app

    app = create_app()
    app.config.update(TESTING=True)
    c = app.test_client()
    c.get("/healthz")
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
    eid = db.create_event(name="Demo Sommerfest", slug="scroll-" + secrets.token_hex(3),
                          upload_token=secrets.token_hex(8))
    with app.test_request_context():
        cookie = app.session_interface.get_signing_serializer(app).dumps(
            {"admin_logged_in": True, "csrf_token": secrets.token_hex(8),
             "admin_epoch": accounts.session_epoch(), "lang": "de"})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {"base": f"http://127.0.0.1:{port}", "eid": eid,
               "cookie": (app.config.get("SESSION_COOKIE_NAME") or "session", cookie)}
    finally:
        server.shutdown()


def open_page(b: Browser, g: dict, path: str, all_open: bool, width: int = 390, height: int = 844) -> None:
    b.viewport(width, height, mobile=width < 769)
    b.cookie(g["base"] + "/", *g["cookie"])
    if all_open:
        b.before_load(ALL_OPEN)
    b.goto(g["base"] + path, settle=1.2)


def scroll_and_watch(b: Browser, y: int) -> dict:
    # html carries scroll-behavior: smooth; a person's swipe is not smooth-scrolled.
    b.eval(f"window.scrollTo({{top: {y}, behavior: 'instant'}})")
    put = b.eval("Math.round(scrollY)")
    time.sleep(1.3)          # the observer fires, the chip row's own smooth scroll ends
    got = b.eval("""({after: Math.round(scrollY),
        nav: (function () { var n = document.querySelector('nav.adm-jump'); return n ? Math.round(n.getBoundingClientRect().top) : null; })(),
        here: (document.querySelector('nav.adm-jump a.is-here') || {}).textContent || ''})""")
    got["set"] = put
    return got


def test_scrolling_down_the_event_page_on_a_phone_keeps_the_position(gallery):
    with Browser() as b:
        open_page(b, gallery, f"/admin/events/{gallery['eid']}/edit#abschnitt-2", all_open=True)
        assert b.eval("innerWidth") == 390
        assert b.eval("document.querySelectorAll('nav.adm-jump a').length") >= 3
        assert b.eval("document.documentElement.scrollHeight - innerHeight") > 3600
        steps = [scroll_and_watch(b, y) for y in (900, 1800, 2700, 3600)]
    for s in steps:
        assert s["after"] == s["set"], f"the page moved by itself: {steps}"


def test_the_chip_row_stays_under_the_top_bar_and_follows_the_section(gallery):
    with Browser() as b:
        open_page(b, gallery, f"/admin/events/{gallery['eid']}/edit", all_open=True)
        bar = b.eval("Math.round(document.querySelector('.admin-topbar').getBoundingClientRect().bottom)")
        steps = [scroll_and_watch(b, y) for y in (1800, 3600)]
        row_scrolled = b.eval("document.querySelector('nav.adm-jump').scrollLeft")
    for s in steps:
        assert s["nav"] is not None and abs(s["nav"] - bar) <= 4, f"chip row not pinned under the bar ({bar}): {steps}"
        assert s["here"], f"a chip is marked for the section on screen: {steps}"
    assert steps[0]["here"] != steps[1]["here"]
    assert row_scrolled > 0, "the marked chip was brought into view inside its row"


def test_arriving_with_a_section_anchor_opens_it_and_then_leaves_the_page_alone(gallery):
    # Default folding: the first section and the one named in the address are open.
    with Browser() as b:
        open_page(b, gallery, f"/admin/events/{gallery['eid']}/edit#abschnitt-2", all_open=False)
        arrived = b.eval("Math.round(scrollY)")
        open2 = b.eval("!document.getElementById('abschnitt-2').classList.contains('is-collapsed')")
        top2 = b.eval("Math.round(document.getElementById('abschnitt-2').getBoundingClientRect().top)")
        step = scroll_and_watch(b, arrived + 300)
    assert open2
    assert arrived > 0 and 56 <= top2 <= 200, (arrived, top2)
    assert step["after"] == step["set"], step


def test_the_booth_page_with_its_search_field_does_not_move_either(gallery):
    dev = db.create_device(name="Scrollbox")
    with Browser() as b:
        open_page(b, gallery, f"/admin/devices/{dev['id']}/booth", all_open=True)
        assert b.eval("!!document.getElementById('ssInput')")   # the shared search bar (settings-search.js)
        steps = [scroll_and_watch(b, y) for y in (1500, 3000)]
    for s in steps:
        assert s["after"] == s["set"], steps


def test_publish_with_unsaved_changes_warns_first_and_switches_on_the_second_press(gallery):
    """The live/draft button leaves the page; unsaved form changes get an inline note first."""
    eid = db.create_event(name="Demo Entwurf", slug="dirty-" + secrets.token_hex(3),
                          upload_token=secrets.token_hex(8), password="demo-pass")
    db.update_event(eid, published=0)
    with Browser() as b:
        open_page(b, gallery, f"/admin/events/{eid}/edit", all_open=True, width=1280, height=900)
        # Desk: badge and button on one line.
        tops = b.eval("[document.querySelector('.pub-head .badge'), document.querySelector('.pub-head .pub-btn')]"
                      ".map(function (e) { var r = e.getBoundingClientRect(); return Math.round(r.top + r.height / 2); })")
        assert abs(tops[0] - tops[1]) <= 6, tops
        b.eval("var n = document.getElementById('name'); n.value = n.value + ' x'; "
               "n.dispatchEvent(new Event('input', {bubbles: true}))")
        b.eval("document.querySelector('.pub-head .pub-btn').click()")
        time.sleep(0.8)
        assert b.eval("!!document.querySelector('.pub-dirty-note')")
        assert b.eval("location.pathname") == f"/admin/events/{eid}/edit"
        assert db.get_event_by_id(eid)["published"] == 0
        b.eval("document.querySelector('.pub-head .pub-btn').click()")
        time.sleep(1.2)
    assert db.get_event_by_id(eid)["published"] == 1
