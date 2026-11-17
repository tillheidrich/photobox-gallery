"""A guest can step out during countdown and recording.

Whoever tapped the wrong mode used to stand through the whole series: the
capture screen had no way out, only the review after the last shot did.
dslrBooth shows an X in the corner. Here an unobtrusive, easily hit button
(at least 64 px) ends the run and goes back to the start. Switched on in the
profile (``countdown.cancel``); off by default, so no box changes on its own.

Cancelling throws the run away through the same door as "Verwerfen" in the
review: ``/abort``. That deletes the run's folder in every mode, and in
data-minimal mode it is the promise that nothing stays on the device. The
dangerous moment is a shot that is still being taken when the abort
arrives: the abort waits for it, and a shot that comes after the abort is
refused, so no file can land in a folder that has just been removed.
"""
import re
import tempfile
import threading
import time
from pathlib import Path

import pytest

from boothcore import config

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-cancel-"))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "collage", "gif"]
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _client(b):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    return TestClient(create_app(b))


def test_the_way_out_is_off_until_the_operator_wants_it():
    assert config.DEFAULT_PROFILE["countdown"]["cancel"] is False


def test_the_capture_screen_has_the_button():
    capture = HTML.split('<section class="screen" id="s-capture">')[1].split("</section>")[0]
    assert 'data-action="capture-cancel"' in capture
    assert 'id="captureCancel"' in capture and "hidden" in capture.split('id="captureCancel"')[1].split(">")[0]


def test_the_button_is_easy_to_hit():
    rule = re.search(r"\.capture-cancel\{([^}]*)\}", CSS).group(1)
    for side in ("width", "height"):
        px = int(re.search(rf"(?<![-\w]){side}:(\d+)px", rule).group(1))
        assert px >= 64, f"{side} {px}px is too small to hit"
    assert "z-index" in rule, "it must lie above the live picture and the countdown"


def test_cancelling_ends_the_run_and_goes_home():
    body = JS.split("function captureAbbrechen()")[1].split("\n  }\n")[0]
    assert "/abort" in body and "home()" in body
    assert "lauf.abgebrochen = true" in body
    # every step of the series looks at its run's token, so nothing runs on after home()
    assert JS.count("pruefeAbbruch(meiner)") >= 5
    # and a late answer of the cancelled run never shows an error screen
    assert "if (e instanceof Abgebrochen || meiner.abgebrochen) return;" in JS


def test_the_button_only_shows_when_switched_on_and_never_on_a_finished_run():
    assert "S.profile.countdown.cancel" in JS
    assert "!retakeFlow" in JS.split("function zeigeCaptureAbbruch")[1].split("\n  }\n")[0]


def test_an_aborted_run_leaves_no_files(booth):
    s = booth.start_session("collage", "4:5", "grid4", None)
    booth.take_shot(s)
    ordner = s.folder
    assert _client(booth).post(f"/api/session/{s.uid}/abort").status_code == 200
    assert not ordner.exists()


def test_in_data_minimal_mode_the_abort_removes_everything(booth):
    booth.store.profile["privacy"].update({"mode": "print_only"})
    s = booth.start_session("collage", "4:5", "grid4", None)
    booth.take_shot(s)
    ordner = s.folder
    assert _client(booth).post(f"/api/session/{s.uid}/abort").status_code == 200
    assert not ordner.exists()
    assert booth.library.get(s.uid) is None


def test_an_abort_during_a_shot_waits_for_it_and_leaves_no_file(booth):
    """A camera driver that creates its target folder must not bring it back."""
    s = booth.start_session("collage", "4:5", "grid4", None)
    ordner = s.folder
    started = threading.Event()

    def slow_capture(target):
        started.set()
        time.sleep(0.6)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"jpeg")
        return target

    booth.camera.capture = slow_capture
    fehler = []

    def shoot():
        try:
            booth.take_shot(s)
        except Exception as e:          # noqa: BLE001 — a refused shot is fine here
            fehler.append(e)

    t = threading.Thread(target=shoot)
    t.start()
    started.wait(2)
    assert _client(booth).post(f"/api/session/{s.uid}/abort").status_code == 200
    t.join(5)
    assert not ordner.exists(), f"left behind: {list(ordner.rglob('*')) if ordner.exists() else ''}"


def test_a_shot_after_the_abort_is_refused(booth):
    s = booth.start_session("collage", "4:5", "grid4", None)
    ordner = s.folder
    booth.delete(s.uid)
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        booth.take_shot(s)
    assert not ordner.exists()
