"""Every state of the booth's admin pages has its own way back (owner, 16.11.2026).

The booth runs in its own window: no browser bar, no back button, no tabs. On
the templates page the editor opened below the list and the only way out was
"← Verwaltung" at the top of the page, scrolled away, and out of the editor
there was none at all, after saving or not. Measured before the change with
finger taps at 1920×1200: no way back on screen in the editor, with the
on-screen keyboard open, after saving and after a failed save.

Now a bar at the top sticks (the keyboard docks at the bottom, so it never
covers it) and holds the way back for the state the page is in: to the admin
from the list, to the list from the editor, with "Speichern und zur Liste"
beside it. Unsaved work costs a second tap, never a native dialog. The
switches for "which layouts the guests see" save with one floating button.
"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
PAGE = (WEB / "vorlagen.html").read_text(encoding="utf-8")
JS = (WEB / "static" / "vorlagen-page.js").read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def test_the_bar_with_the_way_back_sticks_at_the_top():
    bar = _rule(PAGE, ".bar")
    assert "position:sticky" in bar and "top:0" in bar
    header = PAGE.split('<header class="bar">')[1].split("</header>")[0]
    assert 'id="zurueck"' in header and 'id="fertig"' in header
    # Finger-sized: at least 64 px high.
    assert re.search(r"\.btn\{[^}]*min-height:(6[4-9]|[7-9]\d)px", PAGE)


def test_the_page_knows_where_back_leads_in_each_state():
    assert 'back_admin: "← Verwaltung"' in JS and 'back_list: "← Zurück zur Liste"' in JS
    assert 'save_back: "Speichern und zur Liste"' in JS
    # The editor's own save does not leave, but says how to.
    assert "saved_stay" in JS and "zurListe(" in JS
    # Leaving with unsaved work asks with a second tap, not with confirm().
    assert "confirm(" not in JS and "zweimal(k, t(\"leave_unsaved\"))" in JS
    assert "zweimal(k, t(\"vis_unsaved\"))" in JS


def test_the_pin_screen_has_a_way_back_too():
    pin = PAGE.split('id="pin"')[1].split("</div>\n\n")[0]
    assert 'href="/admin"' in pin


def test_one_floating_save_bar_for_the_switches_above_the_bottom_edge_and_the_keyboard():
    bar = _rule(PAGE, ".savebar")
    assert "position:fixed" in bar and "bottom:max(72px,10vh)" in bar
    assert "body.osk-open .savebar{bottom:calc(var(--osk-height,0px) + 16px)}" in PAGE
    assert 'id="sichtbarSpeichern"' in PAGE and "/api/admin/templates/visibility" in JS


def test_deleting_asks_twice_and_built_ins_are_only_hidden():
    assert 'zweimal(d, t("del_sure"))' in JS
    assert "l.deletable" in JS and 't("gallery_delete")' in JS
    assert '"Alle sichtbar"' not in JS            # the bulk buttons live in the markup
    for label in ("Alle sichtbar", "Alle ausblenden", "Nur eigene"):
        assert f">{label}</button>" in PAGE
    assert "none_left" in JS                     # never zero: said, not done


def test_nothing_from_a_template_goes_into_the_page_as_markup():
    assert ".innerHTML" not in JS
    assert "textContent" in JS


def test_every_text_exists_in_german_and_english():
    de = JS.split("    de: {")[1].split("\n    },")[0]
    en = JS.split("    en: {")[1].split("\n    },")[0]
    keys = lambda block: set(re.findall(r"(?:^|[\s,])([a-z_0-9]+):\s*\"", block))  # noqa: E731
    assert keys(de) and keys(de) == keys(en)


def test_admin_and_help_keep_their_way_back_in_a_bar_that_sticks():
    admin = (WEB / "admin.html").read_text(encoding="utf-8")
    assert "position:sticky" in _rule(admin, ".topbar")
    top = admin.split('<div class="topbar">')[1].split("</div>\n  <nav")[0]
    assert 'data-href="/"' in top and 'data-href="/admin/vorlagen"' in top
    hilfe = (WEB / "hilfe.html").read_text(encoding="utf-8")
    assert "position:sticky" in _rule(hilfe, ".bar")
    assert "/admin" in hilfe.split('class="bar"')[1].split("</header>")[0] or 'data-href="/admin"' in hilfe
