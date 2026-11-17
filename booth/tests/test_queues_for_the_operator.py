"""The operator sees the print queue and failed uploads and can clear them.

Owner, 16.11.2026: "Online · 2 offen" said nothing, held prints could only be
thrown away all at once, failed uploads had no reason and no way to give up.
"""
import json
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from boothcore.printing.service import PrintJob, PrintService

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


class NoPaper:
    """Reports paper out: every job waits, nothing comes out."""
    name = "no-paper"

    def __init__(self):
        self.out = []

    def status(self):
        return {"state": "error", "reasons": ["media-empty"], "hinweis": "Papier leer"}

    def print_file(self, path, copies=1):
        raise RuntimeError("Kein Papier")


def _until(cond, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _service(tmp_path, n=3):
    dienst = PrintService(NoPaper(), tmp_path / "counter.txt")
    for i in range(n):
        bild = tmp_path / f"gast{i}.jpg"
        Image.new("RGB", (60, 90)).save(bild)
        dienst.submit(PrintJob(path=bild, copies=2, session_uid=f"run{i}", deferred=True))
    assert _until(lambda: len(dienst.queue_view()) == n)
    return dienst


def test_the_queue_lists_each_print_with_time_copies_and_state(tmp_path):
    view = _service(tmp_path).queue_view()
    assert [j["run"] for j in view] == ["run0", "run1", "run2"]
    assert all(j["copies"] == 2 and j["state"] in ("queued", "printing", "held") and j["created"] for j in view)


def test_one_print_is_discarded_and_only_once(tmp_path):
    dienst = _service(tmp_path)
    first = dienst.queue_view()[0]["id"]
    assert dienst.discard_one(first) is True
    assert dienst.discard_one(first) is False
    assert first not in [j["id"] for j in dienst.queue_view()]
    assert len(dienst.queue_view()) == 2


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-queues-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "4711"})
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def _head(client):
    return {"X-Admin-Token": client.post("/api/admin/login", json={"pin": "4711"}).json()["token"]}


def test_all_prints_go_only_with_the_second_step(booth):
    with pytest.raises(ValueError):
        booth.discard_prints({})
    assert "0 wartende" in booth.discard_prints({"confirm": "all"})


def test_failed_uploads_are_listed_retried_and_discarded(booth, client):
    f = booth.data / "bild.jpg"
    f.write_bytes(b"x")
    for i in range(3):
        booth.outbox.add(target="gallery", uid=f"u{i}", session_uid=f"run{i}", event_slug="fest", kind="photo",
                         variant="", path=f)
    with booth.outbox._con() as con:
        con.execute("UPDATE outbox SET status='failed', last_error='HTTP 413: zu groß'")
    head = _head(client)
    q = client.get("/api/admin/queues", headers=head).json()
    ids = [x["id"] for x in q["failed_uploads"]]
    assert len(ids) == 3
    assert q["failed_uploads"][0]["error"].startswith("HTTP 413")
    r = client.post("/api/admin/queues/uploads/retry", headers=head, json={"ids": [ids[0]]})
    assert r.status_code == 200
    assert client.post("/api/admin/queues/uploads/discard", headers=head, json={}).status_code == 400
    r = client.post("/api/admin/queues/uploads/discard", headers=head, json={"confirm": "all"})
    assert r.status_code == 200
    assert booth.outbox.counts()["failed"] == 0, "nothing failed any more: the light can turn green"


def test_the_card_and_the_self_check_say_why_in_plain_words(booth, client):
    """17.11.2026: "8 Uploads endgültig fehlgeschlagen" and nothing else, at the booth and in the gallery."""
    for i, err in enumerate(['422: {"ok": false, "error": "no event assigned"}'] * 6 + ["Datei fehlt: /x/Gast.jpg"] * 2):
        f = booth.data / f"bild{i}.jpg"
        f.write_bytes(b"x")
        booth.outbox.add(target="gallery", uid=f"w{i}", session_uid=f"lauf{i}", event_slug="", kind="photo",
                         variant="", path=f)
        with booth.outbox._con() as con:
            con.execute("UPDATE outbox SET status='failed', last_error=? WHERE uid=?", (err, f"w{i}"))
    q = client.get("/api/admin/queues", headers=_head(client)).json()
    assert q["failed_reasons"][0] == {"reason": "kein Event zugewiesen", "count": 6}
    assert {x["reason"] for x in q["failed_uploads"]} == {"kein Event zugewiesen", "Datei fehlt"}
    from boothcore.doctor import Doctor
    check = Doctor(booth).check_queue()
    assert "8 Uploads endgültig fehlgeschlagen (6× kein Event zugewiesen, 2× Datei fehlt)" in check.message
    assert "Event zuweisen" in check.hint and "Gast" not in check.message
    js = (WEB / "static" / "admin-page.js").read_text(encoding="utf-8")
    assert '"Grund: " + f.reason' in js and "d.failed_reasons" in js


def test_the_admin_card_and_the_status_line_say_what_waits():
    admin = (WEB / "admin.html").read_text(encoding="utf-8")
    assert 'id="warteschlangenCard"' in admin and 'id="qPrints"' in admin and 'id="qUploads"' in admin
    js = (WEB / "static" / "admin-page.js").read_text(encoding="utf-8")
    assert "/api/admin/queues/prints/discard" in js and '"Wirklich verwerfen?"' in js
    i18n = (WEB / "static" / "i18n.js").read_text(encoding="utf-8")
    assert '"status.queue": "{n} Uploads offen"' in i18n and '"status.prints": "{n} Drucke warten"' in i18n
    assert '"status.queue": "{n} uploads pending"' in i18n


def test_a_camera_change_retires_the_old_camera_finding(booth):
    booth._doctor_cache = {"at": time.time(), "worst": "fail", "camera_state": "disconnected",
                           "findings": [{"name": "Kamera", "state": "fail", "message": "edsdk: nicht verbunden"}]}
    booth._doctor_thread = threading.Thread(target=lambda: None)   # a check "running": no new one now
    booth.camera.state = "ready"
    beat = booth._heartbeat_doctor()
    assert all(f["name"] != "Kamera" for f in beat["findings"]) and beat["worst"] == "ok"
