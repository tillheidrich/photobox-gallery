""""No focus" costs no run, no reconnect and no alarm.

Endurance run on the real booth, 17.11.2026 (Canon EOS M50 over EDSDK,
Windows, dim and static scene): 3 of 117 guest runs failed the same way::

    booth.camera WARNING: Kamerafehler, verbinde neu: EDSDK Auslösen fehlgeschlagen (0x00008D01)
    booth ERROR: Durchgang … (photo): Aufnahme 1/1 fehlgeschlagen — HTTP 503 camera.focus

once followed by the critical notice "Die Kamera antwortet nicht" (state
reconnecting). The camera was fine; the next run worked.

How it got through: the hold sequence fired with the press *without*
autofocus and checked its return code like a link error. On the M50 that
press itself answered 0x8D01 — not as an event, as the return code — and the
CameraError went up to the camera loop, which reconnects on every error of a
shot. The later "once more without autofocus" only ran for the refusal as an
event, only once, and a second refusal after it ended the shot as well.

Now every 0x8D01 (return code or event, half press, full press, press without
AF, classic or hold) releases the button and fires again without autofocus,
up to FOCUS_RETRIES times. Only when every attempt is refused does the shot
fail — as a refusal of that shot, which the camera loop does not answer with a
reconnect.
"""
import logging
import time

import pytest

from fake_edsdk import AF_NG, FakeEdsdk, bereitstellen

NONAF = 0x00010003
OFF = 0x00000000


def _cam(monkeypatch, sdk, release="hold"):
    from boothcore.camera import edsdk as modul
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    cam = modul.EdsdkCamera(release=release)
    cam.open()
    return cam


@pytest.mark.parametrize("spiegellos", [False, True])
def test_no_focus_as_the_return_code_of_the_press_without_af_still_gives_the_picture(monkeypatch, tmp_path,
                                                                                     spiegellos):
    """The branch the endurance run hit: press without AF returns 0x8D01."""
    sdk = FakeEdsdk()
    sdk.spiegellos = spiegellos
    sdk.nonaf_af_ng_mal = 1
    cam = _cam(monkeypatch, sdk)
    try:
        cam.capture(tmp_path / "dunkel.jpg")
    finally:
        cam.close()
    assert (tmp_path / "dunkel.jpg").exists()
    assert sdk.nonaf_versuche == 2
    assert cam.focus_fallbacks == 1


def test_no_focus_twice_as_an_event_is_retried_more_than_once(monkeypatch, tmp_path):
    """The old fallback tried once; a second "no focus" after it ended the shot."""
    sdk = FakeEdsdk()
    sdk.nonaf_af_ng_spaet = 2
    cam = _cam(monkeypatch, sdk)
    try:
        cam.capture(tmp_path / "dunkel.jpg")
    finally:
        cam.close()
    assert (tmp_path / "dunkel.jpg").exists()
    assert sdk.nonaf_versuche == 3
    assert cam.focus_fallbacks == 2


def test_return_code_and_event_mixed_still_give_the_picture(monkeypatch, tmp_path):
    sdk = FakeEdsdk()
    sdk.nonaf_af_ng_mal = 1
    sdk.nonaf_af_ng_spaet = 1
    sdk.halb_meldet_af_ng = True
    cam = _cam(monkeypatch, sdk)
    try:
        cam.capture(tmp_path / "dunkel.jpg")
    finally:
        cam.close()
    assert (tmp_path / "dunkel.jpg").exists()
    assert sdk.ausloesungen == 1


def test_the_button_comes_up_before_every_new_attempt(monkeypatch, tmp_path):
    sdk = FakeEdsdk()
    sdk.nonaf_af_ng_mal = 2
    cam = _cam(monkeypatch, sdk)
    try:
        cam.capture(tmp_path / "dunkel.jpg")
    finally:
        cam.close()
    nonaf = [i for i, b in enumerate(sdk.befehle) if b == NONAF]
    assert len(nonaf) == 3
    for a, b in zip(nonaf, nonaf[1:]):
        assert OFF in sdk.befehle[a + 1:b], "a new press only after the button came up"
    assert sdk.befehle[-1] == OFF


def test_classic_release_survives_no_focus_on_the_press_without_af(monkeypatch, tmp_path):
    """Classic: full press "ok", "no focus" as an event, then the press without
    AF answers 0x8D01 as well — that went straight to _check before."""
    sdk = FakeEdsdk()
    sdk.af_ng_spaet = 1
    sdk.nonaf_af_ng_mal = 1
    cam = _cam(monkeypatch, sdk, release="classic")
    try:
        cam.capture(tmp_path / "dunkel.jpg")
    finally:
        cam.close()
    assert (tmp_path / "dunkel.jpg").exists()
    assert cam.focus_fallbacks >= 1


def test_classic_release_survives_af_ng_on_both_presses(monkeypatch, tmp_path):
    sdk = FakeEdsdk()
    sdk.af_ng_mal = 1          # the full press with AF returns AF_NG
    sdk.nonaf_af_ng_mal = 1    # and so does its fallback
    cam = _cam(monkeypatch, sdk, release="classic")
    try:
        cam.capture(tmp_path / "dunkel.jpg")
    finally:
        cam.close()
    assert (tmp_path / "dunkel.jpg").exists()


def test_the_attempts_are_bounded_and_the_shot_fails_as_a_refusal(monkeypatch, tmp_path):
    from boothcore import errors
    from boothcore.camera import edsdk as modul
    from boothcore.camera.base import CaptureRefused
    sdk = FakeEdsdk()
    sdk.nonaf_af_ng_mal = 99
    cam = _cam(monkeypatch, sdk)
    t = time.monotonic()
    try:
        with pytest.raises(CaptureRefused) as e:
            cam.capture(tmp_path / "dunkel.jpg")
    finally:
        cam.close()
    assert time.monotonic() - t < 5, "refused attempts must not each wait for the 15 s timeout"
    assert sdk.nonaf_versuche == 1 + modul.FOCUS_RETRIES
    assert errors.camera_code(str(e.value)) == "camera.focus"
    assert not (tmp_path / "dunkel.jpg").exists()
    assert sdk.befehle[-1] == OFF


def test_a_late_picture_of_a_refused_attempt_stops_further_attempts(monkeypatch, tmp_path):
    """"No focus" as an event, and the picture of that same press still comes:
    no second press — one requested shot, one shutter release."""
    sdk = FakeEdsdk()
    sdk.nonaf_af_ng_mit_bild = 1
    sdk.bild_nach = 2
    cam = _cam(monkeypatch, sdk)
    try:
        cam.capture(tmp_path / "eins.jpg")
    finally:
        cam.close()
    assert (tmp_path / "eins.jpg").exists()
    assert sdk.nonaf_versuche == 1, "fired a second time although the picture was on its way"
    assert sdk.downloads == 1


def test_a_late_picture_from_an_earlier_attempt_is_not_a_second_picture(monkeypatch, tmp_path):
    """The earlier attempt's picture turns up together with the retry's: one is
    taken, the other cancelled, the file is written once."""
    sdk = FakeEdsdk()
    sdk.nonaf_af_ng_mit_bild = 1
    sdk.bild_nach = None
    cam = _cam(monkeypatch, sdk)
    try:
        cam.capture(tmp_path / "eins.jpg")
        assert sdk.downloads == 1
        assert len(sdk.abgesagt) == 1, "the second picture must be cancelled, not downloaded"
        # And the next shot gets its own picture, not a leftover.
        cam.capture(tmp_path / "zwei.jpg")
    finally:
        cam.close()
    assert sdk.downloads == 2


def test_a_link_error_on_the_press_is_still_a_camera_error(monkeypatch, tmp_path):
    from boothcore.camera.base import CameraError, CaptureRefused
    sdk = FakeEdsdk()
    sdk.nonaf_fehler = 0x00000002              # EDS_ERR_INTERNAL_ERROR: not about the shot
    cam = _cam(monkeypatch, sdk)
    try:
        with pytest.raises(CameraError) as e:
            cam.capture(tmp_path / "x.jpg")
    finally:
        cam.close()
    assert not isinstance(e.value, CaptureRefused)
    assert sdk.nonaf_versuche == 1, "a link error is not retried as if it were 'no focus'"


@pytest.mark.parametrize("code", [0x8D0F, 0x8D0A])
def test_other_refusals_of_the_shot_are_refusals_too_and_not_retried(monkeypatch, tmp_path, code):
    """A retracted lens or a flash still charging: pressing again within the
    same shot does not help, and the link is fine — a refusal of this shot."""
    from boothcore.camera.base import CaptureRefused
    sdk = FakeEdsdk()
    sdk.aufnahmefehler = code
    cam = _cam(monkeypatch, sdk)
    try:
        with pytest.raises(CaptureRefused):
            cam.capture(tmp_path / "x.jpg")
    finally:
        cam.close()
    assert sdk.nonaf_versuche == 1


# Until the review of 17.11.2026 this list was pinned as "refused, no retry, no
# reconnect" as well. Wrong for these: on the EOS M50 "card NG" (0x8D07) is how
# a missing host-capacity announcement looks after a movie, sleep or a turn of
# the dial, and "no card" / "movie mode" went away only by running the photo
# setup again. Left as a refusal of this shot only, every following shot was
# refused until someone switched the camera off and on.
SETUP_CODES = [0x8D06, 0x8D07, 0x8D08, 0x8D0C, 0x8D0E]


@pytest.mark.parametrize("code", SETUP_CODES)
def test_a_card_or_mode_refusal_runs_the_photo_setup_and_the_same_shot_succeeds(monkeypatch, tmp_path,
                                                                                 code, caplog):
    sdk = FakeEdsdk()
    cam = _cam(monkeypatch, sdk)
    sdk.aufnahmefehler = code
    sdk.heilt_mit_kapazitaet = True
    vorher = sdk.kapazitaeten
    with caplog.at_level(logging.INFO, logger="booth.camera.edsdk"):
        try:
            cam.capture(tmp_path / "x.jpg")
        finally:
            cam.close()
    assert (tmp_path / "x.jpg").exists(), "the same shot must succeed after the photo setup"
    assert sdk.kapazitaeten == vorher + 1, "the capacity is announced again"
    assert cam.setup_recoveries == 1
    assert [r for r in caplog.records if "Fotobetrieb neu eingerichtet" in r.getMessage()]
    if code in (0x8D0C, 0x8D0E):
        assert 0x00000108 in sdk.andere_befehle, "movie mode off before the photo setup"


@pytest.mark.parametrize("code", SETUP_CODES)
def test_a_card_or_mode_refusal_that_stays_is_a_camera_error_for_a_reconnect(monkeypatch, tmp_path, code):
    from boothcore import errors
    from boothcore.camera.base import CameraError, CaptureRefused
    sdk = FakeEdsdk()
    cam = _cam(monkeypatch, sdk)
    sdk.aufnahmefehler = code                   # a really missing card: the setup does not help
    try:
        with pytest.raises(CameraError) as e:
            cam.capture(tmp_path / "x.jpg")
    finally:
        cam.close()
    assert not isinstance(e.value, CaptureRefused), "the service must open the camera afresh"
    assert errors.camera_code(str(e.value)) in ("camera.card", "camera.mode")
    assert sdk.ausloesungen == 2, "one press, the setup, one more press — not more"
    assert sdk.befehle[-1] == OFF


def test_no_focus_retries_end_within_the_capture_deadline(monkeypatch, tmp_path):
    """The caller gives up after 40 s; a capture must never fire after that."""
    from boothcore.camera import edsdk as modul
    from boothcore.camera.base import CaptureRefused
    monkeypatch.setattr(modul, "CAPTURE_DEADLINE", 0.6)
    monkeypatch.setattr(modul, "FOCUS_RETRIES", 50)
    sdk = FakeEdsdk()
    sdk.nonaf_af_ng_mal = 10**6
    cam = _cam(monkeypatch, sdk)
    t = time.monotonic()
    try:
        with pytest.raises(CaptureRefused):
            cam.capture(tmp_path / "x.jpg")
    finally:
        cam.close()
    assert time.monotonic() - t < 2.0
    assert sdk.nonaf_versuche < 50


def test_the_capture_deadline_is_below_the_callers_timeout():
    from boothcore.camera import edsdk as modul
    assert modul.CAPTURE_DEADLINE <= 30.0


def test_every_fallback_is_logged_once_with_its_attempt(monkeypatch, tmp_path, caplog):
    sdk = FakeEdsdk()
    sdk.nonaf_af_ng_mal = 2
    cam = _cam(monkeypatch, sdk)
    with caplog.at_level(logging.INFO, logger="booth.camera.edsdk"):
        try:
            cam.capture(tmp_path / "x.jpg")
        finally:
            cam.close()
    zeilen = [r for r in caplog.records if "kein Fokus" in r.getMessage()]
    assert [r.levelno for r in zeilen] == [logging.INFO, logging.INFO]
    assert "ohne Autofokus ausgelöst, Versuch 2" in zeilen[0].getMessage()
    assert "Versuch 3" in zeilen[1].getMessage()


# ── the camera loop ───────────────────────────────────────

def _service(backend_factory):
    from boothcore.camera.service import CameraService
    dienst = CameraService(backend_factory)
    dienst.start()
    for _ in range(100):
        if dienst.state == "ready":
            break
        time.sleep(0.02)
    assert dienst.state == "ready"
    return dienst


def _backend(raise_with, fallbacks=0):
    from boothcore.camera.mock import MockCamera

    class Zickig(MockCamera):
        opened = 0

        def open(self):
            type(self).opened += 1
            return super().open()

        def capture(self, target):
            self.focus_fallbacks = getattr(self, "focus_fallbacks", 0) + fallbacks
            if raise_with is not None:
                raise raise_with
            return super().capture(target)
    return Zickig


def test_a_refused_shot_neither_reconnects_nor_looks_like_a_dead_camera(tmp_path, caplog):
    from boothcore.camera.base import CaptureRefused
    cls = _backend(CaptureRefused("Die Kamera findet keinen Fokus. (0x00008D01)"))
    dienst = _service(cls)
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(CaptureRefused):
                dienst.capture(tmp_path / "x.jpg", timeout=5)
            time.sleep(0.2)
        assert dienst.state == "ready"
        assert dienst.error is None
        assert dienst.stats["reconnects"] == 0
        assert cls.opened == 1, "the camera was opened again for a refused shot"
        assert not [r for r in caplog.records if "verbinde neu" in r.getMessage()]
        assert dienst.stats["refused"] == 1
    finally:
        dienst.stop()


def test_a_real_camera_error_still_reconnects(tmp_path):
    from boothcore.camera.base import CameraError
    cls = _backend(CameraError("EDSDK Download fehlgeschlagen (0x00000002)"))
    dienst = _service(cls)
    try:
        with pytest.raises(CameraError):
            dienst.capture(tmp_path / "x.jpg", timeout=5)
        for _ in range(100):
            if cls.opened >= 2:
                break
            time.sleep(0.02)
        assert cls.opened >= 2, "a link error must still lead to a fresh connection"
    finally:
        dienst.stop()


def test_two_refused_shots_in_a_row_that_are_not_about_focus_reconnect(tmp_path):
    """Safety net: whatever the backend did, a camera that refuses shot after
    shot for its card or mode is opened afresh after the second one."""
    from boothcore.camera.base import CaptureRefused
    cls = _backend(CaptureRefused("Die Speicherkarte der Kamera ist voll oder defekt. (0x00008D07)"))
    dienst = _service(cls)
    try:
        with pytest.raises(CaptureRefused):
            dienst.capture(tmp_path / "a.jpg", timeout=5)
        time.sleep(0.2)
        assert cls.opened == 1, "one refusal is one shot, no reconnect yet"
        with pytest.raises(CaptureRefused):
            dienst.capture(tmp_path / "b.jpg", timeout=5)
        for _ in range(100):
            if cls.opened >= 2:
                break
            time.sleep(0.02)
        assert cls.opened >= 2, "the second refusal in a row must open the camera afresh"
    finally:
        dienst.stop()


def test_many_no_focus_refusals_never_reconnect(tmp_path):
    from boothcore.camera.base import CaptureRefused
    cls = _backend(CaptureRefused("Die Kamera findet keinen Fokus. (0x00008D01)"))
    dienst = _service(cls)
    try:
        for i in range(3):
            with pytest.raises(CaptureRefused):
                dienst.capture(tmp_path / f"{i}.jpg", timeout=5)
        time.sleep(0.2)
        assert cls.opened == 1 and dienst.state == "ready"
    finally:
        dienst.stop()


def test_the_fallbacks_are_counted_in_the_camera_state(tmp_path):
    cls = _backend(None, fallbacks=2)
    dienst = _service(cls)
    try:
        dienst.capture(tmp_path / "a.jpg", timeout=5)
        dienst.capture(tmp_path / "b.jpg", timeout=5)
        assert dienst.info()["focus_fallbacks"] == 4
    finally:
        dienst.stop()


# ── the kiosk ─────────────────────────────────────────────

def test_the_kiosk_takes_a_shot_refused_for_focus_again_instead_of_ending_the_run():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "boothcore" / "web" / "static" / "app.js").read_text("utf-8")
    start = js.index("async function captureShot(")
    body = js[start:js.index("\n  }\n", start)]
    assert '"camera.focus"' in body, "captureShot must know the focus refusal"
    assert "captureShot(index, needed, retake, ersetzt, " in body, "and take the same shot again"


# ── the booth: a refused shot is a retry of that shot ─────

@pytest.fixture()
def booth(monkeypatch):
    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp(prefix="booth-nofocus-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def test_a_series_survives_a_refused_shot_and_raises_no_alarm(booth, monkeypatch):
    """GIF series, shot 2 refused for focus: 503 camera.focus for that shot,
    the camera stays ready, no "camera not answering" notice, the same shot
    taken again works and the run finishes."""
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    from boothcore.camera.base import CaptureRefused
    client = TestClient(create_app(booth), raise_server_exceptions=False)
    backend = booth.camera.backend
    echt = backend.capture
    calls = {"n": 0}

    def einmal_kein_fokus(target):
        calls["n"] += 1
        if calls["n"] == 2:
            raise CaptureRefused("Die Kamera findet keinen Fokus. (0x00008D01)")
        return echt(target)
    monkeypatch.setattr(backend, "capture", einmal_kein_fokus)
    s = booth.start_session("gif", "4:5", None, None)
    assert client.post(f"/api/session/{s.uid}/shot", json={}).status_code == 200
    r = client.post(f"/api/session/{s.uid}/shot", json={})
    assert r.status_code == 503 and r.json()["detail"] == "camera.focus"
    time.sleep(0.2)
    assert booth.camera.state == "ready"
    booth._kamera_melden(booth.camera.state)
    assert not [m for m in booth.meldungen.offen() if m.get("key") == "kamera"]
    for _ in range(s.shots_needed - 1):
        assert client.post(f"/api/session/{s.uid}/shot", json={}).status_code == 200
    assert len(s.shots) == s.shots_needed
    assert booth.camera.backend is backend, "no reconnect for a refused shot"
