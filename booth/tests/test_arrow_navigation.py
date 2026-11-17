"""The guest screens work with the arrow keys alone (``controls.arrow_navigation``).

The owner (15.11.2026): "das ganze Menü muss auch per Pfeiltasten bedienbar
sein für die User, aktuell kann man z. B. nicht zwischen den Layouts
wechseln". Until then ArrowRight was a start key (``keys_start`` ships
with it, for presenters), the other arrows did nothing, and only a card
row reached with Tab moved with the arrows.

Now, on by default: the four arrows move the focus to the nearest control
in that direction (``nearestInDirection``, pure, tested here with Node);
in a row of choices (format, layout, background, look) left/right choose
at once and up/down leave the row; Enter/Space press what the arrows
reached, and otherwise stay the trigger; Escape and Backspace go back.
The arrows are never start keys while it is on. Off, every key does what
it did before. tools/admin_phone_check.mjs --kiosk-keys walks a whole run
with the keyboard against a running booth.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from boothcore import config

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "boothcore" / "web" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _nav(expr: str):
    if not NODE:
        pytest.skip("node is not installed")
    start = JS.index("// <spatial-nav>")
    end = JS.index("// </spatial-nav>")
    script = JS[start:end] + f"\nprocess.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


# A start screen: three mode tiles in a row, a gallery button and a language
# button above them, a "back" pill top left.
TILES = [[100, 600, 300, 200], [450, 600, 300, 200], [800, 600, 300, 200]]
GALLERY = [900, 40, 160, 60]
BACK = [20, 20, 140, 60]


def test_left_and_right_walk_along_a_row():
    boxes = TILES
    assert _nav(f"nearestInDirection({TILES[0]}, {boxes}, 'right')") == 1
    assert _nav(f"nearestInDirection({TILES[1]}, {boxes}, 'right')") == 2
    assert _nav(f"nearestInDirection({TILES[1]}, {boxes}, 'left')") == 0
    assert _nav(f"nearestInDirection({TILES[2]}, {boxes}, 'right')") == -1      # the edge: stay


def test_up_goes_to_what_lies_above_and_prefers_what_is_straight_above():
    boxes = TILES + [GALLERY, BACK]
    assert _nav(f"nearestInDirection({TILES[2]}, {boxes}, 'up')") == 3          # gallery sits over the right tile
    assert _nav(f"nearestInDirection({TILES[0]}, {boxes}, 'up')") == 4          # back sits over the left one
    assert _nav(f"nearestInDirection({GALLERY}, {boxes}, 'down')") == 2
    assert _nav(f"nearestInDirection({GALLERY}, {boxes}, 'left')") == 4


def test_a_far_box_straight_ahead_beats_a_near_one_far_off_to_the_side():
    # setup: format row above, layout row below, the go button at the bottom
    fmt = [700, 120, 150, 200]
    layout = [700, 400, 150, 200]
    side = [960, 330, 60, 40]                   # small, nearer, but well to the right
    assert _nav(f"nearestInDirection({fmt}, {[layout, side]}, 'down')") == 0


def test_nothing_that_way_is_minus_one_and_the_box_itself_never_counts():
    assert _nav(f"nearestInDirection({TILES[0]}, {[TILES[0]]}, 'up')") == -1
    assert _nav(f"nearestInDirection({TILES[0]}, {[]}, 'down')") == -1


# ── the setting ───────────────────────────────────────────

def test_on_by_default_on_both_sides_and_in_the_forms():
    assert config.DEFAULT_PROFILE["controls"]["arrow_navigation"] is True
    sys.path.insert(0, str(ROOT.parent))
    from app import ecosystem
    assert ecosystem.DEFAULT_PROFILE["controls"]["arrow_navigation"] is True
    html = (ROOT.parent / "app" / "templates" / "admin" / "booth_profile.html").read_text(encoding="utf-8")
    assert 'name="arrow_navigation"' in html
    settings = (STATIC / "settings.js").read_text(encoding="utf-8")
    assert '"controls.arrow_navigation":' in settings


# ── the kiosk ─────────────────────────────────────────────

def _handler() -> str:
    return JS.split("function arrowKeys(e)")[1].split("\n  }\n")[0]


def test_the_arrows_are_taken_before_the_start_keys_only_when_it_is_on():
    h = _handler()
    assert "!arrowNavOn()" in h
    # a key the operator gave an own task to keeps it
    assert "keys_actions" in h
    assert 'document.addEventListener("keydown", arrowKeys, true)' in JS
    # the old row handler (Tab, then arrows in a card row) bows out when it is on
    rows = JS.split('const card = document.activeElement && document.activeElement.closest && document.activeElement.closest(".lcard");')[1][:200]
    assert "arrowNavOn()" in rows


def test_enter_presses_only_what_the_arrows_reached_else_it_stays_the_trigger():
    act = JS.split("function navActivate()")[1].split("\n  }\n")[0]
    assert "navByArrows" in act and "isMainAction(" in act
    # an arrow focus left behind does not wait for the next guest
    assert "NAV_FORGET_MS" in act


def test_the_focus_is_always_drawn():
    assert re.search(r"\.is-fokus\{outline:6px solid var\(--accent\)", CSS)
    # inside a sideways row the ring is drawn inside the row's padding, not cut off
    assert re.search(r"\.cards \.lcard\.is-fokus[^{]*\{[^}]*outline-offset:0", CSS)


def test_every_screen_has_an_entry_focus():
    entry = JS.split("function entryTarget(")[1].split("\n  }\n")[0]
    for screen in ("start", "setup", "result", "review", "gallery", "error", "consent"):
        assert f'"{screen}"' in entry, screen
