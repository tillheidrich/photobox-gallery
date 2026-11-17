"""Tests for 3D LUT support (.cube parsing, trilinear application, folder loading)."""
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from boothcore.media import luts


# ── helpers ───────────────────────────────────────────────


def cube_text(size, fn, title="Test"):
    """Build .cube text for ``fn(r, g, b) -> (r, g, b)`` with the red index fastest."""
    axis = [i / (size - 1) for i in range(size)]
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", ""]
    for b in axis:
        for g in axis:
            for r in axis:
                out = fn(r, g, b)
                lines.append(" ".join(f"{v:.6f}" for v in out))
    return "\n".join(lines) + "\n"


def identity_text(size=17):
    return cube_text(size, lambda r, g, b: (r, g, b), title="Identity")


def invert_text(size=2):
    return cube_text(size, lambda r, g, b: (1 - r, 1 - g, 1 - b), title="Invert")


def sample_image(width=64, height=48):
    """Deterministic colour ramp with a bit of noise in every channel."""
    rng = np.random.default_rng(7)
    x = np.linspace(0, 255, width, dtype=np.float32)
    y = np.linspace(0, 255, height, dtype=np.float32)
    arr = np.zeros((height, width, 3), dtype=np.float32)
    arr[..., 0] = x[None, :]
    arr[..., 1] = y[:, None]
    arr[..., 2] = 255 - x[None, :]
    arr += rng.uniform(-8, 8, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def as_array(img):
    return np.asarray(img, dtype=np.int16)


# ── parsing ───────────────────────────────────────────────


def test_parse_cube_from_text_and_path(tmp_path):
    lut = luts.parse_cube(identity_text(5))
    assert lut.size == 5
    assert lut.name == "Identity"
    assert lut.table.shape == (5, 5, 5, 3)
    assert lut.table.dtype == np.float32
    assert lut.domain_min == (0.0, 0.0, 0.0) and lut.domain_max == (1.0, 1.0, 1.0)

    path = tmp_path / "identity.cube"
    path.write_text(identity_text(5), encoding="utf-8")
    assert luts.parse_cube(path).size == 5
    assert luts.parse_cube(str(path)).size == 5


def test_parse_cube_axis_order():
    """Red must be the fastest index; a red-only ramp proves the axis order."""
    lut = luts.parse_cube(cube_text(3, lambda r, g, b: (r, 0.0, 0.0)))
    assert lut.table[2, 0, 0][0] == pytest.approx(1.0)
    assert lut.table[0, 2, 0][0] == pytest.approx(0.0)


def test_parse_cube_tolerates_comments_crlf_and_out_of_range():
    text = identity_text(2).replace("\n", "\r\n")
    text = "# a comment\r\n\r\n" + text + "\r\n  \r\n"
    text = text.replace("1.000000 1.000000 1.000000", "1.400000 1.000000 -0.200000")
    lut = luts.parse_cube(text)
    assert lut.size == 2
    assert float(lut.table.max()) <= 1.0
    assert float(lut.table.min()) >= 0.0


def test_parse_cube_reads_domain():
    text = 'TITLE "D"\nLUT_3D_SIZE 2\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 2.0 2.0 2.0\n' + \
        "\n".join(["0 0 0"] * 8) + "\n"
    lut = luts.parse_cube(text)
    assert lut.domain_max == (2.0, 2.0, 2.0)


@pytest.mark.parametrize("text, needle", [
    ("\n".join(["0.0 0.0 0.0"] * 8), "LUT_3D_SIZE"),          # no size at all
    ("LUT_3D_SIZE 2\n" + "\n".join(["0.0 0.0 0.0"] * 7), "needs 8 entries"),
    ("LUT_3D_SIZE 2\n" + "\n".join(["0.0 0.0 0.0"] * 9), "needs 8 entries"),
    ("LUT_3D_SIZE 2\n" + "\n".join(["0.0 0.0"] * 8), "expected 3"),
    ("LUT_3D_SIZE 2\n" + "\n".join(["0.0 0.0 abc"] * 8), "non-numeric"),
    ("LUT_3D_SIZE\n0.0 0.0 0.0", "without a value"),
    ("LUT_3D_SIZE 1\n0.0 0.0 0.0", "at least 2"),
    ("LUT_3D_SIZE 2\n", "no table entries"),
    ("LUT_1D_SIZE 4\n" + "\n".join(["0.0 0.0 0.0"] * 3), "needs 4 entries"),
])
def test_parse_cube_malformed_raises_value_error(text, needle):
    with pytest.raises(ValueError) as exc:
        luts.parse_cube(text)
    assert needle in str(exc.value)


def test_parse_cube_ignores_unknown_keywords():
    text = "SOME_VENDOR_KEY whatever\n" + identity_text(2)
    assert luts.parse_cube(text).size == 2


# ── applying ──────────────────────────────────────────────


def test_identity_lut_leaves_image_nearly_unchanged():
    img = sample_image()
    out = luts.apply_lut(img, luts.parse_cube(identity_text(17)))
    assert out.size == img.size and out.mode == "RGB"
    assert np.abs(as_array(out) - as_array(img)).max() <= 1


def test_inverting_lut_inverts():
    img = sample_image()
    out = luts.apply_lut(img, luts.parse_cube(invert_text()))
    assert np.abs(as_array(out) - (255 - as_array(img))).max() <= 1


def test_one_dimensional_lut_two_points():
    """A 2-point 1D LUT halving blue must halve blue and leave red/green alone."""
    text = 'TITLE "Half Blue"\nLUT_1D_SIZE 2\n0.0 0.0 0.0\n1.0 1.0 0.5\n'
    lut = luts.parse_cube(text)
    assert lut.size == 2 and lut.table.shape == (2, 2, 2, 3)

    img = Image.new("RGB", (4, 4), (200, 120, 240))
    out = np.asarray(luts.apply_lut(img, lut), dtype=np.int16)
    assert abs(int(out[0, 0, 0]) - 200) <= 1
    assert abs(int(out[0, 0, 1]) - 120) <= 1
    assert abs(int(out[0, 0, 2]) - 120) <= 1


def test_strength_zero_and_one_and_half():
    img = sample_image()
    lut = luts.parse_cube(invert_text())

    untouched = luts.apply_lut(img, lut, strength=0.0)
    assert np.array_equal(as_array(untouched), as_array(img))

    full = as_array(luts.apply_lut(img, lut, strength=1.0))
    assert np.abs(full - (255 - as_array(img))).max() <= 1

    half = as_array(luts.apply_lut(img, lut, strength=0.5))
    expected = (as_array(img) + (255 - as_array(img))) / 2.0
    assert np.abs(half - expected).max() <= 1


def test_strength_is_clamped():
    img = sample_image()
    lut = luts.parse_cube(invert_text())
    assert np.array_equal(as_array(luts.apply_lut(img, lut, strength=-1.0)), as_array(img))
    full = as_array(luts.apply_lut(img, lut, strength=1.0))
    assert np.array_equal(as_array(luts.apply_lut(img, lut, strength=4.0)), full)


def test_converts_other_modes_and_keeps_size_and_exif():
    grey = Image.new("L", (32, 20), 128)
    out = luts.apply_lut(grey, luts.parse_cube(identity_text(5)))
    assert out.mode == "RGB" and out.size == (32, 20)

    img = sample_image()
    img.info["exif"] = b"Exif\x00\x00dummy"
    out = luts.apply_lut(img, luts.parse_cube(identity_text(5)))
    assert out.info.get("exif") == b"Exif\x00\x00dummy"


def test_domain_scaling_is_applied():
    """DOMAIN_MAX 0.5 means mid grey already hits the top of the cube."""
    text = 'LUT_3D_SIZE 2\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 0.5 0.5 0.5\n' + \
        cube_text(2, lambda r, g, b: (r, g, b)).split("\n", 2)[2]
    lut = luts.parse_cube(text)
    out = np.asarray(luts.apply_lut(Image.new("RGB", (2, 2), (128, 128, 128)), lut), dtype=np.int16)
    assert out[0, 0, 0] >= 254


def test_performance_on_two_megapixel_image():
    lut = luts.parse_cube(identity_text(33))
    rng = np.random.default_rng(3)
    img = Image.fromarray(rng.integers(0, 256, (1500, 2000, 3), dtype=np.uint8), "RGB")
    start = time.perf_counter()
    out = luts.apply_lut(img, lut)
    elapsed = time.perf_counter() - start
    assert out.size == (2000, 1500)
    assert elapsed < 1.5, f"apply_lut took {elapsed:.2f}s on 2000x1500"


# ── folders ───────────────────────────────────────────────


def test_load_folder_picks_up_cubes_ignores_others_and_rereads(tmp_path):
    (tmp_path / "Warm Film.cube").write_text(identity_text(2), encoding="utf-8")
    (tmp_path / "invert.cube").write_text(invert_text(), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a lut", encoding="utf-8")
    (tmp_path / "preset.json").write_text("{}", encoding="utf-8")

    found = luts.load_folder(tmp_path)
    assert set(found) == {"warm-film", "invert"}
    assert isinstance(found["invert"], luts.Lut)

    # repeated calls are cached (same object, no re-parse)
    assert luts.load_folder(tmp_path) is found

    # a changed file is picked up again
    (tmp_path / "invert.cube").write_text(identity_text(5), encoding="utf-8")
    again = luts.load_folder(tmp_path)
    assert again is not found
    assert again["invert"].size == 5


def test_load_folder_skips_broken_files_and_missing_folder(tmp_path):
    (tmp_path / "good.cube").write_text(identity_text(2), encoding="utf-8")
    (tmp_path / "broken.cube").write_text("LUT_3D_SIZE 2\n0.0 0.0 0.0\n", encoding="utf-8")
    assert set(luts.load_folder(tmp_path)) == {"good"}
    assert luts.load_folder(tmp_path / "nope") == {}


def test_slugify():
    assert luts.slugify("Warm Film!.cube") == "warm-film-cube"
    assert luts.slugify("  Kodak_2393  ") == "kodak-2393"
    assert luts.slugify("???") == "lut"


# ── builtin LUT ───────────────────────────────────────────


def test_builtin_warm_film_parses_and_changes_image():
    folder = luts.builtin_folder()
    assert (folder / "warm-film.cube").is_file()

    builtin = luts.load_builtin()
    assert "warm-film" in builtin
    lut = builtin["warm-film"]
    assert lut.name == "Warm Film"
    assert lut.size in (17, 33)

    img = sample_image()
    out = luts.apply_lut(img, lut)
    diff = np.abs(as_array(out) - as_array(img))
    assert diff.max() > 3, "the warm film LUT must visibly change the image"
    assert diff.mean() < 40, "the warm film LUT should stay a gentle grade"

    # mid grey comes out warm: more red than blue
    grey = np.asarray(luts.apply_lut(Image.new("RGB", (2, 2), (128, 128, 128)), lut), dtype=np.int16)
    assert grey[0, 0, 0] > grey[0, 0, 2]


def test_as_look_and_looks_from_folder():
    lut = luts.parse_cube(invert_text())
    look = luts.as_look(lut)
    img = sample_image()
    assert np.abs(as_array(look(img)) - (255 - as_array(img))).max() <= 1

    registry = luts.looks_from_folder(luts.builtin_folder())
    assert "lut-warm-film" in registry
    assert callable(registry["lut-warm-film"])
    assert registry["lut-warm-film"](img).size == img.size


def test_an_empty_house_look_is_not_a_search_for_nothing(tmp_path, monkeypatch):
    """`slugify("")` ergibt "lut" — ohne Prüfung suchte die Box bei jedem Foto danach."""
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.store.profile["luts"].update({"baked": "   "})
    assert b.baked_lut() is None
    b.mail.stop()
