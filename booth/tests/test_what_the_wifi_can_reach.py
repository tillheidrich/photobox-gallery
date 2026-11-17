"""What a guest in the same Wi-Fi reaches when the booth listens on the network.

Register B3, the open half: with ``--host 0.0.0.0`` (remote admin, Wi-Fi
buzzer) every guest route answered anyone in the venue's Wi-Fi — the live
camera, the photos of every run, printing, deleting, the profile with the
trigger token and the webhook secret. The kiosk itself always runs on this PC
(127.0.0.1), so the guest routes now answer only there; from the network they
need an admin token, or the operator switches ``lan_guest_access`` on for a
second screen.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

GUEST_ROUTES = [
    ("get", "/api/state"), ("get", "/api/gallery"), ("get", "/api/gallery/x1"),
    ("get", "/media/x1/result"), ("get", "/api/live.jpg"), ("get", "/api/live.mjpg"),
    ("post", "/api/session"), ("post", "/api/session/x1/shot"), ("post", "/api/session/x1/finish"),
    ("post", "/api/session/x1/mail"), ("delete", "/api/session/x1"), ("post", "/api/print/x1"),
    ("get", "/api/print/x1/status"), ("get", "/api/session/x1/preview.jpg"),
]


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-wifi-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    b.listen_host = "0.0.0.0"
    yield b
    b.mail.stop()


def _client(booth, host):
    from boothcore.app import create_app
    return TestClient(create_app(booth), client=(host, 50123))


@pytest.mark.parametrize("method,path", GUEST_ROUTES)
def test_a_guest_in_the_wifi_gets_no_guest_route(booth, method, path):
    r = getattr(_client(booth, "192.168.178.40"), method)(path)
    assert r.status_code == 403, f"{method.upper()} {path} answered {r.status_code} to the network"


def test_the_kiosk_on_this_pc_keeps_working(booth):
    c = _client(booth, "127.0.0.1")
    assert c.get("/api/state").status_code == 200
    assert c.get("/api/gallery").status_code in (200, 403)     # 403 only if gallery_on_booth is off
    assert _client(booth, "::1").get("/api/state").status_code == 200


def test_what_stays_open_on_the_network(booth):
    c = _client(booth, "192.168.178.40")
    for path in ("/", "/admin", "/api/about", "/static/app.js"):
        assert c.get(path).status_code == 200, path
    assert c.post("/api/trigger", json={}).status_code == 403          # off unless switched on, then token
    assert c.post("/api/admin/login", json={"pin": "000000"}).status_code == 401


def test_the_admin_on_the_network_reads_the_state_with_its_token(booth):
    c = _client(booth, "192.168.178.40")
    token = c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]
    assert c.get("/api/state", headers={"X-Admin-Token": token}).status_code == 200
    assert c.get("/api/state", headers={"X-Admin-Token": "falsch"}).status_code == 403


def test_a_second_screen_can_be_allowed_on_purpose(booth):
    booth.store.local["lan_guest_access"] = True
    assert _client(booth, "192.168.178.40").get("/api/state").status_code == 200


def test_loopback_only_booth_is_unchanged(booth):
    booth.listen_host = "127.0.0.1"
    # Nothing but this PC can connect, so the client address decides nothing.
    assert _client(booth, "testclient").get("/api/state").status_code == 200


def test_the_state_carries_no_trigger_token_and_no_webhook_secret(booth):
    booth.store.profile["http_trigger"].update({"enabled": True, "token": "tr-geheim-123"})
    booth.store.profile["webhooks"].update({"enabled": True, "url": "https://hook.example/x",
                                            "secret": "wh-geheim-456"})
    text = _client(booth, "127.0.0.1").get("/api/state").text
    assert "tr-geheim-123" not in text and "wh-geheim-456" not in text
    assert "hook.example" not in text
