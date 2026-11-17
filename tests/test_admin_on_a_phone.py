"""The admin pages on a phone (15.11.2026): what the owner saw at 390 px and
what has to stay true so it does not come back.

He saw the event list with no event name at all, a star over the "Live"
badge and "Bearbeiten" cut off at the right edge, and the print template
page with the file picker and "Einlesen" squeezed into the card header and
cut off. These tests check what can be checked without a browser; the
measurements in a real browser (sideways scrolling, sizes, overlap) come from
tools/admin_phone_check.mjs, see docs/ADMIN-PHONE-CHECK.md.
"""
import re
from pathlib import Path

import pytest

from app import db, ecosystem

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"
ADMIN_CSS = (ROOT / "app" / "static" / "css" / "admin.css").read_text(encoding="utf-8")
PHONE_JS = (ROOT / "app" / "static" / "js" / "admin-phone.js").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
        s["admin_epoch"] = accounts.session_epoch()
        s["lang"] = "de"


def _card(html: str, eid: int) -> str:
    """The event list item of one event (the test data folder is shared)."""
    for chunk in html.split('<div class="event-list-item">')[1:]:
        if f'href="/admin/events/{eid}/edit"' in chunk:
            return chunk
    raise AssertionError("event not in the list")


def _phone_block(css: str) -> str:
    """Every @media (max-width: 768px) block of admin.css, joined."""
    blocks, i = [], 0
    while True:
        i = css.find("@media (max-width: 768px) {", i)
        if i < 0:
            return "\n".join(blocks)
        depth, j = 0, css.index("{", i)
        for k in range(j, len(css)):
            depth += css[k] == "{"
            depth -= css[k] == "}"
            if depth == 0:
                blocks.append(css[j:k])
                i = k
                break


def test_the_event_card_carries_the_name_in_its_heading(client):
    _admin(client)
    eid = db.create_event(name="Sommerfest der Freiwilligen Feuerwehr mit Tag der offenen Tür",
                          slug="sommerfest-mit-einem-sehr-langen-kuerzel-fuer-die-adresse", upload_token="tok-phone-1")
    db.update_event(eid, {"published": 1})
    db.set_event_featured(eid, True)
    html = client.get("/admin/events").get_data(as_text=True)
    card = _card(html, eid)
    heading = re.search(r'<h2 class="event-list-title">(.*?)</h2>', card, flags=re.S).group(1)
    assert "Sommerfest der Freiwilligen Feuerwehr mit Tag der offenen Tür" in heading
    assert f'href="/admin/events/{eid}/edit"' in heading
    # The star sits in the name line, not on top of the badge in the facts line.
    assert "event-list-star" in heading
    meta = re.search(r'<div class="event-list-meta">(.*?)</div>', card, flags=re.S).group(1)
    assert "★" not in meta and "event-list-slug" in meta


def test_the_event_card_has_words_or_a_more_menu_instead_of_bare_icons(client):
    _admin(client)
    eid = db.create_event(name="Hochzeit am Hafen", slug="hochzeit-am-hafen-phone", upload_token="tok-phone-2")
    db.update_event(eid, {"published": 1})
    html = client.get("/admin/events").get_data(as_text=True)
    card = _card(html, eid)
    # Up to the end of "Mehr" (the live/draft switch has a <details> of its own before it).
    actions = card[card.index('<div class="event-list-actions">'):card.rindex('</details>')]
    # The icon-only buttons are desk-only; on a phone the same actions are in "Mehr", with words.
    assert actions.count("adm-desk-only") == 4
    more = actions[actions.index('<details class="adm-more adm-phone-only">'):]
    for words in ("Kiosk-Seite öffnen", "Speicher ansehen", "ervorheben</button>", "Event löschen"):
        assert words in more
    assert '<span class="adm-phone-label">Galerie</span>' in actions
    # The forms in the menu send what the icon buttons send.
    assert more.count('name="csrf_token"') == 2
    assert f'action="/admin/events/{eid}/delete"' in more and f'action="/admin/events/{eid}/toggle-featured"' in more


def test_the_template_import_has_its_own_line_with_a_label(client):
    _admin(client)
    html = client.get("/admin/vorlagen").get_data(as_text=True)
    head = html[html.index('<div class="card-header vl-head">'):]
    head = head[:head.index('<div class="card-body">')]
    form = re.search(r'<form [^>]*class="adm-file vl-import"[^>]*>(.*?)</form>', head, flags=re.S)
    assert form, "the import form is its own block in the header"
    assert re.search(r'<label class="vl-import-label" for="vlPaket">', form.group(1))
    assert re.search(r'<input type="file" id="vlPaket" name="paket"', form.group(1))
    assert "Einlesen" in form.group(1)
    # The title and the import no longer share one flex row.
    actions = re.search(r'<div class="vl-head-actions">(.*?)</div>', head, flags=re.S).group(1)
    assert 'type="file"' not in actions


def test_no_inline_style_in_an_admin_template_is_wider_than_a_phone():
    offenders = []
    files = list((TEMPLATES / "admin").rglob("*.html")) + [TEMPLATES / "layout_admin.html", TEMPLATES / "setup.html"]
    for f in files:
        text = f.read_text(encoding="utf-8")
        for style in re.findall(r'style="([^"]*)"', text):
            for prop, px in re.findall(r'(?<![-\w])((?:min-)?width)\s*:\s*(\d+)px', style):
                if int(px) > 360:
                    offenders.append(f"{f.relative_to(ROOT)}: {prop}:{px}px")
            for track in re.findall(r'grid-template-columns\s*:\s*([^;]*)', style):
                if any(int(px) > 360 for px in re.findall(r'(\d+)px', track)):
                    offenders.append(f"{f.relative_to(ROOT)}: grid {track}")
    assert not offenders, offenders


def test_the_menu_is_an_off_canvas_panel_that_says_where_you_are(client):
    _admin(client)
    html = client.get("/admin/events").get_data(as_text=True)
    toggle = re.search(r'<button type="button" class="sidebar-toggle"[^>]*>', html).group(0)
    assert 'aria-controls="sidebar"' in toggle and 'aria-expanded="false"' in toggle
    assert re.search(r'href="/admin/events" class="nav-item active" aria-current="page"', html)
    assert len(re.findall(r'class="nav-item active" aria-current="page"', html)) == 1
    # Language and theme have words in the menu; the top bar keeps one action.
    prefs = html[html.index('<div class="sidebar-prefs">'):]
    assert "English" in prefs[:1200] and "Dunkle Ansicht" in prefs[:2000]
    assert '<div class="sidebar-backdrop" id="sidebarBackdrop" hidden></div>' in html
    script = html[html.index("// The menu on a phone"):]
    for behaviour in ("Escape", "'Tab'", "inert", "toggle.focus()", "aria-expanded"):
        assert behaviour in script


def test_the_phone_rules_keep_fields_at_16px_and_targets_at_44px():
    phone = _phone_block(ADMIN_CSS)
    assert re.search(r"\.admin-layout select, \.admin-layout textarea \{ font-size: 16px; \}", phone)
    assert re.search(r"\.btn, \.btn-sm, \.btn--sm \{ min-height: 44px; min-width: 44px;", phone)
    assert ".topbar-prefs { display: none; }" in phone
    assert "env(safe-area-inset-bottom)" in phone and "100dvh" in phone
    # Tables become labelled cards; numbers keep three to a line.
    assert ".table--stack td::before" in phone and ".table--nums tr" in phone


def test_the_event_form_has_sections_a_save_bar_and_its_name_first(client):
    _admin(client)
    eid = db.create_event(name="Firmenfeier Nordlicht", slug="firmenfeier-nordlicht", upload_token="tok-phone-3")
    html = client.get(f"/admin/events/{eid}/edit").get_data(as_text=True)
    assert '<h1 class="page-title">Firmenfeier Nordlicht</h1>' in html
    assert 'data-phone-sections="event-edit"' in html
    form = html[html.index('<form method="post" enctype="multipart/form-data" id="eventForm" data-dirty-watch>'):]
    form = form[:form.index("</form>")]
    bar = form[form.index('<div class="adm-savebar"'):]
    assert 'type="submit" class="btn btn--primary" id="saveBtn"' in bar
    assert '<script src="/static/js/admin-phone.js" defer></script>' in html


def test_the_booth_profile_is_searchable_folds_and_sends_the_same_fields(client):
    _admin(client)
    dev = db.create_device(name="Suchbox")
    html = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    form = re.search(r'<form method="post" data-dirty-watch data-phone-sections="booth-profile"[^>]*>', html).group(0)
    assert 'data-section-card=".bp-card"' in form and "data-section-search" in form
    body = html[html.index(form):]
    body = body[:body.index("</form>")]
    assert '<div class="bp-save adm-savebar"><button class="btn btn-primary" type="submit">Speichern</button></div>' in body
    names = set(re.findall(r'\bname="([^"]+)"', body))
    assert len(names) > 200            # the whole profile is still in the one form
    for wanted in ("event_id", "cd_first", "mail_host", "webhook_url", "qr_url"):
        assert wanted in names


def test_the_phone_script_folds_remembers_searches_and_marks_unsaved():
    for piece in ('localStorage.setItem', '"Einstellung suchen"', "Ungespeicherte Änderungen",
                  '"invalid"', "aria-expanded", "IntersectionObserver"):
        assert piece in PHONE_JS


def test_the_template_editor_says_what_works_on_a_phone(client):
    from app.i18n import de
    _admin(client)
    html = client.get("/admin/vorlagen/editor").get_data(as_text=True)
    assert de.STRINGS["editor.small_screen"] in html
    assert "Elemente verschieben, hinzufügen und ausrichten geht an einem größeren Bildschirm" in de.STRINGS["editor.small_screen"]
    assert 'data-zoom="2"' in html and 'id="pbEditorPhone"' in html
    seite = (ROOT / "app" / "static" / "js" / "vorlagen-editor-seite.js").read_text(encoding="utf-8")
    assert "telefonEinrichten" in seite and "ed-sheet" in seite


def test_url_and_mail_fields_bring_the_right_keyboard(client):
    _admin(client)
    html = client.get("/admin/settings/mail").get_data(as_text=True)
    assert re.search(r'name="from_email" inputmode="email" autocomplete="email"', html)
    assert re.search(r'name="host" inputmode="url"', html)
