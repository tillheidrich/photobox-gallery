"""Die Adresse im Sharepic muss eine Maschine lesen können.

Ein Sharepic landet in einer Story. Wer es dort sieht, kann den QR-Code nicht
scannen — er hält das Telefon ja in der Hand. Also muss die Adresse als Text
erkennbar sein: für die Texterkennung des Telefons („Live Text", „Google
Lens") und damit für jeden, der sie antippen will.

Geprüft wird deshalb nicht, ob die Zeile schön aussieht, sondern ob eine
Texterkennung sie fehlerfrei zurückgibt.
"""
import re
from io import BytesIO

import pytest
from PIL import Image

# Siehe test_qr_logo.py und requirements-dev.txt: in CI verpflichtend,
# lokal ohne Tesseract übersprungen.
if __import__("os").environ.get("PHOTOBOX_STRICT_TESTS") == "1":
    import pytesseract
else:
    pytesseract = pytest.importorskip("pytesseract")

URL = "https://musterbox.photobox-gallery.com/gallery/lina"


_CACHE = {}


@pytest.fixture(autouse=True)
def _temp_path_tesseract_can_open(monkeypatch):
    """pytesseract hands Tesseract a picture under tempfile.gettempdir().

    Leptonica (1.87 on the Mac that runs the suites) rewrites any path that
    starts with ``/tmp`` to its own temp folder, so with TMPDIR unset or under
    /tmp Tesseract looks in the wrong place, fails, and pytesseract then trips
    over the PNG bytes in the error text (UnicodeDecodeError). The real path
    (/private/tmp on macOS) is the same folder without the prefix. Only the
    place of the temp file changes, not what is checked.
    """
    import os
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", os.path.realpath(tempfile.gettempdir()))


@pytest.fixture()
def bild(client):
    """Einmal zeichnen reicht — die Texterkennung ist der langsame Teil."""
    if "png" not in _CACHE:
        import secrets

        from app import db, sharepic
        client.get("/healthz")
        slug = "lina-ocr-" + secrets.token_hex(3)
        eid = db.create_event(name="Hochzeit Lina & Jan", slug=slug, upload_token="t")
        _CACHE["png"] = sharepic.render(db.get_event_by_id(eid), f"https://musterbox.photobox-gallery.com/gallery/{slug}")
    return Image.open(BytesIO(_CACHE["png"]))


def _text(img: Image.Image) -> str:
    unten = img.crop((0, int(img.height * 0.70), img.width, img.height))
    return pytesseract.image_to_string(unten)


def test_the_address_comes_back_from_an_ocr_engine(bild):
    erkannt = _text(bild).replace(" ", "")
    assert "musterbox.photobox-gallery.com" in erkannt, erkannt


def test_it_still_reads_after_a_story_has_squeezed_it(bild):
    """Instagram rechnet das Bild klein. Danach muss es immer noch gehen."""
    klein = bild.resize((540, 960), Image.Resampling.LANCZOS)
    erkannt = _text(klein).replace(" ", "")
    assert "photobox-gallery.com" in erkannt, erkannt


def test_the_line_looks_like_a_link_not_like_a_sentence(bild):
    """Kein „Besuche uns unter …" — die Zeile ist die Adresse, sonst nichts."""
    zeile = [z.strip() for z in _text(bild).splitlines() if "photobox-gallery" in z]
    assert zeile, "Adresse nicht gefunden"
    assert re.fullmatch(r"[\w.\-/]+", zeile[0].replace(" ", "")), zeile[0]
