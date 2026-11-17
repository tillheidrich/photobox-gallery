"""Consent as a choice per run: "In die Galerie" or "Nur drucken".

Off by default, so existing profiles behave as before. When it is on and
printing is on, the guest picks before the shot. "Nur drucken" means this one
run never leaves the booth: no upload, no QR link, no mail, not in the kiosk
gallery, and after the print its files go the way of the data-minimal mode.
What is kept is a record without personal data: which option, which text
(version and hash), when.
"""
import json
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    d = Path(tempfile.mkdtemp(prefix="booth-consent-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8},
                        "storage": {"folder": str(tmp_path / "ablage")}})
    b.store.profile["event"].update({"slug": "hochzeit", "name": "Hochzeit", "share_key": "k",
                                     "base_url": "https://galerie.example"})
    b.store.profile["print"].update({"enabled": True, "auto": False})
    b.store.profile["qr_on_screen"] = True
    b.store.profile["mail"]["enabled"] = True
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def _on(booth, **extra):
    booth.store.profile["consent"].update({"enabled": True, **extra})


def _shoot(booth, consent=None):
    s = booth.start_session("photo", "4:5", None, None, consent=consent)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _wait(cond, seconds=60):
    for _ in range(int(seconds * 10)):
        if cond():
            return True
        time.sleep(0.1)
    return cond()


def test_off_by_default_and_existing_profiles_do_not_change(booth, client):
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["consent"]["enabled"] is False
    assert client.get("/api/state").json()["consent"]["options"] == []
    s = _shoot(booth)                                   # no choice needed, as before
    assert s.share_url and booth.outbox.counts()["pending"] > 0


def test_both_options_are_offered_when_printing_is_on(booth, client):
    _on(booth, days=14)
    c = client.get("/api/state").json()["consent"]
    assert c["options"] == ["gallery", "print"]
    assert c["note"]["de"] == ("Galerie: nur mit Passwort, gelöscht nach 14 Tagen. "
                               "Löschen jederzeit über euren QR-Code.")
    assert "14" in c["note"]["en"] and "{" not in c["note"]["en"]


@pytest.mark.parametrize("why", ["print_off", "no_print_option"])
def test_fewer_than_two_options_drop_the_step(booth, client, why):
    _on(booth)
    if why == "print_off":
        booth.store.profile["print"]["enabled"] = False
    else:
        booth.store.profile["consent"]["print_only"] = False
    assert client.get("/api/state").json()["consent"]["options"] == []
    assert _shoot(booth).share_url                       # the run starts without a choice


def test_with_the_step_on_a_run_needs_a_choice(booth, client):
    _on(booth)
    r = client.post("/api/session", json={"mode": "photo"})
    assert r.status_code == 400 and r.json()["detail"] == "consent.missing"
    r = client.post("/api/session", json={"mode": "photo", "consent": {"option": "anything"}})
    assert r.status_code == 400
    r = client.post("/api/session", json={"mode": "photo", "consent": {"option": "gallery", "lang": "de"}})
    assert r.status_code == 200


def test_gallery_works_as_before_and_leaves_a_record(booth):
    _on(booth)
    s = _shoot(booth, {"option": "gallery", "lang": "de"})
    assert s.share_url.startswith("https://galerie.example")
    assert booth.outbox.counts()["pending"] > 0
    rec = booth.library.consent(s.uid)
    assert rec["option"] == "gallery" and rec["lang"] == "de"
    assert len(rec["text_hash"]) == 16 and isinstance(rec["text_version"], int) and rec["at"]
    assert set(rec) == {"option", "lang", "text_hash", "text_version", "at"}, "nothing about the person"


def test_print_only_never_leaves_the_booth(booth, client):
    _on(booth)
    s = _shoot(booth, {"option": "print", "lang": "de"})
    assert s.share_url == ""
    assert booth.outbox.counts()["pending"] == 0, "no upload, no second folder, no stick"
    item = client.get(f"/api/gallery/{s.uid}").json()
    assert item["share_url"] == "" and item["qr_url"] == ""
    assert client.post(f"/api/session/{s.uid}/mail", json={"address": "a@b.de"}).status_code == 403
    listed = [i["uid"] for i in client.get("/api/gallery").json()["items"]]
    assert s.uid not in listed, "not in the kiosk gallery for the next guests"
    assert (s.folder / ".datensparsam").exists(), "a power cut leaves no orphan behind"


def test_print_only_is_wiped_after_the_print_but_the_record_stays(booth):
    _on(booth)
    s = _shoot(booth, {"option": "print", "lang": "en"})
    folder = s.folder
    booth.print_uid(s.uid, 1)
    assert _wait(lambda: not booth.printer.pending)
    assert _wait(lambda: booth.library.get(s.uid) is None), "entry gone after the print"
    assert not folder.exists(), "files gone after the print"
    rec = booth.library.consent(s.uid)
    assert rec["option"] == "print" and rec["lang"] == "en"


def test_the_next_run_is_a_gallery_run_again(booth):
    _on(booth)
    _shoot(booth, {"option": "print"})
    assert booth.outbox.counts()["pending"] == 0
    s = _shoot(booth, {"option": "gallery"})
    assert s.share_url and booth.outbox.counts()["pending"] > 0


def test_the_record_follows_the_text_shown(booth):
    _on(booth)
    a = _shoot(booth, {"option": "gallery", "lang": "de"})
    booth.store.profile["consent"]["note"]["de"] = "Anderer Text, {tage} Tage."
    b2 = _shoot(booth, {"option": "gallery", "lang": "de"})
    assert booth.library.consent(a.uid)["text_hash"] != booth.library.consent(b2.uid)["text_hash"]


def test_the_data_minimal_booth_does_not_ask(booth, client):
    _on(booth)
    booth.store.profile["privacy"]["mode"] = "print_only"
    assert client.get("/api/state").json()["consent"]["options"] == []
