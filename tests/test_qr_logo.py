"""Der QR-Code trägt die Marke — und bleibt trotzdem lesbar.

Ein Logo in der Mitte verdeckt Fläche. Ob der Code das verkraftet, entscheidet
kein Augenmaß, sondern ein Decoder. Deshalb wird hier jeder Stil einmal
gerendert und wieder eingelesen: erkannt oder nicht.
"""
import os

import pytest
from PIL import Image

# In CI müssen diese Prüfungen laufen, nicht stillschweigend ausfallen: Genau
# sie belegen, dass der Code mit Logo noch lesbar ist. Lokal ohne OpenCV wird
# weiterhin übersprungen. Siehe requirements-dev.txt.
if os.environ.get("PHOTOBOX_STRICT_TESTS") == "1":
    import cv2
    import numpy as np
else:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")

from app import qrstyle, sharepic                       # noqa: E402

URL = "https://musterbox.photobox-gallery.com/p/abcd2345xy-1a2b3c4d"


def _lesen(img: Image.Image) -> str:
    arr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    text, *_ = cv2.QRCodeDetector().detectAndDecode(arr)
    return text


def test_the_mark_ships_with_the_gallery():
    assert sharepic.MARKE_IM_QR.is_file(), "tools/build_qr_mark.py laufen lassen"


def test_without_an_uploaded_logo_our_mark_sits_in_the_middle():
    assert sharepic.logo_path({"qr_logo_path": ""}) == sharepic.MARKE_IM_QR


def test_an_operator_logo_wins_over_ours(tmp_path, monkeypatch):
    from app import media
    eigenes = tmp_path / "logo.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 255)).save(eigenes)
    monkeypatch.setattr(media, "data_dir", lambda: tmp_path)
    assert sharepic.logo_path({"qr_logo_path": "logo.png"}) == eigenes


@pytest.mark.parametrize("modules", ["square", "rounded", "dots", "gapped"])
def test_every_style_still_decodes_with_the_mark(modules):
    img = qrstyle.render(URL, 600, {"modules": modules}, sharepic.MARKE_IM_QR)
    assert _lesen(img) == URL, modules


def test_it_survives_being_printed_small():
    """Auf der Karte ist der Code keine 600 Pixel breit."""
    img = qrstyle.render(URL, 600, {"modules": "rounded"}, sharepic.MARKE_IM_QR)
    klein = img.resize((260, 260), Image.Resampling.LANCZOS)
    assert _lesen(klein) == URL


def test_the_mark_stays_under_a_quarter_of_the_code():
    """Mehr verdeckte Fläche, als die Fehlerkorrektur trägt, wäre Glückssache."""
    assert qrstyle.normalize({})["logo_scale"] <= 0.25
