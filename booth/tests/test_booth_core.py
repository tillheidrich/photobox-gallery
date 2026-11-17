"""Kern der Box ohne Hardware: Framing, Vorlagen, Looks, Share-Links, Outbox, Drucklimits, Ablauf."""
import os
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-test-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()


def test_crop_to_aspect():
    from boothcore.media.compose import crop_to_aspect
    img = Image.new("RGB", (6000, 4000))
    assert crop_to_aspect(img, "4:5").size == (3200, 4000)
    assert crop_to_aspect(img, "9:16").size == (2250, 4000)
    assert crop_to_aspect(img, "3:2").size == (6000, 4000)


def test_templates_portrait_and_landscape_render():
    from boothcore.media.compose import BUILTIN_TEMPLATES, render_template, template_info
    shots = [Image.new("RGB", (1200, 800), (i * 40, 80, 120)) for i in range(4)]
    orients = {template_info(t)["orientation"] for t in BUILTIN_TEMPLATES}
    assert orients == {"portrait", "landscape"}
    for t in BUILTIN_TEMPLATES:
        out = render_template(t, shots, qr_url="https://x.de/p/a", texts={"event_name": "Test", "date": "1.1."})
        assert out.size == tuple(t["canvas"])


def test_single_print_orientation():
    from boothcore.media.compose import single_print_template
    assert single_print_template("3:2", True)["canvas"] == [1800, 1200]
    assert single_print_template("4:5", False)["canvas"] == [1200, 1800]
    assert "qr" not in single_print_template("4:5", False)


def test_looks_all_run():
    from boothcore.media import looks
    img = Image.new("RGB", (80, 60), (120, 90, 60))
    for name in looks.LOOKS:
        assert looks.apply(name, img).size == (80, 60)


def test_share_signature_matches_server():
    from boothcore import share
    uid = share.new_uid()
    code = share.share_code("key", uid)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import ecosystem
    u, sig = ecosystem.parse_share_code(code)
    assert u == uid and sig == ecosystem.signature("key", uid)


def test_qrstyle_decodes_with_logo(tmp_path):
    import cv2
    import numpy as np
    from boothcore.media import qrstyle
    logo = tmp_path / "l.png"
    Image.new("RGBA", (200, 200), (229, 72, 77, 255)).save(logo)
    img = qrstyle.render("https://example.org/p/abc", 500, {"modules": "dots", "dark": "#223344"}, logo)
    arr = cv2.copyMakeBorder(np.array(img)[:, :, ::-1], 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    assert cv2.QRCodeDetector().detectAndDecode(arr)[0] == "https://example.org/p/abc"


def test_outbox_backoff_and_cancel(tmp_path):
    from photobox_sync.outbox import Outbox
    ob = Outbox(tmp_path / "o.sqlite")
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    ob.add(target="gallery", uid="u1", session_uid="u1", event_slug="e", kind="photo", variant="web", path=f)
    item = ob.due()[0]
    ob.mark_retry(item["id"], "offline")
    assert ob.due() == []
    ob.reset_backoff()
    assert len(ob.due()) == 1
    assert ob.cancel_session("u1") == 1 and ob.counts()["pending"] == 0


def test_full_photo_session_and_print_limits(booth):
    booth.store.profile["print"].update({"enabled": True, "limit_session": 2, "copies_max": 2})
    s = booth.start_session("photo", "9:16", None, None)
    booth.take_shot(s)
    booth.finalize(s, "warm")
    item = booth.library.get(s.uid)
    assert item and Path(item["result"]).exists()
    with Image.open(item["result"]) as im:
        assert abs(im.width / im.height - 9 / 16) < 0.01
    assert booth.print_uid(s.uid, 2)["queued"] == 2
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        booth.print_uid(s.uid, 1)       # Limit je Aufnahme erreicht
    booth.delete(s.uid)
    assert booth.library.get(s.uid) is None and booth.library.pending_deletions() == [s.uid]


def test_boomerang_blur_layout(booth):
    # "crop" is the default since 15.11.2026; "blur" is still there when chosen.
    booth.store.profile["boomerang"].update({"seconds": 0.6, "fps": 10, "loops": 1, "layout": "blur"})
    s = booth.start_session("boomerang", "", None, None)
    booth.record_clip(s)
    booth.finalize(s, "original")
    assert s.result.exists() and s.result.stat().st_size > 1000
    with Image.open(s.poster) as p:
        assert p.size == (1080, 1920)


def _warte(bedingung, sekunden: float = 10.0) -> None:
    """Auf einen Hintergrund-Thread warten, statt eine Dauer zu raten.

    Feste ``sleep``-Werte sind in einer vollen Suite ein Glücksspiel: Sie
    halten auf dem Entwicklungsrechner und kippen, sobald zweihundert andere
    Tests um dieselben Kerne konkurrieren. Ein Test, der nur meistens grün
    ist, ist schlimmer als ein roter — beim nächsten echten Fehler winkt ihn
    jemand durch.
    """
    import time as _tt
    ende = _tt.monotonic() + sekunden
    while _tt.monotonic() < ende:
        if bedingung():
            return
        _tt.sleep(0.02)
    raise AssertionError("Bedingung wurde nicht erreicht")


def test_two_printers_balance_orientation_and_failover(tmp_path):
    import time as _t
    from PIL import Image as _I
    from boothcore.printing.backends import PrinterBackend
    from boothcore.printing.service import PrintJob, PrintService

    class Slow(PrinterBackend):
        name = "slow"
        def __init__(self, fail=False):
            self.fail, self.done = fail, []
        def status(self):
            return {"state": "error" if self.fail else "ready"}
        def print_file(self, path, copies=1):
            if self.fail:
                raise RuntimeError("Papier leer")
            _t.sleep(0.2); self.done.append(path.name)

    portrait = tmp_path / "p.jpg"; _I.new("RGB", (100, 150)).save(portrait)
    landscape = tmp_path / "l.jpg"; _I.new("RGB", (150, 100)).save(landscape)

    a, b = Slow(), Slow()
    svc = PrintService([a, b], tmp_path / "c.txt", routing="balance")
    assert [svc.submit(PrintJob(portrait)) for _ in range(2)] == [1, 2]

    a2, b2 = Slow(), Slow()
    svc2 = PrintService([a2, b2], tmp_path / "c2.txt", routing="orientation")  # Kurzform bleibt kompatibel
    assert svc2.submit(PrintJob(landscape)) == 2 and svc2.submit(PrintJob(portrait)) == 1

    bad, good = Slow(fail=True), Slow()
    svc3 = PrintService([bad, good], tmp_path / "c3.txt", routing="failover")
    # Warten, bis die Status-Abfrage einmal durch ist — nicht auf gut Glück
    # schlafen. Ein Drucker startet mit "unknown", und "unknown" gilt als
    # verfügbar (siehe UNAVAILABLE). Unter Last kam der Hintergrund-Thread
    # nicht rechtzeitig dran, der Auftrag ging an den kaputten Drucker und der
    # Test schlug zu — aber nur manchmal, was schlimmer ist als immer.
    _warte(lambda: svc3.stations[0].state() == "error")
    assert svc3.submit(PrintJob(portrait)) == 2          # Drucker 1 meldet Fehler → Ersatz
    # Auf den Zähler warten, nicht auf die Attrappe: Der Drucker legt den
    # Dateinamen ab, und erst danach zählt der Dienst das Blatt. Wer auf
    # ``good.done`` wartet, prüft eine Zeile zu früh.
    _warte(lambda: svc3.printed == 1)
    assert good.done == ["p.jpg"]
    assert len(svc3.status()["stations"]) == 2

    # einziger Drucker fällt aus: Auftrag bleibt erhalten und wird nachgedruckt
    class Flaky(Slow):
        def __init__(self):
            super().__init__(); self.calls = 0
        def status(self):
            return {"state": "ready"}
        def print_file(self, path, copies=1):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Papierstau")
            self.done.append(path.name)
    fl = Flaky()
    svc4 = PrintService([fl], tmp_path / "c4.txt")
    svc4.submit(PrintJob(portrait))
    # The retry after a failure belongs to the 5-s status poll; under load
    # that poll came later than the 8 s this test used to wait. Wait for the
    # failure itself, then do what the poll does — here, on this thread.
    # (The poll may have been quicker: then there is nothing left to do.)
    _warte(lambda: svc4.failed or svc4.printed == 1)
    if svc4.printed != 1:
        station = svc4.stations[0]
        station._auffrischen()
        svc4._retry_failed(station)          # takes the list under the lock: never twice
    _warte(lambda: svc4.printed == 1)
    assert fl.done == ["p.jpg"] and svc4.printed == 1


def test_ipp_printer_sends_jpeg_and_reads_state(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from boothcore.printing.backends import IppPrinter

    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            op = int.from_bytes(body[2:4], "big")
            got[op] = body
            resp = b"\x02\x00\x00\x00" + body[4:8] + b"\x01"
            if op == 0x0B:
                resp += b"\x04" + b"\x23" + (13).to_bytes(2, "big") + b"printer-state" + (4).to_bytes(2, "big") + (3).to_bytes(4, "big")
            resp += b"\x03"
            self.send_response(200); self.send_header("Content-Type", "application/ipp")
            self.send_header("Content-Length", str(len(resp))); self.end_headers(); self.wfile.write(resp)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    pr = IppPrinter(f"ipp://127.0.0.1:{srv.server_port}/ipp/print")
    assert pr.status()["state"] == "ready"
    img = tmp_path / "a.jpg"; img.write_bytes(b"\xff\xd8JPEGDATA")
    pr.print_file(img, 2)
    assert got[0x02].endswith(b"\x03\xff\xd8JPEGDATA") and b"image/jpeg" in got[0x02]
    srv.shutdown()
    assert IppPrinter("ipp://127.0.0.1:1/ipp/print", timeout=1).status()["state"] == "offline"


def test_camera_settings_applied_after_connect_and_on_change(tmp_path):
    import time as _t
    from boothcore.camera.mock import MockCamera
    from boothcore.camera.service import CameraService

    cam = MockCamera()
    svc = CameraService(lambda: cam)
    svc.desired_settings = {"iso": "400"}
    svc.start()
    for _ in range(50):
        if svc.state == "ready":
            break
        _t.sleep(0.1)
    assert cam.settings.get("iso") == "400"
    res = svc.apply_settings({"shutter": "1/125", "wb": "daylight"})
    assert res["applied"] == {"shutter": "1/125", "wb": "daylight"}
    svc.stop()


def test_folder_camera_takes_newest_file(tmp_path):
    import threading, time as _t
    from boothcore.camera.digicam import FolderCamera
    cam = FolderCamera(str(tmp_path), timeout=5)
    cam.open()
    def drop():
        _t.sleep(0.5); (tmp_path / "DSC_0001.JPG").write_bytes(b"\xff\xd8x")
    threading.Thread(target=drop).start()
    out = cam.capture(tmp_path / "out" if False else tmp_path.parent / "shot.jpg")
    assert out.read_bytes() == b"\xff\xd8x"


def test_gif_mode_writes_animation_and_keeps_stills(booth):
    from PIL import Image as _I
    booth.store.profile["gif"].update({"shots": 3, "frame_ms": 200, "pingpong": True, "also_mp4": False})
    s = booth.start_session("gif", "4:5", None, None)
    assert s.shots_needed == 3
    for _ in range(3):
        booth.take_shot(s)
    booth.finalize(s, "original")
    assert s.result.suffix == ".gif"
    with _I.open(s.result) as im:
        assert im.is_animated and im.n_frames == 4          # 3 shots + one frame back
    assert s.print_file and s.print_file.exists() and s.thumb.exists()
    assert len(s.shots) == 3 and all(p.exists() for p in s.shots)


def test_video_scaling_can_upscale_for_social(tmp_path):
    from boothcore.media.boomerang import resolve_width
    small = (1024, 576)                                     # Canon M50 as a USB webcam
    assert resolve_width("auto", small, "9:16", "blur", 1080, "native") == 1024
    assert resolve_width("auto", small, "9:16", "blur", 1080, "1080p") == 1080
    assert resolve_width("auto", small, "9:16", "crop", 1080, "720p") == 720
    assert resolve_width("auto", (1920, 1080), "16:9", "blur", 1080, "720p") == 1280   # fixed target wins


# ── Lastverteilung auf zwei Drucker (20.09.2026) ──────────────────────
# Gefunden, weil der Test oben sporadisch rot war. Der Test hatte recht:
# `pending` war `qsize + busy`, und zwischen `q.get()` und `busy = True`
# liegt ein Augenblick, in dem ein angenommener Auftrag nirgends gezählt
# wird. Wer in diesem Fenster den zweiten Auftrag einreicht, bekommt wieder
# Drucker 1, während Drucker 2 leer steht — bei zwei Druckern genau der
# Fall, für den man zwei Drucker kauft.

def test_ein_angenommener_auftrag_zaehlt_sofort_nicht_erst_beim_drucken(tmp_path):
    """Deterministisch: der Drucker-Thread wird im kritischen Fenster angehalten."""
    import threading
    from PIL import Image as _I
    from boothcore.printing.backends import PrinterBackend
    from boothcore.printing.service import PrintJob, PrintService, Spool

    drin, weiter = threading.Event(), threading.Event()

    class Angehalten:
        """Der echte Spool, aber ``started`` hält den Drucker-Thread fest.

        Genau dort steht der Thread: Auftrag aus der Reihe geholt, ``busy``
        noch nicht gesetzt. Das ist das Fenster, um das es geht. (Until
        16.11.2026 the thread asked ``zustand`` first; taking the job is now
        one conditional call, ``started``.)
        """

        def __init__(self, echt):
            self._echt = echt

        def __getattr__(self, name):
            return getattr(self._echt, name)

        def started(self, spool_id, station):
            drin.set()
            weiter.wait(10)
            return self._echt.started(spool_id, station)

    class Still(PrinterBackend):
        name = "still"
        def __init__(self):
            self.done = []
        def status(self):
            return {"state": "ready"}
        def print_file(self, path, copies=1):
            self.done.append(path.name)

    bild = tmp_path / "p.jpg"; _I.new("RGB", (100, 150)).save(bild)
    spool = Angehalten(Spool(tmp_path / "s.sqlite"))
    svc = PrintService([Still(), Still()], tmp_path / "c.txt", routing="balance", spool=spool)

    assert svc.submit(PrintJob(bild)) == 1
    assert drin.wait(10), "Der Drucker-Thread hat den Auftrag nie angefasst"
    # Hier ist der Auftrag aus der Warteschlange raus und der Drucker noch
    # nicht „busy". Vorher stand hier 0 — und der nächste Auftrag ging wieder
    # an denselben Drucker.
    assert svc.stations[0].pending == 1
    assert svc.submit(PrintJob(bild)) == 2, "Beide Aufträge auf einem Drucker, der zweite steht still"
    weiter.set()
