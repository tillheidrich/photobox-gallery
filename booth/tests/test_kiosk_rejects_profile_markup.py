"""Second review R2-U1, booth side: markup in a profile never runs in the kiosk.

Three layers, each tested on its own:

1. the booth drops profile values outside the rules when it loads
   ``profile.json`` or takes a profile from the gallery (and still starts);
2. the kiosk escapes what it writes into HTML — a static check fails on a
   new unescaped ``${…}`` in app.js markup (tools/check_kiosk_escaping.py);
3. the kiosk and the admin pages carry a Content-Security-Policy without
   inline script, and no page has an inline script or handler left.

Plus the reviewer's proof end to end: a booth started with the payload in
``profile.json``, its kiosk opened in headless Chrome (skipped without Chrome).
"""
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boothcore import config
from boothcore.app import PAGE_CSP, create_app

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "booth" / "boothcore" / "web"
PAYLOAD = 'photo" autofocus onfocus="document.documentElement.setAttribute(\'data-pwned\',\'1\')" x="'


def _store_with(tmp_path, profile):
    (tmp_path / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    return config.Store(tmp_path)


def test_a_tampered_profile_file_loses_the_markup_and_the_booth_still_starts(tmp_path, caplog):
    config._PROFILE_WARNED.clear()
    with caplog.at_level(logging.WARNING):
        store = _store_with(tmp_path, {"modes": ["photo", PAYLOAD, "gif"],
                                       "looks": ["original", 'x"><img src=x onerror=alert(1)>'],
                                       "formats": ["4:5", "<b>"],
                                       "theme": {"accent": "red;}body{display:none"},
                                       "countdown": {"first": "drei"}})
    p = store.profile
    assert p["modes"] == ["photo", "gif"]
    assert p["looks"] == ["original"]
    assert p["formats"] == ["4:5"]
    assert p["theme"]["accent"] == config.DEFAULT_PROFILE["theme"]["accent"]
    assert p["countdown"]["first"] == config.DEFAULT_PROFILE["countdown"]["first"]
    assert "ungültige Werte verworfen" in caplog.text
    # logged once, not on every load
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        _store_with(tmp_path, {"modes": ["photo", PAYLOAD, "gif"], "looks": ["original", 'x"><img src=x onerror=alert(1)>'],
                               "formats": ["4:5", "<b>"], "theme": {"accent": "red;}body{display:none"},
                               "countdown": {"first": "drei"}})
    assert "ungültige Werte verworfen" not in caplog.text


def test_a_profile_from_the_gallery_with_markup_is_cleaned_before_it_is_stored(tmp_path):
    store = config.Store(tmp_path)
    store.apply_profile({"modes": [PAYLOAD], "guest_text": {"modes": [PAYLOAD, "photo"]}})
    assert PAYLOAD not in json.dumps(store.profile)
    assert store.profile["guest_text"]["modes"] == ["photo"]
    on_disk = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert PAYLOAD not in json.dumps(on_disk)


def test_the_kiosk_writes_no_unescaped_value_into_markup():
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "check_kiosk_escaping.py")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


def test_the_check_would_have_caught_the_reviewers_line(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import check_kiosk_escaping as check
    (tmp_path / "app.js").write_text(
        'const x = p.modes.map((m) => `<button data-mode="${m}">${esc(m)}</button>`);\n', encoding="utf-8")
    monkeypatch.setattr(check, "WEB", tmp_path)
    assert check.findings() == ["app.js: ${m}"]


@pytest.mark.parametrize("path", ["/", "/admin", "/admin/hilfe", "/admin/vorlagen"])
def test_every_page_carries_a_csp_without_inline_script(tmp_path, monkeypatch, path):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    with TestClient(create_app()) as client:
        r = client.get(path)
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert csp == PAGE_CSP
    script = re.search(r"script-src ([^;]*)", csp).group(1)
    assert script.strip() == "'self'"
    assert "object-src 'none'" in csp and "base-uri 'none'" in csp


@pytest.mark.parametrize("page", ["index.html", "admin.html", "hilfe.html", "vorlagen.html"])
def test_no_page_has_an_inline_script_or_handler(page):
    html = (WEB / page).read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> left"
    assert not re.search(r"\son[a-z]+\s*=", html), "inline event handler left"
    assert "javascript:" not in html


def test_the_kiosk_script_sets_no_inline_handler():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"<[a-z][^>`]*\son[a-z]+=", js), "an on…= attribute in kiosk markup"


# ── The reviewer's proof, end to end, in headless Chrome ─────────────

CHROME = next((p for p in (os.environ.get("CHROME"),
                           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                           shutil.which("google-chrome"), shutil.which("chromium"))
               if p and Path(p).exists()), None)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not CHROME, reason="needs Google Chrome or Chromium")
def test_in_chrome_the_payload_does_not_run_and_the_modes_render(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "profile.json").write_text(json.dumps({"modes": ["photo", PAYLOAD, "gif"]}), encoding="utf-8")
    port = _free_port()
    env = dict(os.environ, BOOTH_DATA=str(data), BOOTH_CAMERA="mock",
               PYTHONPATH=os.pathsep.join([str(ROOT / "booth"), str(ROOT / "sync")]))
    booth = subprocess.Popen([sys.executable, "-m", "boothcore", "--no-kiosk", "--port", str(port)],
                             cwd=ROOT / "booth", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}/"
        deadline = time.monotonic() + 30
        while True:
            try:
                urllib.request.urlopen(url + "api/about", timeout=2).read()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        dom = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                              f"--user-data-dir={tmp_path / 'chrome'}", "--virtual-time-budget=6000",
                              "--dump-dom", url], capture_output=True, text=True, timeout=60).stdout
    finally:
        booth.terminate()
        booth.wait(timeout=20)
    assert "data-pwned" not in dom
    modes = re.findall(r'<button class="mode"[^>]*data-mode="([^"]*)"', dom)
    assert modes == ["photo", "gif"], modes
    assert "onfocus" not in dom
