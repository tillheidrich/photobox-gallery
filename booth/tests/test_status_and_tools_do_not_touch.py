"""The status line and the buttons below it keep their distance.

Till, 14.11.2026, with a photo of the corner: "Achte auf Abstände von
Buttons" — the status pill sat three pixels above "Galerie" and "EN".
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "boothcore" / "web" / "static" / "app.css").read_text(encoding="utf-8")


def _clamp(expr: str, vh: float) -> float:
    lo, mid, hi = re.search(r"clamp\(([\d.]+)px,([\d.]+)vh,([\d.]+)px\)", expr).groups()
    return max(float(lo), min(float(mid) * vh / 100, float(hi)))


def test_there_is_air_between_the_status_line_and_the_tool_row():
    status = re.search(r"\.statusline\{([^}]*)\}", CSS).group(1)
    tools = re.search(r"\.start-tools\{([^}]*)\}", CSS).group(1)
    pad = float(re.search(r"padding:(\d+)px", status).group(1))
    for height in (720, 800, 1080, 1440, 2160):
        line = _clamp(re.search(r"font-size:([^;]+)", status).group(1), height) * 1.5 + 2 * pad
        offset = _clamp(re.search(r"top:calc\(var\(--edge-y\) \+ ([^;]+)\)", tools).group(1), height)
        assert offset - line >= 12, f"{height}px high: only {offset - line:.0f}px between them"


def test_mode_tiles_line_up_their_icons_and_titles():
    """Seen at 1280x800 on 14.11.2026: icon and title sat at a different height
    in each tile, because a button centres its content and one description wrapped."""
    assert ".mode{display:flex;flex-direction:column;align-items:flex-start;justify-content:flex-start}" in CSS


def test_the_start_button_is_as_wide_as_the_list_above_it():
    scroll = re.search(r"\.setup-scroll\{([^}]*)\}", CSS).group(1)
    assert "padding-right:4px;margin-right:-4px" in scroll, "room for the scrollbar must not narrow the list"


def test_scrollbars_wear_the_theme():
    assert "#app *{scrollbar-color:" in CSS


def test_the_toast_is_readable_in_the_light_theme_too():
    """Light theme, 14.11.2026: "Wird gedruckt …" stood dark on a dark pill —
    the pill had a fixed colour, the text the colour of the theme."""
    toast = re.search(r"\.toast\{([^}]*)\}", CSS).group(1)
    assert "background:var(--text)" in toast and "color:var(--bg)" in toast
