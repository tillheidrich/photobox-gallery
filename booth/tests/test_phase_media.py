"""A picture, GIF or video for each phase of the run.

dslrBooth, LumaBooth and Breeze let the operator dress the moments in
between: "your photo is nearly ready", "one more!" between the shots of a
collage, "thank you" before the booth goes back to the start, and a friendly
picture when something goes wrong. Here ``phase_media`` holds one optional
medium per phase (``processing``, ``between``, ``after``, ``error``) with
``fit`` (cover | contain), ``text`` (the phase's own text on top) and
``loop``; ``after`` also has ``seconds`` (0 = off, the default).

``idle`` is not a phase here on purpose: the attract screen
(``attract.source = "own"`` with ``attract.media``) already is the idle
picture, so a second setting for the same thing would only compete with it.

Rules: a phase never waits for its medium — the run is not one second slower,
except ``after`` with a duration the operator set. A file the booth does not
have counts as no medium. "Reduce motion" shows a video as its first frame and
drops a GIF.

The choice is a pure function in the kiosk script between the
``<phase-media>`` markers; these tests run that very code with Node and are
skipped without Node.
"""
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from boothcore import config

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")

PHASES = ("processing", "between", "after", "error")


# ── schema ──────────────────────────────────────────────────────────────────

def test_every_phase_starts_without_a_medium():
    pm = config.DEFAULT_PROFILE["phase_media"]
    assert set(pm) == set(PHASES)
    for phase in PHASES:
        assert pm[phase]["media"] == "", phase
        assert pm[phase]["fit"] == "cover"
        assert pm[phase]["text"] is True and pm[phase]["loop"] is True
    assert pm["after"]["seconds"] == 0, "no pause before the start screen unless the operator asks"
    assert "seconds" not in pm["processing"], "only 'after' may make the run longer"


def test_an_old_profile_gets_the_defaults_and_bad_values_fall_back():
    merged = config._merge(config.DEFAULT_PROFILE, {})
    assert merged["phase_media"] == config.DEFAULT_PROFILE["phase_media"]
    merged = config._merge(config.DEFAULT_PROFILE,
                           {"phase_media": {"after": {"media": "danke.mp4", "seconds": "x"}}})
    assert merged["phase_media"]["after"]["media"] == "danke.mp4"
    assert merged["phase_media"]["after"]["seconds"] == 0


# ── the choice, run with Node ───────────────────────────────────────────────

def _rule_source() -> str:
    return JS[JS.index("// <phase-media>"):JS.index("// </phase-media>")]


def pick(phase, cfg, available, **opts):
    if not NODE:
        pytest.skip("node is not installed")
    call = f"phaseMedia({json.dumps(phase)}, {json.dumps(cfg)}, {json.dumps(available)}, {json.dumps(opts)})"
    script = _rule_source() + f"\nprocess.stdout.write(JSON.stringify({call}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


def _cfg(**phases):
    cfg = json.loads(json.dumps(config.DEFAULT_PROFILE["phase_media"]))
    for phase, values in phases.items():
        cfg[phase].update(values)
    return cfg


def test_the_defaults_show_nothing_anywhere():
    for phase in PHASES:
        assert pick(phase, _cfg(), [], shot=1, needed=4) is None, phase


def test_a_medium_the_booth_has_is_shown():
    got = pick("processing", _cfg(processing={"media": "fast.mp4"}), ["fast.mp4"])
    assert got == {"file": "fast.mp4", "kind": "video", "fit": "cover", "text": True,
                   "loop": True, "still": False, "seconds": 0}


def test_a_missing_file_is_no_medium():
    assert pick("error", _cfg(error={"media": "oops.png"}), []) is None


def test_fit_text_and_loop_come_from_the_profile():
    got = pick("error", _cfg(error={"media": "oops.png", "fit": "contain", "text": False, "loop": False}),
               ["oops.png"])
    assert (got["kind"], got["fit"], got["text"], got["loop"]) == ("image", "contain", False, False)


def test_an_unknown_fit_is_cover():
    assert pick("error", _cfg(error={"media": "a.jpg", "fit": "zoom"}), ["a.jpg"])["fit"] == "cover"


def test_between_only_between_two_shots():
    cfg = _cfg(between={"media": "again.gif"})
    assert pick("between", cfg, ["again.gif"], shot=1, needed=4)["kind"] == "gif"
    assert pick("between", cfg, ["again.gif"], shot=3, needed=4) is not None
    assert pick("between", cfg, ["again.gif"], shot=4, needed=4) is None, "after the last shot comes processing"
    assert pick("between", cfg, ["again.gif"], shot=1, needed=1) is None, "a single photo has no 'between'"


def test_after_is_off_until_a_duration_is_set():
    assert pick("after", _cfg(after={"media": "danke.mp4"}), ["danke.mp4"]) is None
    got = pick("after", _cfg(after={"media": "danke.mp4", "seconds": 4}), ["danke.mp4"])
    assert got["seconds"] == 4


def test_after_may_be_text_only_and_has_a_ceiling():
    got = pick("after", _cfg(after={"seconds": 99}), [])
    assert got["file"] == "" and got["text"] is True and got["seconds"] == 15
    assert pick("after", _cfg(after={"seconds": 3, "text": False}), []) is None, "nothing to show"


def test_reduce_motion_holds_a_video_still_and_drops_a_gif():
    v = pick("processing", _cfg(processing={"media": "fast.mp4"}), ["fast.mp4"], reduceMotion=True)
    assert v["still"] is True and v["file"] == "fast.mp4"
    g = pick("between", _cfg(between={"media": "x.gif"}), ["x.gif"], shot=1, needed=2, reduceMotion=True)
    assert g is None
    t = pick("between", _cfg(between={"media": "x.gif"}), ["x.gif"], shot=1, needed=2, reduceMotion=True,
             keepText=True)
    assert t is None, "between has no text of its own without its picture"


def test_an_unknown_phase_is_nothing():
    assert pick("idle", _cfg(), ["a.jpg"]) is None


# ── the booth tells the kiosk which files it has ────────────────────────────

@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-phase-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def test_state_lists_only_phase_files_that_are_on_the_booth(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    (booth.assets_dir / "fast.mp4").write_bytes(b"\x00")
    booth.store.profile["phase_media"]["processing"]["media"] = "fast.mp4"
    booth.store.profile["phase_media"]["error"]["media"] = "missing.png"
    state = TestClient(create_app(booth)).get("/api/state").json()
    assert state["phase_media"] == ["fast.mp4"]


# ── wiring ──────────────────────────────────────────────────────────────────

def test_each_phase_is_painted_where_it_happens():
    assert 'showPhase("processing")' in JS.split("async function runCapture")[1].split("openResult(item")[0]
    assert 'showPhase("error")' in JS
    assert 'showPhase("between"' in JS.split("async function captureShot")[1].split("function shotStrip")[0]
    assert "afterPhase(" in JS.split("function home()")[0], "'after' runs before home(), not inside it"


def test_the_layers_exist_and_never_take_a_tap():
    assert 'id="phaseProcessing"' in HTML and 'id="phaseError"' in HTML and 'id="phaseOverlay"' in HTML
    rule = CSS.split(".phase-media{")[1].split("}")[0]
    assert "pointer-events:none" in rule


def test_videos_are_preloaded_muted_and_inline():
    src = JS.split("function preloadPhaseMedia")[1].split("\n  }\n")[0]
    assert 'preload = "auto"' in src and "muted = true" in src and "playsInline = true" in src


def test_reduce_motion_is_read_from_the_system():
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in JS
