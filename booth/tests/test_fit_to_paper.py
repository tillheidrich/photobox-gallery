"""print.fit_to_paper: a template made for another paper is fitted to the printer's paper.

Closing list 48 / Paket A (b). ``vorlagen_kern.fuer_papier`` existed but no
print path called it: a 15×23 design went to a SELPHY as a 1800×2700 picture
and the driver squeezed it. With the switch on, the booth fits the template
to the paper of its first printer (model and medium of the local settings)
before it renders: same aspect family only, every element scaled by one
factor. Another family stays as it is (the driver handles it, as before).
Off (the default), nothing changes, to the pixel.
"""
import tempfile
import time
from pathlib import Path

import pytest

from boothcore.media import vorlagen_kern
from boothcore.media.compose import BUILTIN_TEMPLATES


def _wait(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-papier-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    _wait(lambda: b.camera.state == "ready")
    b.store.local["printer"].update({"model": "selphy-cp", "medium": "postcard"})
    yield b
    b.camera.stop()
    b.mail.stop()


def _big_template():
    """A built-in collage, made for 15×23 (same 2:3 family as the SELPHY postcard)."""
    base = next(t for t in BUILTIN_TEMPLATES if t.get("paper") == "10x15" and len(t.get("slots") or []) > 1)
    big, _ = vorlagen_kern.fuer_papier(base, "15x23", anschnitt_mm=0.0)
    assert big["canvas"] == [1800, 2700]
    return big


def _collage(booth, tpl):
    # Started with the built-in it was made from, so the number of shots fits.
    s = booth.start_session("collage", "4:5", tpl["id"], None)
    s.template = tpl
    while len(s.shots) < s.shots_needed:
        booth.take_shot(s)
    return s


def test_off_by_default_on_both_sides():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from boothcore.config import DEFAULT_PROFILE
    from app.ecosystem import DEFAULT_PROFILE as GALLERY
    assert DEFAULT_PROFILE["print"]["fit_to_paper"] is False
    assert GALLERY["print"]["fit_to_paper"] is False


def test_off_the_picture_is_the_same_to_the_pixel_as_without_the_feature(booth, monkeypatch):
    s = _collage(booth, _big_template())
    off = booth.render(s, "original")
    monkeypatch.setattr(type(booth), "_fit_to_paper", lambda self, tpl: tpl)   # the code path before
    before = booth.render(s, "original")
    assert off.size == before.size == (1800, 2700)
    assert off.tobytes() == before.tobytes()


def test_on_a_15x23_design_is_printed_on_the_postcard_canvas(booth):
    booth.store.profile["print"]["fit_to_paper"] = True
    s = _collage(booth, _big_template())
    booth.finalize(s, "original")
    from PIL import Image
    with Image.open(s.print_file) as card:
        assert card.size == (1200, 1800)


def test_on_a_design_of_another_family_stays_as_it_is(booth):
    booth.store.profile["print"]["fit_to_paper"] = True
    tpl = next(t for t in BUILTIN_TEMPLATES if t.get("paper") == "10x15" and len(t.get("slots") or []) > 1)
    other, _ = vorlagen_kern.fuer_papier(tpl, "10x15", anschnitt_mm=0.0)
    other = dict(other, paper="13x18", canvas=[1535, 2126])       # 5:7, not the postcard's 2:3
    assert booth._fit_to_paper(other) is other


def test_on_without_a_known_printer_model_nothing_changes(booth):
    booth.store.profile["print"]["fit_to_paper"] = True
    booth.store.local["printer"]["model"] = ""
    tpl = _big_template()
    assert booth._fit_to_paper(tpl) is tpl


def test_on_a_template_already_on_the_paper_is_left_alone(booth):
    booth.store.profile["print"]["fit_to_paper"] = True
    tpl = next(t for t in BUILTIN_TEMPLATES if t.get("paper") == "10x15")
    assert booth._fit_to_paper(tpl) is tpl
