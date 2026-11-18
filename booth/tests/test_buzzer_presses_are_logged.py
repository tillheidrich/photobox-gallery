"""Every buzzer press leaves one line in booth.log (owner, 18.11.2026).

"Der Buzzer löst mal aus und mal nicht" — and booth.log had no trace of a
press, so nobody could tell a press that was ignored (camera reconnecting,
a window open, the bounce guard) from one that never arrived. The kiosk now
reports each mouse press and start key it handles as the trigger to
/api/input-log: what it was, on which screen, and what came of it. Only
short tokens from a fixed alphabet reach the log, never text a guest typed,
and a flood is cut off.
"""
import logging
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-buzzer-log-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from fastapi.testclient import TestClient
    from boothcore.app import Booth, create_app
    b = Booth(base=d, start_threads=False)
    yield TestClient(create_app(b))
    b.mail.stop()


def test_a_press_is_one_info_line_with_screen_and_outcome(client, caplog):
    with caplog.at_level(logging.INFO):
        r = client.post("/api/input-log", json={"source": "mouse", "input": "button0", "screen": "start",
                                                "outcome": "acted:start"})
    assert r.status_code == 200
    lines = [rec.getMessage() for rec in caplog.records if "Auslöser" in rec.getMessage()]
    assert len(lines) == 1
    assert "mouse" in lines[0] and "button0" in lines[0] and "start" in lines[0] and "acted:start" in lines[0]


def test_only_plain_tokens_reach_the_log(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/api/input-log", json={"source": "mouse<script>", "input": "a b\nFAKE LINE",
                                            "screen": "x" * 200, "outcome": "ignored:camera"})
    line = next(rec.getMessage() for rec in caplog.records if "Auslöser" in rec.getMessage())
    assert "<" not in line and "\n" not in line and "FAKE LINE" not in line
    assert "x" * 40 not in line


def test_a_flood_is_cut_off(client, caplog):
    with caplog.at_level(logging.INFO):
        for _ in range(50):
            client.post("/api/input-log", json={"source": "key", "input": "Space", "screen": "start",
                                                "outcome": "ignored:bounce"})
    assert sum("Auslöser" in rec.getMessage() for rec in caplog.records) <= 20


def test_the_kiosk_reports_mouse_presses_and_start_keys():
    js = (ROOT / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "function reportPress(" in js
    assert 'reportPress("mouse"' in js and 'reportPress("key"' in js
    # the trigger says what it did or why it did nothing
    assert 'S.camera.state !== "ready" ? "camera"' in js and 'return "ignored:bounce"' in js
    assert 'return "ignored:overlay"' in js


def test_a_held_key_and_a_chattering_buzzer_do_not_flood_the_route():
    """Review 18.11.2026: a stuck key repeats ~30 times a second."""
    js = (ROOT / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'if (!e.repeat) reportPress("key", e.code, outcome);' in js
    assert 'if (outcome === "ignored:bounce") {' in js and "Date.now() - lastBounceReport < 1000" in js
