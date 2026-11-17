"""The Canon driver films a movie on the camera and always hands the camera back as it was.

The order was measured on a real EOS M50 (17.11.2026): pictures to the card,
movie mode on, wait until the mode has changed, record, stop, the file
announces itself, download, then (and only then) delete it from the card;
movie mode off, pictures to the PC again. A fake SDK plays that camera here,
including every way it can go wrong. The point: whatever fails, the camera
ends in photo mode with SaveTo=host, and nothing is deleted from the card
that was not downloaded whole.
"""
import ctypes

import pytest

from fake_edsdk import BUSY, FakeEdsdk, OK, bereitstellen, _wert

RECORD, SAVE_TO, AE_MODE = 0x510, 0x0B, 0x400
SW_ON, SW_OFF = 0x107, 0x108
MOVIE = b"\x00\x00\x00\x18ftypmp42" + b"\x11" * 4000


class MovieCamera(FakeEdsdk):
    """A camera with a card: DCIM/100CANON/ holding a photo and an old movie."""

    def __init__(self):
        super().__init__()
        self.movie_mode = False
        self.recording = False
        self.ae_photo = 2                    # Av on the dial
        self.switch_error = OK
        self.start_error = OK                # Record=4 refused even in movie mode
        self.stop_error = OK
        self.announce = True                 # the "item created" event comes
        self.writes_file = True              # the camera writes a file at all
        self.download_error = OK
        self.short_download = False
        self.commands = []                   # every non-shutter command, in order
        self.deleted = []
        self.card = {"MVI_0007.MP4": len(MOVIE), "IMG_0001.JPG": 10}
        self._info = {}                      # ref -> (name, is_folder, size)
        self._tree = {}                      # ref -> [child refs]
        self._created = []
        self.cam_ref = None
        self.calls = []                      # ("set", prop, value) / ("cap",) / ("cmd", command), in order

    # The card, built on demand so a new file appears after the stop.
    def _items(self, name, folder, size):
        ref = self._neu()
        self._info[ref] = (name, folder, size)
        return ref

    def _volume(self):
        files = [self._items(n, False, s) for n, s in sorted(self.card.items())]
        canon = self._items("100CANON", True, 0)
        self._tree[canon] = files
        dcim = self._items("DCIM", True, 0)
        self._tree[dcim] = [canon]
        misc = self._items("MISC", True, 0)
        self._tree[misc] = []
        vol = self._neu()
        self._tree[vol] = [dcim, misc]
        return vol

    def EdsGetChildAtIndex(self, parent, i, out):
        p = _wert(parent)
        if p in self._tree:
            out._obj.value = self._tree[p][i]
            return OK
        if p == self.cam_ref:
            out._obj.value = self._volume()
            return OK
        ref = self._neu()
        out._obj.value = ref
        self.cam_ref = ref                   # the first child of the camera list is the camera
        return OK

    def EdsGetChildCount(self, ref, out):
        r = _wert(ref)
        if r in self._tree:
            out._obj.value = len(self._tree[r])
            return OK
        if r == self.cam_ref:
            out._obj.value = 1               # one card slot
            return OK
        return super().EdsGetChildCount(ref, out)

    def EdsGetDirectoryItemInfo(self, item, out):
        r = _wert(item)
        if r not in self._info:
            return super().EdsGetDirectoryItemInfo(item, out)
        name, folder, size = self._info[r]
        out._obj.szFileName = name.encode()
        out._obj.isFolder = int(folder)
        out._obj.size = size
        return OK

    def EdsSetCapacity(self, *_a):
        self.calls.append(("cap",))
        return OK

    def EdsGetPropertyData(self, cam, prop, p, size, out):
        if prop == SAVE_TO and self.save_to is not None:
            out._obj.value = self.save_to
            return OK
        if prop == AE_MODE:
            self.gelesen.append(prop)
            out._obj.value = 3 if self.movie_mode else self.ae_photo
            return OK
        return super().EdsGetPropertyData(cam, prop, p, size, out)

    def EdsSetPropertyData(self, cam, prop, param, size, data):
        err = super().EdsSetPropertyData(cam, prop, param, size, data)
        value = getattr(getattr(data, "_obj", data), "value", None)
        self.calls.append(("set", prop, value))
        if prop == RECORD and err == OK:
            if value == 4:
                if not self.movie_mode:
                    return BUSY              # 0x81: what the M50 says in photo mode
                if self.start_error:
                    return self.start_error
                self.recording = True
            elif value == 0:
                if self.stop_error:
                    return self.stop_error
                if self.recording and self.writes_file:
                    name = f"MVI_{len(self.card) + 10:04d}.MP4"
                    self.card[name] = len(MOVIE)
                    if self.announce:
                        self._created.append(name)
                self.recording = False
        return err

    def EdsSendCommand(self, cam, command, param):
        if command in (SW_ON, SW_OFF):
            self.commands.append(command)
            self.calls.append(("cmd", command))
            if command == SW_ON:
                if self.switch_error:
                    return self.switch_error
                self.movie_mode = True
            else:
                self.movie_mode = False
            return OK
        return super().EdsSendCommand(cam, command, param)

    def EdsGetEvent(self):
        while self._created and self.handler:
            ref = self._items(self._created.pop(0), False, len(MOVIE))
            self.handler(0x00000204, ref, None)
        return super().EdsGetEvent()

    def EdsDownload(self, item, size, stream):
        if self.download_error:
            return self.download_error
        entry = self._streams.get(_wert(stream), {})
        data = MOVIE[: len(MOVIE) // 2] if self.short_download else MOVIE
        with open(entry["pfad"], "wb") as f:
            f.write(data)
        return OK

    def EdsDeleteDirectoryItem(self, item):
        name = self._info[_wert(item)][0]
        self.deleted.append(name)
        self.card.pop(name, None)
        return OK

    @property
    def save_to(self):
        values = [v for p, _s, v in self.gesetzt if p == SAVE_TO]
        return values[-1] if values else None


@pytest.fixture()
def kamera(monkeypatch, tmp_path):
    from boothcore.camera import edsdk as modul
    sdk = MovieCamera()
    bereitstellen(monkeypatch, sdk)
    monkeypatch.setattr(modul.EdsdkCamera, "_live", [])
    for name in ("MOVIE_MODE_WAIT", "MOVIE_START_WAIT", "MOVIE_FILE_WAIT", "MOVIE_RESTORE_WAIT"):
        monkeypatch.setattr(modul, name, 0.3)
    cam = modul.EdsdkCamera()
    cam.open()
    yield cam, sdk, tmp_path
    cam.close()


def _in_photo_state(sdk):
    assert sdk.movie_mode is False and sdk.recording is False
    assert sdk.save_to == 2                                  # pictures to the PC again


def test_a_movie_is_recorded_downloaded_and_then_deleted_from_the_card(kamera):
    cam, sdk, tmp = kamera
    ticks, stops = [], []
    path = cam.record_movie(0.2, tmp / "movies", tick=ticks.append, stopped=stops.append)
    assert path.read_bytes() == MOVIE and path.name.startswith("MVI_")
    assert sdk.deleted == [path.name]                        # only this file, after the download
    assert "MVI_0007.MP4" in sdk.card and "IMG_0001.JPG" in sdk.card
    assert sdk.commands == [SW_ON, SW_OFF]
    assert True in ticks and stops and stops[0] >= 0.2       # the live view ran while filming
    saves = [v for p, _s, v in sdk.gesetzt if p == SAVE_TO]
    assert saves[-2:] == [1, 2]                              # card while filming, PC afterwards
    _in_photo_state(sdk)
    assert cam.restore_problem == ""


def test_without_the_event_the_new_file_is_found_on_the_card(kamera):
    cam, sdk, tmp = kamera
    sdk.announce = False
    path = cam.record_movie(0.1, tmp)
    assert path.name not in ("MVI_0007.MP4",) and sdk.deleted == [path.name]
    _in_photo_state(sdk)


@pytest.mark.parametrize("failure", ["switch", "start", "stop", "no_file", "download", "short"])
def test_every_failure_raises_and_hands_the_camera_back_in_photo_mode(kamera, failure):
    from boothcore.camera.base import CameraError
    cam, sdk, tmp = kamera
    if failure == "switch":
        sdk.switch_error = 0x8D0D
    elif failure == "start":
        sdk.start_error = 0x8D06                             # no card
    elif failure == "stop":
        sdk.stop_error = BUSY
    elif failure == "no_file":
        sdk.writes_file = False
    elif failure == "download":
        sdk.download_error = 0x00000002
    else:
        sdk.short_download = True
    card_before = dict(sdk.card)
    with pytest.raises(CameraError):
        cam.record_movie(0.1, tmp)
    assert sdk.deleted == []                                 # nothing leaves the card unless downloaded whole
    assert set(card_before) <= set(sdk.card)
    sdk.stop_error = OK
    assert sdk.movie_mode is False and sdk.save_to == 2
    if failure in ("switch",):
        assert SW_OFF in sdk.commands                        # switched back although it said no


def test_a_camera_that_stays_in_movie_mode_is_reported(kamera, monkeypatch):
    cam, sdk, tmp = kamera
    real = sdk.EdsSendCommand

    def stuck(c, command, param):
        if command == SW_OFF:
            sdk.commands.append(command)
            return BUSY
        return real(c, command, param)

    monkeypatch.setattr(sdk, "EdsSendCommand", stuck)
    cam.record_movie(0.1, tmp)
    assert "Filmmodus aus" in cam.restore_problem and "Betriebsart" in cam.restore_problem
    assert sdk.save_to == 2                                  # the other steps still ran


def test_photos_work_again_after_a_movie(kamera):
    cam, sdk, tmp = kamera
    cam.record_movie(0.1, tmp)
    cam.capture(tmp / "after.jpg")
    assert (tmp / "after.jpg").exists()


def test_other_backends_say_not_supported():
    from boothcore.camera.base import CameraBackend, CameraError
    from boothcore.camera.webcam import WebcamCamera
    from boothcore.camera.gphoto2cam import Gphoto2Camera
    for cls in (WebcamCamera, Gphoto2Camera):
        assert cls.can_record_movie is False
    with pytest.raises(CameraError):
        CameraBackend().record_movie(1, ".")
    assert ctypes.sizeof(ctypes.c_uint64) == 8


# What connect() sets up for a photo, in its order (device test 17.11.2026:
# SaveTo=host without the capacity after it, and the next shot said 0x8D07).
PHOTO_SETUP = [("set", SAVE_TO, 2), ("cap",), ("set", 0x401, 0), ("set", 0x6, None),
               ("set", 0x501, 1), ("set", 0x500, 2)]


def _setup_runs(calls):
    """Start indices of every complete photo set-up sequence in ``calls``."""
    def norm(c):
        return ("set", c[1], None) if c[0] == "set" and c[1] == 0x6 else c
    seq = [norm(c) for c in calls]
    n = len(PHOTO_SETUP)
    return [i for i in range(len(seq) - n + 1) if seq[i:i + n] == PHOTO_SETUP]


def test_connect_runs_the_photo_setup(kamera):
    _cam, sdk, _tmp = kamera
    assert _setup_runs(sdk.calls)                            # the sequence the restore must repeat


@pytest.mark.parametrize("failure", ["none", "switch", "start", "stop", "no_file", "download", "short"])
def test_the_restore_repeats_the_connect_setup_save_to_host_then_capacity(kamera, failure):
    cam, sdk, tmp = kamera
    if failure == "switch":
        sdk.switch_error = 0x8D0D
    elif failure == "start":
        sdk.start_error = 0x8D06
    elif failure == "stop":
        sdk.stop_error = BUSY
    elif failure == "no_file":
        sdk.writes_file = False
    elif failure == "download":
        sdk.download_error = 0x00000002
    elif failure == "short":
        sdk.short_download = True
    mark = len(sdk.calls)
    try:
        cam.record_movie(0.1, tmp)
    except Exception:                                        # noqa: BLE001 — the failure paths raise
        pass
    after = sdk.calls[mark:]
    runs = _setup_runs(after)
    assert runs, after
    last = runs[-1]
    # after the movie mode is off, never before it
    offs = [i for i, c in enumerate(after) if c == ("cmd", SW_OFF)]
    if offs:
        assert last > offs[-1]
    # nothing after the set-up puts the pictures back on the card
    assert not [c for c in after[last:] if c[:2] == ("set", SAVE_TO) and c[2] != 2]


def test_a_camera_that_does_not_come_back_to_its_mode_is_not_carried_on(kamera, monkeypatch):
    cam, sdk, tmp = kamera
    real = sdk.EdsSendCommand

    def keeps_movie(c, command, param):
        if command == SW_OFF:
            sdk.commands.append(command)
            return OK                                        # says ok, stays in movie mode
        return real(c, command, param)

    monkeypatch.setattr(sdk, "EdsSendCommand", keeps_movie)
    cam.record_movie(0.1, tmp)
    assert "Betriebsart" in cam.restore_problem              # the service reconnects on this


def test_a_long_download_goes_in_parts_with_a_sign_of_life_between_them(kamera, monkeypatch):
    """Review 17.11.2026: download, card listing and the way back gave the
    watchdog no sign of life; a long clip passed its 60 s and the screen said
    "Die Kamera antwortet nicht". Now every part and every step beats."""
    from boothcore.camera import edsdk as modul
    cam, sdk, tmp = kamera
    monkeypatch.setattr(modul, "MOVIE_DOWNLOAD_PART", 1000)
    parts = []

    def download(item, size, stream):
        entry = sdk._streams.get(_wert(stream), {})
        n = int(getattr(size, "value", size))
        done = sum(parts)
        with open(entry["pfad"], "ab") as f:
            f.write(MOVIE[done:done + n])
        parts.append(n)
        ticks.append("part")
        return OK
    sdk.EdsDownload = download
    ticks = []
    path = cam.record_movie(0.1, tmp / "movies", tick=ticks.append)
    assert path.read_bytes() == MOVIE
    assert parts == [1000, 1000, 1000, 1000, len(MOVIE) - 4000]
    between = ticks[ticks.index("part"):]
    for a, b in zip(between, between[1:]):
        assert not (a == "part" and b == "part"), "a beat between two parts"
    assert ticks[-1] is None, "a beat after the way back to photo mode"
    assert ticks[0] is None, "a beat before the card is listed"
