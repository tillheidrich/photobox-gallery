"""The search over the long settings forms (box settings, event form).

The owner called the settings "endless". The booth's own settings page has a
search field; the gallery pages get the same one, from one partial and one
script. The rule that matters most: a setting the filter hides is still in its
form and is still sent when the form is saved.
"""
from __future__ import annotations

import json
import re
import secrets
import socket
import threading
import time
from pathlib import Path

import pytest

from app import db, ecosystem
from tests.chrome_cdp import CHROME, Browser

ROOT = Path(__file__).resolve().parent.parent
JS_FULL = (ROOT / "app" / "static" / "js" / "settings-search.js").read_text(encoding="utf-8")
#: The code without its comments, so the rules below test what runs, not what is said.
JS = "\n".join(l for l in JS_FULL.splitlines() if not l.lstrip().startswith("//"))
PARTIAL = (ROOT / "app" / "templates" / "admin" / "_settings_search.html").read_text(encoding="utf-8")


def _admin(client):
    client.get("/healthz")
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


# ---- what the pages carry ---------------------------------------------------

def test_the_box_settings_page_has_the_search_bar_outside_the_form(client):
    _admin(client)
    dev = db.create_device(name="Suchbox")
    html = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert html.count('class="ss-bar"') == 1
    assert '<script src="/static/js/settings-search.js" defer></script>' in html
    bar = html[html.index('class="ss-bar"'):]
    bar = bar[:bar.index("</div>\n<script")]
    # A label that belongs to the field, a live region for the count, no name.
    assert '<label class="ss-label" for="ssInput">Einstellung suchen</label>' in bar
    assert 'id="ssInput"' in bar and ' name=' not in bar
    assert 'role="status" aria-live="polite"' in bar
    assert 'placeholder="Suchen, z. B. Countdown, Druck, Schloss"' in bar
    assert html.index('class="ss-bar"') < html.index('<form method="post" data-dirty-watch data-phone-sections="booth-profile"')
    assert 'data-ss-scope="#bpForm"' in bar and 'id="bpForm"' in html
    # The anchors and the jump list that were there stay.
    assert '<nav class="bp-jump" id="bpJump"' in html
    assert 'data-section-card=".bp-card"' in html


def test_the_event_form_has_the_bar_and_chips(client):
    _admin(client)
    eid = db.create_event(name="Suchfest", slug="suchfest", upload_token="tok-suche-1")
    html = client.get(f"/admin/events/{eid}/edit").get_data(as_text=True)
    assert html.count('class="ss-bar"') == 1
    assert 'data-ss-chips="1"' in html
    assert html.index('class="ss-bar"') < html.index('id="eventForm"')


def test_the_bar_speaks_both_languages():
    from app.i18n import de, en
    for key in ("label", "placeholder", "clear", "one", "many", "none", "try", "reset"):
        k = f"search.settings.{key}"
        assert de.STRINGS[k] and en.STRINGS[k] and de.STRINGS[k] != en.STRINGS[k]
    assert de.STRINGS["search.settings.many"] == "{n} Treffer"
    assert "{n}" in en.STRINGS["search.settings.many"]


def test_the_script_only_hides_with_a_class():
    # Nothing a filter does may take a setting out of what the form sends.
    assert "disabled" not in JS
    assert ".removeChild(" not in JS and "insertBefore(" not in JS and "appendChild(el" not in JS
    assert "classList.add(\"ss-miss\")" in JS
    css = (ROOT / "app" / "static" / "css" / "admin.css").read_text(encoding="utf-8")
    assert ".ss-active .ss-miss { display: none !important; }" in css
    assert "[type=hidden]" in JS                    # hidden inputs are never "settings"


def test_the_script_folds_umlauts_and_escapes():
    for piece in ('"ä") return "ae"', '"ö") return "oe"', '"ü") return "ue"', '"ß") return "ss"',
                  'e.key === "Escape"', "terms.every"):
        assert piece in JS


def test_the_phone_search_steps_aside_for_the_shared_bar():
    phone = (ROOT / "app" / "static" / "js" / "admin-phone.js").read_text(encoding="utf-8")
    assert '!document.querySelector(".ss-bar")' in phone


def test_pages_that_are_short_do_not_get_the_bar(client):
    _admin(client)
    for path in ("/admin/settings", "/admin/settings/galerie", "/admin/settings/qr", "/admin/settings/tracking"):
        assert 'class="ss-bar"' not in client.get(path).get_data(as_text=True), path


def test_the_homepage_settings_page_has_the_bar(client):
    _admin(client)
    html = client.get("/admin/settings/homepage").get_data(as_text=True)
    assert html.count('class="ss-bar"') == 1 and 'data-ss-scope="#homepageForm"' in html
    assert html.index('class="ss-bar"') < html.index('id="homepageForm"')


def test_the_partial_marks_the_bar_as_not_part_of_the_form():
    assert "data-not-dirty" in PARTIAL and 'role="search"' in PARTIAL


# ---- in a real browser ------------------------------------------------------

browser = pytest.mark.skipif(CHROME is None, reason="no Chrome on this machine")


@pytest.fixture(scope="module")
def gallery():
    from werkzeug.serving import make_server

    from app import accounts, create_app

    app = create_app()
    app.config.update(TESTING=True)
    c = app.test_client()
    c.get("/healthz")
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
    eid = db.create_event(name="Suchfest", slug="suche-" + secrets.token_hex(3), upload_token=secrets.token_hex(8))
    dev = db.create_device(name="Suchbox")
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
        yield {"base": f"http://127.0.0.1:{port}", "eid": eid, "dev": dev["id"],
               "cookie": (app.config.get("SESSION_COOKIE_NAME") or "session", cookie)}
    finally:
        server.shutdown()


def _open(b, g, path, width=1280, height=900):
    b.viewport(width, height, mobile=width < 769)
    b.cookie(g["base"] + "/", *g["cookie"])
    b.goto(g["base"] + path, settle=1.2)


def _type(b, text):
    b.eval("(function(){var i=document.getElementById('ssInput');i.focus();i.value=%s;"
           "i.dispatchEvent(new Event('input',{bubbles:true}));})()" % json.dumps(text))
    time.sleep(0.6)


def _posted(b, form_sel):
    """What the form would send: every named control with its value, as the browser builds it."""
    return b.eval("""(function(){var f=document.querySelector(%s);
        return JSON.stringify(Array.from(new FormData(f).entries()).filter(function(e){return typeof e[1]==='string';}));})()""" % json.dumps(form_sel))


VISIBLE = """Array.from(document.querySelectorAll(%s + ' input:not([type=hidden]), ' + %s + ' select, ' + %s + ' textarea'))
    .filter(function(e){return !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);}).length"""


def _visible_controls(b, scope):
    return b.eval(VISIBLE % ((json.dumps(scope),) * 3))


@browser
def test_typing_countdown_shows_only_the_matching_settings_and_counts_them(gallery):
    with Browser() as b:
        _open(b, gallery, f"/admin/devices/{gallery['dev']}/booth")
        all_visible = _visible_controls(b, "#bpForm")
        assert all_visible > 100
        ids_before = b.eval("JSON.stringify(Array.from(document.querySelectorAll('[id]')).map(function(e){return e.id;}).sort())")
        _type(b, "countdown")
        shown = _visible_controls(b, "#bpForm")
        assert 0 < shown < all_visible / 4, (shown, all_visible)
        marks = b.eval("JSON.stringify(Array.from(document.querySelectorAll('mark.ss-mark')).map(function(m){return m.textContent.toLowerCase();}))")
        assert marks != "[]" and set(json.loads(marks)) == {"countdown"}
        count = b.eval("document.getElementById('ssCount').textContent")
        assert re.fullmatch(r"\d+ Treffer|1 Treffer", count), count
        # Every setting that stays says "countdown" itself, or sits under a heading that does.
        stray = b.eval("""Array.from(document.querySelectorAll('#bpForm input:not([type=hidden]), #bpForm select, #bpForm textarea'))
            .filter(function(e){return !!(e.offsetWidth || e.getClientRects().length);})
            .filter(function(e){var c=e.closest('.bp-card'); var t=((c&&c.textContent)||'').toLowerCase();
                return t.indexOf('countdown')<0 && (e.name||'').toLowerCase().indexOf('countdown')<0;}).length""")
        assert stray == 0
        # The section anchors and ids are the same with a filter on.
        ids_after = b.eval("JSON.stringify(Array.from(document.querySelectorAll('[id]')).map(function(e){return e.id;}).sort())")
        assert ids_after == ids_before
        # Clear: everything is back, no marks, no count.
        b.eval("document.querySelector('.ss-clear').click()")
        time.sleep(0.4)
        assert _visible_controls(b, "#bpForm") == all_visible
        assert b.eval("document.querySelectorAll('.ss-miss, mark.ss-mark').length") == 0
        assert b.eval("document.getElementById('ssCount').textContent") == ""
        assert b.eval("document.activeElement.id") == "ssInput"


@browser
def test_umlauts_case_and_several_words(gallery):
    with Browser() as b:
        _open(b, gallery, f"/admin/devices/{gallery['dev']}/booth")
        _type(b, "GROESSE")
        a = b.eval("document.getElementById('ssCount').textContent")
        _type(b, "Größe")
        assert b.eval("document.getElementById('ssCount').textContent") == a and a != "Nichts gefunden."
        _type(b, "countdown")
        one = int(b.eval("document.getElementById('ssCount').textContent").split()[0])
        _type(b, "countdown vorschau")
        both = int(b.eval("document.getElementById('ssCount').textContent").split()[0])
        assert 0 < both <= one
        # A second word that is nowhere gives nothing: AND, not OR.
        _type(b, "countdown qqqxyz")
        assert b.eval("document.getElementById('ssCount').textContent") == "Nichts gefunden."


@browser
def test_nothing_found_says_so_and_offers_to_clear_and_escape_restores(gallery):
    with Browser() as b:
        _open(b, gallery, f"/admin/devices/{gallery['dev']}/booth")
        total = _visible_controls(b, "#bpForm")
        _type(b, "qqqxyz")
        assert b.eval("document.getElementById('ssCount').textContent") == "Nichts gefunden."
        assert b.eval("!document.querySelector('.ss-empty').hidden")
        assert _visible_controls(b, "#bpForm") == 0
        b.eval("document.querySelector('.ss-reset').click()")
        time.sleep(0.4)
        assert _visible_controls(b, "#bpForm") == total
        assert b.eval("document.querySelector('.ss-empty').hidden")
        # Escape in the field does the same.
        _type(b, "countdown")
        b.eval("document.getElementById('ssInput').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        time.sleep(0.4)
        assert b.eval("document.getElementById('ssInput').value") == ""
        assert _visible_controls(b, "#bpForm") == total


@browser
def test_saving_with_a_filter_on_sends_every_field_unchanged(gallery):
    with Browser() as b:
        _open(b, gallery, f"/admin/devices/{gallery['dev']}/booth")
        without = _posted(b, "#bpForm")
        assert len(json.loads(without)) > 200
        _type(b, "countdown")
        assert _visible_controls(b, "#bpForm") < 60
        assert _posted(b, "#bpForm") == without
        # The real submit, too: catch the submit event, look at what it carries, stop it.
        got = b.eval("""(function(){var f=document.querySelector('#bpForm'),out=null;
            f.addEventListener('submit',function(e){e.preventDefault();
              out=JSON.stringify(Array.from(new FormData(f).entries()).filter(function(x){return typeof x[1]==='string';}));},{once:true});
            f.requestSubmit(); return out;})()""")
        assert got == without
        # And no control was taken out of the form on the way.
        assert b.eval("document.querySelectorAll('#bpForm input, #bpForm select, #bpForm textarea').length") > 250


@browser
def test_the_bar_is_sticky_big_enough_and_reachable_by_keyboard(gallery):
    with Browser() as b:
        _open(b, gallery, f"/admin/devices/{gallery['dev']}/booth")
        b.eval("window.scrollTo({top: 2500, behavior: 'instant'})")
        time.sleep(0.4)
        top = b.eval("Math.round(document.querySelector('.ss-bar').getBoundingClientRect().top)")
        bar = b.eval("Math.round(document.querySelector('.admin-topbar').getBoundingClientRect().bottom)")
        assert abs(top - bar) <= 4, (top, bar)
        assert b.eval("document.getElementById('ssInput').getBoundingClientRect().height") >= 44
        assert b.eval("document.querySelector('label[for=ssInput]') !== null")
        _type(b, "countdown")
        assert b.eval("document.querySelector('.ss-clear').getBoundingClientRect().height") >= 44
        assert b.eval("document.querySelector('.ss-clear').tabIndex") == 0


@browser
def test_the_event_form_searches_too_and_still_sends_everything(gallery):
    with Browser() as b:
        _open(b, gallery, f"/admin/events/{gallery['eid']}/edit")
        without = _posted(b, "#eventForm")
        assert len(json.loads(without)) > 30
        # The chips are there, built from the headings.
        assert b.eval("document.querySelectorAll('.ss-chips a').length") >= 5
        total = _visible_controls(b, "#eventForm")
        _type(b, "passwort")
        shown = _visible_controls(b, "#eventForm")
        assert 0 < shown < total
        assert _posted(b, "#eventForm") == without
        # A hidden required field must not trap the save: the filter lets go.
        b.eval("document.getElementById('name').value=''")
        b.eval("document.getElementById('eventForm').requestSubmit()")
        time.sleep(0.6)
        assert b.eval("document.getElementById('ssInput').value") == ""
        assert _visible_controls(b, "#eventForm") == total


@browser
def test_on_a_phone_the_same_bar_opens_folded_sections_and_folds_them_back(gallery):
    with Browser() as b:
        _open(b, gallery, f"/admin/devices/{gallery['dev']}/booth", width=390, height=844)
        folded = b.eval("document.querySelectorAll('.adm-sec.is-collapsed').length")
        assert folded > 5 and b.eval("document.querySelectorAll('#admSearch').length") == 0
        _type(b, "countdown")
        assert b.eval("document.getElementById('ssCount').textContent").endswith("Treffer")
        shown = b.eval("Array.from(document.querySelectorAll('#bpForm .bp-card')).filter(function(c){return c.offsetHeight>0;}).length")
        assert shown >= 1
        assert b.eval("document.querySelectorAll('.adm-sec.is-collapsed:not(.ss-miss)').length") == 0
        b.eval("document.querySelector('.ss-clear').click()")
        time.sleep(0.4)
        assert b.eval("document.querySelectorAll('.adm-sec.is-collapsed').length") == folded


@browser
def test_the_homepage_settings_filter_and_still_send_everything(gallery):
    with Browser() as b:
        _open(b, gallery, "/admin/settings/homepage")
        without = _posted(b, "#homepageForm")
        total = _visible_controls(b, "#homepageForm")
        _type(b, "stiller")
        shown = _visible_controls(b, "#homepageForm")
        assert 0 < shown < total
        assert _posted(b, "#homepageForm") == without
        b.eval("document.querySelector('.ss-clear').click()")
        time.sleep(0.3)
        assert _visible_controls(b, "#homepageForm") == total
