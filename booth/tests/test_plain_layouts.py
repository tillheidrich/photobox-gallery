"""The plain layouts ("Schlicht"): photos only, no text, white or black paper.

Owner, 15.11.2026: "es muss auch ein paar Basic Layouts ohne Text oder so
geben". A fixed set of built-ins for 10×15 (the SELPHY CP1300 postcard,
1200×1800 at 300 dpi): 1, 2, 3 and 4 photos and the 2×6 strip with 3 or 4,
portrait and landscape where it makes sense, each on white and on black.
No line of text, no frame line from the style, a QR code only when the
operator switches ``collage_plain_qr`` on. They are offered only where they
were ticked (box or event), so a box with no selection offers what it
offered before.
"""
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import compose, vorlagen_render

PLAIN = [t for t in compose.BUILTIN_TEMPLATES if t.get("plain")]


def test_the_family_is_complete():
    ids = {t["id"] for t in PLAIN}
    for tone in ("weiss", "schwarz"):
        for kern in ("1", "1-quer", "2", "2-quer", "3", "3-quer", "4", "4-quer", "streifen3", "streifen4"):
            assert f"schlicht-{kern}-{tone}" in ids, (kern, tone)
    assert len(PLAIN) == 20


@pytest.mark.parametrize("t", PLAIN, ids=[t["id"] for t in PLAIN])
def test_photos_only_on_the_paper_tone_within_the_safe_margin(t):
    assert not t.get("texts")
    assert t["background"] in ("#ffffff", "#000000")
    assert t["background"] == ("#ffffff" if t["id"].endswith("-weiss") else "#000000")
    assert t["paper"] in ("10x15", "10x15-quer", "streifen")
    assert tuple(t["canvas"]) in ((1200, 1800), (1800, 1200))
    rand = vorlagen_render.sicherer_rand(t)
    w, h = t["canvas"]
    for s in t["slots"]:
        assert s["radius"] == 0
        assert s["x"] >= rand and s["y"] >= rand, t["id"]
        assert s["x"] + s["w"] <= w - rand and s["y"] + s["h"] <= h - rand, t["id"]
    assert t["qr"]["hidden"] is True                   # off unless switched on
    assert t["shots"] == len({s.get("shot", i) for i, s in enumerate(t["slots"])})
    assert t["name"].startswith("Schlicht")


@pytest.mark.parametrize("t", PLAIN[:4] + PLAIN[-4:], ids=[t["id"] for t in PLAIN[:4] + PLAIN[-4:]])
def test_the_print_shows_no_text_and_no_frame_line_even_with_a_framed_style(t):
    shots = compose.beispielbilder(t["shots"])
    img = compose.render_template(t, shots, qr_url="https://example.invalid/q", texts={"event_name": "Anna & Ben"},
                                  style_cfg={"preset": "hochzeit"})       # hochzeit draws a 10 px frame
    assert img.size == tuple(t["canvas"])
    bg = Image.new("RGB", (1, 1), t["background"]).getpixel((0, 0))
    w, h = img.size
    rgb = img.convert("RGB")
    # Margin all round, where a frame line, a line of text or a QR code would be.
    for x, y in ((10, h // 2), (w - 11, h // 2), (w // 2, 10), (w // 2, h - 11), (w - 30, h - 30), (30, 30)):
        assert rgb.getpixel((x, y)) == bg, (t["id"], x, y)


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-plain-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def test_a_box_without_a_selection_offers_what_it_offered_before(booth):
    booth.store.profile["collage_builtins"] = []
    offered = [t["id"] for t in booth.templates()]
    assert offered == [t["id"] for t in compose.BUILTIN_TEMPLATES if not t.get("plain")]


def test_a_ticked_plain_layout_is_offered_and_its_qr_follows_the_switch(booth):
    booth.store.profile["collage_builtins"] = ["schlicht-4-weiss"]
    booth.store.profile["collage_plain_qr"] = False
    [t] = booth.templates()
    assert t["id"] == "schlicht-4-weiss" and t["qr"]["hidden"] is True
    booth.store.profile["collage_plain_qr"] = True
    [t] = booth.templates()
    assert not t["qr"].get("hidden")
    # The catalogue itself is not changed by the switch.
    assert next(x for x in compose.BUILTIN_TEMPLATES if x["id"] == "schlicht-4-weiss")["qr"]["hidden"] is True


def test_the_switch_is_off_by_default_on_both_sides():
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["collage_plain_qr"] is False


def test_a_fitted_photo_sits_on_the_paper_tone_not_the_styles_colour():
    """A landscape photo fitted into a portrait slot leaves room above and below:
    on the black sheet that room is black, on the white one white."""
    for tid, tone in (("schlicht-1-schwarz", (0, 0, 0)), ("schlicht-1-weiss", (255, 255, 255))):
        t = next(x for x in PLAIN if x["id"] == tid)
        img = compose.render_template(t, compose.beispielbilder(1), style_cfg={"preset": "hochzeit"},
                                      einpassen="einpassen").convert("RGB")
        assert img.getpixel((600, 120)) == tone, tid
