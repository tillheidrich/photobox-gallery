"""The booth's own admin page takes the accent colour of the kiosk (list item 37 / closing list 26).

The kiosk has been amber ("Abzug" tokens, theme.accent) for a while; the admin
page at the box still used the old red #e5484d of app.css with white text
(3.91:1, below 4.5:1 — finding B2). Now the stylesheet's own default is the
amber with dark text, and the admin page follows theme.accent when the
operator set own colours (mode "eigene"), as the kiosk does.
"""
import re
from pathlib import Path
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


def _contrast(a: str, b: str) -> float:
    def lum(h):
        r, g, bl = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda x: x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4   # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_default_accent_is_the_kiosk_amber_with_readable_text():
    css = (WEB / "static/app.css").read_text(encoding="utf-8")
    root = css[css.index(":root{"):css.index("}", css.index(":root{"))]
    accent = re.search(r"--accent:(#[0-9a-fA-F]{6})", root).group(1)
    on = re.search(r"--on-accent:(#[0-9a-fA-F]{6})", root).group(1)
    assert accent.lower() == "#e7ad59"
    assert _contrast(accent, on) >= 4.5


def test_the_admin_page_follows_own_theme_colours():
    html = page(WEB / "admin.html")
    assert "function adminAkzent" in html
    assert "adminAkzent(PROFILE.theme" in html
