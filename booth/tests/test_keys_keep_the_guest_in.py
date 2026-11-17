"""A guest on a keyboard or a buzzer must not be sent home by the idle or the
result timer: keys restart both timers, like a touch does."""
import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "boothcore" / "web" / "static" / "app.js").read_text()


def _body(start_marker: str) -> str:
    i = JS.index(start_marker)
    depth, j = 0, JS.index("{", i)
    k = j
    while True:
        if JS[k] == "{":
            depth += 1
        elif JS[k] == "}":
            depth -= 1
            if depth == 0:
                return JS[j:k + 1]
        k += 1


def test_one_helper_restarts_idle_attract_and_result_timer():
    body = _body("function bumpTimers(")
    assert "armIdle()" in body and "armAttract()" in body and "startResultTimer()" in body
    assert '"result"' in body


def test_pointerdown_uses_the_helper():
    i = JS.index('document.addEventListener("pointerdown", () => {\n    if (dismissAttract()) return;')
    assert "bumpTimers" in JS[i:i + 400]


def test_main_keydown_handler_restarts_timers_after_waking_the_attract_screen():
    i = JS.index("if (dismissAttract()) { e.preventDefault(); return; }   // jede Taste weckt auf")
    assert "bumpTimers(true)" in JS[i:i + 250]


def test_button_actions_restart_timers():
    body = _body("function fuehreAus(")
    assert "bumpTimers(true)" in body
    assert body.index("bumpTimers(true)") < body.index('a === "trigger"')


def test_key_path_never_restarts_the_result_timer_under_an_open_window():
    body = _body("function bumpTimers(")
    assert "overlayOpen()" in body
