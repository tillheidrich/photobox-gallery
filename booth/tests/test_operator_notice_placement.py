"""An operator notice must never cover a guest's button or eat their tap.

Found at an event (11.11.2026): "Das Papier ist rechnerisch alle" sat as a
floating box bottom left of the start screen, right on top of the "Foto"
tile. It hid the label, and a tap on that part of the tile went to the
notice instead of starting a run.

These tests read the stylesheet and the script, because the kiosk has no
build step and this machine runs no browser tests. They prove where the
notice is placed and that its container lets taps through; they do not
measure pixels on a real screen.
"""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


def _rule(css: str, selector: str) -> str:
    start = css.index(selector + "{")
    return css[start:css.index("}", start)]


def test_the_operator_notice_lives_in_the_status_line_not_over_the_mode_tiles():
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")

    notice = _rule(css, ".betriebsmeldung")
    assert "position:absolute" not in notice and "position:fixed" not in notice, \
        "A notice with its own position floats over whatever is underneath"
    assert 'id="betriebsmeldung"' not in html, \
        "The floating box on the start screen is the thing that covered the tile"

    render = js[js.index("function renderStatus"):js.index("function", js.index("function renderStatus") + 10)]
    assert 'class="betriebsmeldung"' in render and "#statusline" in render, \
        "The notice should be rendered into the status line"


def test_the_status_line_never_swallows_a_tap():
    """It shows state; it is not a control. Taps fall through to what is below."""
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    assert "pointer-events:none" in _rule(css, ".statusline")


def test_a_long_notice_title_cannot_push_the_status_line_across_the_screen():
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    title = _rule(css, ".betriebsmeldung b")
    assert "text-overflow:ellipsis" in title and "white-space:nowrap" in title
