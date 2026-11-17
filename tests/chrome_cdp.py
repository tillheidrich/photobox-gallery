"""Headless Chrome driven over the DevTools protocol, standard library only.

``--dump-dom`` with ``--virtual-time-budget`` (booth/tests/kiosk_stage.py)
is enough to measure a page that settles by itself, but under virtual time
Chrome draws no frames, so an IntersectionObserver never fires and a page
cannot be scrolled the way a person does it. This module starts Chrome with
a debugging port, speaks the protocol over a minimal WebSocket client (no
Playwright, no websocket package) and lets a test set a phone viewport,
load a page, run JavaScript in real time and take a screenshot.

    with Browser() as b:
        b.viewport(390, 844, mobile=True)
        b.goto(url)
        y = b.eval("scrollY")
        b.screenshot(path)
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

CHROME = next((p for p in (os.environ.get("CHROME"),
                           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                           shutil.which("google-chrome"), shutil.which("chromium"),
                           shutil.which("chromium-browser"))
               if p and Path(p).exists()), None)


class _WebSocket:
    """Just enough RFC 6455 for one local DevTools connection."""

    def __init__(self, url: str, timeout: float = 30.0):
        assert url.startswith("ws://")
        hostport, path = url[5:].split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("DevTools closed the handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.decode(errors="replace"))

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("DevTools connection closed")
            buf += chunk
        return buf

    def send(self, text: str) -> None:
        data = text.encode()
        head = bytes([0x81])
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if (b0 & 0x0F) == 0x9:                      # ping: answer, keep reading
                continue
            parts.append(payload)
            if b0 & 0x80:
                return b"".join(parts).decode()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    def __init__(self):
        if CHROME is None:
            raise RuntimeError("no Chrome on this machine")
        self.profile = tempfile.mkdtemp(prefix="cdp-chrome-")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--hide-scrollbars", f"--user-data-dir={self.profile}", f"--remote-debugging-port={self.port}",
             "--window-size=1280,900", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws: Optional[_WebSocket] = None
        self._id = 0

    def __enter__(self) -> "Browser":
        deadline = time.time() + 20
        targets = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2) as r:
                    targets = [t for t in json.load(r) if t.get("type") == "page"]
                if targets:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        if not targets:
            self.__exit__()
            raise RuntimeError("Chrome did not open its debugging port")
        self.ws = _WebSocket(targets[0]["webSocketDebuggerUrl"])
        self.send("Page.enable")
        self.send("Runtime.enable")
        return self

    def __exit__(self, *exc) -> None:
        if self.ws:
            self.ws.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    def send(self, method: str, **params) -> dict:
        self._id += 1
        my = self._id
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def viewport(self, width: int, height: int, mobile: bool = False, scale: float = 1) -> None:
        self.send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                  deviceScaleFactor=scale, mobile=mobile)
        if mobile:
            self.send("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)

    def cookie(self, url: str, name: str, value: str) -> None:
        self.send("Network.setCookie", url=url, name=name, value=value)

    def before_load(self, js: str) -> None:
        self.send("Page.addScriptToEvaluateOnNewDocument", source=js)

    def goto(self, url: str, settle: float = 1.0) -> None:
        self.send("Page.navigate", url=url)
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.eval("document.readyState") == "complete":
                break
            time.sleep(0.1)
        time.sleep(settle)

    def eval(self, expression: str, await_promise: bool = False) -> Any:
        r = self.send("Runtime.evaluate", expression=expression, returnByValue=True,
                      awaitPromise=await_promise)
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:600])
        return r.get("result", {}).get("value")

    def screenshot(self, path: Path, full_page: bool = False) -> None:
        params = {"format": "png"}
        if full_page:
            params["captureBeyondViewport"] = True
        data = self.send("Page.captureScreenshot", **params)["data"]
        Path(path).write_bytes(base64.b64decode(data))
