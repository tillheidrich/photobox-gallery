"""On a Full HD booth screen no guest-facing text is smaller than 18 px.

The booth runs on a 14-inch touch screen read at arm's length. At 1920x1080
the names under the look previews were 14 px, the status line 16 px and the
second line of the format chips 17 px: correct, and too small to read without
leaning in. These sizes now grow with the height of the screen and reach at
least 18 px at 1080 lines, while a 768-line screen keeps the old sizes so
nothing that fitted there stops fitting.

The test reads the stylesheet and does the arithmetic of clamp(). It does not
render anything; how it looks was checked by eye at 1920x1080.
"""
import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "boothcore" / "web" / "static" / "app.css").read_text(encoding="utf-8")

# selector -> the size it had when everything was fixed
SMALL_TEXT = {".look span": 14, ".statusline": 16, ".chip small": 17}


def _font_size(selector: str) -> str:
    rule = re.search(re.escape(selector) + r"\{([^}]*)\}", CSS)
    assert rule, f"no rule for {selector}"
    size = re.search(r"font-size:([^;]+)", rule.group(1))
    assert size, f"{selector} sets no font size"
    return size.group(1).strip()


def _px(value: str, vh: int) -> float:
    def one(term: str) -> float:
        term = term.strip()
        if term.endswith("vh"):
            return float(term[:-2]) * vh / 100
        assert term.endswith("px"), f"cannot read {term!r}"
        return float(term[:-2])

    clamp = re.fullmatch(r"clamp\(([^,]+),([^,]+),([^,]+)\)", value)
    if not clamp:
        return one(value)
    low, ideal, high = (one(t) for t in clamp.groups())
    return max(low, min(ideal, high))


@pytest.mark.parametrize("selector", sorted(SMALL_TEXT))
def test_small_text_reaches_18_px_on_a_full_hd_screen(selector):
    assert _px(_font_size(selector), 1080) >= 18


@pytest.mark.parametrize("selector", sorted(SMALL_TEXT))
def test_it_is_no_larger_than_before_on_a_small_screen(selector):
    assert _px(_font_size(selector), 768) == SMALL_TEXT[selector]


@pytest.mark.parametrize("selector", sorted(SMALL_TEXT))
def test_it_does_not_grow_without_limit(selector):
    # the status line may reach 24 px: it is read from further away than the
    # labels under a finger (11.11.2026)
    assert _px(_font_size(selector), 2160) <= (24 if selector == ".statusline" else 22)
