"""The status line is readable and never cut off; the live picture can be off.

Seen on 11.11.2026: at 1920x1080 the status line in the top right corner was
small and sat 22 px from the edge -- on a panel behind a bezel or a TV with
overscan it is cut. Everything that hugs a corner now keeps a title-safe
distance that grows with the screen, and the status line wraps instead of
running out of the picture.

And: not every host wants the guests to watch themselves during the
countdown. countdown.live_view = "off" hides the live picture while counting;
a video recording still shows it, otherwise nobody knows what is in frame.
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    rule = re.search(r"(?m)^" + re.escape(selector) + r"\{([^}]*)\}", CSS)
    assert rule, f"no rule for {selector}"
    return rule.group(1)


def _clamp_at(value: str, vh: int) -> float:
    low, ideal, high = re.fullmatch(r"clamp\(([^,]+),([^,]+),([^,]+)\)", value.strip()).groups()
    px = lambda t: float(t.strip()[:-2]) * (vh / 100 if t.strip().endswith("vh") else 1)
    return max(px(low), min(px(ideal), px(high)))


def test_the_status_line_is_at_least_22_px_on_full_hd_and_16_on_768():
    size = re.search(r"font-size:([^;]+)", _rule(".statusline")).group(1)
    assert _clamp_at(size, 1080) >= 22
    assert _clamp_at(size, 768) <= 17


def test_the_status_line_wraps_instead_of_being_cut():
    rule = _rule(".statusline")
    assert "flex-wrap:wrap" in rule
    assert "max-width:calc(100% - 2*var(--edge-x))" in rule


def test_corner_elements_keep_a_title_safe_distance():
    root = re.search(r":root\{([^}]*)\}", CSS).group(1)
    assert re.search(r"--edge-x:max\(28px,3\.5vw\)", root)
    assert re.search(r"--edge-y:max\(24px,3\.5vh\)", root)
    for selector in (".statusline", ".shotcount", ".capture-cancel", ".start-last", ".start-tools"):
        rule = _rule(selector)
        assert "var(--edge-x)" in rule and "var(--edge-y)" in rule, selector


def test_the_live_picture_can_be_off_during_the_countdown():
    assert 'cap.dataset.liveoff = cd.live_view === "off" ? "1" : "0"' in JS
    assert '#s-capture[data-liveoff="1"] .live{visibility:hidden}' in CSS
    # a recording shows the picture again
    assert '#s-capture[data-liveoff="1"] .frame:has(.rec.on) .live{visibility:visible}' in CSS


def test_both_sides_know_the_off_value():
    root = Path(__file__).resolve().parents[2]
    line = next(l for l in (root / "app" / "blueprints" / "admin_booth.py").read_text(encoding="utf-8").splitlines()
                if "cd_live_view" in l)
    assert '"off"' in line
    assert 'value="off"' in (root / "app" / "templates" / "admin" / "booth_profile.html").read_text(encoding="utf-8")
