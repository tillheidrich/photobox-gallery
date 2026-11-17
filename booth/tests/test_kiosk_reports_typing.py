"""The kiosk tells the booth when someone types into it (final review A10).

Remote control refuses a screenshot or live picture while an e-mail, greeting
or PIN dialog is open (fernsteuerung.py). The booth cannot see the page, so the
kiosk reports it on its state poll and at once when such a dialog opens or
closes. The pure part sits between ``<typing-helpers>`` markers in app.js; the
tests run that very code with Node. The wiring is checked in the source.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _helpers() -> str:
    return JS[JS.index("// <typing-helpers>"):JS.index("// </typing-helpers>")]


def _node(expr: str):
    if not NODE:
        pytest.skip("node is not installed")
    script = _helpers() + f"\nprocess.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


def test_every_dialog_with_a_keyboard_or_pin_pad_is_watched():
    watched = _node("TYPING_OVERLAYS")
    keyboards = re.findall(r'class="overlay overlay--(?:keyboard|pin)" id="(\w+)"', HTML)
    assert keyboards and sorted(watched) == sorted(keyboards)


def test_one_open_dialog_is_enough():
    assert _node("typingOpen((id) => id !== 'mailOverlay')") is True
    assert _node("typingOpen((id) => id !== 'pinOverlay')") is True
    assert _node("typingOpen(() => true)") is False
    # A dialog missing from the page counts as closed, not as open.
    assert _node("typingOpen(() => undefined)") is False


def test_the_report_rides_on_the_state_poll():
    assert _node("[typingQuery(true), typingQuery(false)]") == ["/api/state?typing=1", "/api/state?typing=0"]
    refresh = JS[JS.index("async function refresh()"):]
    refresh = refresh[:refresh.index("\n  }\n")]
    assert "api(typingQuery(" in refresh


def test_opening_and_closing_is_reported_at_once():
    assert "new MutationObserver(reportTyping)" in JS
    assert 'attributeFilter: ["hidden"]' in JS
