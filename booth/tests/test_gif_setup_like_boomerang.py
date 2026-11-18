"""The GIF's set-up follows the boomerang's (owner, 18.11.2026).

"Bei GIF kommt vorher gar nichts. GIF soll in der Logik dieselben
Einstellungen haben wie Boomerang, nur dass Fotos gemacht werden." The GIF
now takes its format like the boomerang (``guest_choice.format`` or its own
``gif.guest_choice``, else ``gif.format``) and the same motion frames
(``gif.frames`` and the frame keys of the boomerang). A frame is drawn
around every picture of the GIF; the frames offered are those made for the
boomerang. Off (the default for frames) the GIF is as before.
"""
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-gif-setup-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "gif", "boomerang"]
    b.store.profile["formats"] = ["4:5", "9:16", "3:2"]
    b.store.profile["gif"].update({"shots": 2, "also_mp4": False, "format": "4:5"})
    b.store.profile["gif"]["boomerang"]["enabled"] = False
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def test_the_new_gif_keys_exist_and_frames_are_off_by_default(booth):
    from boothcore import config
    gif = config.DEFAULT_PROFILE["gif"]
    assert gif["guest_choice"] is False and gif["frames"] is False
    assert gif["frame_templates"] == [] and gif["frame_builtins"] == [] and gif["frame_none"] is True
    assert gif["frame_default"] == "" and gif["frame_guest_choice"] is True
    assert booth.motion_frame_offer("gif")["enabled"] is False


def test_the_gallery_knows_the_same_gif_keys():
    import sys
    sys.path.insert(0, str(ROOT))
    from app import ecosystem
    from boothcore import config
    assert ecosystem.DEFAULT_PROFILE["gif"] == config.DEFAULT_PROFILE["gif"]


def test_the_guest_picks_the_gif_format_like_the_boomerangs(booth):
    assert booth.start_session("gif", "9:16", None, None).fmt == "9:16"
    booth.sessions.clear()
    assert booth.start_session("gif", "16:9", None, None).fmt == "4:5"     # not offered: the GIF's own


def test_without_any_choice_the_gifs_own_format_wins(booth):
    booth.store.profile["guest_choice"]["format"] = False
    assert booth.start_session("gif", "9:16", None, None).fmt == "4:5"
    booth.sessions.clear()
    booth.store.profile["gif"]["guest_choice"] = True                       # its own switch opens it
    assert booth.start_session("gif", "9:16", None, None).fmt == "9:16"
    assert booth.format_without_choice("gif") == "4:5"


def test_frames_on_offer_the_boomerangs_frames_and_frame_every_picture(booth):
    booth.store.profile["gif"]["frames"] = True
    offer = booth.motion_frame_offer("gif")
    assert offer["enabled"] and "titel-unten" in [i for i, _f in offer["frames"]]
    s = booth.start_session("gif", "4:5", None, None, motion_frame="titel-unten")
    assert s.motion_frame_id == "titel-unten" and s.fmt == "9:16"        # the frame's format is the GIF's
    for _ in range(s.shots_needed):
        booth.take_shot(s)
    booth.finalize(s, "original")
    with Image.open(s.result) as gif:
        assert gif.size[0] * 16 == gif.size[1] * 9
    with Image.open(s.poster) as poster:
        assert max(poster.convert("RGB").getpixel((5, poster.size[1] - 5))) < 80   # the frame's dark ground


def test_without_frame_the_gif_is_as_before(booth):
    booth.store.profile["gif"]["frames"] = True
    s = booth.start_session("gif", "4:5", None, None, motion_frame="none")
    assert s.motion_frame is None and s.fmt == "4:5"


def test_the_kiosk_offers_format_and_frames_for_the_gif():
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'const FORMAT_MODES = ["photo", "boomerang", "gif"];' in js
    assert 'const FRAME_MODES = ["boomerang", "video", "gif"];' in js
    assert "FRAME_MODES.includes(mode) && chooseFrames(mode, showFormat)" in js


def test_the_kiosk_state_lists_the_gif_frames(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["gif"]["frames"] = True
    st = TestClient(create_app(booth)).get("/api/state").json()
    assert st["motion_frames"]["gif"]["enabled"] is True
