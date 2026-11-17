"""A print style that asks for a serif never falls back to a tiny bitmap font.

The "hochzeit" style prints its lines in a serif. The renderer looked for
Georgia (Windows) and DejaVu Serif (Linux) and, finding neither, used
Pillow's built-in bitmap font: a headline a few pixels high on a 1800 px
print. Seen on macOS on 12.11.2026 in the editor preview; the same happens
in a slim Docker image and on any booth without those two fonts. The bundled
fonts are always there, so they are the last resort.
"""
from PIL import ImageFont

from boothcore.media import vorlagen_render as vr


def test_without_any_system_font_the_bundled_one_is_used(monkeypatch):
    nowhere = {True: ["/nowhere/a.ttf"], False: ["/nowhere/b.ttf"]}
    monkeypatch.setattr(vr, "_SERIF_FALLBACK", nowhere)
    monkeypatch.setattr(vr, "_SYSTEM_FALLBACK", nowhere)
    for bold in (True, False):
        font = vr.load_font(120, bold=bold, family="serif")
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size == 120
        left, top, right, bottom = font.getbbox("Anna & Ben")
        assert bottom - top > 60, "the line must be about as high as asked for"


def test_macos_knows_where_its_georgia_is():
    for bold in (True, False):
        assert any("/System/Library/Fonts/Supplemental/" in p for p in vr._SERIF_FALLBACK[bold])
