"""Touch screen and mouse buzzer act alike — and one press counts once.

Till wants both ways in: a guest who cannot reach the buzzer taps the screen.
With the buzzer on (``controls.mouse_start``) the rule is:

* a press ON a control does what the control does — "Collage" picks the
  collage, finger or mouse. A real mouse press with ``mouse_swallow`` on stays
  the buzzer, as before: the pointer of a buzzer sits wherever it was left.
* a press on free space triggers like the buzzer. For a finger only on start
  and setup — the screens where a tap on the background already meant "go".
  On the result screen (with "delete"), in review and in the gallery a finger
  that misses a button does nothing, as before.
* a touch screen reports every tap a second time as a left click. That echo
  is not a second press. Until 11.11.2026 it counted as the buzzer: measured
  in headless Chrome at 1920x1080, a finger on "Zurück" in setup started a
  recording and left a photo in the gallery.

With the buzzer off nothing changes.

The decision is a pure function in the kiosk script, between the
``<press-rule>`` markers; these tests run that very code with Node. Without
Node on the machine they are skipped, the wiring tests below still run.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _rule_source() -> str:
    start = JS.index("// <press-rule>")
    end = JS.index("// </press-rule>")
    return JS[start:end]


def decide(**press):
    if not NODE:
        pytest.skip("node is not installed")
    script = _rule_source() + f"\nprocess.stdout.write(JSON.stringify(pressRule({json.dumps(press)})));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)["act"]


BUZZER = {"mouseStart": "left", "swallow": True, "overlay": False}


@pytest.mark.parametrize("screen", ["start", "setup"])
def test_a_finger_on_free_space_triggers_like_the_buzzer(screen):
    assert decide(**BUZZER, touch=True, onControl=False, screen=screen) == "trigger"


@pytest.mark.parametrize("screen", ["start", "setup"])
def test_a_finger_on_collage_picks_collage_and_does_not_start_the_default_mode(screen):
    assert decide(**BUZZER, touch=True, onControl=True, screen=screen) == "none"


@pytest.mark.parametrize("screen", ["result", "review", "gallery", "error", "capture", "processing"])
def test_a_finger_that_misses_a_button_elsewhere_does_nothing(screen):
    """On the result screen a near miss next to "delete" must not send anyone home."""
    assert decide(**BUZZER, touch=True, onControl=False, screen=screen) == "none"


def test_with_the_buzzer_off_a_finger_triggers_nothing_new():
    for on_control in (True, False):
        assert decide(mouseStart="off", touch=True, onControl=on_control, screen="setup") == "none"


def test_with_the_buzzer_off_a_mouse_is_just_a_mouse():
    assert decide(mouseStart="off", touch=False, button=0, onControl=False, screen="start") == "none"


def test_the_mouse_buzzer_with_swallow_on_triggers_wherever_the_pointer_is():
    """Unchanged: nobody aims a buzzer, so the button under the pointer is not meant."""
    for on_control in (True, False):
        assert decide(**BUZZER, touch=False, button=0, onControl=on_control, screen="result") == "trigger"


def test_with_swallow_off_a_mouse_click_on_a_button_is_the_buttons():
    assert decide(mouseStart="left", swallow=False, overlay=False, touch=False, button=0,
                  onControl=True, screen="start") == "none"


def test_with_swallow_off_a_mouse_click_on_free_space_triggers():
    assert decide(mouseStart="left", swallow=False, overlay=False, touch=False, button=0,
                  onControl=False, screen="setup") == "trigger"


def test_an_open_window_keeps_finger_and_mouse_ordinary():
    """PIN pad, menu, delete question: there the press always belongs to the window."""
    assert decide(mouseStart="left", swallow=True, overlay=True, touch=False, button=0,
                  onControl=False, screen="start") == "none"
    assert decide(mouseStart="left", swallow=True, overlay=True, touch=True,
                  onControl=False, screen="start") == "none"


def test_a_second_button_keeps_its_own_task():
    assert decide(mouseStart="left", mouseRight="mode:collage", swallow=True, overlay=False,
                  touch=False, button=2, onControl=False, screen="start") == "mode:collage"


# ── wiring: the rule is what the page actually uses ─────────────────────────

def _handler(event: str) -> str:
    start = JS.index(f'document.addEventListener("{event}", (e) => {{\n    if (fromTouch(e))')
    return JS[start:JS.index("}, true);", start)]


def test_the_echo_of_a_tap_is_not_a_second_press():
    body = _handler("mousedown")
    assert body.index("fromTouch(e)") < body.index("fuehreAus"), "the echo is dropped before anything runs"
    assert "sourceCapabilities" in JS and "firesTouchEvents" in JS
    assert 'pointerType !== "mouse"' in JS


def test_a_tap_either_works_its_button_or_triggers_never_both():
    body = _handler("click")
    assert "pressRule({ touch: true" in body
    trigger = body.index("fuehreAus(r.act)")
    assert "stopPropagation" in body[:trigger], "a tap that triggers must not reach a button as well"


def test_the_click_after_a_buzzer_press_does_not_reach_a_button():
    assert "swallowClickUntil = performance.now()" in _handler("mousedown")
    assert "swallowClickUntil" in _handler("click")


def test_the_free_space_screens_are_exactly_start_and_setup():
    assert 'const FREE_TAP_SCREENS = ["start", "setup"];' in JS
