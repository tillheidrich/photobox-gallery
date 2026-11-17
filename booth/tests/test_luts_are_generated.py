"""Every built-in look is made by code in this repository (final review V4).

Nine looks had no documented origin. ``tools/make_luts.py`` now defines each
one with its own maths; these tests regenerate them and compare byte for
byte, so a file that is edited by hand or swapped for one from elsewhere
fails here. Since the second review ``warm-film`` is made by the script too.
"""
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from boothcore.media import luts

ROOT = Path(__file__).resolve().parents[2]


def _maker():
    spec = importlib.util.spec_from_file_location("make_luts", ROOT / "tools" / "make_luts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MAKER = _maker()


@pytest.mark.parametrize("name", sorted(MAKER.LOOKS))
def test_the_shipped_file_is_exactly_what_the_script_makes(name):
    shipped = (luts.builtin_folder() / f"{name}.cube").read_bytes()
    assert shipped == MAKER.cube_text(name).encode("utf-8")


def test_the_check_mode_agrees():
    assert MAKER.main(["--check"]) == 0


def test_every_shipped_look_is_generated_or_documented():
    shipped = {p.stem for p in luts.builtin_folder().glob("*.cube")}
    assert shipped == set(MAKER.LOOKS)
    assert "warm-film" in MAKER.LOOKS


def test_names_titles_and_grid_sizes_stay_as_profiles_know_them():
    for name, (title, size, _fn, _line) in MAKER.LOOKS.items():
        lut = luts.parse_cube(luts.builtin_folder() / f"{name}.cube")
        assert lut.meta.get("title") == title
        assert lut.size == size


def test_the_looks_are_clearly_different_from_each_other():
    sample = Image.new("RGB", (64, 48))
    px = sample.load()
    for x in range(64):
        for y in range(48):
            px[x, y] = (x * 4, y * 5, (x * y) % 256)
    out = {n: np.asarray(luts.apply_lut(sample, luts.parse_cube(luts.builtin_folder() / f"{n}.cube")),
                         dtype=np.float64) for n in MAKER.LOOKS}
    names = sorted(out)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert np.abs(out[a] - out[b]).mean() > 3.0, (a, b)
