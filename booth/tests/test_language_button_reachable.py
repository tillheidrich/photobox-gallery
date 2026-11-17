"""The language button on the start screen can be tapped.

The invisible admin corner sits top right by default (layout.corner) and was
110x110 px. The tool row with "EN" starts 64 px from the top at the right
edge, so the corner lay over the language button: a guest tapping "EN" held
the admin corner instead. In the top-right corner the hotspot is now wide and
flat and ends above the tool row; it still covers the corner (the operator
holds it for a few seconds), and the other three corners are unchanged.
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")


def _rule(selector):
    m = re.search(r"(?m)^" + re.escape(selector) + r"\{([^}]*)\}", CSS)
    assert m, f"no rule for {selector}"
    return dict((k.strip(), v.strip()) for k, v in
                (d.split(":", 1) for d in m.group(1).split(";") if ":" in d))


def _px(v):
    # The tool row sits below the status line, a title-safe distance from the
    # top: calc(var(--edge-y) + clamp(A, .., ..)). Its smallest value counts:
    # --edge-y is never below 24 px.
    m = re.fullmatch(r"calc\(var\(--edge-y\) \+ clamp\((\d+)px,[^)]*\)\)", v)
    if m:
        return 24 + float(m.group(1))
    assert v.endswith("px"), v
    return float(v[:-2])


def _hotspot(corner):
    box = _rule(".hotspot")
    try:
        box.update(_rule(f'#app[data-corner="{corner}"] .hotspot'))
    except AssertionError:
        pass
    return box


def test_the_top_right_corner_ends_above_the_tool_row():
    spot = _hotspot("top-right")
    tools = _rule(".start-tools")
    assert spot["top"] == "0" and spot["right"] == "0"
    assert _px(spot["height"]) <= _px(tools["top"]) - 4, "hotspot must end above the language button"


def test_the_corner_is_still_easy_to_hold():
    spot = _hotspot("top-right")
    assert _px(spot["height"]) >= 48 and _px(spot["width"]) * _px(spot["height"]) >= 110 * 56


def test_the_other_corners_keep_their_size():
    base = _rule(".hotspot")
    assert base["width"] == "110px" and base["height"] == "110px"
    for corner in ("bottom-left", "bottom-right"):
        assert "height" not in _rule(f'#app[data-corner="{corner}"] .hotspot')


def test_the_hotspot_and_the_language_button_are_both_there():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="adminHotspot"' in html and 'id="langBtn"' in html
