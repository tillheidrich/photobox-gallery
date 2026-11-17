"""What goes to the printer is a plain (baseline) JPEG.

14.11.2026, first real print on the SELPHY CP1300: "Daten nicht lesbar!
Inkompatible Bilder nicht druckbar". The print service hands a JPEG to this
printer unchanged, and the booth wrote its print file as a progressive JPEG —
good for web pages, unreadable for a small photo printer.
"""
from pathlib import Path

from PIL import Image

from boothcore.media import compose
from boothcore.printing import backends


def _progressive(path) -> bool:
    with Image.open(path) as im:
        return bool(im.info.get("progressive") or im.info.get("progression"))


def test_the_print_file_is_written_baseline(tmp_path):
    src = (Path(__file__).resolve().parent.parent / "boothcore" / "app.py").read_text(encoding="utf-8")
    assert src.count('s.folder / "print.jpg"') == src.count('s.folder / "print.jpg", quality=95, progressive=False')
    out = compose.save_jpeg(Image.new("RGB", (1200, 1800), "#c08040"), tmp_path / "print.jpg",
                            quality=95, progressive=False)
    assert not _progressive(out)


def test_pictures_for_screens_stay_progressive(tmp_path):
    out = compose.save_jpeg(Image.new("RGB", (800, 600), "#c08040"), tmp_path / "result.jpg")
    assert _progressive(out)


def test_a_progressive_file_is_made_plain_before_it_reaches_the_queue(tmp_path):
    """A print file from an older run, or one somebody put there by hand."""
    alt = tmp_path / "alt.jpg"
    Image.new("RGB", (1200, 1800), "#4080c0").save(alt, "JPEG", quality=95, progressive=True)
    plain = backends.plain_jpeg(alt)
    assert plain != alt and not _progressive(plain)
    with Image.open(plain) as im:
        assert im.size == (1200, 1800)
    assert _progressive(alt), "the original stays as it was"


def test_a_plain_file_goes_out_untouched(tmp_path):
    gut = tmp_path / "gut.jpg"
    Image.new("RGB", (1200, 1800), "#4080c0").save(gut, "JPEG", quality=95)
    assert backends.plain_jpeg(gut) == gut


def test_both_queues_that_pass_a_jpeg_through_use_it():
    src = Path(backends.__file__).read_text(encoding="utf-8")
    for klasse in ("class CupsPrinter", "class IppPrinter"):
        teil = src[src.index(klasse):]
        teil = teil[teil.index("def print_file"):]
        assert "plain_jpeg(" in teil[:600], klasse
