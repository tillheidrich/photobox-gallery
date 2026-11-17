"""At 1920x1080 the result screen uses the width; 1280x800 and 1024x768 stay as they were.

The side column (QR code, print, mail, done) was a fixed 520 px. On a Full HD
screen the portrait picture is bound by the height, so the space between
picture and column stayed empty while the QR code was 180 px. The column and
the QR code now grow with the width, but never below the old sizes, so a
1280 px screen looks exactly as before and the 1024 px layout (its own rule)
is untouched.

Like test_readable_at_full_hd.py this reads the stylesheet and does the
arithmetic of clamp(); how it looks was not rendered here.
"""
import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "boothcore" / "web" / "static" / "app.css").read_text(encoding="utf-8")


def _top_level(selector: str) -> str:
    """All rules for this selector outside any @media block, in order (later wins)."""
    depth, start, out = 0, 0, []
    for m in re.finditer(r"[{}]", CSS):
        if m.group() == "{":
            if depth == 0:
                head = re.sub(r"/\*.*?\*/", "", CSS[start:m.start()], flags=re.S).strip()
                if head == selector:
                    out.append(CSS[m.end():CSS.index("}", m.end())])
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                start = m.end()
    assert out, f"no top-level rule for {selector}"
    return ";".join(out)


def _prop(rule: str, name: str) -> str:
    found = re.findall(r"(?:^|;)\s*" + re.escape(name) + r":([^;]+)", rule)
    assert found, f"{name} missing"
    return found[-1].strip()


def _px(term: str, w: int, h: int) -> float:
    term = term.strip()
    m = re.fullmatch(r"clamp\(([^,]+),([^,]+),([^,]+)\)", term)
    if m:
        lo, ideal, hi = (_px(t, w, h) for t in m.groups())
        return max(lo, min(ideal, hi))
    for unit, base in (("vw", w), ("vh", h)):
        if term.endswith(unit):
            return float(term[:-2]) * base / 100
    assert term.endswith("px"), term
    return float(term[:-2])


def _side_column(w: int, h: int) -> float:
    cols = _prop(_top_level(".result"), "grid-template-columns")
    side = re.fullmatch(r"minmax\(0,1fr\)\s+(clamp\([^)]*\)|\S+)", cols)
    assert side, cols
    return _px(side.group(1), w, h)


def test_the_side_column_grows_on_full_hd():
    assert _side_column(1920, 1080) >= 640


@pytest.mark.parametrize("w,h", [(1280, 800), (1366, 768), (1440, 900)])
def test_smaller_landscape_screens_keep_the_old_column(w, h):
    assert _side_column(w, h) == 520


def test_it_stops_growing_on_very_wide_screens():
    assert _side_column(3840, 2160) <= 760


def test_the_picture_keeps_most_of_the_width():
    assert 1920 - _side_column(1920, 1080) > 0.55 * 1920


def test_the_qr_code_grows_with_the_column():
    share = _prop(_top_level(".share"), "grid-template-columns").split()[0]
    img = _top_level(".share img")
    assert _px(share, 1920, 1080) >= 240 and _px(share, 1280, 800) == 180
    assert _prop(img, "width") == share and _prop(img, "height") == share


def test_1024_keeps_its_own_layout():
    block = CSS[CSS.index("@media (max-width:1100px) and (orientation:landscape)"):]
    block = block[:block.index("\n}") + 2]
    assert "grid-template-columns:1fr minmax(0,440px)" in block
