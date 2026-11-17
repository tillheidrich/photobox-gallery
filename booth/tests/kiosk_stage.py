"""The kiosk page in headless Chrome at a given window size, measured.

No Playwright needed (the machines that run the suites mostly have none, so
those tests skip): Chrome itself, ``--dump-dom`` and ``--screenshot``. A small
HTTP server hands out the booth's real web folder and a ``/api/state`` the
test chose; ``index.html`` gets one extra script, ``/__measure.js``, that
writes the boxes of the elements named in ``MEASURE`` into
``<html data-measure>`` after the page has settled.

Used by test_start_screen_fits_small_windows.py and, for before/after
pictures, by hand:

    python booth/tests/kiosk_stage.py out-dir 1024x768 [en] [hell]
"""
from __future__ import annotations

import html
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
# Pictures of an older state of the page: point this at an exported web folder.
if os.environ.get("KIOSK_STAGE_WEB"):
    WEB = Path(os.environ["KIOSK_STAGE_WEB"])

CHROME = next((p for p in (os.environ.get("CHROME"),
                           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                           shutil.which("google-chrome"), shutil.which("chromium"),
                           shutil.which("chromium-browser"))
               if p and Path(p).exists()), None)

MEASURE = {
    "notice": "#notice",
    "status": "#statusline",
    "tools": "#s-start .start-tools",
    "lock": "#adminHotspot",
    "title": "#s-start .start-copy h1",
    "copy": "#s-start .start-copy",
    "modes": "#modes",
}

MEASURE_JS = """
(function () {
  function box(sel) {
    var e = document.querySelector(sel);
    if (!e || e.hidden || getComputedStyle(e).display === 'none' || getComputedStyle(e).visibility === 'hidden') return null;
    var b = e.getBoundingClientRect();
    return b.width && b.height ? [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)] : null;
  }
  function measure() {
    var out = {w: innerWidth, h: innerHeight, screen: document.getElementById('app').dataset.screen,
               lang: document.documentElement.lang, text: (document.getElementById('noticeText') || {}).textContent || ''};
    var sel = %s;
    for (var k in sel) out[k] = box(sel[k]);
    out.tiles = [].slice.call(document.querySelectorAll('#modes .mode')).map(function (m) {
      var b = m.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)]; });
    var s = document.getElementById('s-start');
    out.scroll = s ? [s.scrollHeight, s.clientHeight] : null;
    document.documentElement.setAttribute('data-measure', JSON.stringify(out));
  }
  // The first state arrives on the boot screen; the notice follows with the
  // next poll (every 4 s).
  setTimeout(measure, 5000);
})();
""" % json.dumps(MEASURE)


def booth_state(base: Path, camera_code: str = "camera.off", lang: str = "de", scheme: str = "dunkel",
                **profile) -> dict:
    """/api/state of a real booth with the mock camera, the camera then reported not ready."""
    from fastapi.testclient import TestClient

    from boothcore.app import Booth, create_app
    os.environ.setdefault("BOOTH_CAMERA", "mock")
    b = Booth(base=base, start_threads=False)
    try:
        st = TestClient(create_app(b)).get("/api/state").json()
    finally:
        b.mail.stop()
    st["camera"]["state"] = "error"
    st["camera"]["error_code"] = camera_code
    st["profile"]["languages"] = ["de", "en"]
    st["profile"].setdefault("theme", {})["mode"] = scheme
    # The full corner cluster of the report: status line, "Galerie", "EN", visible lock.
    st["gallery_count"] = 12
    st["profile"]["gallery_on_booth"] = True
    st["profile"].setdefault("start", {})["show_gallery_button"] = True
    st["profile"].setdefault("access", {})["lock_icon"] = True
    st["profile"].update(profile)
    st["kiosk_lang"] = lang
    return st


@contextmanager
def serve(state: Optional[dict], extra_js: str = "", page_name: str = "index.html", api=None,
          measure_js: Optional[str] = None) -> Iterator[str]:
    """The web folder, ``state`` as /api/state, the measuring script in ``page_name``.

    ``api``: a TestClient of a real booth; /api/ and /assets/ go to it then
    (one request at a time), so a page like vorlagen.html runs against it.
    """
    page = (WEB / page_name).read_text(encoding="utf-8").replace(
        "</body>", '<script src="/__measure.js"></script></body>')
    body_state = json.dumps(state).encode()
    measure = ((MEASURE_JS if measure_js is None else measure_js) + extra_js).encode()
    api_lock = threading.Lock()

    def forward(handler, method: str):
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else None
        headers = {k: v for k, v in handler.headers.items() if k.lower() in ("x-admin-token", "content-type")}
        with api_lock:
            r = api.request(method, handler.path, content=body, headers=headers)
        handler.send_response(r.status_code)
        for k, v in r.headers.items():
            if k.lower() in ("content-type", "cache-control", "retry-after"):
                handler.send_header(k, v)
        handler.send_header("Content-Length", str(len(r.content)))
        handler.end_headers()
        handler.wfile.write(r.content)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB), **kw)

        def log_message(self, *a):
            pass

        def _send(self, data: bytes, kind: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/" + page_name):
                return self._send(page.encode(), "text/html; charset=utf-8")
            if path == "/__measure.js":
                return self._send(measure, "text/javascript")
            if path == "/api/state" and state is not None:
                return self._send(body_state, "application/json")
            if path.startswith(("/api/", "/assets/")):
                if api is not None:
                    return forward(self, "GET")
                return self._send(b"{}", "application/json", 404)
            return super().do_GET()

        def do_POST(self):  # noqa: N802
            if api is not None and self.path.startswith("/api/"):
                return forward(self, "POST")
            return self._send(b"{}", "application/json")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.shutdown()
        server.server_close()


_CHROME_KEEPS = [0]      # window height headless Chrome does not give the page, learnt once


def chrome(url: str, size: str, screenshot: Optional[Path] = None, budget_ms: int = 9000) -> Dict:
    """Load ``url`` at ``size`` ("1024x768"); return the measured boxes (and save a picture)."""
    w, h = (int(n) for n in size.split("x"))
    profile = tempfile.mkdtemp(prefix="kiosk-stage-chrome-")

    def args(height: int):
        return [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                f"--user-data-dir={profile}", f"--window-size={w},{height}", "--force-device-scale-factor=1",
                f"--virtual-time-budget={budget_ms}"]

    def dump(height: int) -> Dict:
        dom = subprocess.run(args(height) + ["--dump-dom", url], capture_output=True, text=True, timeout=90).stdout
        m = re.search(r'data-measure="([^"]*)"', dom)
        if not m:
            raise AssertionError("the page did not finish: " + dom[:400])
        return json.loads(html.unescape(m.group(1)))

    try:
        # Headless Chrome keeps some of the window height for itself; ask
        # again with the difference added so the page gets exactly h.
        height = h + _CHROME_KEEPS[0]
        got = dump(height)
        if got["h"] != h:
            _CHROME_KEEPS[0] += h - got["h"]
            height = h + _CHROME_KEEPS[0]
            got = dump(height)
        if screenshot:                 # --screenshot renders the whole window size as the page
            subprocess.run(args(h) + [f"--screenshot={screenshot}", url], capture_output=True, timeout=90)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return got


def overlaps(a, b, gap: int = 0) -> bool:
    return bool(a and b) and a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


if __name__ == "__main__":                              # pictures by hand
    import sys
    out, size = Path(sys.argv[1]), sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 else "de"
    scheme = sys.argv[4] if len(sys.argv) > 4 else "dunkel"
    tag = sys.argv[5] if len(sys.argv) > 5 else "shot"
    sys.path.insert(0, str(WEB.parents[1]))
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        st = booth_state(Path(d), lang=lang, scheme=scheme)
    with serve(st) as url:
        print(json.dumps(chrome(url, size, out / f"{tag}-start-{size}-{scheme}-{lang}.png")))
