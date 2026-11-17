"""What the guest sees when the first printer fails and the second takes over,
and what happens when no printer can print at all.

Only fake printers here: a backend that prints into a list and one that
raises. Nothing reaches a real printer.
"""
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boothcore.printing.backends import PrinterBackend
from boothcore.printing.service import PrintJob, PrintService

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"


class FakePrinter(PrinterBackend):
    name = "fake"

    def __init__(self, works=True, honest=True):
        self.works = works
        self.honest = honest          # False: says "ready" and still fails, like a jam mid-print
        self.printed = []

    def status(self):
        return {"state": "ready" if self.works or not self.honest else "error", "name": "Fake"}

    def print_file(self, path, copies=1):
        if not self.works:
            raise RuntimeError("paper jam (fake)")
        self.printed.append(Path(path).name)


def _wait(cond, seconds=20):
    for _ in range(int(seconds * 20)):
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def _sheet(tmp_path, name="a.jpg"):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (60, 40), "white").save(p)
    return p


# ── the print service on its own ────────────────────────────────────
def test_a_takeover_is_remembered_for_the_run(tmp_path):
    first, second = FakePrinter(works=False), FakePrinter()
    svc = PrintService([first, second], tmp_path / "count.txt", routing="failover")
    svc.submit(PrintJob(_sheet(tmp_path), 1, "run1"))
    assert _wait(lambda: svc.outcome("run1").get("state") == "printed")
    out = svc.outcome("run1")
    assert out["slot"] == 2 and out["moved_to"] == 2
    assert second.printed and not first.printed


def test_the_status_says_which_printer_took_over(tmp_path):
    first, second = FakePrinter(works=False), FakePrinter()
    svc = PrintService([first, second], tmp_path / "count.txt", routing="failover")
    assert _wait(lambda: svc.status()["stations"][0].get("state") == "error")
    assert svc.status()["takeover"] == {"down": [1], "up": [2]}


def test_without_any_working_printer_the_run_is_stuck(tmp_path):
    svc = PrintService([FakePrinter(False), FakePrinter(False)], tmp_path / "count.txt", routing="failover")
    svc.submit(PrintJob(_sheet(tmp_path), 1, "run2"))
    assert _wait(lambda: svc.outcome("run2").get("state") == "stuck")
    assert svc.status()["takeover"] is None


def test_a_single_good_printer_has_no_takeover(tmp_path):
    svc = PrintService([FakePrinter()], tmp_path / "count.txt", routing="failover")
    svc.submit(PrintJob(_sheet(tmp_path), 1, "run3"))
    assert _wait(lambda: svc.outcome("run3").get("state") == "printed")
    assert "moved_to" not in svc.outcome("run3") and svc.status()["takeover"] is None


# ── the booth: what the guest is told ───────────────────────────────
@pytest.fixture()
def booth(monkeypatch, tmp_path):
    d = Path(tempfile.mkdtemp(prefix="booth-takeover-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8}})
    b.store.profile["event"].update({"slug": "hochzeit", "name": "Hochzeit", "share_key": "k",
                                     "base_url": "https://galerie.example"})
    b.store.profile["print"].update({"enabled": True, "auto": False})
    b.store.profile["consent"]["enabled"] = True
    b.camera.start()
    _wait(lambda: b.camera.state == "ready", 5)
    yield b
    b.camera.stop()
    b.mail.stop()


def _printers(booth, *works, honest=True):
    fakes = [FakePrinter(w, honest) for w in works]
    booth.printer = PrintService(fakes, booth.data / "fake_counter.txt", on_printed=booth._on_printed,
                                 on_failed=booth._on_print_failed, routing="failover")
    return fakes


def _shoot(booth, option):
    s = booth.start_session("photo", "4:5", None, None, consent={"option": option, "lang": "de"})
    booth.take_shot(s)
    booth.finalize(s, "original")
    return s


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def _status(client, uid):
    return client.get(f"/api/print/{uid}/status").json()


def test_the_guest_learns_the_print_comes_from_the_second_printer(booth, client):
    _printers(booth, False, True)
    s = _shoot(booth, "gallery")
    client.post(f"/api/print/{s.uid}", json={"copies": 1})
    assert _wait(lambda: _status(client, s.uid)["state"] == "printed")
    st = _status(client, s.uid)
    assert st["moved_to"] == 2 and st["saved"] is True
    assert client.get("/api/state").json()["printer"]["takeover"]["up"] == [2]


def test_no_printer_but_the_photo_is_saved(booth, client):
    _printers(booth, False, False, honest=False)
    s = _shoot(booth, "gallery")
    client.post(f"/api/print/{s.uid}", json={"copies": 1})
    assert _wait(lambda: _status(client, s.uid)["state"] == "stuck")
    st = _status(client, s.uid)
    assert st["saved"] is True and st["print_only"] is False
    assert booth.outbox.counts()["pending"] > 0


def test_print_only_without_printer_waits_for_the_guest_to_decide(booth, client):
    _printers(booth, False, False, honest=False)
    s = _shoot(booth, "print")
    client.post(f"/api/print/{s.uid}", json={"copies": 1})
    assert _wait(lambda: _status(client, s.uid)["state"] == "stuck")
    st = _status(client, s.uid)
    assert st["saved"] is False and st["print_only"] is True
    assert s.folder.exists(), "kept until the guest decides (or the safety net runs)"
    assert booth.outbox.counts()["pending"] == 0


def test_print_only_can_still_go_to_the_gallery(booth, client):
    _printers(booth, False, False, honest=False)
    s = _shoot(booth, "print")
    client.post(f"/api/print/{s.uid}", json={"copies": 1})
    assert _wait(lambda: _status(client, s.uid)["state"] == "stuck")
    r = client.post(f"/api/session/{s.uid}/keep", json={"choice": "gallery"})
    assert r.status_code == 200
    body = r.json()
    assert body["share_url"].startswith("https://galerie.example") and body["print_only"] is False
    assert booth.outbox.counts()["pending"] > 0
    rec = booth.library.consent(s.uid)
    assert rec["option"] == "gallery" and rec["changed_from"] == "print"
    assert s.uid in [i["uid"] for i in client.get("/api/gallery").json()["items"]]
    assert not (s.folder / ".datensparsam").exists()
    assert s.uid not in booth._wipe_timers


def test_print_only_can_be_thrown_away(booth, client):
    _printers(booth, False, False, honest=False)
    s = _shoot(booth, "print")
    client.post(f"/api/print/{s.uid}", json={"copies": 1})
    assert _wait(lambda: _status(client, s.uid)["state"] == "stuck")
    assert client.post(f"/api/session/{s.uid}/keep", json={"choice": "discard"}).status_code == 200
    assert booth.library.get(s.uid) is None and not s.folder.exists()
    assert booth.library.consent(s.uid)["option"] == "print"


def test_printers_known_to_be_down_refuse_and_the_guest_is_still_told(booth, client):
    """Both printers already report an error: the print is refused at once, and
    the status tells the kiosk whether the photo is saved (it shows the same words)."""
    _printers(booth, False, False)
    assert _wait(lambda: all(st.get("state") == "error" for st in booth.printer.status()["stations"]))
    s = _shoot(booth, "print")
    r = client.post(f"/api/print/{s.uid}", json={"copies": 1})
    assert r.status_code == 503 and r.json()["detail"].startswith("printer.")
    st = _status(client, s.uid)
    assert st["print_only"] is True and st["saved"] is False
    assert client.post(f"/api/session/{s.uid}/keep", json={"choice": "gallery"}).status_code == 200


def test_keep_is_only_for_print_only_runs(booth, client):
    _printers(booth, True)
    s = _shoot(booth, "gallery")
    assert client.post(f"/api/session/{s.uid}/keep", json={"choice": "discard"}).status_code == 409


# ── the kiosk side ──────────────────────────────────────────────────
JS = (WEB / "static" / "app.js").read_text("utf-8")
I18N = (WEB / "static" / "i18n.js").read_text("utf-8")
HTML = (WEB / "index.html").read_text("utf-8")


@pytest.mark.parametrize("key", ["print.moved", "print.moved_n", "print.failed_saved", "print.failed_print_only",
                                 "print.to_gallery", "print.discard", "print.kept", "status.printer_takeover"])
def test_the_messages_exist_in_both_languages(key):
    assert I18N.count(f'"{key}"') == 2, key


def test_the_kiosk_watches_the_print_and_offers_the_choice():
    assert "/api/print/${uid}/status" in JS
    assert 'id="printFailOverlay"' in HTML
    assert '"#printFailOverlay"' in JS, "the buzzer must not act behind this window (overlayOpen)"
    assert 'data-action="pf-gallery"' in HTML and 'data-action="pf-discard"' in HTML
    assert "takeover" in JS, "the status line shows the takeover to the operator"
    assert "return printStuck(!!view.print_only);" in JS, "a refused print gets the same words as a failed one"
