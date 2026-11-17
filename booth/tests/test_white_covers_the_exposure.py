"""The white of a shot covers the real exposure, not the moment before it.

The owner, 15.11.2026: "the screen turns white BEFORE the photo is taken".
On the booth PC the shutter request (``POST /api/session/{uid}/shot``) takes
1.7–2.4 s — focus, press, download — and the camera fires late in it. The
kiosk played a 0.6 s white at the start of the request: gone a second before
the exposure. Now the white (the shimmer ``#flash`` and the screen light
``#screenFlash``) goes up right before the request, stands until its answer,
at most ``WHITE_CAP_MS`` (4 s), and then fades.

The timing helper lives between the ``<run-gate>`` markers and runs here in
Node with a delayed fake answer; the wiring is checked in the source.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def run_js_async(body: str):
    if not NODE:
        pytest.skip("node is not installed")
    gate = JS[JS.index("// <run-gate>"):JS.index("// </run-gate>")]
    script = (gate + "\nconst sleep = (ms) => new Promise((r) => setTimeout(r, ms));\n"
              + f"(async () => {{ const out = await (async () => {{ {body} }})();"
              + " process.stdout.write(JSON.stringify(out)); })();")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def _delayed(ms: int, fail: bool = False) -> str:
    how = "rej(new Error('camera'))" if fail else "res({shot_url: 'x'})"
    return f"new Promise((res, rej) => setTimeout(() => {how}, {ms}))"


def test_the_white_stands_until_a_slow_shot_answers():
    # the booth PC's shutter: about two seconds
    held, why = run_js_async(
        f"const t0 = Date.now(); const why = await untilSettledOr({_delayed(1800)}, WHITE_CAP_MS, sleep);"
        " return [Date.now() - t0, why];")
    assert why == "answer"
    assert 1700 <= held < 3000, held


def test_the_white_goes_at_once_when_the_shot_answers_quickly():
    held, why = run_js_async(
        f"const t0 = Date.now(); const why = await untilSettledOr({_delayed(50)}, WHITE_CAP_MS, sleep);"
        " return [Date.now() - t0, why];")
    assert why == "answer" and held < 1000


def test_a_hanging_camera_does_not_leave_the_guests_in_white():
    held, why = run_js_async(
        f"const t0 = Date.now(); const why = await untilSettledOr({_delayed(20000)}, 300, sleep);"
        " return [Date.now() - t0, why];")
    assert why == "cap" and 250 <= held < 2000


def test_a_failed_shot_ends_the_white_too_and_the_error_still_reaches_the_run():
    why, err = run_js_async(
        f"const shot = {_delayed(100, fail=True)}; const why = await untilSettledOr(shot, WHITE_CAP_MS, sleep);"
        " let err = null; try { await shot; } catch (e) { err = e.message; } return [why, err];")
    assert why == "answer" and err == "camera"


def test_the_cap_is_about_four_seconds():
    assert run_js_async("return WHITE_CAP_MS;") == 4000


def _capture_shot() -> str:
    m = re.search(r"\n  async function captureShot\(.*?\n  \}\n", JS, re.S)
    assert m
    return m.group(0)


def test_the_white_goes_up_right_before_the_request_and_fades_after_the_answer():
    body = _capture_shot()
    up = body.index("flash(true);")
    request = body.index("api(`/api/session/${flow.uid}/shot`")
    wait = body.index("await untilSettledOr(shot, WHITE_CAP_MS, sleep);")
    fade = body.index("flashFade();")
    light_off = body.index("await schirmblitzRunter(weissSeit);")
    answer = body.index("r = await shot;")
    assert body.index("await schirmblitzHoch();") < up < request < wait < fade < light_off < answer
    # the fade and the screen light's end are in the finally of the wait: a failed shot ends them too
    assert "} finally {" in body[wait:fade]


def test_the_held_white_is_opaque_until_it_fades():
    assert ".flash.hold{opacity:1}" in CSS
    assert 'f.classList.add(hold ? "hold" : "on");' in JS
