"""/p/<code>: the quiet "delete these shots" button must still reach 4.5:1."""
import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "css"
PUBLIC = (CSS_DIR / "public.css").read_text(encoding="utf-8")
DESIGN = (CSS_DIR / "design.css").read_text(encoding="utf-8")


def _lum(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_delete_button_has_no_opacity_and_uses_secondary_text():
    rule = re.search(r"\.sess-delete-btn \{([^}]*)\}", PUBLIC).group(1)
    assert "opacity: 1" in rule
    assert "var(--color-text-2)" in rule


def test_secondary_text_token_reaches_4_5_on_both_themes():
    slate600 = re.search(r"--c-slate-600:\s*(#[0-9a-f]{6})", DESIGN).group(1)
    assert _ratio(slate600, "#ffffff") >= 4.5
    dark = re.search(r'\[data-theme="dark"\] \{.*?--color-text-2:\s*(#[0-9a-f]{6})', DESIGN, re.S).group(1)
    assert _ratio(dark, "#0f172a") >= 4.5
