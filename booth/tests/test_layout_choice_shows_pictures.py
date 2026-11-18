"""The set-up screen shows each layout as a picture of the printed sheet.

Owner at the real booth, 15.11.2026: "Man sieht nicht die ganzen
Vorschaubilder vom Layout" — the guest chose the print layout from text
chips. Every offered layout (collage templates, the print card of each photo
format, the GIF sheet) now has a picture the booth renders with its own
renderer, the event's texts and sample photos; cached on disk under a key of
everything that changes it, and never rendered while a guest's run is going.
"""
import json
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import compose

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


@pytest.fixture
def booth(tmp_path):
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.store.profile["event"] = {**b.profile["event"], "name": "Mara & Jonas"}
    b.store.profile["collage_builtins"] = ["grid4", "strips3", "solo-quer"]
    b.store.profile["formats"] = ["4:5", "3:2"]
    return b


@pytest.fixture
def client(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    return TestClient(create_app(booth), client=("127.0.0.1", 50000))


def test_every_offered_layout_has_a_picture_of_its_sheet(booth, client):
    state = client.get("/api/state").json()
    assert [t["id"] for t in state["templates"]] == ["grid4", "strips3", "solo-quer"]
    urls = [t["thumb"] for t in state["templates"]] + list(state["layout_thumbs"]["formats"].values())
    urls.append(state["layout_thumbs"]["gif"])
    assert sorted(state["layout_thumbs"]["formats"]) == ["3:2", "4:5"]
    for url in urls:
        r = client.get(url)
        assert r.status_code == 200, url
        assert r.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(r.content)) as im:
            assert im.height == 600 or im.width == 960, (url, im.size)
    # The landscape template's picture lies, the portrait one stands.
    with Image.open(BytesIO(client.get(state["templates"][2]["thumb"]).content)) as quer:
        assert quer.width > quer.height


def test_the_picture_is_the_booths_own_render_with_the_event_texts(booth):
    tpl = next(t for t in compose.BUILTIN_TEMPLATES if t["id"] == "grid4")
    pfad = booth.layoutbilder.datei("t", "grid4")
    erwartet = compose.render_template(tpl, compose.beispielbilder(4), assets_dir=booth.assets_dir, qr_url=None,
                                       texts=booth._texts(booth.layoutbilder._lauf()),
                                       style_cfg=booth.profile.get("collage_style"))
    erwartet.thumbnail((960, 600))
    with Image.open(pfad) as bild:
        unterschied = sum(abs(a - b) for a, b in zip(bild.convert("RGB").resize((60, 90)).tobytes(),
                                                      erwartet.convert("RGB").resize((60, 90)).tobytes()))
    assert unterschied / (60 * 90 * 3) < 6, "the same sheet, only smaller (JPEG aside)"


def test_a_change_of_texts_or_style_makes_a_new_picture_and_nothing_else_does(booth):
    erst = booth.layoutbilder.urls()["t"]["grid4"]
    assert booth.layoutbilder.urls()["t"]["grid4"] == erst, "the key is stable"
    pfad = booth.layoutbilder.datei("t", "grid4")
    mtime = pfad.stat().st_mtime_ns
    assert booth.layoutbilder.datei("t", "grid4").stat().st_mtime_ns == mtime, "a cached picture is not rendered again"
    booth.store.profile["event"]["name"] = "Lena & Ole"
    assert booth.layoutbilder.urls()["t"]["grid4"] != erst
    zweit = booth.layoutbilder.urls()["t"]["grid4"]
    booth.store.profile["collage_style"] = {"preset": "hochzeit"}
    assert booth.layoutbilder.urls()["t"]["grid4"] != zweit


def test_nothing_is_rendered_while_a_guest_is_in_a_run(booth, client, tmp_path):
    from boothcore.session import Session
    booth.sessions["lauf"] = Session(uid="lauf", mode="collage", fmt="3:2", folder=tmp_path, event_slug="",
                                     event_name="", created=time.time())
    url = client.get("/api/state").json()["templates"][0]["thumb"]
    assert client.get(url).status_code == 503
    assert not list(booth.layoutbilder.ordner.glob("*.jpg"))
    booth.sessions["lauf"].finalized = True
    assert client.get(url).status_code == 200


def test_an_unknown_layout_is_not_found(client):
    assert client.get("/api/layout-thumb/t/gibts-nicht.jpg").status_code == 404
    assert client.get("/api/layout-thumb/x/grid4.jpg").status_code == 404


def test_the_kiosk_draws_cards_with_pictures_and_keeps_the_trigger_keys():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "layoutCard({ attr: \"template\"" in js and "aria-label=" in js
    assert 'class="picker-arrow next"' in html and 'data-scroll="1"' in html
    # Arrow keys only move the choice for a keyboard user; a buzzer's ArrowRight still starts.
    assert ':focus-visible' in js
    assert '"ArrowRight", "F13"' in js
    # Only one layout: no choice is shown (the old rule stays).
    # (since 18.11.2026 per mode: templatesFor, test_photo_offers_single_layouts.py)
    assert "showTemplate = mayTemplate && sheetModes && tpls.length > 1" in js


def test_template_info_says_where_each_slot_sits():
    info = compose.template_info(next(t for t in compose.BUILTIN_TEMPLATES if t["id"] == "fuenf"))
    assert len(info["slot_boxes"]) == len(info["slots"]) == 5
    assert info["slot_boxes"][1] == [60, 790, 530, 350]
