"""The booth shows itself in its own window, not in a browser.

Owner's decision on 15.11.2026: "Bau alles auf das eigene Programm um - auch
den PC." The booth hosts its kiosk page in a native window (pywebview, on
Windows the Edge WebView2 runtime). Chrome is left only as the automatic
fallback and as an explicit setting.

No screen here: the web view is replaced by a fake that records what the
booth asked of it. What a real window does on the booth PC is on the test
list in docs/EIGENES-FENSTER.md.
"""
import json
import logging
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from boothcore import window as w

ROOT = Path(__file__).resolve().parents[2]


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


# ── which mode ──────────────────────────────────────────────────────────
def test_the_own_window_is_the_default_for_new_and_existing_booths():
    from boothcore.config import DEFAULT_LOCAL, Store
    assert DEFAULT_LOCAL["window"]["mode"] == "own"
    d = Path(tempfile.mkdtemp(prefix="booth-window-"))
    # An installation from before the window existed: no "window" section.
    (d / "local.json").write_text(json.dumps({"kiosk": True, "port": 8765}), encoding="utf-8")
    assert w.requested_mode(Store(d).local) == "own"


def test_the_command_line_beats_local_json_and_no_kiosk_beats_both():
    local = {"window": {"mode": "chrome"}}
    assert w.requested_mode(local) == "chrome"
    assert w.requested_mode(local, cli="own") == "own"
    assert w.requested_mode(local, cli="own", no_kiosk=True) == "none"


def test_the_old_kiosk_switch_still_means_no_screen():
    assert w.requested_mode({"kiosk": False, "window": {"mode": "own"}}) == "none"


def test_an_unknown_mode_is_the_own_window():
    assert w.requested_mode({"window": {"mode": "firefox"}}) == "own"


def test_the_window_mode_cannot_be_set_from_the_gallery():
    from boothcore.config import REMOTE_LOCAL_KEYS
    assert not any(k.startswith("window") for k in REMOTE_LOCAL_KEYS)


def test_without_webview2_the_booth_falls_back_to_chrome_with_one_clear_line(caplog):
    caplog.set_level(logging.WARNING, logger="booth.window")
    p = w.plan({}, camera_backend="mock", platform="win32",
               engine=lambda: w.Engine(False, "edgechromium", reason="WebView2-Laufzeit von Microsoft fehlt"))
    assert (p.requested, p.mode) == ("own", "chrome")
    assert "WebView2" in p.fallback_reason
    lines = [r for r in caplog.records if r.name == "booth.window"]
    assert len(lines) == 1 and "Chrome" in lines[0].getMessage()
    assert w.status()["fallback_reason"] == p.fallback_reason


def test_with_webview2_the_own_window_is_used():
    p = w.plan({}, camera_backend="edsdk", platform="win32",
               engine=lambda: w.Engine(True, "edgechromium", "131.0.2903.70"))
    assert p.mode == "own" and not p.fallback_reason
    s = w.status()
    assert (s["mode"], s["backend"], s["backend_version"]) == ("own", "edgechromium", "131.0.2903.70")


def test_a_mac_with_the_canon_sdk_keeps_chrome_because_both_need_the_main_thread():
    assert w.plan({}, camera_backend="auto", platform="darwin",
                  engine=lambda: w.Engine(True, "cocoa")).mode == "chrome"
    assert w.plan({}, camera_backend="mock", platform="darwin",
                  engine=lambda: w.Engine(True, "cocoa")).mode == "own"


def test_webview2_is_found_in_the_registry_and_a_broken_entry_is_not_a_runtime():
    key = w.WEBVIEW2_KEYS[0]
    found = {("HKEY_LOCAL_MACHINE", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{key}"): "131.0.2903.70"}
    assert w.webview2_version(lambda h, p: found.get((h, p), "")) == "131.0.2903.70"
    assert w.webview2_version(lambda h, p: "0.0.0.0") == ""
    assert w.webview2_version(lambda h, p: "") == ""


def test_the_engine_check_never_raises():
    def broken(name):
        raise ImportError("no pythonnet")
    assert not w.detect_engine("win32", importer=broken).ok
    fake = SimpleNamespace(__version__="6.2.1")
    assert not w.detect_engine("win32", importer=lambda n: fake, wv2=lambda: "").ok
    assert w.detect_engine("win32", importer=lambda n: fake, wv2=lambda: "131.0.1.1").ok
    assert not w.detect_engine("linux", importer=lambda n: fake).ok


# ── one booth per machine ───────────────────────────────────────────────
def test_a_second_start_brings_the_first_window_forward_and_leaves():
    d = Path(tempfile.mkdtemp(prefix="booth-instance-"))
    first = w.SingleInstance(d)
    assert w.claim(first)
    first.serve()
    shown = []
    first.handlers["show"] = lambda: shown.append(1) or "ok"
    second = w.SingleInstance(d)
    assert w.claim(second, wait=1.0) is False
    assert shown == [1]
    first.release()
    assert w.claim(w.SingleInstance(d), wait=1.0)


def test_a_successor_waits_while_the_old_booth_is_leaving(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-instance-"))
    first = w.SingleInstance(d)
    assert first.acquire()
    first.serve()
    monkeypatch.setattr(w, "_leaving", lambda: True)
    slept = []

    def sleep(s):
        slept.append(s)
        if len(slept) == 3:
            first.release()          # the old process has ended
    assert w.claim(w.SingleInstance(d), wait=30.0, sleep=sleep)
    assert len(slept) == 3


def test_a_hung_booth_that_holds_the_lock_is_given_up_on():
    d = Path(tempfile.mkdtemp(prefix="booth-instance-"))
    first = w.SingleInstance(d)
    assert first.acquire()           # holds the lock, never answers
    clock = Clock()

    def sleep(s):
        clock.now += s
    assert w.claim(w.SingleInstance(d), wait=5.0, sleep=sleep, clock=clock) is False
    first.release()


# ── heartbeat, reload, server death ────────────────────────────────────
def test_a_silent_page_is_reloaded_once_per_silence():
    clock = Clock()
    dog = w.Watchdog(timeout=15, grace=25, clock=clock)
    assert not dog.due()                       # not armed before the first load
    dog.arm()
    clock.now += 24
    assert not dog.due()                       # grace for the first paint
    clock.now += 2
    assert dog.due() and dog.reloads == 1
    assert not dog.due()                       # the reload gets its own grace
    clock.now += 10
    dog.beat()
    clock.now += 14
    assert not dog.due()
    clock.now += 2
    assert dog.due() and dog.reloads == 2


@pytest.mark.parametrize("dies", ["raises", "returns"])
def test_a_dead_web_server_ends_the_program_with_a_failure_code(dies):
    codes = []
    done = threading.Event()

    def serve():
        if dies == "raises":
            raise OSError("port taken")

    def on_exit(code):
        codes.append(code)
        done.set()
    w.run_server(serve, on_exit)
    assert done.wait(5)
    assert codes == [1]


def test_the_splash_stays_until_the_server_answers():
    calls = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(url, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("refused")
        return Resp()
    assert w.wait_for_server("http://127.0.0.1:1/", sleep=lambda s: None, opener=opener)
    assert len(calls) == 3


# ── the window, with the web view faked ────────────────────────────────
class FakeEvent(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeWindow:
    def __init__(self, **kw):
        self.kw = kw
        self.events = SimpleNamespace(closing=FakeEvent(), loaded=FakeEvent())
        self.exposed = []
        self.urls = []
        self.scripts = []
        self.on_top = False
        self.destroyed = False

    def expose(self, *fns):
        self.exposed += fns

    def load_url(self, url):
        self.urls.append(url)

    def evaluate_js(self, script):
        self.scripts.append(script)

    def restore(self):
        pass

    def show(self):
        self.shown = True

    def destroy(self):
        self.destroyed = True


class FakeWebview:
    def __init__(self, fail=False):
        self.settings = {}
        self.fail = fail
        self.screens = [SimpleNamespace(x=1920, y=0, width=1280, height=800, scale=1.0),
                        SimpleNamespace(x=0, y=0, width=3840, height=2160, scale=2.0)]

    def create_window(self, title, **kw):
        self.window = FakeWindow(title=title, **kw)
        return self.window

    def start(self, func=None, **kw):
        if self.fail:
            raise RuntimeError("WebView2 initialisation failed")
        self.start_kw = kw


def _window(opts=None, fake=None, tmp=None):
    fake = fake or FakeWebview()
    data = Path(tmp or tempfile.mkdtemp(prefix="booth-window-"))
    win = w.OwnWindow("http://127.0.0.1:8765/", data, {**w.settings({}), **(opts or {})}, webview=fake)
    return win, fake, data


def test_the_window_is_frameless_fullscreen_dark_and_not_a_browser(monkeypatch):
    monkeypatch.delenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", raising=False)
    win, fake, data = _window()
    assert win.start() == "closed"
    kw = fake.window.kw
    assert kw["title"] == "Photobox"
    assert kw["fullscreen"] and kw["frameless"]
    assert kw["zoomable"] is False and kw["text_select"] is False and kw["easy_drag"] is False
    assert kw["background_color"].upper() != "#FFFFFF"           # no white flash
    assert "Photobox startet" in kw["html"]                        # the splash
    assert fake.settings["ALLOW_DOWNLOADS"] is False
    # Persistent profile inside the booth's data folder, not a private session.
    assert fake.start_kw["private_mode"] is False
    assert Path(fake.start_kw["storage_path"]).is_relative_to(data)
    assert fake.start_kw["debug"] is False
    import os
    args = os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
    assert "--autoplay-policy=no-user-gesture-required" in args
    assert args.count("--disable-features=") == 1 and "ElasticOverscroll" in args


def test_the_window_opens_on_the_primary_screen_unless_another_is_set():
    win, fake, _ = _window()
    win.start()
    assert fake.window.kw["screen"].x == 0 and w.status()["screen"]["width"] == 3840
    win, fake, _ = _window({"screen": 0})
    win.start()
    assert fake.window.kw["screen"].x == 1920


def test_alt_f4_is_refused_in_kiosk_mode_and_allowed_with_debug():
    win, fake, _ = _window()
    win.start()
    assert [fn() for fn in fake.window.events.closing] == [False]
    win, fake, _ = _window({"debug": True})
    win.start()
    assert [fn() for fn in fake.window.events.closing] == [True]
    assert fake.start_kw["debug"] is True


def test_every_page_gets_the_guard_and_the_guard_beats():
    win, fake, _ = _window()
    win.start()
    assert win.heartbeat in fake.window.exposed
    for fn in fake.window.events.loaded:
        fn()
    script = fake.window.scripts[-1]
    assert "pywebview.api.heartbeat" in script and "ctrlKey" in script and "touches.length > 1" in script


def test_a_heartbeat_marks_the_window_alive_and_reports_what_the_page_sees():
    win, fake, _ = _window()
    win.start()
    w._set(window_created=True, mode="own")
    assert w.status()["alive"] is False
    win.heartbeat({"w": 1280, "h": 800, "dpr": 1.5, "autoplay_sound": "allowed", "evil": {"x": 1}})
    s = w.status()
    assert s["alive"] is True and s["page"] == {"w": 1280, "h": 800, "dpr": 1.5, "autoplay_sound": "allowed"}


def test_a_window_that_never_shows_the_page_hands_over_to_chrome():
    clock = Clock()
    win, fake, _ = _window()
    win.clock = clock
    win.watchdog = w.Watchdog(clock=clock)
    win.start()
    win.window = fake.window
    win._first_load = clock.now
    win.watchdog.arm()
    clock.now += w.START_FALLBACK + 1
    assert win.tick() == "fallback"
    win.give_up("test")
    assert fake.window.destroyed and win.allow_close and win.result == "fallback"
    assert w.status()["mode"] == "chrome"


def test_the_watchdog_reloads_the_kiosk_page():
    clock = Clock()
    win, fake, _ = _window()
    win.clock = clock
    win.watchdog = w.Watchdog(clock=clock)
    win.start()
    win.window = fake.window
    win._first_load = clock.now
    win.ever_beat = True
    win.watchdog.arm()
    clock.now += w.LOAD_GRACE + 1
    assert win.tick() is None
    for _ in range(50):
        if fake.window.urls:
            break
        time.sleep(0.02)
    assert fake.window.urls == ["http://127.0.0.1:8765/"]
    assert w.status()["reloads"] == 1


def test_an_engine_that_cannot_start_means_chrome():
    opened = []
    data = Path(tempfile.mkdtemp(prefix="booth-window-"))
    result = w.run_own("http://127.0.0.1:8765/", data, w.settings({}), None,
                       open_chrome=opened.append, webview=FakeWebview(fail=True))
    assert result == "fallback"
    for _ in range(50):
        if opened:
            break
        time.sleep(0.02)
    assert opened == ["http://127.0.0.1:8765/"]
    assert "WebView2" in w.status()["fallback_reason"]


def test_a_second_start_shows_the_running_window():
    win, fake, _ = _window({"on_top": False})
    win.start()
    win.window = fake.window
    assert win.show() == "ok" and fake.window.shown and fake.window.on_top is False


# ── checked from afar: /api/state, self-test, screenshot ───────────────
@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-window-api-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def _fake_grab():
    from PIL import Image
    return Image.new("RGB", (64, 40), (12, 12, 14))


def test_the_state_says_which_window_shows_the_booth_and_whether_it_lives(client):
    w.plan({}, camera_backend="mock", platform="win32",
           engine=lambda: w.Engine(True, "edgechromium", "131.0.2903.70"))
    w._set(window_created=True)
    state = client.get("/api/state").json()["window"]
    assert state["mode"] == "own" and state["backend"] == "edgechromium"
    assert {"alive", "last_heartbeat", "reloads", "fallback_reason", "heartbeat_age"} <= set(state)
    assert state["alive"] is False                 # no heartbeat yet


def test_the_screenshot_needs_the_pin_and_lands_in_the_data_folder(client, booth, monkeypatch):
    assert client.get("/api/admin/window/screenshot.png").status_code == 401
    from PIL import ImageGrab
    monkeypatch.setattr(ImageGrab, "grab", _fake_grab)
    token = client.post("/api/admin/login", json={"pin": "468213"}).json()["token"]
    r = client.get("/api/admin/window/screenshot.png", headers={"X-Admin-Token": token})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert (booth.data / w.SCREENSHOT_FILE).is_file()


def test_the_selftest_of_the_running_booth_writes_its_facts_and_a_picture(booth):
    w.plan({}, camera_backend="mock", platform="win32",
           engine=lambda: w.Engine(True, "edgechromium", "131.0.2903.70"))
    w._set(window_created=True, last_heartbeat=time.time(), page={"w": 1280, "h": 800, "dpr": 1.0})
    report = w.selftest_report(booth.data, grab=_fake_grab)
    saved = json.loads((booth.data / w.SELFTEST_FILE).read_text(encoding="utf-8"))
    assert saved["ok"] is True and saved["mode"] == "own" and saved["alive"] is True
    assert saved["page"]["w"] == 1280 and saved["screenshot"]["width"] == 64
    assert report["running"] is True


def test_the_selftest_from_the_command_line_asks_the_running_booth(capsys):
    d = Path(tempfile.mkdtemp(prefix="booth-selftest-"))
    instance = w.claim_instance(d, "own")
    assert instance is not None
    w._set(mode="chrome", fallback_reason="WebView2-Laufzeit von Microsoft fehlt")
    import boothcore.window as mod
    real = mod.selftest_report
    mod.selftest_report = lambda data, **kw: real(data, take_screenshot=False)
    try:
        code = w.selftest_cli(d, {}, wait=5.0)
    finally:
        mod.selftest_report = real
        instance.release()
    out = json.loads(capsys.readouterr().out)
    assert out["running"] is True and out["fallback_reason"].startswith("WebView2")
    assert code == 0                               # Chrome mode, running: nothing to fix here


def test_the_selftest_without_a_running_booth_says_how_to_start_it(capsys):
    d = Path(tempfile.mkdtemp(prefix="booth-selftest-"))
    assert w.selftest_cli(d, {}) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["running"] is False and "schtasks /Run" in out["hint"]
    assert (d / w.SELFTEST_FILE).is_file()


def test_a_web_view_that_stays_dead_after_reloads_restarts_the_booth():
    """Killing msedgewebview2.exe leaves the control dead; only a new process helps."""
    clock = Clock()
    restarts = []
    win, fake, _ = _window()
    win.clock = clock
    win.watchdog = w.Watchdog(clock=clock)
    win.on_stuck = lambda: restarts.append(1)
    win.start()
    win.window = fake.window
    win._first_load = clock.now
    win.ever_beat = True
    win.watchdog.arm()
    results = []
    for _ in range(w.STUCK_AFTER + 2):
        clock.now += w.LOAD_GRACE + 1
        results.append(win.tick())
    assert results[:w.STUCK_AFTER] == [None] * w.STUCK_AFTER
    assert results[w.STUCK_AFTER:] == ["stuck", "stuck"]
    assert restarts == [1]                         # once, not once per tick


def test_a_heartbeat_between_reloads_means_the_reload_helped():
    clock = Clock()
    dog = w.Watchdog(clock=clock)
    dog.arm()
    clock.now += w.LOAD_GRACE + 1
    assert dog.due() and dog.silent_reloads == 1
    dog.beat()
    assert dog.silent_reloads == 0


# ── packaging and the PC ──────────────────────────────────────────────
WIN = ROOT / "packaging" / "windows"


def _code(path):
    return "\n".join(l for l in path.read_text(encoding="utf-8-sig").splitlines() if not l.lstrip().startswith("#"))


def test_the_exe_carries_the_web_view_and_says_who_made_it():
    spec = _code(WIN / "photobox-booth.spec")
    for needed in ('"webview.platforms.winforms"', '"webview.platforms.edgechromium"', '"clr"', "'pywebview'",
                   "version=VERSION_FILE", '"version_resource.py"', "binaries=_webview_binaries"):
        assert needed in spec, needed
    # Who made it lives in the resource both specs share.
    resource = _code(WIN / "version_resource.py")
    assert 'COMPANY_NAME = "Till Heidrich"' in resource and 'PRODUCT_NAME = "Photobox"' in resource


def test_the_web_view_is_installed_for_the_windows_build_only():
    req = (ROOT / "booth" / "requirements.txt").read_text(encoding="utf-8")
    line = next(l for l in req.splitlines() if l.startswith("pywebview"))
    assert 'sys_platform == "win32"' in line


def test_the_switch_script_for_the_pc_does_what_the_owner_asked():
    ps1 = _code(WIN / "Umstellen-auf-eigenes-Fenster.ps1")
    assert "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" in ps1            # WebView2 runtime key
    assert '$TaskName = "Photobox Booth"' in ps1
    from boothcore.winsetup import TASK_NAME
    assert TASK_NAME == "Photobox Booth"
    assert "Set-ScheduledTask" in ps1 and "--window-selftest" in ps1
    assert "photobox-kiosk" in ps1 and "Move-Item" in ps1               # old starters moved, not deleted
    assert "umstellung-fenster.log" in ps1
    assert "Umstellen-auf-eigenes-Fenster.ps1" in _code(WIN / "alles-einrichten.ps1")


def test_the_operator_page_explains_the_window_in_german():
    doc = (ROOT / "docs" / "EIGENES-FENSTER.md").read_text(encoding="utf-8")
    for needed in ("Notausgang", "Alt+F4", "--window-selftest", "window.reloads", '"mode": "own"'):
        assert needed in doc, needed
    guide = (ROOT / "docs" / "WINDOWS-INSTALLATION.md").read_text(encoding="utf-8")
    assert "EIGENES-FENSTER.md" in guide
