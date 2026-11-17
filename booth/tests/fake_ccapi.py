"""Eine nachgebaute Canon-Kamera mit CCAPI — für Tests ohne Hardware.

Bildet nach, was das Backend wirklich anfasst: die Liste der Funktionen, das
Auslösen, den Ereignis-Abruf, den Dateiabruf und das Live-Bild. Dazu die
unangenehmen Fälle, die an einem Eventabend vorkommen: Kamera beschäftigt,
Karte voll, falscher Modus, Datei kommt erst nach ein paar Abfragen.

Das ist kein Beweis, dass es an einer echten Kamera läuft. Es ist der Beweis,
dass das Protokoll stimmt und die Fehler als lesbare Sätze ankommen.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 512 + b"\xff\xd9"


class FakeCamera:
    def __init__(self, versions=("ver100", "ver110"), model="EOS R50"):
        self.versions = versions
        self.model = model
        self.shots = 0
        self.pending: list[str] = []          # Dateien, die der nächste Abruf meldet
        self.delay_polls = 0                  # so oft leer antworten, bevor die Datei kommt
        self.busy_times = 0                   # so oft „Device busy" auf das Auslösen
        self.fail_shutter: str | None = None  # feste Fehlermeldung statt Auslösen
        self.liveview = None                  # zuletzt gesetzte Live-View-Einstellung
        self.settings: dict[str, str] = {}
        self.requests: list[str] = []
        self._server = None
        self._thread = None

    # ── Serverleben ──────────────────────────────────────
    def start(self) -> str:
        cam = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):        # stumm bleiben
                pass

            def _send(self, code, payload=None, raw=None, ctype="application/json"):
                body = raw if raw is not None else json.dumps(payload or {}).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                cam.requests.append("GET " + self.path)
                path = self.path.split("?")[0]
                if path == "/ccapi":
                    return self._send(200, cam._listing())
                if path.endswith("/deviceinformation"):
                    return self._send(200, {"productname": cam.model, "serialnumber": "0123456789"})
                if path.endswith("/event/polling"):
                    if cam.delay_polls > 0:
                        cam.delay_polls -= 1
                        return self._send(200, {})
                    added, cam.pending = cam.pending, []
                    return self._send(200, {"addedcontents": added} if added else {})
                if path.endswith("/shooting/liveview/flip"):
                    if not cam.liveview or cam.liveview.get("liveviewsize") == "off":
                        return self._send(503, {"message": "Device busy"})
                    return self._send(200, raw=JPEG, ctype="image/jpeg")
                if "/contents/" in path:
                    if path.endswith("MISSING.JPG"):
                        return self._send(404, {"message": "URL not found"})
                    return self._send(200, raw=JPEG, ctype="image/jpeg")
                return self._send(404, {"message": "URL not found"})

            def do_POST(self):
                cam.requests.append("POST " + self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                path = self.path.split("?")[0]
                if path.endswith("/shooting/control/shutterbutton"):
                    if cam.busy_times > 0:
                        cam.busy_times -= 1
                        return self._send(503, {"message": "Device busy"})
                    if cam.fail_shutter:
                        return self._send(503, {"message": cam.fail_shutter})
                    cam.shots += 1
                    cam.pending.append(
                        f"http://192.168.1.2:8080/ccapi/ver100/contents/sd/100CANON/IMG_{cam.shots:04d}.JPG")
                    return self._send(200, {})
                if path.endswith("/shooting/liveview"):
                    cam.liveview = body
                    return self._send(200, {})
                return self._send(404, {"message": "URL not found"})

            def do_PUT(self):
                cam.requests.append("PUT " + self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                name = self.path.rstrip("/").split("/")[-1]
                if name == "iso" and body.get("value") == "kaputt":
                    return self._send(400, {"message": "Invalid parameter"})
                cam.settings[name] = body.get("value")
                return self._send(200, {})

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_port}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    # ── Inhalte ──────────────────────────────────────────
    def _listing(self) -> dict:
        paths = ["deviceinformation", "shooting/control/shutterbutton", "shooting/liveview",
                 "shooting/liveview/flip", "event/polling", "shooting/settings",
                 "shooting/settings/iso", "shooting/settings/av", "shooting/settings/tv",
                 "shooting/settings/wb"]
        out = {}
        for version in self.versions:
            out[version] = [{"url": f"http://127.0.0.1/ccapi/{version}/{p}",
                             "get": True, "post": True, "put": True, "delete": False} for p in paths]
        return out
