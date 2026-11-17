"""Kiosk and slideshow sit on a fixed dark ground; their text must not follow
a light OS theme (it was 1.17:1)."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "public.css").read_text(encoding="utf-8")


def _lum(hexc):
    h = hexc.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_screen_pages_pin_dark_scheme_and_light_text():
    m = re.search(r"\.ss-body, \.kiosk-body \{(.*?)\}", CSS, re.S)
    assert m, "shared rule for .ss-body/.kiosk-body missing"
    block = m.group(1)
    assert "color-scheme: dark" in block
    for tok in ("--pb-text", "--pb-text-2", "--pb-text-muted", "--color-text"):
        val = re.search(re.escape(tok) + r":\s*(#[0-9a-fA-F]{6})", block)
        assert val, tok
        # darkest ground in use is ink-1000 (#070504); lightest used by the pages is far below #2a2622
        assert _ratio(val.group(1), "#2a2622") >= 4.5, tok
