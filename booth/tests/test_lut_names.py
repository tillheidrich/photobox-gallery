"""The built-in looks carry descriptive names, not film-stock trademarks.

Until 0.3 seven of them were called after Kodak and Fujifilm films (Portra,
Ektar, Gold, Tri-X, Eterna, 800Z). Those names are trademarks, and the looks
are not those films. They were renamed after what they do. Every profile,
house look and printed menu that still says the old name keeps working: the
old keys resolve to the new ones, and the picture comes out the same.
"""
import hashlib
import re
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import luts

FILM_STOCKS = re.compile(r"(?i)portra(?!it)|ektar|tri-?x|trix|eterna|kodak|fuji|gold[- ]?200|800z")

# sha256 of each file's table (everything but the TITLE line). A rename must
# not change a single number of a look. The nine looks without a documented
# origin were replaced on 15.11.2026 by the ones tools/make_luts.py generates
# (final review V4, booth/tests/test_luts_are_generated.py); these are their
# tables. warm-film was remade by the script in the second review (same look).
TABLES = {
    "bleach-bypass": "9f85f0cc10b0f4c3", "earthy": "bf7f73714928df1a",
    "vivid-colour": "5e11f4340f57642d", "soft-matte": "e81261614c19da15",
    "faded-print": "019dd9d51019cf4b", "natural-portrait": "bdfe5fee98466b71",
    "soft-cinema": "4bbf34febd5ee4ff", "golden-warm": "71c1cf805bf9ef8e",
    "classic-mono": "72fb870c5a43d016", "warm-film": "5c3b560ff6609493",   # remade by tools/make_luts.py (second review)
}


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-luts-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def test_no_builtin_look_is_named_after_a_film_stock():
    for path in luts.builtin_folder().glob("*.cube"):
        title = luts.parse_cube(path).meta.get("title", "")
        assert not FILM_STOCKS.search(path.stem), path.name
        assert not FILM_STOCKS.search(title), f"{path.name}: TITLE {title!r}"


def test_the_rename_changed_no_number_of_any_look():
    found = {}
    for path in luts.builtin_folder().glob("*.cube"):
        table = "".join(l for l in path.read_text(encoding="utf-8").splitlines(keepends=True)
                        if not l.startswith("TITLE"))
        found[path.stem] = hashlib.sha256(table.encode("utf-8")).hexdigest()[:16]
    assert found == TABLES


def test_every_old_key_points_at_a_look_that_exists():
    builtin = luts.load_builtin()
    for old, new in luts.LEGACY_KEYS.items():
        assert new in builtin, f"{old} -> {new}, but {new} is not shipped"
        assert old not in builtin, f"{old} is still a file of its own"


def test_old_keys_resolve_with_and_without_the_prefix():
    assert luts.canonical_key("kodak-gold") == "golden-warm"
    assert luts.canonical_key("lut-mono-trix-400") == "lut-classic-mono"
    assert luts.canonical_key("warm-film") == "warm-film"
    assert luts.canonical_key("my-own-upload") == "my-own-upload"
    assert luts.canonical_key("") == ""


def test_an_old_profile_offers_and_applies_the_same_look(booth):
    booth.store.profile["luts"].update({"show": ["kodak-gold"]})
    offered = [n for n in booth.look_names() if n.startswith("lut-")]
    assert offered == ["lut-golden-warm"]
    img = Image.new("RGB", (24, 16), (150, 110, 80))
    assert booth.apply_look("lut-kodak-gold", img).tobytes() == booth.apply_look("lut-golden-warm", img).tobytes()
    assert booth.look_label("lut-kodak-gold") == booth.look_label("lut-golden-warm") == "Golden Warm"


def test_an_old_look_in_the_profile_list_keeps_its_place(booth):
    booth.store.profile["looks"] = ["original", "lut-mono-trix-400"]
    names = booth.look_names()
    assert names[:2] == ["original", "lut-classic-mono"]
    assert "lut-mono-trix-400" not in names


def test_an_old_house_look_is_still_burned_in(booth):
    booth.store.profile["luts"]["baked"] = "lut-film-portra-400"
    baked = booth.baked_lut()
    assert baked is not None
    assert (baked.table == luts.load_builtin()["natural-portrait"].table).all()


def test_the_doctor_finds_an_old_house_look(booth):
    from boothcore.doctor import Doctor, OK
    booth.store.profile["luts"]["baked"] = "fuji-eterna"
    assert Doctor(booth).check_luts().state == OK


def test_a_guest_choice_with_an_old_name_is_not_dropped(booth):
    booth.store.profile["luts"].update({"show": ["film-ektar-100"]})
    assert booth.canonical_look("lut-film-ektar-100") == "lut-vivid-colour"
    assert booth.canonical_look("lut-film-ektar-100") in booth.look_names()
