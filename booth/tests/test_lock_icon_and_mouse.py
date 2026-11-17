"""A visible lock instead of a hidden corner, a start screen without status, a mouse that works.

Asked for on 14.11.2026: the start screen should look completely clean for
guests — no status line at all — except for a lock like the one in dslrBooth.
A tap on it asks for the PIN and leads to the operator menu. And the booth
has to be usable with a mouse, which a hidden pointer and a three-second
press on an invisible corner did not allow.
"""
import re
from pathlib import Path
from _pages import page  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
WEB = ROOT / "booth" / "boothcore" / "web"
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


def test_the_profile_knows_the_lock_icon_on_both_sides_and_it_is_off_by_default():
    import sys
    sys.path.insert(0, str(ROOT))
    from boothcore.config import DEFAULT_PROFILE as booth
    from app.ecosystem import DEFAULT_PROFILE as gallery
    assert booth["access"]["lock_icon"] is False
    assert gallery["access"]["lock_icon"] is False
    assert booth["access"] == gallery["access"]


def test_the_corner_button_carries_a_lock_drawing():
    button = re.search(r'<button class="hotspot" id="adminHotspot"[^>]*>(.*?)</button>', HTML, re.S)
    assert button and "<svg" in button.group(1)


def test_the_lock_is_only_drawn_when_switched_on():
    assert ".hotspot svg{display:none}" in CSS
    assert '.hotspot[data-icon="1"] svg{display:block' in CSS
    assert 'hs.dataset.icon = access.lock_icon ? "1" : ""' in JS


def test_one_tap_or_click_on_the_lock_opens_the_menu():
    """No three-second press: a mouse user cannot guess a hidden long press."""
    assert re.search(r'hs\.addEventListener\("click",[^\n]*\n[^\n]*lock_icon[^\n]*openBoothMenu\(\)', JS)


def test_the_lock_is_big_enough_for_a_finger_and_stays_above_the_tool_row():
    rule = re.search(r'#app\[data-corner\] \.hotspot\[data-icon="1"\]\{([^}]*)\}', CSS).group(1)
    assert "width:56px" in rule and "height:56px" in rule
    top = re.search(r'#app\[data-corner="top-right"\] \.hotspot\[data-icon="1"\]\{([^}]*)\}', CSS).group(1)
    assert "top:8px" in top and "right:8px" in top      # ends at 64 px, where the tool row starts


def test_with_the_lock_the_hidden_status_line_leaves_nothing_behind():
    """Status off + lock on: no dot, no line. A notice colours the lock instead."""
    assert "statusline.hidden = lockIcon || !meldungSichtbar" in JS
    assert 'hs.dataset.notice = meldungSichtbar ? "1" : ""' in JS
    assert '.hotspot[data-icon="1"][data-notice="1"]{' in CSS


def test_the_pointer_shows_up_when_a_real_mouse_moves():
    assert 'if (e.pointerType !== "mouse" || (!e.movementX && !e.movementY)) return;' in JS
    assert 'document.body.classList.toggle("no-cursor", c.hide_cursor !== false && !mouseAwake)' in JS


def test_the_gallery_form_offers_the_lock_icon():
    form = (ROOT / "app" / "templates" / "admin" / "booth_profile.html").read_text(encoding="utf-8")
    save = (ROOT / "app" / "blueprints" / "admin_booth.py").read_text(encoding="utf-8")
    assert 'name="lock_icon"' in form
    assert '"lock_icon": bool(f.get("lock_icon"))' in save


def test_the_pin_can_be_typed_on_a_keyboard_at_the_booth_and_in_the_admin():
    """Clicking six digits with a mouse works; typing them did nothing (14.11.2026)."""
    admin = page(WEB / "admin.html")
    assert 'if (/^[0-9]$/.test(e.key)) return pinTaste(Number(e.key));' in admin
    assert 'if (e.key === "Enter") return pinTaste("OK");' in admin
    assert 'if (e.key === "Backspace" || e.key === "Escape") return pinTaste("C");' in admin
    assert 'if (/^[0-9]$/.test(e.key)) { e.preventDefault(); return pinKey(e.key); }' in JS
    assert 'if (e.key === "Enter") { e.preventDefault(); return pinKey("OK"); }' in JS
