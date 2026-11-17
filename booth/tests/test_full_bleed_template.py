"""A card without any white: the photo fills the whole sheet.

Asked for on 14.11.2026. Every built-in layout had a margin and two lines of
text under the pictures. "Vollbild" has neither: one photo, edge to edge, no
rounded corners, no text, no QR. A frame is then entirely the operator's own
overlay (frame.print_overlay) or a template from the editor.
"""
import pytest

from boothcore.media.compose import BUILTIN_TEMPLATES

BY_ID = {t["id"]: t for t in BUILTIN_TEMPLATES}


@pytest.mark.parametrize("tid,canvas,paper", [("vollbild", [1200, 1800], "10x15"),
                                               ("vollbild-quer", [1800, 1200], "10x15-quer")])
def test_the_photo_covers_the_whole_sheet(tid, canvas, paper):
    t = BY_ID[tid]
    assert t["canvas"] == canvas and t["paper"] == paper and t["shots"] == 1
    assert len(t["slots"]) == 1
    slot = t["slots"][0]
    assert (slot["x"], slot["y"], slot["w"], slot["h"]) == (0, 0, canvas[0], canvas[1])
    assert slot["radius"] == 0, "rounded corners would show the background in the corners"
    assert not t.get("texts") and not t.get("qr")


def test_the_gallery_offers_the_same_two_layouts():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from app.ecosystem import BUILTIN_COLLAGE_IDS
    assert "vollbild" in BUILTIN_COLLAGE_IDS and "vollbild-quer" in BUILTIN_COLLAGE_IDS
    assert [t["id"] for t in BUILTIN_TEMPLATES] == BUILTIN_COLLAGE_IDS
