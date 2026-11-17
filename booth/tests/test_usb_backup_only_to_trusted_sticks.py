"""Security review G3, finding S-7 (15.11.2026): the stick backup is a local decision.

Two holes: the gallery could switch the automatic backup on from afar (job
``set_local_config`` with ``storage.usb.enabled``), and once on, the booth
copied every guest picture onto *any* stick that was plugged in — a stranger
at the venue needed thirty seconds with a USB port. Now:

* the switch and the originals choice are not on ``REMOTE_LOCAL_KEYS`` on
  either side; only the admin at the box (PIN) changes them;
* a stick must be confirmed once at the box with the admin PIN. The booth
  writes a random id onto it and remembers that id (and the label); an
  unknown stick gets nothing, and the admin page asks.
"""
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _run(root, event, uid):
    d = root / "sessions" / event / "2026-11-18" / uid
    d.mkdir(parents=True)
    for name in ("result.jpg", "print.jpg", "shot1.jpg", "thumb.jpg"):
        (d / name).write_bytes(b"x" * 2000)


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    d = Path(tempfile.mkdtemp(prefix="booth-usb-trust-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    monkeypatch.setenv("BOOTH_USB_ROOTS", str(tmp_path / "vol"))
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    b.profile["event"]["slug"] = "mara-jonas"
    _run(d, "mara-jonas", "aaa111")
    b._vol = tmp_path / "vol"
    yield b
    b.mail.stop()


@pytest.fixture()
def admin(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    token = c.post("/api/admin/login", json={"pin": "468213"}).json()["token"]
    c.headers.update({"X-Admin-Token": token})
    return c


def _plug(booth, name="STICK"):
    (booth._vol / name).mkdir(parents=True)
    booth._usb_seen = (0.0, [])
    return booth._vol / name


def _wait(booth):
    for _ in range(200):
        if booth.usb_job and booth.usb_job.status()["state"] != "running":
            return
        time.sleep(0.02)


def test_the_switch_is_not_on_the_remote_list_of_either_side():
    from boothcore.config import REMOTE_LOCAL_KEYS
    assert "storage.usb.enabled" not in REMOTE_LOCAL_KEYS
    assert "storage.usb.originals" not in REMOTE_LOCAL_KEYS


def test_a_remote_job_cannot_switch_the_backup_on(booth):
    result = booth.agent.job_handlers["set_local_config"]({"storage.usb.enabled": True})
    assert result.startswith("nichts übernommen")
    assert not (booth.store.local.get("storage") or {}).get("usb", {}).get("enabled")


def test_an_unknown_stick_gets_nothing_even_when_switched_on(booth):
    booth.store.save_local({"storage": {"usb": {"enabled": True}}})
    stick = _plug(booth, "FREMD")
    assert booth.usb_auto_tick() is False
    assert not (stick / "Fotobox-Sicherung").exists()
    assert "bestätigen" in booth.usb_error


def test_a_manual_backup_to_an_unknown_stick_asks_first(booth, admin):
    _plug(booth, "FREMD")
    r = admin.post("/api/admin/usb/backup", json={"scope": "all"})
    assert r.status_code == 409 and "bestätigen" in r.json()["detail"]
    assert admin.get("/api/admin/usb").json()["drives"][0]["trusted"] is False


def test_confirming_a_stick_needs_the_pin_and_then_it_is_used(booth, admin):
    from boothcore.app import create_app
    booth.store.save_local({"storage": {"usb": {"enabled": True}}})
    stick = _plug(booth, "MEINER")
    stranger = TestClient(create_app(booth))
    assert stranger.post("/api/admin/usb/trust", json={"drive": str(stick)}).status_code == 401
    r = admin.post("/api/admin/usb/trust", json={"drive": str(stick)})
    assert r.status_code == 200 and r.json()["trusted"] is True
    assert booth.usb_auto_tick() is True
    _wait(booth)
    assert (stick / "Fotobox-Sicherung" / "mara-jonas").is_dir()


def test_a_stick_with_a_copied_label_is_still_unknown(booth, admin):
    """The label is shown to the operator; it is not what decides."""
    booth.store.save_local({"storage": {"usb": {"enabled": True}}})
    first = _plug(booth, "GLEICH")
    admin.post("/api/admin/usb/trust", json={"drive": str(first)})
    (booth._vol / "GLEICH").rename(booth._vol / "weg")
    (booth._vol / "weg" / ".photobox-usb-id").unlink()
    twin = _plug(booth, "GLEICH")
    assert booth.usb_auto_tick() is False
    assert not (twin / "Fotobox-Sicherung").exists()
