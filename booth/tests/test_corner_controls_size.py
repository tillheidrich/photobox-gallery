"""The three controls in the corners of the start screen are big enough to hit.

Seen on 15.11.2026 at the real booth (14" touch screen, 1920x1200): "der
Galerie-Button, der EN-Button und das Schloss müssen deutlich größer sein".
The gallery and language pills were 64 px high with 21 px text, the lock a
56 px circle at 42 % opacity with a 26 px drawing — faint on a photo.

``start.corner_size`` (m / l / xl) sizes all three together. ``m`` is the
old look, untouched; ``l`` is the default (owner's decision 15.11.2026):
pills at least 72 px high with at least 22 px text, the lock at least 72 px
with a 36 px drawing on the same pill background, fully opaque. ``xl`` makes
the pills at least 88 px high. In the top-right corner the lock joins the
tool row instead of sitting above it, and the title and the QR code move
down when the row would reach them.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WEB = ROOT / "booth" / "boothcore" / "web"
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
SETTINGS = (WEB / "static" / "settings.js").read_text(encoding="utf-8")


def _rule(selector):
    m = re.search(r"(?m)^" + re.escape(selector) + r"\{([^}]*)\}", CSS)
    assert m, f"no rule for {selector}"
    return dict((k.strip(), v.strip()) for k, v in
                (d.split(":", 1) for d in m.group(1).split(";") if ":" in d))


def _px(value):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)px", value)
    assert m, value
    return float(m.group(1))


def _size_vars(size):
    return _rule(f'#s-start[data-corner-size="{size}"]')


def test_the_default_is_l_on_both_sides():
    sys.path.insert(0, str(ROOT))
    from boothcore.config import DEFAULT_PROFILE as booth
    from app.ecosystem import DEFAULT_PROFILE as gallery
    assert booth["start"]["corner_size"] == "l"
    assert gallery["start"]["corner_size"] == "l"


def test_m_keeps_the_old_sizes_to_the_pixel():
    # The rules of the old look are still there, unchanged ...
    pill = _rule(".pill")
    assert pill["min-height"] == "64px" and pill["padding"] == "0 24px" and pill["font-size"] == "21px"
    lock = _rule('#app[data-corner] .hotspot[data-icon="1"]')
    assert lock["width"] == "56px" and lock["height"] == "56px" and lock["opacity"] == ".42"
    assert '.hotspot[data-icon="1"] svg{display:block;width:26px;height:26px;' in CSS
    assert _rule(".start-tools")["gap"] == "12px"
    # ... and nothing that resizes is ever keyed to "m".
    assert 'data-corner-size="m"' not in CSS
    for selector in re.findall(r"(?m)^([^{}\n]*data-corner-size[^{}\n]*)\{", CSS):
        sizes = set(re.findall(r'data-corner-size="(\w+)"', selector))
        assert sizes and sizes <= {"l", "xl"}, selector


def test_l_and_xl_are_large_enough():
    lv, xv = _size_vars("l"), _size_vars("xl")
    assert _px(lv["--cs-h"]) >= 72 and _px(lv["--cs-font"]) >= 22
    assert _px(xv["--cs-h"]) >= 88 and _px(xv["--cs-font"]) > _px(lv["--cs-font"])
    # The pictures grow with the text: the old gallery icon was 1.2em of 21 px.
    assert _px(lv["--cs-icon"]) > 21 * 1.2 and _px(xv["--cs-icon"]) > _px(lv["--cs-icon"])
    # The lock: a finger-sized target with a drawing one can see.
    assert _px(lv["--cs-lock"]) >= 72 and _px(lv["--cs-lock-icon"]) >= 36
    xl_lock = re.fullmatch(r"clamp\((\d+)px,[\d.]+vw,(\d+)px\)", xv["--cs-lock"])
    assert xl_lock and int(xl_lock.group(1)) >= 72 and int(xl_lock.group(2)) >= 88
    assert _px(xv["--cs-lock-icon"]) > _px(lv["--cs-lock-icon"])
    # "prominent" stays the bigger of the two gallery buttons at every size.
    assert _px(lv["--cs-prom-h"]) > _px(lv["--cs-h"]) and _px(xv["--cs-prom-h"]) > _px(xv["--cs-h"])


def test_l_and_xl_apply_to_all_three_controls():
    sizes = '#s-start:is([data-corner-size="l"],[data-corner-size="xl"])'
    pill = _rule(f"{sizes} .start-tools .pill")
    assert pill["min-height"] == "var(--cs-h)" and pill["font-size"] == "var(--cs-font)"
    assert _rule(f"{sizes} .start-tools .pill svg")["width"] == "var(--cs-icon)"
    prominent = _rule(f'{sizes}[data-gallery-style="prominent"] #galleryBtn')
    assert prominent["min-height"] == "var(--cs-prom-h)"
    lock = _rule(f'#app[data-corner] {sizes} .hotspot[data-icon="1"]')
    assert lock["width"] == "var(--cs-lock)" and lock["height"] == "var(--cs-lock)"
    # Visible on a photo or a video: the pills' own background, not 42 % grey.
    assert lock["opacity"] == "1" and "var(--panel)" in lock["background"]
    assert "backdrop-filter" in lock and "box-shadow" in lock
    assert _rule(f'{sizes} .hotspot[data-icon="1"] svg')["width"] == "var(--cs-lock-icon)"


def test_top_right_the_lock_stands_in_the_tool_row():
    sizes = '#s-start:is([data-corner-size="l"],[data-corner-size="xl"])'
    lock = _rule(f'#app[data-corner="top-right"] {sizes} .hotspot[data-icon="1"]')
    assert lock["top"] == _rule(".start-tools")["top"] and lock["right"] == "var(--edge-x)"
    assert lock["width"] == "var(--cs-row)" and lock["height"] == "var(--cs-row)"
    row = _rule(f'#app[data-corner="top-right"] {sizes}:has(.hotspot[data-icon="1"]) .start-tools')
    assert row["right"] == "calc(var(--edge-x) + var(--cs-row) + var(--cs-gap))"


def test_the_title_and_the_qr_code_keep_clear_of_the_bigger_row():
    sizes = '#s-start:is([data-corner-size="l"],[data-corner-size="xl"])'
    assert "var(--cs-row-bottom)" in _rule(f"{sizes} .start-copy")["top"]
    assert "var(--cs-row-bottom)" in _rule(f'{sizes}[data-last="1"] .start-copy')["top"]
    assert "var(--cs-row-bottom)" in _rule(f"{sizes} .start-qr")["top"]


def test_the_kiosk_reads_the_setting_and_falls_back_to_l():
    assert 'st.dataset.cornerSize = ["m", "xl"].includes(p.start.corner_size) ? p.start.corner_size : "l";' in JS


def test_the_booth_admin_offers_the_same_values_with_a_german_label():
    assert '"start.corner_size": ["m", "l", "xl"]' in SETTINGS
    assert '"start.corner_size": "' in SETTINGS
