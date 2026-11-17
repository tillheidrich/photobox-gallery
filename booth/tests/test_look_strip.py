"""The look previews on the result screen form one clean, swipeable row.

Found 11.11.2026 at 1280x800 and 1024x768: the ~17 previews sat in a
three-column grid inside the squeezed side column. The grid rows had no
fixed height, the tiles (square, overflow hidden) had no minimum, so the
rows collapsed to a few pixels and the tiles slid over each other — the
guest saw stripes, not pictures. At 1024x768 the single-column fallback
also clipped "Fertig" off the bottom of the screen.

Measured once with a headless browser against a static copy of the markup
(not part of this suite). These tests pin the CSS that produces the fixed
row; they do not render anything and cannot prove pixel sizes.
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


def _css() -> str:
    return (WEB / "static" / "app.css").read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    start = css.index(selector + "{")
    return css[start:css.index("}", start)]


def test_the_looks_form_one_horizontal_row_that_scrolls_sideways():
    looks = _rule(_css(), ".looks")
    assert "grid-auto-flow:column" in looks, "One row, not a stack of rows"
    assert "overflow-x:auto" in looks and "overflow-y:hidden" in looks
    assert "repeat(3" not in looks, "The three-column grid is what stacked the tiles"
    assert "touch-action:pan-x" in looks, "A swipe on the row must scroll the row"


def test_every_look_tile_has_a_size_of_its_own_so_tiles_cannot_slide_over_each_other():
    css = _css()
    looks = _rule(css, ".looks")
    assert "grid-template-rows:minmax(0,1fr)" in looks, "Exactly one row"
    assert "container-type:size" in looks, "Tiles measure themselves against the row"
    assert re.search(r"flex:0 1 calc\(var\(--look-size\)", looks), \
        "The row has a height of its own and is the part that gives way"

    tile = _rule(css, ".look")
    assert "aspect-ratio:1" in tile
    # The fixed size comes first; browsers with container units replace it
    # with the row's own height, so a squeezed row gives smaller squares.
    assert tile.index("width:var(--look-size)") < tile.index("width:100cqh")


def test_the_buttons_below_the_looks_still_cannot_be_squeezed():
    css = _css()
    for selector in (".share", ".print", ".result-actions"):
        assert "flex-shrink:0" in _rule(css, selector), f"{selector} can be squeezed"


def test_the_side_column_stays_beside_the_picture_on_a_small_landscape_screen():
    """At 1024x768 the single column pushed "Fertig" below the screen edge."""
    css = _css()
    query = "@media (max-width:1100px) and (orientation:landscape){"
    assert query in css
    block = css[css.index(query):css.index("\n}", css.index(query))]
    result = block[block.index(".result{"):block.index("}", block.index(".result{"))]
    assert re.search(r"grid-template-columns:1fr minmax\(", result), \
        "Picture and side column must sit next to each other"
