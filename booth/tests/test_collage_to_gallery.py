"""Was von einer Collage in der Galerie landet — und das Filmchen dazu.

Auf dem Papier bleibt die Collage die Collage. In der Galerie will mancher
Betreiber jedes Bild einzeln haben: Auf dem Blatt stehen vier Leute, auf Bild
drei nur eine, und genau die will es herunterladen. Das ist eine Einstellung,
kein zweiter Durchgang.

Dazu die Zugabe: Aus den Aufnahmen einer Collage entsteht auf Wunsch ein GIF —
einmal posiert, zweierlei mitgenommen.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-collage-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8}})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _collage(booth, vorlage: str = "grid4"):
    from boothcore.media import compose
    tpl = next(t for t in compose.BUILTIN_TEMPLATES if t["id"] == vorlage)
    s = booth.start_session("collage", "4:5", tpl["id"], None)
    for _ in range(s.shots_needed):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _hochgeladen(booth):
    """Was in der Warteschlange steht: (uid, kind, variant)."""
    return {(j["uid"], j["kind"], j["variant"]) for j in booth.outbox.due(200)}


def test_by_default_the_sheet_and_every_single_picture_travel(booth):
    """Till, 14.11.2026: single shots, GIFs and collages all have to land in
    the gallery, on the stick and in the folder on the PC. Until then the
    default was the sheet alone."""
    s = _collage(booth)
    arten = {(uid.split(".")[-1] if "." in uid else "blatt", kind) for uid, kind, _v in _hochgeladen(booth)}
    assert ("blatt", "collage") in arten
    assert sum(1 for _u, kind in arten if kind == "photo") == s.shots_needed


def test_only_the_sheet_is_still_a_choice(booth):
    booth.store.profile["collage_upload"] = "collage"
    _collage(booth)
    assert not any(kind == "photo" for _u, kind, _v in _hochgeladen(booth))


def test_the_film_of_a_gif_travels_too(booth):
    """The GIF was uploaded, its MP4 twin stayed on the booth."""
    s = booth.start_session("gif", "4:5", None, None)
    for _ in range(s.shots_needed):
        booth.take_shot(s)
    booth.finalize(s, "original")
    booth.wait_for_films(s)          # made after the result since 15.11.2026, queued when ready
    assert (s.folder / "result.mp4").is_file(), "the booth makes the film (gif.also_mp4)"
    jobs = _hochgeladen(booth)
    assert (s.uid, "gif", "web") in jobs
    assert (f"{s.uid}.mp4", "video", "web") in jobs


def test_the_operator_can_ask_for_the_single_pictures(booth):
    booth.store.profile["collage_upload"] = "photos"
    s = _collage(booth)
    hoch = _hochgeladen(booth)
    einzeln = [uid for uid, kind, variant in hoch if kind == "photo" and variant == "web"]
    assert len(einzeln) == s.shots_needed
    assert not any(kind == "collage" for _u, kind, _v in hoch), "nur die Einzelbilder, so war es eingestellt"
    for datei in booth.sessions[s.uid].singles:
        assert datei[0].exists() and datei[1].exists()


def test_both_means_both(booth):
    booth.store.profile["collage_upload"] = "both"
    s = _collage(booth)
    hoch = _hochgeladen(booth)
    assert any(kind == "collage" for _u, kind, _v in hoch)
    assert len([1 for _u, kind, variant in hoch if kind == "photo" and variant == "web"]) == s.shots_needed


def test_the_single_pictures_keep_the_chosen_framing(booth):
    from PIL import Image
    booth.store.profile["collage_upload"] = "photos"
    s = _collage(booth)
    gross, _web = booth.sessions[s.uid].singles[0]
    with Image.open(gross) as im:
        verhaeltnis = im.width / im.height
    assert abs(verhaeltnis - 4 / 5) < 0.02, "das Einzelbild trägt das Framing des Gastes"


def test_the_sheet_on_paper_is_never_affected(booth):
    booth.store.profile["collage_upload"] = "photos"
    s = _collage(booth)
    assert s.print_file and s.print_file.exists(), "gedruckt wird weiter die Collage"


# ── GIF aus der Collage ───────────────────────────────────

def test_four_shots_can_become_a_gif(booth):
    booth.store.profile["gif"].update({"from_collage": True, "from_collage_min": 4})
    s = _collage(booth, "grid4")
    assert s.anim and s.anim.exists() and s.anim.suffix == ".gif"
    assert any(uid.endswith(".gif") and kind == "gif" for uid, kind, _v in _hochgeladen(booth))


def test_six_shots_too(booth):
    booth.store.profile["gif"].update({"from_collage": True, "from_collage_min": 4})
    s = _collage(booth, "sechs")
    assert s.shots_needed == 6 and s.anim and s.anim.exists()


def test_two_shots_are_not_a_film(booth):
    """Aus zwei Bildern wird kein Film, sondern ein Zucken."""
    booth.store.profile["gif"].update({"from_collage": True, "from_collage_min": 4})
    s = _collage(booth, "duo")
    assert s.anim is None


def test_the_gif_comes_by_default_and_stays_off_when_switched_off(booth):
    # On by default since 18.11.2026 (owner); off is still off.
    s = _collage(booth, "grid4")
    assert s.anim is not None
    booth.store.profile["gif"]["from_collage"] = False
    s = _collage(booth, "grid4")
    assert s.anim is None
