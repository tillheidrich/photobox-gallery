"""Pairing the booth itself with a code from the gallery.

Before 15.11.2026 only the stand-alone sync program could redeem the code the
gallery admin hands out ("Kopplungscode erzeugen"); the booth needed its
address and the raw device key typed in. Now the card "Galerie-Server" takes
the code (short code with the gallery's address or name, or the long PBX1
code), a ``kopplung.txt`` in the data folder pairs a booth without a keyboard,
and the card and the self-check say which gallery, which box name and which
event — and warn when uploads would be refused or no QR code can be made.

The gallery is faked at httpx.post (the one call the pairing makes).
"""
from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

import httpx
import pytest

KEY = "geraete-schluessel-1234567890"


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-pairing-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    yield b
    b.mail.stop()


class FakeGallery:
    """/api/v2/pair as the gallery answers it."""

    def __init__(self, monkeypatch, status=200, body=None, error=None):
        self.calls = []
        self.status, self.error = status, error
        self.body = body if body is not None else {
            "ok": True, "url": "https://galerie.example.de", "api_key": KEY,
            "device_name": "Box 2", "gallery_name": "Fotobox Heidrich"}
        monkeypatch.setattr(httpx, "post", self.post)

    def post(self, url, json=None, **kw):
        self.calls.append((url, json))
        if self.error:
            raise self.error
        return httpx.Response(self.status, json=self.body)


def _client(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.admin_tokens["t"] = time.time() + 600
    return TestClient(create_app(booth)), {"X-Admin-Token": "t"}


def test_the_short_code_with_the_gallery_address_pairs_the_booth(booth, monkeypatch, caplog):
    gallery = FakeGallery(monkeypatch)
    client, head = _client(booth)
    caplog.set_level(logging.DEBUG)
    r = client.post("/api/admin/pair", json={"code": "galerie.example.de ABCD-EFGH"}, headers=head)
    assert r.status_code == 200, r.text
    out = r.json()
    assert gallery.calls == [("https://galerie.example.de/api/v2/pair", {"code": "ABCDEFGH"})]
    server = booth.store.local["server"]
    assert server["url"] == "https://galerie.example.de" and server["api_key"] == KEY
    assert server["device_name"] == "Box 2" and server["gallery_name"] == "Fotobox Heidrich"
    # Saved to local.json, not only in memory
    assert KEY in (booth.data / "local.json").read_text(encoding="utf-8")
    # No restart: the sync reads the settings at every round
    assert out["restart_required"] is False
    assert out["pairing"]["paired"] and out["pairing"]["gallery_name"] == "Fotobox Heidrich"
    # The key never goes back to the page, the code never into the log
    assert KEY not in r.text
    assert "ABCD" not in caplog.text and KEY not in caplog.text


def test_the_long_code_needs_no_network(booth, monkeypatch):
    from photobox_sync.pairing import make_code
    FakeGallery(monkeypatch, error=AssertionError("no network wanted"))
    client, head = _client(booth)
    code = make_code("https://galerie.example.de", KEY, "Box 3")
    r = client.post("/api/admin/pair", json={"code": code}, headers=head)
    assert r.status_code == 200, r.text
    assert booth.store.local["server"]["api_key"] == KEY
    assert booth.store.local["server"]["device_name"] == "Box 3"


@pytest.mark.parametrize("status,body,error,wanted,code", [
    (404, {"ok": False, "reason": "expired"}, None, "abgelaufen", 400),
    (404, {"ok": False, "reason": "used"}, None, "schon eingelöst", 400),
    (429, {"ok": False}, None, "Zu viele Versuche", 429),
    (200, None, httpx.ConnectError("down"), "nicht erreichbar", 502),
])
def test_a_refused_code_gets_a_plain_sentence_and_changes_nothing(booth, monkeypatch, status, body, error,
                                                                   wanted, code):
    booth.store.save_local({"server": {"url": "https://alt.example.de", "api_key": "alter-schluessel"}})
    FakeGallery(monkeypatch, status=status, body=body, error=error)
    client, head = _client(booth)
    r = client.post("/api/admin/pair", json={"code": "galerie.example.de ABCD-EFGH"}, headers=head)
    assert r.status_code == code
    assert wanted in r.json()["detail"]
    assert booth.store.local["server"]["api_key"] == "alter-schluessel"


def test_a_malformed_code_is_refused_before_anything_is_sent(booth, monkeypatch):
    gallery = FakeGallery(monkeypatch)
    client, head = _client(booth)
    r = client.post("/api/admin/pair", json={"code": "ABC-12"}, headers=head)
    assert r.status_code == 400 and "acht Zeichen" in r.json()["detail"]
    assert gallery.calls == []


def test_pairing_needs_the_admin_pin(booth, monkeypatch):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    FakeGallery(monkeypatch)
    r = TestClient(create_app(booth)).post("/api/admin/pair", json={"code": "galerie.example.de ABCD-EFGH"})
    assert r.status_code == 401
    assert not booth.store.local["server"]["api_key"]


def test_after_pairing_the_booth_asks_the_new_gallery_for_its_profile(booth, monkeypatch):
    """A new gallery may hold the same profile version number as the old one."""
    FakeGallery(monkeypatch)
    booth.store.profile["version"] = 7
    booth.pair("galerie.example.de ABCD-EFGH")
    assert booth.agent.profile_version() == -1
    booth._apply_server_profile({"version": 7, "event": {"slug": "fest", "name": "Fest"}})
    assert booth.agent.profile_version() == 7


# ── kopplung.txt ──────────────────────────────────────────
def test_a_pairing_file_in_the_data_folder_is_redeemed_and_deleted(booth, monkeypatch):
    FakeGallery(monkeypatch)
    f = booth.data / "kopplung.txt"
    f.write_text("galerie.example.de ABCD-EFGH\n", encoding="utf-8")
    assert booth.pair_from_file() is True
    assert not f.exists()
    assert booth.store.local["server"]["api_key"] == KEY


def test_a_refused_pairing_file_stays_and_the_log_names_no_code(booth, monkeypatch, caplog):
    FakeGallery(monkeypatch, status=404, body={"ok": False, "reason": "expired"})
    f = booth.data / "pairing.txt"
    f.write_text("galerie.example.de WXYZ-EFGH", encoding="utf-8")
    caplog.set_level(logging.INFO)
    assert booth.pair_from_file() is False
    assert f.exists()
    assert "abgelaufen" in caplog.text and "WXYZ" not in caplog.text
    assert not booth.store.local["server"]["api_key"]


def test_a_paired_booth_leaves_a_pairing_file_alone(booth, monkeypatch):
    gallery = FakeGallery(monkeypatch)
    booth.store.save_local({"server": {"url": "https://alt.example.de", "api_key": "alter-schluessel"}})
    (booth.data / "kopplung.txt").write_text("galerie.example.de ABCD-EFGH", encoding="utf-8")
    assert booth.pair_from_file() is False
    assert gallery.calls == []
    assert booth.store.local["server"]["api_key"] == "alter-schluessel"


# ── What the card and the self-check say ──────────────────
def _paired(booth, **event):
    booth.store.save_local({"server": {"url": "https://galerie.example.de", "api_key": KEY,
                                       "gallery_name": "Fotobox Heidrich", "device_name": "Box 2"}})
    booth.store.profile["event"].update({"slug": "", "name": "", "base_url": "", "published": None, **event})


def _event_check(booth):
    from boothcore.doctor import Doctor
    return Doctor(booth).check_event()


def test_no_event_assigned_is_a_warning_in_the_card_and_the_self_check(booth):
    _paired(booth)
    status = booth.pairing_status()
    assert status["paired"] and status["device_name"] == "Box 2"
    assert any("noch kein Event zugewiesen" in w and "Uploads werden abgelehnt" in w for w in status["warnings"])
    check = _event_check(booth)
    assert check.state == "warn" and "noch kein Event zugewiesen" in check.message


def test_an_event_without_address_has_no_qr_code(booth):
    _paired(booth, slug="fest", name="Fest", base_url="")
    status = booth.pairing_status()
    assert status["event"]["name"] == "Fest"
    assert any("keinen QR-Code" in w for w in status["warnings"])
    assert _event_check(booth).state == "warn"


def test_start_qr_on_an_unpublished_event_is_a_warning(booth):
    _paired(booth, slug="fest", name="Fest", base_url="https://galerie.example.de", published=False)
    booth.store.profile["start"]["show_qr"] = True
    check = _event_check(booth)
    assert check.state == "warn" and "nicht veröffentlicht" in check.message


def test_when_the_gallery_does_not_say_the_check_says_it_cannot_know(booth):
    _paired(booth, slug="fest", name="Fest", base_url="https://galerie.example.de", published=None)
    booth.store.profile["start"]["show_qr"] = True
    check = _event_check(booth)
    assert check.state == "ok" and "nicht feststellen" in check.message


def test_a_ready_event_is_fine(booth):
    _paired(booth, slug="fest", name="Fest", base_url="https://galerie.example.de", published=True)
    booth.store.profile["start"]["show_qr"] = True
    assert booth.pairing_status()["warnings"] == []
    assert _event_check(booth).state == "ok"


def test_without_a_gallery_the_event_check_is_skipped(booth):
    assert _event_check(booth).state == "skip"


# ── Pairing from the phone (the whole way: tests/test_pairing_by_phone.py) ──
def test_phone_pairing_needs_the_pin_and_keeps_the_verifier_to_itself(booth, monkeypatch):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    sent = []

    def fake_post(url, json=None, **kw):
        sent.append(json)
        if url.endswith("/api/v2/pair/request"):
            return httpx.Response(200, json={"ok": True, "code": "ABCDEFGH", "expires_in": 600,
                                             "url": "https://galerie.example.de/admin/devices/pair?c=ABCDEFGH"})
        return httpx.Response(200, json={"ok": True, "state": "open"})
    monkeypatch.setattr(httpx, "post", fake_post)
    plain = TestClient(create_app(booth))
    assert plain.post("/api/admin/pair/phone", json={"url": "galerie.example.de"}).status_code == 401
    client, head = _client(booth)
    r = client.post("/api/admin/pair/phone", json={"url": "galerie.example.de"}, headers=head)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["code"] == "ABCD-EFGH" and out["qr"].startswith("data:image/png")
    verifier = booth._phone_pair["verifier"]
    assert len(verifier) >= 32 and verifier not in r.text
    assert verifier not in str(sent[0]) and sent[0]["install_id"] == booth.store.local["install_id"]
    assert client.get("/api/admin/pair/phone", headers=head).json()["state"] == "open"
    assert sent[1]["verifier"] == verifier            # only the poll carries it, to the gallery
    client.post("/api/admin/pair/phone/cancel", headers=head)
    assert client.get("/api/admin/pair/phone", headers=head).json()["state"] == "none"


def test_phone_pairing_without_an_address_asks_for_one(booth):
    client, head = _client(booth)
    r = client.post("/api/admin/pair/phone", json={}, headers=head)
    assert r.status_code == 400 and "Adresse der Galerie" in r.json()["detail"]


def test_phone_pairing_with_an_unreachable_gallery_says_so(booth, monkeypatch):
    FakeGallery(monkeypatch, error=httpx.ConnectError("down"))
    client, head = _client(booth)
    r = client.post("/api/admin/pair/phone", json={"url": "galerie.example.de"}, headers=head)
    assert r.status_code == 502 and "nicht erreichbar" in r.json()["detail"]
