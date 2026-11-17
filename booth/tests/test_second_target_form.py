"""A second upload target (Picdrop, a NAS) without writing JSON by hand.

14 Nov 2026: for the wedding the pictures are to go to Picdrop in parallel,
"der sichere Weg". Until now a target was raw JSON in a text box — nobody
types an FTP password into that at a venue, and nobody could tell whether it
works before the first guest stood there.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"


@pytest.fixture()
def admin(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-ziel-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    c = TestClient(app_mod.create_app(b))
    c.booth = b
    yield c
    b.mail.stop()


def _login(c):
    c.headers.update({"X-Admin-Token": c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]})


def test_the_admin_page_has_a_form_for_it():
    html = page(WEB / "admin.html")
    for feld in ('id="zt_on"', 'id="zt_type"', 'id="zt_host"', 'id="zt_user"', 'id="zt_pass"', 'id="zt_path"', 'id="zt_test"'):
        assert feld in html, feld
    assert 'id="zt_pass" type="password"' in html


def test_testing_a_target_needs_the_admin(admin):
    assert admin.post("/api/admin/targets/check", json={"target": {"type": "ftps", "host": "127.0.0.1"}}).status_code in (401, 403)


def test_a_target_that_does_not_answer_says_so_in_words(admin):
    _login(admin)
    r = admin.post("/api/admin/targets/check", json={"target": {
        "type": "ftps", "name": "picdrop", "host": "127.0.0.1", "port": 1, "user": "u", "password": "streng-geheim", "timeout": 2}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["message"]
    assert "streng-geheim" not in r.text


def test_a_working_target_says_ok(admin, tmp_path):
    _login(admin)
    r = admin.post("/api/admin/targets/check", json={"target": {"type": "folder", "name": "ordner", "path": str(tmp_path)}})
    assert r.json()["ok"] is True


def test_the_stored_password_is_used_when_the_form_sends_the_dots(admin, monkeypatch):
    _login(admin)
    admin.booth.store.save_local({"targets": [{"type": "ftps", "name": "picdrop", "host": "h", "user": "u", "password": "echt"}]})
    seen = {}
    from photobox_sync import targets as t

    def fake_check(self):
        seen["password"] = self.cfg.get("password")
        return "ok"
    monkeypatch.setattr(t.FtpsTarget, "check", fake_check)
    r = admin.post("/api/admin/targets/check", json={"target": {"type": "ftps", "name": "picdrop", "host": "h", "user": "u", "password": "••••"}})
    assert r.json()["ok"] is True and seen["password"] == "echt"


def test_an_unknown_kind_is_refused(admin):
    _login(admin)
    assert admin.post("/api/admin/targets/check", json={"target": {"type": "gopher"}}).json()["ok"] is False


def test_the_form_talks_through_the_pages_own_helper():
    html = page(WEB / "admin.html")
    assert 'await call("/api/admin/targets/check"' in html and "await api(" not in html


@pytest.mark.parametrize("roh, wort", [("[Errno 61] Connection refused", "lehnt"), ("timed out", "antwortet nicht"),
                                       ("[Errno 8] nodename nor servname provided, or not known", "Adresse"),
                                       ("Anmeldung abgelehnt: 530 Login incorrect.", "Passwort")])
def test_the_reason_is_said_for_a_person_not_for_a_programmer(roh, wort):
    from boothcore.app import ziel_fehler_text
    text = ziel_fehler_text(roh)
    assert wort in text and "Errno" not in text.split("(")[0]
