"""Watcher, own window and kiosk keeper are one design, not three features side by side.

Three branches met on 15.11.2026: the .exe as a watcher that brings a crashed
booth back (feat/tag-ohne-betreiber), the booth in its own program window
(feat/eigenes-fenster) and the preview geometry (fix/setup-vorschau). Each
was right on its own; together they had seams:

* a second start of the .exe started a second watcher that waited forever
  for the port instead of bringing the window forward;
* the console the watcher owns was never hidden (four processes share it,
  the window only hid a console shared by two);
* a page that asked the operator something (confirm/prompt in the admin)
  stopped beating and was reloaded under the operator's finger;
* the window reloaded or restarted the booth in the middle of a run;
* the Chrome keeper could open Chrome over a healthy own window;
* an own window that failed at every start restarted forever.

The process model (watcher -> booth child with server and window) and the
exit codes are in docs/EIGENES-FENSTER.md and business_logic/index.md.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from boothcore import waechter
from boothcore import window as w

ROOT = Path(__file__).resolve().parents[2]
WIN = ROOT / "packaging" / "windows"


@pytest.fixture(autouse=True)
def fresh_status():
    w._reset_status()
    yield
    w._reset_status()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ── one process model: watcher -> booth (server + window) ──────────────
def test_a_second_start_of_the_exe_brings_the_window_forward_and_starts_nothing():
    asked = []
    code = waechter.startup(port_busy=lambda: True, ask_running=lambda: asked.append(1) or "ok")
    assert code == 0 and asked == [1]


def test_a_foreign_program_on_the_port_is_waited_for_not_given_up_on():
    # Nobody answers on the instance line: not a booth. The old wait loop takes over.
    assert waechter.startup(port_busy=lambda: True, ask_running=lambda: "") is None
    assert waechter.startup(port_busy=lambda: True, ask_running=lambda: "leaving") is None
    assert waechter.startup(port_busy=lambda: False, ask_running=lambda: "ok") is None


def test_a_child_that_found_another_booth_ends_the_second_watcher_without_a_restart():
    calls, pauses = [], []

    def start():
        calls.append(1)
        return waechter.ALREADY_RUNNING
    assert waechter.watch(start, port_busy=lambda: False, sleep=pauses.append) == 0
    assert calls == [1] and pauses == []


@pytest.mark.parametrize("argv", [["--window-selftest"], ["--quit"], ["--dauerbetrieb", "aktivzeit"],
                                  ["--install"], ["--doctor"], ["--uninstall"]])
def test_commands_that_are_not_the_booth_are_never_watched(argv):
    assert waechter.should_watch(argv, frozen=True, environ={}) is False


def test_the_booth_learns_what_its_watcher_saw():
    record, envs = {}, []
    codes = iter([0xC0000005, waechter.RESTART_CODE, 0])

    def start():
        envs.append(waechter.child_env(record, {}))
        return next(codes)
    waechter.watch(start, port_busy=lambda: False, sleep=lambda s: None, record=record)
    first, second, third = (waechter.supervisor_status(e) for e in envs)
    assert first["watched"] and first["restarts"] == 0 and first["last_exit_code"] is None
    assert second["restarts"] == 1 and second["last_exit_code"] == 0xC0000005 and second["last_reason"] == "crash"
    assert third["restarts"] == 2 and third["last_exit_code"] == 3 and third["last_reason"] == "restart"
    assert waechter.supervisor_status({})["watched"] is False


@pytest.mark.parametrize("argv,local,expected", [
    ([], {}, True),                                              # task start, own window: no console
    ([], {"window": {"mode": "chrome"}}, True),                  # Chrome covers the screen; the log is in logs/
    (["--window-debug"], {}, False),
    (["--no-kiosk"], {}, False),                                 # server only: the console is the only sign of life
    ([], {"window": {"hide_console": False}}, False),
])
def test_the_watcher_hides_the_console_it_owns(argv, local, expected):
    assert waechter.console_should_hide(argv, local) is expected


def test_under_the_watcher_the_window_leaves_the_console_alone(monkeypatch):
    hidden = []
    monkeypatch.setattr(w, "_hide_own_console", lambda: hidden.append(1))
    monkeypatch.setenv(waechter.CHILD_ENV, "1")
    win = w.OwnWindow("http://127.0.0.1:8765/", Path(tempfile.mkdtemp()), w.settings({}), webview=FakeWebview())
    win.start()
    assert hidden == []
    monkeypatch.delenv(waechter.CHILD_ENV)
    win = w.OwnWindow("http://127.0.0.1:8765/", Path(tempfile.mkdtemp()), w.settings({}), webview=FakeWebview())
    win.start()
    assert hidden == [1]


class FakeInstance:
    def __init__(self, free, answers):
        self.free, self.answers = free, list(answers)

    def acquire(self):
        return self.free

    def send(self, command, timeout=3.0):
        return self.answers.pop(0) if self.answers else ""


def test_claiming_tells_a_shown_booth_from_a_hung_one():
    clock = Clock()

    def sleep(s):
        clock.now += s
    assert w.claim_outcome(FakeInstance(True, []), sleep=sleep, clock=clock) == "mine"
    assert w.claim_outcome(FakeInstance(False, ["ok"]), sleep=sleep, clock=clock) == "shown"
    assert w.claim_outcome(FakeInstance(False, ["leaving", "ok"]), sleep=sleep, clock=clock) == "shown"
    assert w.claim_outcome(FakeInstance(False, []), wait=5, sleep=sleep, clock=clock) == "silent"
    assert w.EXIT_FOR_CLAIM == {"shown": waechter.ALREADY_RUNNING, "silent": 1}


def test_quit_over_the_instance_line_leaves_with_code_zero():
    from boothcore import __main__ as start
    d = Path(tempfile.mkdtemp(prefix="booth-quit-"))
    instance = w.claim_instance(d, "own")
    codes = []
    try:
        start.wire_instance(instance, SimpleNamespace(shutdown=codes.append), delay=0.0)
        assert w.SingleInstance(d / "window").send("quit") == "ok"
        for _ in range(100):
            if codes:
                break
            time.sleep(0.01)
    finally:
        instance.release()
    assert codes == [0]


# ── the web view, faked ────────────────────────────────────────────────
class FakeEvent(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeWindow:
    def __init__(self, **kw):
        self.kw = kw
        self.events = SimpleNamespace(closing=FakeEvent(), loaded=FakeEvent())
        self.urls = []

    def expose(self, *fns):
        pass

    def load_url(self, url):
        self.urls.append(url)

    def evaluate_js(self, script):
        pass

    def destroy(self):
        pass


class FakeWebview:
    def __init__(self):
        self.settings = {}
        self.screens = []

    def create_window(self, title, **kw):
        self.window = FakeWindow(title=title, **kw)
        return self.window

    def start(self, func=None, **kw):
        pass


def _live_window(busy=None, on_stuck=None, data=None):
    clock = Clock()
    fake = FakeWebview()
    win = w.OwnWindow("http://127.0.0.1:8765/", Path(data or tempfile.mkdtemp(prefix="booth-one-")),
                      w.settings({}), webview=fake, clock=clock, watchdog=w.Watchdog(clock=clock),
                      busy=busy, on_stuck=on_stuck)
    win.start()
    win.window = fake.window
    win._first_load = clock.now
    win.watchdog.arm()
    win.heartbeat({})
    return win, fake, clock


def _wait_for(cond, seconds=1.0):
    end = time.time() + seconds
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


# ── kiosk keeper vs. own window ────────────────────────────────────────
def test_the_kiosk_keeper_never_opens_chrome_over_the_own_window():
    """The page in the own window also polls /api/state; the keeper must not care."""
    from boothcore import __main__ as start
    booth = SimpleNamespace(kiosk_seen=None)            # nothing asked for ten minutes
    opened = []
    clock = Clock()

    def sleep(s):
        clock.now += s
    start._keep_kiosk("http://127.0.0.1:8765/", booth, wait=0, opener=opened.append, sleep=sleep,
                      clock=clock, rounds=60, mode=lambda: "own")
    assert opened == []
    # The same silence with Chrome showing the booth: opened, and again after the gap.
    start._keep_kiosk("http://127.0.0.1:8765/", booth, wait=0, opener=opened.append, sleep=sleep,
                      clock=clock, rounds=60, mode=lambda: "chrome")
    assert len(opened) >= 2


def test_the_reopen_rule_itself_knows_the_own_window():
    assert waechter.kiosk_should_reopen("own", last_seen=None, last_launch=0.0, now=10_000.0) is False
    assert waechter.kiosk_should_reopen("chrome", last_seen=None, last_launch=0.0, now=10_000.0) is True
    assert waechter.kiosk_should_reopen("none", last_seen=None, last_launch=0.0, now=10_000.0) is False


# ── the heartbeat and what may starve it ──────────────────────────────
def test_a_question_to_the_operator_does_not_reload_the_page_under_his_finger():
    """admin.html asks with confirm()/prompt(); while the dialog is open no script runs."""
    win, fake, clock = _live_window()
    win.heartbeat({"dialog": True})
    clock.now += w.HEARTBEAT_TIMEOUT * 10                 # the operator reads for two and a half minutes
    assert win.tick() is None
    time.sleep(0.05)
    assert fake.window.urls == []
    win.heartbeat({})                                     # dialog answered, the page beats again
    clock.now += w.LOAD_GRACE + 1
    assert win.tick() is None
    assert _wait_for(lambda: fake.window.urls == ["http://127.0.0.1:8765/"])


def test_a_dialog_left_open_all_night_is_still_reloaded_in_the_end():
    win, fake, clock = _live_window()
    win.heartbeat({"dialog": True})
    clock.now += w.DIALOG_HOLD + w.LOAD_GRACE + 1
    win.tick()
    assert _wait_for(lambda: len(fake.window.urls) == 1)


def test_the_guard_reports_dialogs_and_keeps_beating_where_the_page_moves():
    js = w.GUARD_JS
    for name in ("confirm", "alert", "prompt"):
        assert f"window.{name} = " in js
    assert "dialog: true" in js
    assert "visibilitychange" in js


def test_a_covered_window_keeps_its_timers():
    """A window behind another one would throttle setInterval to once a minute."""
    for switch in ("--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                   "--disable-backgrounding-occluded-windows", "IntensiveWakeUpThrottling"):
        assert switch in w.BROWSER_ARGS
    assert w.BROWSER_ARGS.count("--disable-features=") == 1


def test_no_reload_while_a_guest_is_in_the_middle_of_a_run():
    busy = [True]
    win, fake, clock = _live_window(busy=lambda: busy[0])
    clock.now += w.LOAD_GRACE + 1
    assert win.tick() is None
    time.sleep(0.05)
    assert fake.window.urls == [] and w.status()["reload_deferred"] is True
    busy[0] = False                                       # the run is printed and done
    clock.now += 2
    win.tick()
    assert _wait_for(lambda: fake.window.urls == ["http://127.0.0.1:8765/"])
    assert w.status()["reload_deferred"] is False


def test_a_page_dead_for_minutes_during_a_run_is_reloaded_after_all():
    win, fake, clock = _live_window(busy=lambda: True)
    clock.now += w.LOAD_GRACE + 1
    win.tick()
    clock.now += w.RELOAD_DEFER_MAX + 1
    win.tick()
    assert _wait_for(lambda: len(fake.window.urls) == 1)


def test_no_restart_of_the_booth_in_the_middle_of_a_run():
    busy, restarts = [True], []
    win, fake, clock = _live_window(busy=lambda: busy[0], on_stuck=lambda: restarts.append(1))
    busy[0] = False
    for _ in range(w.STUCK_AFTER):                        # reloads that did not help
        clock.now += w.LOAD_GRACE + 1
        win.tick()
    busy[0] = True                                        # a guest starts a run on the still screen
    clock.now += w.LOAD_GRACE + 1
    assert win.tick() is None and restarts == []
    busy[0] = False
    clock.now += 2
    assert win.tick() == "stuck" and restarts == [1]


def test_a_busy_check_that_fails_never_blocks_the_watchdog():
    def broken():
        raise RuntimeError("printer service gone")
    win, fake, clock = _live_window(busy=broken)
    clock.now += w.LOAD_GRACE + 1
    win.tick()
    assert _wait_for(lambda: len(fake.window.urls) == 1)


# ── own window -> Chrome without a loop ───────────────────────────────
def _plan(failures):
    return w.plan({}, camera_backend="mock", platform="win32",
                  engine=lambda: w.Engine(True, "edgechromium", "131.0.2903.70"), failures=failures)


def test_an_own_window_that_failed_three_starts_in_a_row_gives_way_to_chrome(caplog):
    d = Path(tempfile.mkdtemp(prefix="booth-fail-"))
    memory = w.FailureMemory(d / "window")
    for _ in range(w.FALLBACK_AFTER - 1):
        memory.attempt()
    assert _plan(memory.count()).mode == "own"
    memory.attempt()
    memory.note("keine Oberfläche nach 60 s")
    with caplog.at_level("WARNING", logger="booth.window"):
        p = _plan(memory.count())
    assert p.mode == "chrome" and "hintereinander" in p.fallback_reason
    assert "keine Oberfläche" in p.fallback_reason
    assert w.status()["fallback_reason"] == p.fallback_reason
    assert sum("Chrome" in r.getMessage() for r in caplog.records) == 1


def test_a_window_that_ran_a_while_forgets_its_failures(tmp_path):
    memory = w.FailureMemory(tmp_path / "window")
    for _ in range(w.FALLBACK_AFTER - 1):
        memory.attempt()
    win, fake, clock = _live_window(data=tmp_path)
    clock.now += w.STABLE_AFTER - 1
    win.heartbeat({})
    assert memory.count()[0] == w.FALLBACK_AFTER - 1
    clock.now += 2
    win.heartbeat({})
    assert memory.count()[0] == 0


def test_a_new_day_gives_the_own_window_a_new_try(tmp_path):
    memory = w.FailureMemory(tmp_path / "window")
    for _ in range(5):
        memory.attempt()
    assert memory.count(now=time.time() + w.FAILURE_MEMORY + 1)[0] == 0


# ── checked from afar ─────────────────────────────────────────────────
@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-one-api-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    b = app_mod.Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def test_the_state_says_whether_a_watcher_runs_the_booth(booth, monkeypatch):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    sup = client.get("/api/state").json()["supervisor"]
    assert sup["watched"] is False
    for key, value in waechter.child_env({"starts": 3, "restarts": 2, "last_exit_code": 1,
                                         "last_reason": "crash", "since": 1.0}, {}).items():
        monkeypatch.setenv(key, value)
    state = client.get("/api/state").json()
    assert state["supervisor"] == {**state["supervisor"], "watched": True, "restarts": 2,
                                   "last_exit_code": 1, "last_reason": "crash"}
    assert "window" in state and "failures_in_a_row" in state["window"]


def test_a_run_and_a_print_count_as_busy(booth, monkeypatch):
    assert booth.session_busy() is False
    # A run as start_session leaves it: started, not finished (no camera here to start one).
    s = SimpleNamespace(finalized=False, deleted=False, created=time.time())
    booth.sessions["run-1"] = s
    assert booth.session_busy() is True
    s.finalized = True
    assert booth.session_busy() is False
    monkeypatch.setattr(type(booth.printer), "busy", property(lambda self: True))
    assert booth.session_busy() is True


# ── texts, scripts, packaging ─────────────────────────────────────────
def test_the_wide_format_has_a_name_in_both_languages_and_both_places():
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "i18n.js").read_text(encoding="utf-8")
    assert js.count('"format.16:9":') == 2 and js.count('"format.16:9.hint":') == 2
    src = (ROOT / "app" / "booth_texts.py").read_text(encoding="utf-8")
    assert '("format.16:9",' in src and '"format.16:9": ' in src


def test_the_source_starters_stop_when_another_booth_already_runs():
    bat = (ROOT / "booth" / "scripts" / "start-booth.bat").read_text(encoding="utf-8")
    assert "%ERRORLEVEL%==4 goto end" in bat
    sh = (ROOT / "booth" / "scripts" / "start-booth.command").read_text(encoding="utf-8")
    assert '"$code" -eq 4' in sh


def _code(path):
    return "\n".join(l for l in path.read_text(encoding="utf-8-sig").splitlines() if not l.lstrip().startswith("#"))


def test_the_switch_script_works_over_ssh_and_says_what_needs_an_administrator():
    ps1 = _code(WIN / "Umstellen-auf-eigenes-Fenster.ps1")
    assert "--quit" in ps1                                   # the booth hands its camera back first
    assert "IsInRole" in ps1 and "uebersprungen" in ps1      # active hours: admin or a clear skip line
    assert "--dauerbetrieb" in ps1 and "aktivzeit" in ps1
    assert "IgnoreNew" in ps1 and "RestartCount" in ps1      # task settings checked, not only the action
    assert "fenster-fehlversuche.json" in ps1                # a fixed PC gets the own window again
    assert "Wait-BoothGone" in ps1


def test_the_exe_starts_the_watcher_and_the_watcher_carries_the_window():
    launch = (WIN / "launch_booth.py").read_text(encoding="utf-8")
    assert "waechter.should_watch" in launch and "waechter.main" in launch
    spec = _code(WIN / "photobox-booth.spec")
    assert 'collect_submodules("boothcore")' in spec          # waechter, window, all backends
    assert "console=True" in spec


def test_the_preview_is_laid_out_again_when_its_box_changes_size():
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'window.addEventListener("resize", applyFrame)' in js
    assert "new ResizeObserver" in js and "(resolution:" in js


def test_an_updated_exe_starts_as_a_new_watcher_not_as_an_orphaned_child(monkeypatch):
    """The update script starts the new .exe from inside the watched booth.

    It inherited PHOTOBOX_WATCHED=1: the new .exe then ran without a watcher
    but believed it had one - "Neustart" ended it for good, a crash too.
    """
    from boothcore import lifecycle
    for key, value in waechter.child_env({"starts": 2, "last_exit_code": 3}, {}).items():
        monkeypatch.setenv(key, value)
    env = lifecycle.respawn_kwargs()["env"]
    assert not [k for k in env if k.startswith("PHOTOBOX_WATCH")]
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert waechter.should_watch([], frozen=True, environ=env) is True


def test_the_watcher_starts_the_exe_itself_or_the_booth_from_the_source():
    import sys
    assert waechter.child_command(["--port", "1"], frozen=True) == [sys.executable, "--port", "1"]
    assert waechter.child_command(["--port", "1"], frozen=False) == [sys.executable, "-m", "boothcore", "--port", "1"]


def test_restarts_somebody_asked_for_do_not_count_against_the_own_window(booth, monkeypatch):
    """Found on the Mac: three admin restarts within two minutes switched the booth to Chrome."""
    restarts = []
    monkeypatch.setattr(booth, "_restart_later", lambda: restarts.append(1) or "Neustart in 2 s")
    memory = w.FailureMemory(booth.data / "window")
    for _ in range(w.FALLBACK_AFTER + 2):
        memory.attempt()                          # the window of this process starts ...
        booth.restart_on_purpose()                # ... and the operator restarts it at once
    assert memory.count()[0] == 0 and len(restarts) == w.FALLBACK_AFTER + 2
    memory.attempt()
    w.FailureMemory(booth.data / "window").settle()
    w.FailureMemory(booth.data / "window").settle()   # once per process, not once per call
    assert memory.count()[0] == 0
    memory.attempt()
    memory.attempt()                              # a crash: the next process counts on
    w.ended_on_purpose(booth.data)
    assert memory.count()[0] == 1


# ── goodbye to the gallery (feat/alarm-akku-vormerken) under the watcher ──
@pytest.fixture()
def fresh_lifecycle(monkeypatch):
    from boothcore import lifecycle
    monkeypatch.setattr(lifecycle, "_announced", [])
    monkeypatch.setattr(lifecycle, "_code", [])
    monkeypatch.setattr(lifecycle, "_hooks", [])
    monkeypatch.setattr(lifecycle, "_leaving", threading.Lock())
    return lifecycle


@pytest.mark.parametrize("code,expected", [
    (waechter.QUIT_CODE, ("quit", False)),          # "Beenden", --quit: not coming back
    (waechter.RESTART_CODE, ("restart", True)),     # "Neustart", restart_app, a dead web view
    (1, ("error", True)),                           # web server died: the watcher brings it back
])
def test_the_goodbye_follows_the_exit_code_table(fresh_lifecycle, monkeypatch, code, expected):
    said = []
    monkeypatch.setattr(fresh_lifecycle.os, "_exit", lambda c: said.append(("exit", c)))
    fresh_lifecycle.before_exit(lambda: said.append(fresh_lifecycle.why(fresh_lifecycle.exit_code())), first=True)
    fresh_lifecycle.leave(code)
    fresh_lifecycle.leave(code)                     # a second way out says nothing more
    assert said == [expected, ("exit", code)]


def test_the_watcher_itself_never_says_goodbye():
    src = (ROOT / "booth" / "boothcore" / "waechter.py").read_text(encoding="utf-8")
    assert ".goodbye(" not in src and "going_down(" not in src and "say_goodbye" not in src


def test_session_end_reaches_the_booth_over_the_instance_line_and_leaves_once():
    from boothcore import __main__ as start
    d = Path(tempfile.mkdtemp(prefix="booth-end-"))
    instance = w.claim_instance(d, "own")
    ended = []
    booth = SimpleNamespace(end_session=ended.append)
    try:
        start.wire_instance(instance, booth, delay=0.0)
        line = w.SingleInstance(d / "window")
        assert line.send("end:windows_shutdown") == "ok"
        assert line.send("end:windows_logoff") == "ok"
        assert _wait_for(lambda: len(ended) == 2)
    finally:
        instance.release()
    assert ended == ["windows_shutdown", "windows_logoff"]


def test_ending_the_session_announces_why_and_leaves_with_code_zero(booth, fresh_lifecycle, monkeypatch):
    codes = []
    monkeypatch.setattr(booth, "shutdown", codes.append)
    memory = w.FailureMemory(booth.data / "window")
    memory.attempt()
    booth.end_session("windows_logoff")
    assert memory.count()[0] == 0                   # Windows ending the session is no window failure
    booth.end_session("signal")                     # the first word counts
    assert fresh_lifecycle.why(0) == ("windows_logoff", False) and codes[0] == 0


def test_the_watcher_forwards_a_session_end_and_restarts_nothing_after_it():
    sent, record = [], {}
    stop = threading.Event()
    codes = iter([1, 1, 1])

    def start():
        waechter.forward_session_end("windows_shutdown", send=lambda c: sent.append(c) or "ok",
                                     child_gone=lambda: True, stop=stop)
        return next(codes)                          # Windows kills the box before it is done
    pauses = []
    assert waechter.watch(start, port_busy=lambda: False, sleep=pauses.append, record=record, stop=stop) == 0
    assert sent == ["end:windows_shutdown"] and pauses == [] and record["starts"] == 1


def test_a_box_whose_watcher_is_gone_leaves_in_order():
    alive = iter([True, True, False])
    gone = []
    waechter.watch_parent(lambda: next(alive), gone.append, sleep=lambda s: None, rounds=10)
    assert gone == ["signal"]


def test_the_watcher_loads_no_window_library():
    """A console process that loaded user32/gdi32 gets no sign-out/shutdown event."""
    src = (ROOT / "booth" / "boothcore" / "waechter.py").read_text(encoding="utf-8")
    main_src = src[src.index("def main("):]
    assert "_hide_own_console(" not in main_src and "windll.user32" not in src
    launch = (WIN / "launch_booth.py").read_text(encoding="utf-8")
    top = launch[:launch.index('if __name__ == "__main__":')]
    assert "boothcore.__main__" not in top                     # the booth's imports only in the booth
    env = waechter.child_env({"hide_console": True}, {})
    assert env[waechter.HIDE_CONSOLE_ENV] == "1"
    assert waechter.HIDE_CONSOLE_ENV not in waechter.child_env({}, {})


def test_the_booth_hides_the_console_the_watcher_asked_it_to():
    hidden = []
    assert w.hide_console_for("own", {waechter.CHILD_ENV: "1", waechter.HIDE_CONSOLE_ENV: "1"},
                              hide=lambda: hidden.append(1)) is True
    assert w.hide_console_for("chrome", {waechter.CHILD_ENV: "1", waechter.HIDE_CONSOLE_ENV: "1"},
                              hide=lambda: hidden.append(1)) is True
    assert w.hide_console_for("none", {waechter.CHILD_ENV: "1", waechter.HIDE_CONSOLE_ENV: "1"},
                              hide=lambda: hidden.append(1)) is False
    assert w.hide_console_for("own", {waechter.CHILD_ENV: "1"}, hide=lambda: hidden.append(1)) is False
    assert hidden == [1, 1]


def test_alt_f4_says_no_goodbye_but_windows_shutting_down_is_let_through(fresh_lifecycle):
    ends = []
    win = w.OwnWindow("http://127.0.0.1:8765/", Path(tempfile.mkdtemp()), w.settings({}), webview=FakeWebview(),
                      on_session_end=ends.append, shutting_down=lambda: False)
    assert win.on_closing() is False and ends == [] and not fresh_lifecycle._leaving.locked()
    win.shutting_down = lambda: True
    assert win.on_closing() is True and _wait_for(lambda: ends == ["windows_shutdown"])
    assert win.on_closing() is True and win.result == "session_end"
    time.sleep(0.05)
    assert ends == ["windows_shutdown"]                                  # once
