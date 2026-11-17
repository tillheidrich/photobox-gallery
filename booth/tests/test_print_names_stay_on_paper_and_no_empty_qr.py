"""What the booth prints: the names stay on the paper, an absent QR code leaves nothing.

Both from prints taken at the booth on 15.11.2026. A one-photo landscape
card with the couple's names in the column beside the photo: the second name
ran off the paper. A three-photo sheet whose overlay has a window for the
QR code, printed by a booth with no gallery: an empty pale square where the
code would be — the booth dropped the QR layer, so the renderer no longer
knew the window was there.
"""
import time
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

from boothcore.media import compose
from boothcore.session import Session


@pytest.fixture
def booth(tmp_path):
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.store.profile["print"] = {**(b.profile.get("print") or {}), "enabled": True, "qr_on_print": True,
                                "sharpen": "off"}
    return b


def _session(b, tmp_path, mode, fmt, name, share_url=""):
    folder = tmp_path / "run"
    folder.mkdir(exist_ok=True)
    return Session(uid="abc123", mode=mode, fmt=fmt, folder=folder, event_slug="probe", event_name=name,
                   share_url=share_url, created=time.mktime((2026, 11, 18, 18, 0, 0, 0, 0, -1)))


@pytest.mark.parametrize("name", ["Mia & Tom", "Mara & Jonas Berg", "Maximiliane & Jonas Oberwasser",
                                  "Maximiliane Wilhelmina & Jonas Bartholomäus K", "WWMMWW & MMWWMM"])
def test_the_landscape_photo_card_keeps_every_name_on_the_paper(booth, tmp_path, name):
    booth.store.profile["collage_style"] = {"preset": "hell", "frame": 0}
    s = _session(booth, tmp_path, "photo", "3:2", name)
    foto = Image.new("RGB", (1500, 1000), (90, 110, 140))
    with Image.open(booth._still_print_card(s, foto)) as karte:
        karte = karte.convert("RGB")
    assert karte.size == (1800, 1200)
    papier = karte.getpixel((1799, 600))
    rand = Image.new("RGB", (47, 1200), papier)
    assert ImageChops.difference(karte.crop((1753, 0, 1800, 1200)), rand).getbbox() is None, \
        "nothing printed in the right margin"


def test_an_unpaired_booth_leaves_no_empty_box_where_the_qr_code_would_be(booth, tmp_path):
    ov = Image.new("RGBA", (1200, 1800), (246, 241, 232, 255))
    d = ImageDraw.Draw(ov)
    for box in [(60, 60, 1139, 779), (60, 804, 587, 1155), (612, 804, 1139, 1155), (990, 1590, 1139, 1739)]:
        d.rectangle(box, fill=(0, 0, 0, 0))
    ov.save(booth.assets_dir / "rahmen-gestanzt.png")
    tpl = {"id": "gestanzt", "paper": "10x15", "canvas": [1200, 1800], "background": "#ffffff",
           "overlay": "rahmen-gestanzt.png", "shots": 3,
           "slots": [{"x": 60, "y": 60, "w": 1080, "h": 720, "shot": 0},
                     {"x": 60, "y": 804, "w": 528, "h": 352, "shot": 1},
                     {"x": 612, "y": 804, "w": 528, "h": 352, "shot": 2}],
           "texts": [{"x": 60, "y": 1700, "size": 26, "value": "QR-Code scannen", "color": "#2b2723",
                      "bold": False, "nur_mit_qr": True}],
           "qr": {"x": 990, "y": 1590, "size": 150}}
    (booth.assets_dir / "gestanzt.json").write_text(__import__("json").dumps(tpl))
    booth.store.profile["gif"] = {**(booth.profile.get("gif") or {}), "print_template": "gestanzt.json"}
    stills = [Image.new("RGB", (1500, 1000), c) for c in ((90, 110, 140), (120, 90, 120), (70, 110, 100))]

    ohne = _session(booth, tmp_path, "gif", "3:2", "Mara & Jonas", share_url="")
    with Image.open(booth._gif_print_card(ohne, stills)) as blatt:
        fenster = blatt.convert("RGB").crop((995, 1595, 1135, 1735))
        hinweis = blatt.convert("RGB").crop((60, 1700, 400, 1740))
    farben = fenster.getcolors()
    assert len(farben) <= 3 and all(max(abs(a - b) for a, b in zip(c, (246, 241, 232))) <= 3 for _n, c in farben), \
        "the window is closed in the colour of the paper"
    assert all(max(abs(a - b) for a, b in zip(c, (246, 241, 232))) <= 3 for _n, c in hinweis.getcolors(10000)), \
        "the 'scan the code' caption is gone with the code"

    mit = _session(booth, tmp_path, "gif", "3:2", "Mara & Jonas", share_url="https://example.invalid/g/abc123")
    with Image.open(booth._gif_print_card(mit, stills)) as blatt:
        assert blatt.convert("L").crop((995, 1595, 1135, 1735)).getextrema()[0] < 40, "the code is printed"
    assert compose.qr_gedruckt(tpl, "https://example.invalid/x")


def test_qr_off_for_the_print_means_no_code_even_with_a_link(booth, tmp_path):
    booth.store.profile["print"]["qr_on_print"] = False
    s = _session(booth, tmp_path, "collage", "3:2", "Mia & Tom", share_url="https://example.invalid/g/x")
    assert booth._print_qr(s) is None
    booth.store.profile["print"]["qr_on_print"] = True
    assert booth._print_qr(s) == "https://example.invalid/g/x"
    s.share_url = ""
    assert booth._print_qr(s) is None
