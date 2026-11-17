"""The live view's own exposure: visible, far enough, and in the PC's own order.

Booth PC, 15.11.2026, build 2fd8bd0 (Canon EOS M50, M, ISO 200, f/5.6, 1/125,
``flash.enabled``, ``preview_iso`` "auto", a dim room): the served live frames
stayed at mean 3/255 for minutes while six captures at the shot's own ISO 200
came out at 8.5/255. ``/api/state`` said nothing about the preview and the log
had no line about it, so nobody could tell whether the automatic ISO had moved
at all. Here:

* the PC's sequence (booth starts with the flash already in the profile, the
  camera connects later, nobody touches the kiosk) walked with the fake EDSDK;
* the ladder: ISO stop by stop up to ``camera.preview_iso_max``, then the
  shutter slower down to ``camera.preview_shutter_min``, back the other way;
  a refused rung stops that axis instead of being retried every 0.6 s;
* the state in ``/api/state`` → ``camera.preview`` and one INFO line per step;
* the software gain as the last rung;
* the shot's values read from the camera survive a crash in a small file.
"""
import io
import json
import logging
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

import fake_edsdk
from fake_edsdk import FakeEdsdk, bereitstellen

ISO, AV, TV = 0x402, 0x405, 0x406
BUSY = 0x00000081


def _jpeg(level: int, size=(320, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (level, level, level)).save(buf, "JPEG")
    return buf.getvalue()


def _fake(monkeypatch):
    """The fake SDK with a dial that remembers what was written (and can refuse single values)."""
    from boothcore.camera import edsdk as modul
    sdk = FakeEdsdk()
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    monkeypatch.setattr(fake_edsdk, "JPEG", _jpeg(3))
    sdk.werte = {ISO: modul.ISO_CODES["200"], TV: modul.TV_CODES["1/125"], AV: modul.AV_CODES["5.6"]}
    sdk.ablehnen = set()                                   # (prop, code) the camera refuses
    sdk.ablauf = []
    real_set, real_cmd = sdk.EdsSetPropertyData, sdk.EdsSendCommand

    def set_prop(cam, prop, param, size, data):
        err = real_set(cam, prop, param, size, data)
        value = sdk.gesetzt[-1][2]
        if prop in (ISO, AV, TV):
            if (prop, value) in sdk.ablehnen:
                err = BUSY
            sdk.ablauf.append(("set", prop, value))
            if err == 0:
                sdk.werte[prop] = value
        return err

    def cmd(cam, befehl, param):
        if befehl == 0x00000004:
            sdk.ablauf.append(("press", param))
        return real_cmd(cam, befehl, param)

    def get_prop(cam, prop, p, size, out):
        out._obj.value = sdk.werte.get(prop, 3)
        return 0
    sdk.EdsSetPropertyData, sdk.EdsSendCommand, sdk.EdsGetPropertyData = set_prop, cmd, get_prop
    return modul, sdk


def _names(modul, prop):
    table = {ISO: modul.ISO_CODES, TV: modul.TV_CODES, AV: modul.AV_CODES}[prop]
    return {v: k for k, v in table.items()}


def _writes(sdk, modul, prop):
    names = _names(modul, prop)
    return [names.get(v, v) for kind, p, *rest in sdk.ablauf if kind == "set" and p == prop for v in rest]


# ── the PC's sequence ──────────────────────────────────────────────────────
@pytest.fixture()
def pc(monkeypatch):
    """A data folder whose saved profile already has the PC's flash settings."""
    modul, sdk = _fake(monkeypatch)
    d = Path(tempfile.mkdtemp(prefix="booth-pcseq-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "edsdk")
    monkeypatch.setattr(modul.EdsdkCamera, "FIND_TIMEOUT", 0.1)
    from boothcore.app import Booth
    first = Booth(base=d, start_threads=False)
    profile = json.loads(json.dumps(first.profile))
    profile["flash"].update({"enabled": True, "recycle_seconds": 2.5, "preview_gain": 0, "preview_gain_auto": False})
    profile["camera"].update({"iso": "200", "aperture": "5.6", "shutter": "1/125", "wb": "",
                              "preview_iso": "auto", "preview_aperture": "", "preview_shutter": ""})
    first.store.apply_profile(profile)
    first.mail.stop()
    return d, modul, sdk


def test_a_booth_started_with_the_flash_climbs_the_ladder_once_the_camera_connects_late(pc, caplog):
    d, modul, sdk = pc
    sdk.kameras = 0                                         # switched on later: the first connects fail
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.AUTO_EVERY = 0.02
    with caplog.at_level(logging.INFO, logger="booth.camera"):
        b.camera.start()
        try:
            time.sleep(1.0)
            assert b.camera.state != "ready" and b.camera.stats["reconnects"] >= 1
            assert _writes(sdk, modul, ISO) == []
            sdk.kameras = 1
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not b.camera.preview_at_end:
                time.sleep(0.05)
            assert b.camera.state == "ready"
            pv = b.camera.info()["preview"]
        finally:
            b.camera.stop()
            b.mail.stop()
    isos = _writes(sdk, modul, ISO)
    climb = [v for i, v in enumerate(isos) if i == 0 or v != isos[i - 1]]   # connect and preview both write 200
    assert climb[:7] == ["200", "400", "800", "1600", "3200", "6400", "12800"]
    assert ["1/60", "1/30"] == [v for v in _writes(sdk, modul, TV) if v in ("1/60", "1/30")]
    assert pv["on"] is True and pv["at_end"] is True
    assert pv["live"] == {"iso": "12800", "shutter": "1/30"}
    assert pv["shot"] == {"iso": "200", "shutter": "1/125"}
    assert pv["mean"] is not None and pv["mean"] < 10
    assert any("auto iso 12800" in e for e in pv["log"])
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Live-Bild:")]
    assert "Live-Bild: ISO 3200" in lines and "Live-Bild: Verschlusszeit 1/30" in lines
    assert lines.count("Live-Bild: ISO 3200") == 1, "one line per step, never per frame"
    # stop() put the shot's values back on the camera
    assert sdk.werte[ISO] == modul.ISO_CODES["200"] and sdk.werte[TV] == modul.TV_CODES["1/125"]


# ── the ladder as a pure function ──────────────────────────────────────────
def _step(iso, shutter, mean, shot="1/125", auto_shutter=True, refused=()):
    from boothcore.camera import exposure
    return exposure.next_auto_step(iso, shutter, shot, mean, iso_max="12800", shutter_min="1/30",
                                   auto_shutter=auto_shutter, refused=set(refused))


@pytest.mark.parametrize("iso,shutter,mean,shot,auto_shutter,refused,step", [
    ("200", "1/125", 3, "1/125", True, (), ("iso", "400")),
    ("6400", "1/125", 3, "1/125", True, (), ("iso", "12800")),
    ("12800", "1/125", 3, "1/125", True, (), ("shutter", "1/60")),
    ("12800", "1/60", 3, "1/125", True, (), ("shutter", "1/30")),
    ("12800", "1/30", 3, "1/125", True, (), None),                      # the end of the ladder
    ("12800", "1/125", 3, "1/125", False, (), None),                    # preview_shutter set: no auto shutter
    ("6400", "1/125", 3, "1/125", True, (("iso", "12800"),), ("shutter", "1/60")),
    ("1600", "1/125", 3, "1/125", True, (("iso", "3200"),), ("shutter", "1/60")),
    ("12800", "1/30", 200, "1/125", True, (), ("shutter", "1/60")),     # back: the shutter first
    ("12800", "1/60", 200, "1/125", True, (), ("shutter", "1/125")),
    ("12800", "1/125", 200, "1/125", True, (), ("iso", "6400")),
    ("100", "1/125", 200, "1/125", True, (), None),
    ("800", "1/60", 110, "1/125", True, (), None),                      # in the band: stays
    ("12800", "1/125", 3, "1/100", True, (), ("shutter", "1/60")),      # a third-stop shot shutter
    ("12800", "1/60", 200, "1/100", True, (), ("shutter", "1/100")),    # back exactly to it
])
def test_the_ladder_takes_one_step(iso, shutter, mean, shot, auto_shutter, refused, step):
    assert _step(iso, shutter, mean, shot, auto_shutter, refused) == step


def test_only_values_the_camera_table_knows_are_offered():
    from boothcore.camera import edsdk, exposure
    for v in exposure.ISO_STOPS + exposure.ISO_MAX_CHOICES:
        assert v in edsdk.ISO_CODES, v
    for v in exposure.SHUTTER_STOPS + exposure.SHUTTER_MIN_CHOICES:
        assert v in edsdk.TV_CODES, v


# ── the camera service with the fake SDK ───────────────────────────────────
def _service(modul, desired=None, preview=None, shot_file=None):
    from boothcore.camera.service import CameraService
    svc = CameraService(lambda: modul.EdsdkCamera())
    svc.desired_settings = dict(desired or {})
    svc.preview_settings = dict(preview or {})
    svc.shot_values_file = shot_file
    svc.AUTO_EVERY = 0.0
    assert svc._connect()
    return svc


AUTO = {"iso": "auto", "iso_max": "12800", "shutter_min": "1/30"}


def test_a_shot_after_iso_and_shutter_moved_gets_both_back_first(monkeypatch, tmp_path):
    modul, sdk = _fake(monkeypatch)
    svc = _service(modul, desired={"iso": "200", "shutter": "1/125", "aperture": "5.6"}, preview=AUTO)
    for _ in range(12):
        svc._preview_auto(_jpeg(3))
    assert svc.preview_status()["live"] == {"iso": "12800", "shutter": "1/30"}
    sdk.ablauf.clear()
    svc._capture(tmp_path / "a.jpg")
    first_press = next(i for i, e in enumerate(sdk.ablauf) if e[0] == "press")
    last_press = max(i for i, e in enumerate(sdk.ablauf) if e[0] == "press")
    before, after = sdk.ablauf[:first_press], sdk.ablauf[last_press + 1:]
    assert ("set", ISO, modul.ISO_CODES["200"]) in before and ("set", TV, modul.TV_CODES["1/125"]) in before
    assert ("set", ISO, modul.ISO_CODES["12800"]) in after and ("set", TV, modul.TV_CODES["1/30"]) in after
    assert not [e for e in sdk.ablauf[first_press:last_press] if e[0] == "set"], "nothing written mid-shot"


def test_a_shot_is_taken_when_neither_iso_nor_shutter_can_be_put_back(monkeypatch, tmp_path, caplog):
    modul, sdk = _fake(monkeypatch)
    svc = _service(modul, desired={"iso": "200", "shutter": "1/125"}, preview=AUTO)
    for _ in range(12):
        svc._preview_auto(_jpeg(3))
    sdk.ablehnen |= {(ISO, modul.ISO_CODES["200"]), (TV, modul.TV_CODES["1/125"])}
    with caplog.at_level(logging.ERROR, logger="booth.camera"):
        svc._capture(tmp_path / "a.jpg")
    assert (tmp_path / "a.jpg").exists() and sdk.ausloesungen == 1
    assert any("ausgelöst wird trotzdem" in r.getMessage() for r in caplog.records)


def test_a_refused_rung_stops_that_axis_instead_of_being_tried_again(monkeypatch, caplog):
    modul, sdk = _fake(monkeypatch)
    svc = _service(modul, desired={"iso": "200", "shutter": "1/125"}, preview=AUTO)
    sdk.ablehnen.add((ISO, modul.ISO_CODES["3200"]))
    with caplog.at_level(logging.WARNING, logger="booth.camera"):
        for _ in range(20):
            svc._preview_auto(_jpeg(3))
    assert _writes(sdk, modul, ISO).count("3200") == 1, "tried once, not every 0.6 s"
    assert "6400" not in _writes(sdk, modul, ISO), "no climbing past a refused rung"
    assert svc.preview_status()["live"] == {"iso": "1600", "shutter": "1/30"}
    assert svc.preview_at_end is True
    assert len([r for r in caplog.records if "3200" in r.getMessage()]) == 1


def test_a_value_the_camera_does_not_keep_is_reported(monkeypatch, caplog):
    modul, sdk = _fake(monkeypatch)
    svc = _service(modul, desired={"iso": "200"}, preview={"iso": "auto"})
    real = sdk.EdsSetPropertyData

    def says_ok_keeps_200(cam, prop, param, size, data):
        err = real(cam, prop, param, size, data)
        sdk.werte[ISO] = modul.ISO_CODES["200"]
        return err
    sdk.EdsSetPropertyData = says_ok_keeps_200
    with caplog.at_level(logging.WARNING, logger="booth.camera"):
        svc._preview_auto(_jpeg(3))
    assert svc.preview_status()["camera_reports"] == {"iso": "200"}
    assert any("meldet" in r.getMessage() for r in caplog.records)


def test_with_a_fixed_preview_shutter_only_the_iso_moves(monkeypatch):
    modul, sdk = _fake(monkeypatch)
    svc = _service(modul, desired={"iso": "200", "shutter": "1/125"},
                   preview={"iso": "auto", "shutter": "1/60", "iso_max": "6400"})
    for _ in range(10):
        svc._preview_auto(_jpeg(3))
    assert svc.preview_status()["live"] == {"iso": "6400", "shutter": "1/60"}
    assert _writes(sdk, modul, TV) == ["1/125", "1/60"], "the profile's shot value, then the fixed one"


def test_a_bright_frame_walks_back_shutter_first(monkeypatch):
    modul, sdk = _fake(monkeypatch)
    svc = _service(modul, desired={"iso": "200", "shutter": "1/125"}, preview=AUTO)
    for _ in range(12):
        svc._preview_auto(_jpeg(3))
    sdk.ablauf.clear()
    for _ in range(3):
        svc._preview_auto(_jpeg(240))
    assert [e[1] for e in sdk.ablauf] == [TV, TV, ISO]
    assert svc.preview_status()["live"] == {"iso": "6400", "shutter": "1/125"}
    assert svc.preview_at_end is False


# ── the shot's values survive a crash ─────────────────────────────────────
def test_the_shot_values_read_from_the_camera_survive_a_crash(monkeypatch, tmp_path):
    modul, sdk = _fake(monkeypatch)
    f = tmp_path / "preview_shot.json"
    svc = _service(modul, desired={}, preview=AUTO, shot_file=f)
    assert json.loads(f.read_text()) == {"iso": "200", "shutter": "1/125"}
    for _ in range(12):
        svc._preview_auto(_jpeg(3))
    # the process dies here: the camera keeps ISO 12800 and 1/30
    assert sdk.werte[ISO] == modul.ISO_CODES["12800"]
    again = _service(modul, desired={}, preview=AUTO, shot_file=f)
    assert again._shot_values == {"iso": "200", "shutter": "1/125"}
    again.set_preview_settings({})
    again._handle_jobs()
    assert not f.exists(), "switched off cleanly: nothing left to remember"
    assert sdk.werte[ISO] == modul.ISO_CODES["200"] and sdk.werte[TV] == modul.TV_CODES["1/125"]


def test_the_profile_still_wins_over_a_remembered_value(monkeypatch, tmp_path):
    modul, sdk = _fake(monkeypatch)
    f = tmp_path / "preview_shot.json"
    f.write_text(json.dumps({"iso": "100", "shutter": "1/60"}))
    svc = _service(modul, desired={"iso": "400"}, preview=AUTO, shot_file=f)
    assert svc._shot_values == {"iso": "400", "shutter": "1/60"}


# ── the booth: profile, state, self-check, gain ───────────────────────────
@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-pvstate-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def test_the_profile_carries_the_ladder_limits(booth):
    booth.store.profile["flash"]["enabled"] = True
    assert booth._preview_exposure() == {"iso": "auto", "iso_max": "12800", "shutter_min": "1/30"}
    booth.store.profile["camera"].update({"preview_iso_max": "25600", "preview_shutter_min": "1/15"})
    assert booth._preview_exposure() == {"iso": "auto", "iso_max": "25600", "shutter_min": "1/15"}
    booth.store.profile["camera"].update({"preview_iso_max": "999", "preview_shutter_min": "2"})
    assert booth._preview_exposure() == {"iso": "auto", "iso_max": "12800", "shutter_min": "1/30"}
    booth.store.profile["camera"]["preview_shutter"] = "1/60"
    assert booth._preview_exposure() == {"iso": "auto", "shutter": "1/60", "iso_max": "12800"}
    assert booth.camera.shot_values_file == booth.store.local_path.parent / "preview_shot.json"


def test_the_state_and_the_self_check_show_the_preview(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    cam = booth.camera
    cam._preview_on = True
    cam._preview_now = {"iso": "6400", "shutter": "1/30"}
    cam._shot_values = {"iso": "200", "shutter": "1/125"}
    cam._last_mean = 42.0
    cam._note("auto iso 6400")
    pv = TestClient(create_app(booth)).get("/api/state").json()["camera"]["preview"]
    assert pv["on"] is True and pv["live"] == {"iso": "6400", "shutter": "1/30"}
    assert pv["shot"] == {"iso": "200", "shutter": "1/125"} and pv["mean"] == 42.0
    assert pv["log"][-1] == "auto iso 6400"
    line = cam.preview_line()
    assert "\n" not in line and "ISO 6400" in line and "1/30" in line and "ISO 200" in line and "42" in line
    booth.store.profile["flash"]["enabled"] = True
    from boothcore.doctor import Doctor
    check = Doctor(booth).check_flash()
    assert line in check.message
    cam._preview_on = False
    assert cam.preview_line() == ""


def test_the_end_of_the_ladder_switches_the_software_gain_on(booth):
    from boothcore.camera import exposure
    booth.store.profile["flash"].update({"enabled": True, "preview_gain": 0, "preview_gain_auto": False})
    dark = _jpeg(10)
    assert booth.screen_frame(dark, 1) is dark, "the ladder still has rungs: nothing in software"
    booth.camera.preview_at_end = True
    out = booth.screen_frame(dark, 2)
    assert exposure.GAIN_CAP == 8.0
    assert 70 <= exposure.jpeg_mean(out) <= 90, "x8, the cap"
    booth.store.profile["flash"]["enabled"] = False
    assert booth.screen_frame(dark, 3) is dark


def test_the_gain_does_not_burn_out_a_lamp_in_the_frame():
    from boothcore.camera import exposure
    img = Image.new("RGB", (320, 240), (12, 12, 12))
    img.paste((150, 150, 150), (0, 0, 40, 40))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    out = Image.open(io.BytesIO(exposure.brighten_jpeg(buf.getvalue(), "auto"))).convert("L")
    assert out.getpixel((20, 20)) < 255, "a soft shoulder, not a clip"
    assert out.getpixel((20, 20)) > out.getpixel((200, 200)) + 60, "the lamp is still the brightest thing"


def test_a_start_without_the_flash_puts_remembered_shot_values_back_once(monkeypatch, tmp_path):
    modul, sdk = _fake(monkeypatch)
    f = tmp_path / "preview_shot.json"
    f.write_text(json.dumps({"iso": "200", "shutter": "1/125"}))
    sdk.werte[ISO], sdk.werte[TV] = modul.ISO_CODES["12800"], modul.TV_CODES["1/30"]   # left by a crash
    _service(modul, desired={}, preview={}, shot_file=f)
    assert sdk.werte[ISO] == modul.ISO_CODES["200"] and sdk.werte[TV] == modul.TV_CODES["1/125"]
    assert not f.exists()
