"""Chroma keying: mask quality, compositing, colour detection, safety rule, speed."""
import time

import pytest
from PIL import Image

from boothcore.media.chroma import composite, estimate_key_color, key_out

GREEN = (0, 177, 64)
BLUE = (0, 71, 187)
RED = (210, 40, 35)
SKIN = (232, 186, 154)

BASE_SIZE = (400, 300)
SUBJECT_BOX = (120, 80, 280, 260)      # left, top, right, bottom, at BASE_SIZE
SUBJECT_CENTER = (200, 170)            # inside the subject, at BASE_SIZE


def _scaled_box(size):
    """The subject rectangle scaled from BASE_SIZE to ``size``."""
    sx, sy = size[0] / BASE_SIZE[0], size[1] / BASE_SIZE[1]
    left, top, right, bottom = SUBJECT_BOX
    return int(left * sx), int(top * sy), int(right * sx), int(bottom * sy)


def _shot(backdrop=GREEN, subject=RED, size=BASE_SIZE, lighting=False):
    """A synthetic booth photo: coloured backdrop, rectangular 'guest'."""
    img = Image.new("RGB", size, backdrop)
    if lighting:
        # Vertical brightness gradient over the backdrop, like a single softbox.
        px = img.load()
        for y in range(size[1]):
            factor = 0.45 + 0.55 * (y / max(1, size[1] - 1))
            row = tuple(int(round(c * factor)) for c in backdrop)
            for x in range(size[0]):
                px[x, y] = row
    left, top, right, bottom = _scaled_box(size)
    img.paste(Image.new("RGB", (right - left, bottom - top), subject), (left, top))
    return img


def test_subject_stays_opaque_and_keeps_its_colour():
    out = key_out(_shot())
    assert out.mode == "RGBA"
    assert out.size == (400, 300)
    r, g, b, a = out.getpixel(SUBJECT_CENTER)
    assert a == 255
    assert (r, g, b) == pytest.approx(RED, abs=6)


def test_backdrop_becomes_transparent():
    out = key_out(_shot())
    for corner in [(2, 2), (397, 2), (2, 297), (397, 297), (60, 150), (340, 40)]:
        assert out.getpixel(corner)[3] == 0, corner


def test_uneven_backdrop_lighting_is_still_keyed():
    out = key_out(_shot(lighting=True))
    # Top of the backdrop is dark, bottom is bright - both must disappear.
    for corner in [(10, 5), (390, 5), (10, 295), (390, 295)]:
        assert out.getpixel(corner)[3] == 0, corner
    assert out.getpixel(SUBJECT_CENTER)[3] == 255


def test_edges_are_soft_not_binary():
    out = key_out(_shot())
    column = [out.getpixel((x, SUBJECT_CENTER[1]))[3] for x in range(100, 140)]
    assert any(0 < value < 255 for value in column), "edge should have partial alpha"


def test_spill_does_not_grey_out_skin():
    out = key_out(_shot(subject=SKIN), spill=1.0)
    r, g, b, a = out.getpixel(SUBJECT_CENTER)
    assert a == 255
    assert (r, g, b) == pytest.approx(SKIN, abs=6)
    assert max(r, g, b) - min(r, g, b) > 40, "skin must keep its saturation"


def test_blue_backdrop_with_explicit_colour():
    out = key_out(_shot(backdrop=BLUE), color="blue")
    assert out.getpixel((5, 5))[3] == 0
    assert out.getpixel(SUBJECT_CENTER)[3] == 255


def test_explicit_hex_colour():
    out = key_out(_shot(backdrop=(20, 200, 90)), color="#14c85a")
    assert out.getpixel((5, 5))[3] == 0
    assert out.getpixel(SUBJECT_CENTER)[3] == 255


def test_unknown_colour_raises():
    with pytest.raises(ValueError):
        key_out(_shot(), color="banana")


def test_auto_detection_finds_green_and_blue():
    green = estimate_key_color(_shot(backdrop=GREEN))
    assert green is not None
    assert green[1] > green[0] + 60 and green[1] > green[2] + 60

    blue = estimate_key_color(_shot(backdrop=BLUE))
    assert blue is not None
    assert blue[2] > blue[0] + 60 and blue[2] > blue[1] + 60

    # And keying works without naming the colour.
    for backdrop in (GREEN, BLUE):
        out = key_out(_shot(backdrop=backdrop))
        assert out.getpixel((5, 5))[3] == 0
        assert out.getpixel(SUBJECT_CENTER)[3] == 255


def test_auto_detection_ignores_a_grey_wall():
    assert estimate_key_color(Image.new("RGB", (400, 300), (140, 140, 142))) is None


def test_composite_puts_subject_over_new_background():
    background = Image.new("RGB", (800, 600), (12, 24, 200))
    out = composite(_shot(), background)
    assert out.mode == "RGB"
    assert out.size == (400, 300)
    assert out.getpixel((5, 5)) == pytest.approx((12, 24, 200), abs=3)
    assert out.getpixel(SUBJECT_CENTER) == pytest.approx(RED, abs=6)


def test_composite_with_colour_string():
    out = composite(_shot(), "#ff00ff")
    assert out.getpixel((5, 5)) == pytest.approx((255, 0, 255), abs=3)
    assert out.getpixel(SUBJECT_CENTER) == pytest.approx(RED, abs=6)


def test_composite_with_background_file(tmp_path):
    path = tmp_path / "bg.png"
    Image.new("RGB", (100, 100), (7, 200, 7)).save(path)
    out = composite(_shot(), str(path))
    assert out.size == (400, 300)
    assert out.getpixel((5, 5)) == pytest.approx((7, 200, 7), abs=3)


@pytest.mark.parametrize("fit", ["cover", "contain", "stretch"])
def test_composite_fit_modes_keep_the_canvas(fit):
    background = Image.new("RGB", (1000, 200), (30, 30, 220))
    out = composite(_shot(), background, fit=fit)
    assert out.size == (400, 300)
    assert out.getpixel(SUBJECT_CENTER) == pytest.approx(RED, abs=6)


def test_composite_contain_pads_instead_of_cropping():
    background = Image.new("RGB", (1000, 200), (30, 30, 220))
    out = composite(_shot(), background, fit="contain")
    assert out.getpixel((200, 2)) == (0, 0, 0)          # padding at the top
    assert out.getpixel((10, 150)) == pytest.approx((30, 30, 220), abs=3)


def test_composite_unknown_fit_raises():
    with pytest.raises(ValueError):
        composite(_shot(), "#000000", fit="squeeze")


def test_safety_rule_returns_the_original():
    # Almost nothing but backdrop: a 10x10 guest in a 400x300 frame.
    img = Image.new("RGB", (400, 300), GREEN)
    img.paste(Image.new("RGB", (10, 10), RED), (100, 100))
    out = key_out(img)
    assert out.mode == "RGBA"
    assert out.getpixel((5, 5)) == GREEN + (255,)
    assert out.getpixel((105, 105)) == RED + (255,)
    assert out.convert("RGB").tobytes() == img.tobytes()


def test_safety_rule_for_a_guest_without_a_green_screen():
    img = Image.new("RGB", (400, 300), (150, 148, 145))
    img.paste(Image.new("RGB", (100, 100), SKIN), (150, 100))
    out = key_out(img)
    assert out.convert("RGB").tobytes() == img.tobytes()
    assert out.getpixel((5, 5))[3] == 255
    # And compositing such a photo still shows the guest, not the background.
    assert composite(img, "#ff00ff").tobytes() == img.tobytes()


def test_input_is_not_modified():
    img = _shot()
    before = img.tobytes()
    key_out(img)
    composite(img, "#123456")
    assert img.tobytes() == before


def test_large_photo_is_fast():
    img = _shot(size=(2000, 1500))
    start = time.perf_counter()
    out = key_out(img)
    elapsed = time.perf_counter() - start
    assert out.size == (2000, 1500)
    assert out.getpixel((5, 5))[3] == 0
    assert out.getpixel((1000, 850))[3] == 255
    assert elapsed < 2.0, f"keying a 2000x1500 photo took {elapsed:.2f}s"
