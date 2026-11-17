"""The factory PIN 2580 is in every manual, so it cannot stay.

Two promises: the self-diagnosis fails as long as it is in force (also when the
admin is only reachable at the booth itself — the touchscreen is right there,
and every guest can tap four digits), and the booth admin asks for a new PIN
right after a login with the factory one, before anything else works.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _pages import page as _page_with_scripts  # noqa: E402


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-factory-pin-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def _access(booth):
    from boothcore.doctor import Doctor
    return next(c for c in Doctor(booth).run() if c.name == "Zugang")


def test_a_fresh_booth_still_has_the_factory_pin(booth):
    assert booth.current_pin() == "2580"
    assert booth.factory_pin_active() is True


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "0.0.0.0"])
def test_the_factory_pin_fails_the_self_diagnosis_everywhere(booth, host):
    booth.listen_host = host
    check = _access(booth)
    assert check.state == "fail", host
    assert "2580" in check.message
    assert "Verwaltung" in check.hint and "PIN" in check.hint


def test_an_own_pin_passes_the_self_diagnosis(booth):
    booth.store.save_local({"admin_pin": "468213"})
    booth.listen_host = "127.0.0.1"
    assert _access(booth).state == "ok"


def test_a_factory_pin_set_from_the_gallery_fails_as_well(booth):
    booth.store.save_local({"admin_pin": "468213"})
    booth.store.profile["access"]["pin"] = "2580"
    assert booth.factory_pin_active() is True
    assert _access(booth).state == "fail"


def test_login_with_the_factory_pin_demands_a_new_one_first(client):
    r = client.post("/api/admin/login", json={"pin": "2580"})
    assert r.status_code == 200
    body = r.json()
    assert body["must_change_pin"] is True
    head = {"X-Admin-Token": body["token"]}
    # Nothing else works with this token: not reading, not saving.
    assert client.get("/api/admin/config", headers=head).status_code == 403
    assert client.post("/api/admin/config", headers=head, json={"booth_name": "x"}).status_code == 403
    assert client.post("/api/admin/profile", headers=head, json={}).status_code == 403


@pytest.mark.parametrize("weak", ["2580", "12345", "000000", "111111", "123456", "654321",
                                  "234567", "0123456789", "12a456", ""])
def test_weak_new_pins_are_refused(client, booth, weak):
    token = client.post("/api/admin/login", json={"pin": "2580"}).json()["token"]
    r = client.post("/api/admin/pin", headers={"X-Admin-Token": token}, json={"new_pin": weak})
    assert r.status_code == 400, weak
    assert r.json()["detail"]
    assert booth.current_pin() == "2580"


def test_after_the_change_the_admin_opens_and_the_old_pin_is_gone(client, booth):
    token = client.post("/api/admin/login", json={"pin": "2580"}).json()["token"]
    head = {"X-Admin-Token": token}
    r = client.post("/api/admin/pin", headers=head, json={"new_pin": "468213"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert booth.current_pin() == "468213" and booth.factory_pin_active() is False
    assert booth.store.local["admin_pin"] == "468213"
    # The same session goes on; no second login at the touchscreen.
    assert client.get("/api/admin/config", headers=head).status_code == 200
    assert client.post("/api/admin/login", json={"pin": "2580"}).status_code == 401
    again = client.post("/api/admin/login", json={"pin": "468213"}).json()
    assert again.get("must_change_pin") is False


def test_the_change_also_clears_a_factory_pin_from_the_profile(client, booth):
    booth.store.save_local({"admin_pin": "2580"})
    booth.store.profile["access"]["pin"] = "2580"
    token = client.post("/api/admin/login", json={"pin": "2580"}).json()["token"]
    client.post("/api/admin/pin", headers={"X-Admin-Token": token}, json={"new_pin": "468213"})
    assert booth.current_pin() == "468213"


def test_the_admin_form_cannot_put_the_factory_pin_back(client, booth):
    booth.store.save_local({"admin_pin": "468213"})
    token = client.post("/api/admin/login", json={"pin": "468213"}).json()["token"]
    head = {"X-Admin-Token": token}
    for weak in ("2580", "1234", "999999"):
        r = client.post("/api/admin/config", headers=head, json={"admin_pin": weak})
        assert r.status_code == 400, weak
    assert booth.store.local["admin_pin"] == "468213"
    # Sending the unchanged PIN along with other fields is fine.
    assert client.post("/api/admin/config", headers=head,
                       json={"admin_pin": "468213", "booth_name": "Box 1"}).status_code == 200


def test_the_admin_page_has_the_change_step():
    page = _page_with_scripts(Path(__file__).resolve().parents[1] / "boothcore" / "web" / "admin.html")
    assert 'id="newpin"' in page and "must_change_pin" in page and "/api/admin/pin" in page
