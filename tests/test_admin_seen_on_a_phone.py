"""What a look at the admin on a phone and in the dark theme found (UI review, 12.11.2026).

* The closed side menu still cast its shadow past the left screen edge: a
  grey band down every admin page on a phone.
* The template editor ran off the card on a phone without a word; it now
  says honestly that editing needs a computer, and what is there scrolls.
* Warnings stayed a cream box on the dark dashboard.
"""
import re
from pathlib import Path

import pytest

from app import db, ecosystem
from app.i18n import de, en

ROOT = Path(__file__).resolve().parent.parent
ADMIN_CSS = ROOT / "app" / "static" / "css" / "admin.css"
DESIGN_CSS = ROOT / "app" / "static" / "css" / "design.css"


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _admin(client, lang="de"):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
        s["admin_epoch"] = accounts.session_epoch()
        s["lang"] = lang


def test_the_closed_side_menu_casts_no_shadow_onto_the_page():
    css = ADMIN_CSS.read_text(encoding="utf-8")
    phone = re.search(r"@media \(max-width: 768px\) \{(.*?)\n\}", css, flags=re.S).group(1)
    closed = re.search(r"\.admin-sidebar \{(.*?)\}", phone, flags=re.S).group(1)
    assert "box-shadow" not in closed
    assert re.search(r"\.admin-sidebar\.open \{[^}]*box-shadow", phone)


@pytest.mark.parametrize("lang,strings", [("de", de.STRINGS), ("en", en.STRINGS)])
def test_the_template_editor_says_on_a_phone_that_editing_needs_a_computer(client, lang, strings):
    _admin(client, lang)
    html = client.get("/admin/vorlagen/editor").get_data(as_text=True)
    assert strings["editor.small_screen"] in html
    assert re.search(r"@media \(max-width: 768px\) \{[^@]*\.editor-small-screen \{ display: block; \}", html)
    assert "#editor { overflow-x: auto; }" in html


def test_warnings_follow_the_dark_theme():
    css = DESIGN_CSS.read_text(encoding="utf-8")
    last = css.rsplit(".alert--warning, .alert-warning {", 1)[1].split("}", 1)[0]
    assert "var(--pb-warn-bg)" in last


@pytest.mark.parametrize("lang,strings", [("de", de.STRINGS), ("en", en.STRINGS)])
def test_the_booth_profile_has_a_jump_list_built_from_its_cards(client, lang, strings):
    _admin(client, lang)
    dev = db.create_device(name="Sprungbox")
    html = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert f'<nav class="bp-jump" id="bpJump" aria-label="{strings["booth.jump"]}" hidden></nav>' in html
    assert 'document.querySelectorAll(".bp-card > h2")' in html
    assert len(re.findall(r'class="bp-card[^"]*"[^>]*>\s*<h2', html)) >= 20     # what the list is built from


def test_the_smile_text_placeholder_carries_no_emoji(client):
    _admin(client)
    dev = db.create_device(name="Smilebox")
    html = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    field = re.search(r'name="cd_smile_text"[^>]*>', html).group(0)
    assert re.search(r'placeholder="[^"]*"', field)
    assert not re.search(r"[\U0001F300-\U0001FAFF]", field)
