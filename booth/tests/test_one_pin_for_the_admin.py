"""From the booth's menu into the admin with one PIN entry, not two.

Seen on 14.11.2026: the kiosk checked the PIN, stored it for the admin page —
and the admin page never looked for it and showed its own PIN pad. What is
handed over now is the sign-in the kiosk already got, not the PIN itself.
"""
from pathlib import Path
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
ADMIN = page(WEB / "admin.html")


def test_the_kiosk_hands_over_the_sign_in_not_the_pin():
    assert "booth-pin" not in JS, "a PIN has no business in the browser's storage"
    assert 'sessionStorage.setItem("booth-admin"' in JS


def test_the_admin_takes_it_and_forgets_it():
    assert 'sessionStorage.getItem("booth-admin")' in ADMIN
    assert 'sessionStorage.removeItem("booth-admin")' in ADMIN


# ── The admin page is one very long sheet: it needs a way around ──────

def test_the_admin_has_a_jump_bar_that_stays_in_view():
    assert 'id="jump"' in ADMIN
    assert ".topbar{position:sticky" in ADMIN
    assert 'querySelectorAll("#main .card > h2")' in ADMIN


def test_the_action_buttons_stand_in_two_even_columns():
    assert ".card .actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))" in ADMIN


def test_the_page_scrolls_as_one_so_the_bar_can_stick():
    """With html and body both scrolling, the bar scrolled away (seen in the browser)."""
    assert "body{overflow:visible}" in ADMIN
