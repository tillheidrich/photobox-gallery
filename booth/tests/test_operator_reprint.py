"""The operator prints an earlier picture once more, from the admin.

14 Nov 2026 (competitor comparison, gap "reprint"): a guest comes back and
asks for the picture from an hour ago. Guests can reprint from the kiosk
gallery if the event allows it and their quota lasts; the operator must be
able to regardless of both — and without leaving the admin.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"


@pytest.fixture()
def admin(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-nachdruck-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    b.gedruckt = []
    monkeypatch.setattr(b.printer, "submit", lambda job: b.gedruckt.append(job))     # never a real printer
    c = TestClient(app_mod.create_app(b))
    c.booth = b
    yield c
    b.mail.stop()


def _login(c):
    c.headers.update({"X-Admin-Token": c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]})


def _lauf(b, monkeypatch, uid="abc123", exists=True):
    f = b.data / "druck.jpg"
    if exists:
        from PIL import Image
        Image.new("RGB", (120, 180), "#777").save(f, "JPEG")
    monkeypatch.setattr(b.library, "get", lambda u: {"uid": uid, "print_file": str(f)} if u == uid else None)
    monkeypatch.setattr(b.library, "add_prints", lambda u, n: b.gedruckt.append(("gezählt", u, n)))
    monkeypatch.setattr(b, "sheet_for", lambda p, u="": p)


def test_it_needs_the_admin(admin):
    assert admin.post("/api/admin/reprint/abc123", json={}).status_code in (401, 403)


def test_the_operator_prints_even_when_guests_may_not(admin, monkeypatch):
    b = admin.booth
    _lauf(b, monkeypatch)
    b.profile["print"]["reprint_from_gallery"] = False
    b.profile["print"]["limit_session"] = 0
    _login(admin)
    r = admin.post("/api/admin/reprint/abc123", json={"copies": 2})
    assert r.status_code == 200 and r.json()["queued"] == 2
    job = b.gedruckt[0]
    assert job.copies == 2 and job.session_uid == "abc123"
    assert ("gezählt", "abc123", 2) in b.gedruckt            # the paper counter must stay true


def test_copies_stay_within_reason(admin, monkeypatch):
    _lauf(admin.booth, monkeypatch)
    _login(admin)
    assert admin.post("/api/admin/reprint/abc123", json={"copies": 99}).json()["queued"] == 5
    assert admin.post("/api/admin/reprint/abc123", json={"copies": 0}).json()["queued"] == 1


def test_a_picture_that_is_gone_says_so(admin, monkeypatch):
    _lauf(admin.booth, monkeypatch, exists=False)
    _login(admin)
    assert admin.post("/api/admin/reprint/abc123", json={}).status_code == 409
    assert admin.post("/api/admin/reprint/unbekannt", json={}).status_code == 409


def test_the_admin_page_offers_it():
    html = page(WEB / "admin.html")
    assert 'id="nachdruckCard"' in html and "/api/admin/reprint/" in html
