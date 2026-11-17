"""Every screen of the run can have its own backdrop.

dslrBooth and PSPhotoBox let the operator dress the booth: a picture or a
colour behind setup, countdown and result, not only on the start screen.
Here each of setup, capture and result takes a picture from the booth's media
or a colour, plus a dim in percent so text on top stays readable. Empty means
what it always was: the theme background. The start screen already had its
pictures (``start.media``); it gets the colour and the dim.

During live view and countdown the camera picture stays in front; the
backdrop only fills the space around it.
"""
from pathlib import Path

from boothcore import config

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")


def test_a_box_that_sets_nothing_looks_as_before():
    b = config.DEFAULT_PROFILE["backdrops"]
    assert set(b) == {"setup", "capture", "result"}
    for screen in b.values():
        assert screen == {"image": "", "color": "", "dim": 0}
    start = config.DEFAULT_PROFILE["start"]
    assert start["bg_color"] == ""
    assert start["dim"] == -1, "-1 keeps the old gradient over the start picture"


def test_an_unknown_value_from_an_old_server_falls_back():
    merged = config._merge(config.DEFAULT_PROFILE, {"backdrops": {"setup": {"image": "a.jpg", "dim": "x"}}})
    assert merged["backdrops"]["setup"] == {"image": "a.jpg", "color": "", "dim": 0}
    assert merged["backdrops"]["result"]["image"] == ""


def test_the_kiosk_paints_each_screen_from_the_profile():
    assert "function applyBackdrops()" in JS
    assert 'const BACKDROP_SCREENS = ["setup", "capture", "result"];' in JS
    assert "applyBackdrops();" in JS.split("async function refresh()")[1].split("renderStatus();")[0], \
        "a new profile must repaint the backdrops at once"
    assert "--backdrop-image" in JS and "--backdrop-dim" in JS


def test_the_dim_has_a_ceiling():
    """At 100 % the picture would be gone; 80 % is the same ceiling as the attract screen."""
    assert "Math.min(80, Number(c.dim) || 0)" in JS


def test_the_backdrop_lies_behind_everything_on_its_screen():
    assert '.screen[data-backdrop="1"]{' in CSS
    assert '.screen[data-backdrop="1"]::before{' in CSS
    rule = CSS.split('.screen[data-backdrop="1"]::before{')[1].split("}")[0]
    assert "pointer-events:none" in rule, "the dim must never take a tap"


def test_the_camera_picture_stays_in_front():
    """The full-screen countdown screen takes the backdrop; the live frame covers it."""
    assert '#s-capture[data-backdrop="1"] .countdown-full{' in CSS
    assert ".countdown-full b{position:relative" in CSS
    # live view in full screen is a fixed layer above the screen's own background
    assert '#s-capture[data-live="fullscreen"] .frame--big{position:fixed' in CSS


def test_the_start_screen_can_be_a_colour_and_a_flat_dim():
    assert "st.bg_color" in JS or "start.bg_color" in JS
    assert '#s-start[data-shade="flat"] .start-shade{' in CSS
