"""One capture run at a time — a second start is dropped, not queued.

Seen on 15.11.2026 at the real booth (GIF mode, the set-up screen had nothing
to choose): the mode tile had already started the run on its own when a tap
on "Los geht's" 1.2 s later started a second one. Two sessions, two
countdowns counting over each other (5, 3, 4, 2, 3, 1 …), and the run ended
in the error screen at shot 4 of 6.

Every way into a run (tile, "Los geht's", buzzer, keys, remote trigger,
"Nochmal", a reshoot from the review) now goes through one gate in the kiosk
script. The gate and the countdown arithmetic are pure functions between the
``<run-gate>`` markers; these tests run that very code with Node. The wiring
tests below check that each entry asks the gate.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _gate_source() -> str:
    start = JS.index("// <run-gate>")
    end = JS.index("// </run-gate>")
    return JS[start:end]


def run_js(expr: str):
    if not NODE:
        pytest.skip("node is not installed")
    script = _gate_source() + f"\nprocess.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


def _function(name: str) -> str:
    """The body of a top-level kiosk function, up to the next one at the same indent."""
    m = re.search(rf"\n  (?:async )?function {name}\(.*?\n  \}}\n", JS, re.S)
    assert m, name
    return m.group(0)


def test_a_second_start_while_a_run_is_held_is_dropped():
    assert run_js("(() => { const g = makeGate(); const a = g.enter('run'); const b = g.enter('run');"
                  " return [a !== null, b]; })()") == [True, None]


def test_after_the_run_ends_the_next_guest_can_start():
    assert run_js("(() => { const g = makeGate(); const a = g.enter('run'); g.leave('run', a);"
                  " return g.enter('run') !== null; })()") is True


def test_a_late_end_of_an_abandoned_run_does_not_open_the_gate_of_the_new_one():
    # home() resets the gate; the old run finishing later must not release the new run
    assert run_js("(() => { const g = makeGate(); const old = g.enter('run'); g.reset();"
                  " const neu = g.enter('run'); g.leave('run', old); return [neu !== null, g.busy('run')]; })()"
                  ) == [True, True]


def test_print_and_run_are_held_separately():
    assert run_js("(() => { const g = makeGate(); g.enter('run'); return g.enter('print') !== null; })()") is True


@pytest.mark.parametrize("mode", ["photo", "collage", "gif", "boomerang", "video"])
def test_the_first_shot_of_every_mode_gets_the_full_first_countdown(mode):
    assert run_js(f"shotSeconds('{mode}', 1, {{first: 7, next: 3}}, {{interval: 1}}, false)") == 7


@pytest.mark.parametrize("stored,shown", [(0, 1), (0.4, 1), (-2, 3), (25, 20), ("x", 3), (7, 7)])
def test_a_first_countdown_outside_one_to_twenty_is_held_to_it(stored, shown):
    # a profile saved before the 1–20 rule may still carry 0
    assert run_js(f"shotSeconds('photo', 1, {{first: {json.dumps(stored)}, next: 0}}, {{}}, false)") == shown
    assert run_js("shotSeconds('collage', 2, {first: 3, next: 0}, {}, false)") == 1


def test_the_following_gif_shots_count_the_gif_gap_by_design():
    assert run_js("shotSeconds('gif', 2, {first: 7, next: 3}, {interval: 1}, false)") == 1


def test_the_following_collage_shots_count_countdown_next():
    assert run_js("shotSeconds('collage', 3, {first: 7, next: 3}, {interval: 1}, false)") == 3


def test_a_gif_picture_taken_again_is_not_rushed_with_the_one_second_gap():
    assert run_js("shotSeconds('gif', 4, {first: 7, next: 3}, {interval: 1}, true)") == 3


def test_countdown_digits_are_whole_and_strictly_descending():
    assert run_js("countdownSteps(5)") == [[5, 10], [4, 10], [3, 10], [2, 10], [1, 10]]
    assert run_js("countdownSteps(20)")[0] == [20, 10]


def test_a_fractional_gif_gap_shows_whole_digits_and_keeps_its_length():
    # gif.interval accepts 0.5 steps; "1.5", "0.5" used to appear on screen
    assert run_js("countdownSteps(1.5)") == [[2, 5], [1, 10]]
    assert run_js("countdownSteps(0.5)") == [[1, 5]]
    assert run_js("countdownSteps(0)") == []


# ── wiring: every entry asks the gate ────────────────────────────────────

def test_run_capture_takes_the_gate_before_anything_else_and_gives_it_back():
    body = _function("runCapture")
    first = body.split("{", 1)[1].strip().splitlines()[0]
    assert 'gate.enter("run")' in first
    assert 'gate.leave("run", token)' in body
    assert "finally" in body


def test_the_mode_tile_does_not_overwrite_a_running_run():
    body = _function("openSetup")
    assert body.index('gate.busy("run")') < body.index("flow = {")


def test_the_set_up_skip_still_starts_through_run_capture():
    body = _function("openSetup")
    assert "return runCapture();" in body


def test_all_starts_of_a_run_go_through_run_capture_or_open_setup():
    # no second door: /api/session is posted in exactly one place
    assert JS.count('api("/api/session", { method: "POST"') == 1


def test_a_reshoot_is_held_too():
    body = _function("reviewNochmal")
    assert 'gate.enter("reshoot")' in body


def test_home_opens_the_gate_and_ends_the_old_run():
    body = _function("home")
    assert "gate.reset()" in body
    assert "lauf.abgebrochen = true" in body


def test_print_keys_obey_the_same_hold_as_the_print_button():
    body = _function("doPrint")
    assert 'gate.enter("print")' in body
    assert "printHold" in body


def test_the_countdown_uses_whole_digits():
    assert "countdownSteps(seconds)" in _function("countdown")


# ── a GIF is a quick series (booth PC: a shot every 5.6 s at a 1 s gap) ──
def test_a_gif_series_is_quick_and_a_reshoot_is_not():
    assert run_js("[quickSeries('gif', false), quickSeries('gif', true), quickSeries('collage', false)]") \
        == [True, False, False]


def test_between_quick_shots_nothing_is_shown_or_said():
    body = _function("captureShot")
    assert "if (!(schnell && index > 1)) await bitteLaecheln();" in body
    review = body.index("if (!schnell) {")
    assert body.index('review.classList.add("on")') > review
    assert body.index('showPhase("between"') > review


def test_the_flash_recharges_during_the_next_countdown_too():
    # A GIF shows no picture between its shots: the full recycle time was
    # waited and the gap counted on top of it. The next countdown counts now.
    body = _function("captureShot")
    assert "shotSeconds(flow.mode, index + 1, cd, S.profile.gif" in body
    assert "* 1000 - review_ms - naechster_ms" in body
