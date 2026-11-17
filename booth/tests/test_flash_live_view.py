"""A bright live view with flash exposure, the flash only at the shot.

Booth PC, 15.11.2026: for the flash the M50 is in M at 1/125 s, f/5.6, ISO
200. Its live view simulates that exposure without the flash: almost black.
Now, with ``flash.enabled`` only:

* the live view gets its own, brighter values (``camera.preview_*``; "auto"
  raises the ISO stop by stop until the frame is bright enough), the shot's
  values go back on immediately before the shutter and the live view's after
  it — on the camera thread, so never during a capture; a failure to put the
  shot's values back is logged and the shot is taken anyway;
* the frames the booth serves to screens are brightened in software
  (``flash.preview_gain`` or ``preview_gain_auto``), never a captured picture;
* a near-black picture taken with the flash is logged, counted and raises one
  operator notice (EXIF "flash fired" is no evidence: the M50 wrote Flash=9 for
  a frame of mean 2/255).

Without ``flash.enabled`` nothing is switched at all. The order of property
writes is checked against the fake EDSDK.
"""
import io
import logging
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from fake_edsdk import FakeEdsdk, bereitstellen

ISO, AV, TV = 0x402, 0x405, 0x406


@pytest.fixture()
def edsdk(monkeypatch):
    from boothcore.camera import edsdk as modul
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    # One list in order: property writes and shutter commands as they happen.
    sdk.ablauf = []
    real_set, real_cmd = sdk.EdsSetPropertyData, sdk.EdsSendCommand

    def set_prop(cam, prop, param, size, data):
        err = real_set(cam, prop, param, size, data)
        if prop in (ISO, AV, TV):
            sdk.ablauf.append(("set", prop, sdk.gesetzt[-1][2]))
        return err

    def cmd(cam, befehl, param):
        if befehl == 0x00000004:
            sdk.ablauf.append(("press", param))
        return real_cmd(cam, befehl, param)
    sdk.EdsSetPropertyData, sdk.EdsSendCommand = set_prop, cmd
    # The dial: what the camera says it is set to (ISO 200, 1/125, f/5.6).
    sdk.werte = {ISO: modul.ISO_CODES["200"], TV: modul.TV_CODES["1/125"], AV: modul.AV_CODES["5.6"]}

    def get_prop(cam, prop, p, size, out):
        out._obj.value = sdk.werte.get(prop, 3)
        return 0
    sdk.EdsGetPropertyData = get_prop
    return modul, sdk


def _service(modul, desired=None, preview=None):
    from boothcore.camera.service import CameraService
    svc = CameraService(lambda: modul.EdsdkCamera())
    svc.desired_settings = dict(desired or {})
    svc.preview_settings = dict(preview or {})
    assert svc._connect()
    return svc


def _iso_writes(sdk, modul):
    names = {v: k for k, v in modul.ISO_CODES.items()}
    return [names[v] for kind, prop, *rest in sdk.ablauf if kind == "set" and prop == ISO for v in rest]


def test_the_shot_values_go_on_right_before_the_shutter_and_the_live_view_values_after(edsdk, tmp_path):
    modul, sdk = edsdk
    svc = _service(modul, desired={"iso": "200", "shutter": "1/125", "aperture": "5.6"},
                   preview={"iso": "3200", "shutter": "1/30"})
    sdk.ablauf.clear()
    svc._capture(tmp_path / "a.jpg")
    assert (tmp_path / "a.jpg").exists()
    first_press = next(i for i, e in enumerate(sdk.ablauf) if e[0] == "press")
    last_press = max(i for i, e in enumerate(sdk.ablauf) if e[0] == "press")
    before = sdk.ablauf[:first_press]
    after = sdk.ablauf[last_press + 1:]
    assert ("set", ISO, modul.ISO_CODES["200"]) in before and ("set", TV, modul.TV_CODES["1/125"]) in before
    assert ("set", ISO, modul.ISO_CODES["3200"]) in after and ("set", TV, modul.TV_CODES["1/30"]) in after
    assert not [e for e in sdk.ablauf[first_press:last_press] if e[0] == "set"], "nothing written mid-shot"


def test_without_the_flash_nothing_is_switched(edsdk, tmp_path):
    modul, sdk = edsdk
    svc = _service(modul, desired={"iso": "200"}, preview={})
    assert _iso_writes(sdk, modul) == ["200"], "only the profile's own value, at connect"
    sdk.ablauf.clear()
    svc._capture(tmp_path / "a.jpg")
    assert [e for e in sdk.ablauf if e[0] == "set"] == []


def test_the_shot_values_come_from_the_dial_when_the_profile_leaves_them_empty(edsdk, tmp_path):
    modul, sdk = edsdk
    svc = _service(modul, desired={}, preview={"iso": "1600"})
    assert svc._shot_values == {"iso": "200"}
    sdk.ablauf.clear()
    svc._capture(tmp_path / "a.jpg")
    assert _iso_writes(sdk, modul) == ["200", "1600"]


def test_a_shot_is_taken_even_when_its_values_cannot_be_put_back(edsdk, tmp_path, caplog):
    modul, sdk = edsdk
    svc = _service(modul, desired={"iso": "200"}, preview={"iso": "3200"})
    sdk.eigenschaft_fehler[ISO] = 0x00000081                # the camera answers "busy" to the ISO
    with caplog.at_level(logging.ERROR, logger="booth.camera"):
        svc._capture(tmp_path / "a.jpg")
    assert (tmp_path / "a.jpg").exists() and sdk.ausloesungen == 1
    assert any("ausgelöst wird trotzdem" in r.getMessage() for r in caplog.records)


def test_a_shot_through_the_service_queue_switches_too(edsdk, tmp_path):
    modul, sdk = edsdk
    svc = _service(modul, desired={"iso": "200"}, preview={"iso": "3200"})
    sdk.ablauf.clear()
    from concurrent.futures import Future
    fut = Future()
    svc._jobs.put(("capture", tmp_path / "b.jpg", fut))
    svc._handle_jobs()
    assert fut.result(1) == tmp_path / "b.jpg"
    assert _iso_writes(sdk, modul) == ["200", "3200"]


def test_switching_the_flash_off_puts_the_shot_values_back_once(edsdk):
    modul, sdk = edsdk
    svc = _service(modul, desired={}, preview={"iso": "3200"})
    sdk.ablauf.clear()
    svc.set_preview_settings({})
    svc._handle_jobs()
    assert _iso_writes(sdk, modul) == ["200"]
    svc.set_preview_settings({})                            # nothing new: nothing written
    svc._handle_jobs()
    assert _iso_writes(sdk, modul) == ["200"]


def test_a_reconnect_does_not_take_the_live_view_values_for_the_shots(edsdk):
    modul, sdk = edsdk
    svc = _service(modul, desired={}, preview={"iso": "3200"})
    sdk.werte[ISO] = modul.ISO_CODES["3200"]                # the camera kept the live-view ISO
    assert svc._connect()
    assert svc._shot_values == {"iso": "200"}


def test_a_new_profile_value_for_the_shot_is_the_one_put_back(edsdk, tmp_path):
    modul, sdk = edsdk
    svc = _service(modul, desired={"iso": "200"}, preview={"iso": "3200"})
    from concurrent.futures import Future
    svc.desired_settings = {"iso": "400"}
    fut = Future()
    svc._jobs.put(("settings", {"iso": "400"}, fut))
    svc._handle_jobs()
    sdk.ablauf.clear()
    svc._capture(tmp_path / "a.jpg")
    assert _iso_writes(sdk, modul) == ["400", "3200"]


# ── "auto": one stop at a time until the live frame is bright enough ──────
def _jpeg(level: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (level, level, level)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.mark.parametrize("current,level,step", [
    ("200", 10, "400"), ("400", 10, "800"), ("6400", 10, "12800"), ("12800", 10, None),
    ("1600", 230, "800"), ("100", 230, None), ("800", 110, None), ("250", 10, "400"), ("auto", 10, "800"),
])
def test_the_automatic_preview_iso_moves_one_stop(current, level, step):
    from boothcore.camera import exposure
    assert exposure.next_auto_iso(current, exposure.jpeg_mean(_jpeg(level))) == step


def test_auto_raises_the_live_view_iso_on_a_dark_frame(edsdk):
    modul, sdk = edsdk
    svc = _service(modul, desired={"iso": "200"}, preview={"iso": "auto"})
    assert svc._auto_iso == "200"
    svc.AUTO_EVERY = 0.0
    sdk.ablauf.clear()
    svc._preview_auto(_jpeg(8))
    svc._preview_auto(_jpeg(8))
    svc._preview_auto(_jpeg(110))                           # in the band: stays
    assert _iso_writes(sdk, modul) == ["400", "800"]
    assert svc._auto_iso == "800"


def test_auto_does_nothing_without_the_flash(edsdk):
    modul, sdk = edsdk
    svc = _service(modul, desired={"iso": "200"}, preview={})
    svc.AUTO_EVERY = 0.0
    sdk.ablauf.clear()
    svc._preview_auto(_jpeg(8))
    assert sdk.ablauf == []


# ── the booth: profile → camera service ────────────────────────────────
@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-flashlive-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["event"].update({"slug": "hochzeit"})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def test_the_booth_asks_for_live_view_values_only_with_the_flash(booth):
    assert booth._preview_exposure() == {}
    booth.store.profile["flash"]["enabled"] = True
    assert booth._preview_exposure() == {"iso": "auto", "iso_max": "12800", "shutter_min": "1/30"}
    booth.store.profile["camera"].update({"preview_iso": "1600", "preview_shutter": "1/30"})
    assert booth._preview_exposure() == {"iso": "1600", "shutter": "1/30"}
    booth.on_profile_changed()
    assert booth.camera.preview_settings == {"iso": "1600", "shutter": "1/30"}
    # the live-view keys never reach the shot's settings
    assert "preview_iso" not in booth._shot_settings()


# ── software gain on the frames served to screens ──────────────────────
def _mean(data: bytes) -> float:
    from boothcore.camera import exposure
    return exposure.jpeg_mean(data)


def test_the_gain_brightens_the_served_frame_only_with_the_flash(booth):
    dark = _jpeg(20)
    assert booth.screen_frame(dark, 1) is dark                 # flash off: untouched
    booth.store.profile["flash"].update({"enabled": True, "preview_gain": 100})
    out = booth.screen_frame(dark, 2)
    assert 36 <= _mean(out) <= 44, "+100 % = twice as bright"
    assert booth.screen_frame(dark, 2) is out, "one computation per frame for all screens"


def test_the_automatic_gain_stretches_towards_the_target_with_a_cap(booth):
    from boothcore.camera import exposure
    booth.store.profile["flash"].update({"enabled": True, "preview_gain_auto": True})
    assert abs(_mean(booth.screen_frame(_jpeg(50), 1)) - exposure.GAIN_TARGET) < 6
    assert _mean(booth.screen_frame(_jpeg(10), 2)) < 10 * exposure.GAIN_CAP + 4
    assert booth.screen_frame(_jpeg(140), 3) is not None
    assert abs(_mean(booth.screen_frame(_jpeg(140), 4)) - 140) < 4, "never darker, nothing to stretch"


def test_the_live_endpoints_serve_the_brightened_frame(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["flash"].update({"enabled": True, "preview_gain": 100})
    client = TestClient(create_app(booth))
    frame, seq = booth.camera.latest_frame(-1, 2.0)
    r = client.get("/api/live.jpg")
    assert r.status_code == 200
    assert _mean(r.content) > _mean(frame) * 1.5


def test_the_gain_never_touches_a_captured_picture(booth):
    booth.store.profile["flash"].update({"enabled": True, "preview_gain": 100})
    s = booth.start_session("photo", "4:5", None, None)
    src = Path(tempfile.mkdtemp()) / "src.jpg"
    Image.new("RGB", (600, 400), (60, 60, 60)).save(src, "JPEG", quality=95)
    booth.camera.capture = lambda target, timeout=0: Path(target).write_bytes(src.read_bytes()) and Path(target)
    shot = booth.take_shot(s)
    assert shot.read_bytes()[:2] == b"\xff\xd8" and abs(_mean(shot.read_bytes()) - 60) < 3


# ── a picture with the flash that came out black ──────────────────────
def _shoot(booth, level):
    s = booth.start_session("photo", "4:5", None, None)
    src = Path(tempfile.mkdtemp()) / "src.jpg"
    Image.new("RGB", (1200, 800), (level, level, level)).save(src, "JPEG", quality=90)
    booth.camera.capture = lambda target, timeout=0: Path(target).write_bytes(src.read_bytes()) and Path(target)
    return s, booth.take_shot(s)


def _notices(booth):
    return [m for m in booth.meldungen.offen() if m.get("code") == "op.flash_dark"]


def test_a_black_picture_with_the_flash_is_logged_counted_and_told(booth, caplog):
    booth.store.profile["flash"]["enabled"] = True
    with caplog.at_level(logging.WARNING, logger="booth"):
        s, shot = _shoot(booth, 2)
    assert shot.is_file(), "the picture is kept"
    assert any("fast schwarz" in r.getMessage() and s.uid in r.getMessage() for r in caplog.records)
    assert booth.zahlen.tag("hochzeit").get("flash.dark") == 1
    assert len(_notices(booth)) == 1
    _shoot(booth, 2)
    assert len(_notices(booth)) == 1, "one notice, not one per picture"
    _shoot(booth, 120)
    assert _notices(booth) == [], "a lit picture ends it"


def test_a_dark_picture_without_the_flash_is_nobodys_business(booth):
    _shoot(booth, 2)
    assert _notices(booth) == [] and "flash.dark" not in booth.zahlen.tag("hochzeit")


def test_the_notice_has_words_in_both_languages_on_both_sides():
    root = Path(__file__).resolve().parents[2]
    js = (root / "booth" / "boothcore" / "web" / "static" / "i18n.js").read_text(encoding="utf-8")
    py = (root / "app" / "booth_texts.py").read_text(encoding="utf-8")
    for key in ("op.flash_dark.title", "op.flash_dark.text"):
        assert js.count(f'"{key}"') == 2 and py.count(f'"{key}"') == 2, key
    assert "Blitz hat nicht ausgelöst oder ist zu schwach – bitte prüfen" in js
