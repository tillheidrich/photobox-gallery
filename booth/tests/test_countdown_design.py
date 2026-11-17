"""A countdown that looks designed.

dslrBooth draws the number inside an arc that closes as time runs out;
PSPhotoBox has eight animations. Ours was a bare number. Now the operator
picks the style (``number`` as before, or ``ring``: the number inside a ring
that runs out over the whole countdown), a colour for number and ring (empty =
number as before, ring in the main colour) and a size (m, l, xl; l is today's
size). Pure CSS and SVG, no library. With "reduce motion" the ring steps once
a second instead of gliding.
"""
import re
from pathlib import Path

from boothcore import config

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")


def _countdown_fn() -> str:
    return JS.split("async function countdown(seconds)")[1].split("\n  }\n")[0]


def test_a_box_that_sets_nothing_counts_as_before():
    cd = config.DEFAULT_PROFILE["countdown"]
    assert cd["display"] == "number"
    assert cd["color"] == ""
    assert cd["size"] == "l"


def test_large_is_todays_size():
    """No rule for "l": the sizes that were there stay the sizes of "l"."""
    assert ".countdown{position:absolute;inset:0;display:grid;place-items:center;font-size:min(42vh,420px)" in CSS
    assert 'data-size="l"' not in CSS


def test_medium_and_extra_large_scale_both_countdowns():
    for sel in ('.countdown[data-size="m"]', '.countdown[data-size="xl"]',
                '.countdown-full[data-size="m"]', '.countdown-full[data-size="xl"]'):
        assert sel in CSS, sel


def test_the_extra_large_number_still_fits_a_full_hd_screen():
    size = re.search(r'\.countdown-full\[data-size="xl"\]\{font-size:min\((\d+)vh', CSS)
    assert size and int(size.group(1)) <= 80


def test_the_ring_runs_out_over_the_whole_countdown():
    body = _countdown_fn()
    assert 'display === "ring"' in body
    assert 'pathLength="100"' in body, "the arc is measured in percent of the countdown"
    # each digit the arc loses its share of the whole time (whole digits since
    # 15.11.2026, a fractional GIF gap goes to the first one: countdownSteps)
    assert "100 * done / total" in body, "each second the arc loses its share"
    assert ".cd-arc{" in CSS and "transition:stroke-dashoffset 1s linear" in CSS


def test_reduced_motion_steps_instead_of_gliding():
    assert re.search(r"@media \(prefers-reduced-motion:\s*reduce\)\{[^}]*\.cd-arc\{transition:none", CSS)


def test_the_colour_is_the_operators_or_the_main_colour():
    body = _countdown_fn()
    assert "--cd-color" in body
    assert "stroke:var(--cd-color,var(--accent))" in CSS
    assert '[data-color="1"]' in CSS, "an own colour applies to the number as well"
