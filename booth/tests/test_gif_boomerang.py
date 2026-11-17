"""Das GIF zusätzlich als Boomerang — mit eigenen Maßen.

Ein GIF entsteht im Bildformat der Box, meist 4:5. Gebraucht wird es auf dem
Handy, und dort ist 9:16 das Format. Ein Zuschnitt wäre fatal: Bei vier
Leuten steht immer jemand am Rand. Deshalb eine eigene Fassung statt eines
Beschnitts — und der Betreiber legt ihre Maße fest.
"""
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import boomerang


def _bilder(n=4, groesse=(1200, 1500)):
    return [Image.new("RGB", groesse, (30 + 40 * i, 70, 110)) for i in range(n)]


def _masse(pfad):
    """Width, height and duration, read from the header ffmpeg prints for ``-i``.

    Deliberately not ffprobe: the booth ships imageio-ffmpeg, which bundles
    ffmpeg only, so a probe next to it would make these tests depend on a
    system install the booth itself never needs.
    """
    err = subprocess.run([boomerang.ffmpeg_exe(), "-hide_banner", "-i", str(pfad)],
                         capture_output=True, text=True).stderr
    groesse = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    zeit = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    assert groesse and zeit, f"ffmpeg did not describe {pfad}: {err[-400:]}"
    h, m, sek = zeit.groups()
    return int(groesse.group(1)), int(groesse.group(2)), int(h) * 3600 + int(m) * 60 + float(sek)


def test_the_operator_decides_the_dimensions(tmp_path):
    ziel = boomerang.make_boomerang_from_stills(_bilder(), tmp_path / "b.mp4", aspect="9:16",
                                                layout="blur", frame_ms=400, fps=15, loops=3)
    breite, hoehe, _ = _masse(ziel)
    assert (breite, hoehe) == (1080, 1920), "Hochkant fürs Handy, obwohl 4:5 aufgenommen wurde"


def test_cropping_costs_picture_and_the_numbers_show_it(tmp_path):
    """Warum „ganzes Bild" die Voreinstellung ist.

    Beim Zuschneiden bleibt von einem 4:5-Bild nur ein schmaler Streifen für
    9:16 übrig — und weil nie hochskaliert wird, ist das Ergebnis auch
    kleiner. „blur" behält die volle Breite und stellt das Bild mittig in den
    Rahmen. Bei vier Leuten nebeneinander ist das der Unterschied zwischen
    „alle drauf" und „die Außen weg".
    """
    weit = boomerang.make_boomerang_from_stills(_bilder(), tmp_path / "b.mp4", aspect="9:16",
                                                layout="blur", frame_ms=400, fps=15, loops=1)
    eng = boomerang.make_boomerang_from_stills(_bilder(), tmp_path / "c.mp4", aspect="9:16",
                                               layout="crop", frame_ms=400, fps=15, loops=1)
    assert _masse(weit)[0] > _masse(eng)[0]
    assert _masse(weit)[:2] == (1080, 1920)


def test_the_length_follows_from_stills_hold_and_loops(tmp_path):
    """Vier Aufnahmen, hin und zurück, 0,4 s je Bild, drei Durchläufe ≈ 7 s."""
    ziel = boomerang.make_boomerang_from_stills(_bilder(4), tmp_path / "b.mp4", frame_ms=400,
                                                fps=15, loops=3)
    _b, _h, dauer = _masse(ziel)
    assert 6.5 <= dauer <= 8.0, f"war {dauer} s"


def test_a_short_series_does_not_become_a_twitch(tmp_path):
    """Ohne Standzeit wären vier Bilder bei 15 fps ein Viertelsekunden-Zucken."""
    ziel = boomerang.make_boomerang_from_stills(_bilder(4), tmp_path / "b.mp4", frame_ms=400,
                                                fps=15, loops=1)
    assert _masse(ziel)[2] >= 2.0


def test_without_pictures_it_says_so(tmp_path):
    with pytest.raises(ValueError):
        boomerang.make_boomerang_from_stills([], tmp_path / "b.mp4")


# ── Im Durchgang ───────────────────────────────────────────────────────
@pytest.fixture()
def booth(monkeypatch):
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=Path(tempfile.mkdtemp(prefix="booth-boom-")), start_threads=False)
    b.store.profile["modes"] = ["photo", "gif"]
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _gif_lauf(booth):
    s = booth.start_session("gif", "4:5", None, None)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def test_switched_off_nothing_extra_is_made(booth):
    booth.store.profile["gif"]["boomerang"]["enabled"] = False       # on by default since 14.11.2026
    s = _gif_lauf(booth)
    assert s.boom is None
    assert not (s.folder / "boomerang.mp4").exists()


def test_switched_on_it_travels_with_the_run(booth):
    booth.store.profile["gif"]["boomerang"] = {"enabled": True, "format": "9:16", "layout": "blur",
                                               "fps": 15, "loops": 2, "max_width": 1080,
                                               "scale": "native", "guest_choice": True}
    s = _gif_lauf(booth)
    assert s.boom and s.boom.exists(), "Der Boomerang gehört zum Durchgang, nicht zu einem Extraschritt"
    breite, hoehe, _ = _masse(s.boom)
    assert (breite, hoehe) == (1080, 1920)
    # Der Gast sieht ihn am Ergebnisbildschirm als zweite Fassung.
    eintrag = booth.library.get(s.uid)
    assert eintrag["anim"] and Path(eintrag["anim"]).exists()


def test_a_broken_boomerang_does_not_take_the_gif_with_it(booth, monkeypatch):
    booth.store.profile["gif"]["boomerang"] = {"enabled": True, "format": "9:16", "layout": "blur",
                                               "fps": 15, "loops": 2, "max_width": 1080,
                                               "scale": "native", "guest_choice": True}
    monkeypatch.setattr(boomerang, "make_boomerang_from_stills",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg weg")))
    s = _gif_lauf(booth)
    assert s.boom is None
    assert s.result.exists(), "Das GIF steht — daran darf die Zugabe nichts ändern"


def test_it_goes_up_under_its_own_name(booth):
    """Sonst verdrängt der Boomerang in der Galerie das GIF, das er ergänzt."""
    from boothcore.app import Booth
    booth.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8}})
    booth.store.profile["event"].update({"slug": "fest", "name": "Fest", "share_key": "k",
                                         "base_url": "https://galerie.example"})
    booth.store.profile["gif"]["boomerang"] = {"enabled": True, "format": "9:16", "layout": "blur",
                                               "fps": 15, "loops": 2, "max_width": 1080,
                                               "scale": "native", "guest_choice": True}
    s = _gif_lauf(booth)
    offen = booth.outbox.due(50)
    kennungen = {e["uid"] for e in offen}
    assert s.uid in kennungen and f"{s.uid}.boom" in kennungen
    arten = {e["uid"]: e["kind"] for e in offen}
    assert arten[f"{s.uid}.boom"] == "boomerang" and arten[s.uid] == "gif"


def test_gallery_and_booth_ship_the_same_block():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import ecosystem
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["gif"] == ecosystem.DEFAULT_PROFILE["gif"]
    assert DEFAULT_PROFILE["gif"]["boomerang"]["enabled"] is True    # 14.11.2026: every film also in 9:16


# --- 14 Nov 2026: "schneller, und mindestens ein 15-Sekunden-Video" ---

def _stills(n):
    from PIL import Image
    return [Image.new("RGB", (600, 400), (40 * i % 255, 90, 120)) for i in range(n)]


def test_the_film_of_a_gif_runs_at_least_as_long_as_asked(tmp_path, monkeypatch):
    from boothcore.media import boomerang
    seen = {}

    def fake_encode(frames, size, target, fps):
        seen["frames"], seen["fps"] = len(list(frames)), fps
        target.write_bytes(b"mp4")
        return target
    monkeypatch.setattr(boomerang, "encode_mp4", fake_encode)
    boomerang.make_gif(_stills(6), tmp_path / "a.gif", frame_ms=200, mp4=tmp_path / "a.mp4", mp4_seconds=15)
    assert seen["frames"] / seen["fps"] >= 15
    assert seen["frames"] % 10 == 0, "whole passes forth and back, so it loops without a jump"
    boomerang.make_gif(_stills(6), tmp_path / "b.gif", frame_ms=200, mp4=tmp_path / "b.mp4")
    assert seen["frames"] == 30, "without a length it stays three passes, as before"
    boomerang.make_gif(_stills(6), tmp_path / "c.gif", frame_ms=200, mp4=tmp_path / "c.mp4", mp4_seconds=9999)
    assert seen["frames"] <= boomerang.MAX_FRAMES


def test_a_fresh_booth_takes_six_quick_pictures_and_a_fifteen_second_film():
    from boothcore import config
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import ecosystem
    for gif in (config.DEFAULT_PROFILE["gif"], ecosystem.DEFAULT_PROFILE["gif"]):
        assert gif["shots"] == 6 and gif["frame_ms"] <= 220 and gif["video_seconds"] == 15


def test_the_portrait_film_is_made_by_default_and_runs_as_long(tmp_path, monkeypatch):
    """14 Nov 2026: every film must also be there in 9:16, in the gallery."""
    from boothcore import config
    from boothcore.media import boomerang
    boom = config.DEFAULT_PROFILE["gif"]["boomerang"]
    assert boom["enabled"] is True and boom["format"] == "9:16" and boom["guest_choice"] is False
    seen = {}

    def fake_encode(frames, size, target, fps):
        seen["frames"], seen["fps"], seen["size"] = len(list(frames)), fps, size
        target.write_bytes(b"mp4")
        return target
    monkeypatch.setattr(boomerang, "encode_mp4", fake_encode)
    boomerang.make_boomerang_from_stills(_stills(6), tmp_path / "b.mp4", frame_ms=200, fps=15, loops=3, min_seconds=15)
    assert seen["frames"] / seen["fps"] >= 15 and seen["frames"] <= boomerang.MAX_FRAMES
    assert abs(seen["size"][0] / seen["size"][1] - 9 / 16) < 0.01
