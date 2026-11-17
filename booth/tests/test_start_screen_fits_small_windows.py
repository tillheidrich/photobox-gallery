"""The start screen in a small window: the guest notice covers nothing, every tile is on screen.

Seen on 17.11.2026, a booth that ran without its monitor at 1024x768: "Die
Kamera ist nicht bereit. Bitte sagt dem Team Bescheid." lay over the status
chip, "Galerie", "EN" and the lock (and took their taps), and the five mode
tiles, stacked one under the other, stood partly above the top edge.

The first tests read the files and run everywhere. The browser tests load the
real kiosk page in headless Chrome (kiosk_stage.py) at the sizes the booths
run at, dark and light, German and the longer English text, with the full
corner cluster, and measure; they skip without Chrome.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import kiosk_stage as ks

CSS = (ks.WEB / "static" / "app.css").read_text(encoding="utf-8")
JS = (ks.WEB / "static" / "app.js").read_text(encoding="utf-8")


def test_tiles_stack_only_on_a_narrow_portrait_screen():
    stacked = re.findall(r"@media[^{]*\{[^@]*?\.modes\{grid-template-columns:1fr!important", CSS)
    assert stacked and all("portrait" in m.split("{", 1)[0] for m in stacked), stacked


def test_the_notice_is_placed_on_the_start_screen_and_re_placed_on_resize():
    assert "function placeNotice()" in JS
    assert 'window.addEventListener("resize", placeNotice)' in JS
    body = JS.split("function placeNotice()", 1)[1].split("\n  }\n", 1)[0]
    for sel in ("#statusline", ".start-tools", "#adminHotspot", ".start-copy", "#modes .mode"):
        assert sel in body, sel


SIZES = ["1024x768", "1280x800", "1920x1080", "1920x1200", "1080x1920"]


@pytest.fixture(scope="module", params=[("dunkel", "de", "camera.off"), ("hell", "en", "camera.lens")],
                ids=["dark-de", "light-en-long"])
def stage(request, tmp_path_factory):
    if not ks.CHROME:
        pytest.skip("needs Google Chrome or Chromium")
    scheme, lang, code = request.param
    st = ks.booth_state(tmp_path_factory.mktemp("booth"), camera_code=code, lang=lang, scheme=scheme)
    with ks.serve(st) as url:
        yield url, lang


@pytest.mark.parametrize("size", SIZES)
def test_the_notice_covers_no_control_and_every_tile_is_on_screen(stage, size):
    url, lang = stage
    m = ks.chrome(url, size)
    assert m["screen"] == "start" and m["h"] == int(size.split("x")[1]), m
    assert m["notice"], "the notice is not shown"
    assert m["text"] and (("staff" in m["text"]) if lang == "en" else ("Team" in m["text"])), m["text"]
    hits = [k for k in ("status", "tools", "lock", "title", "copy") if ks.overlaps(m["notice"], m[k])]
    hits += [f"tile {i}" for i, t in enumerate(m["tiles"]) if ks.overlaps(m["notice"], t)]
    assert hits == [], (hits, m)
    n = m["notice"]
    assert n[0] >= 0 and n[1] >= 0 and n[2] <= m["w"] and n[3] <= m["h"], n
    assert m["tiles"], "no mode tiles"
    off = [t for t in m["tiles"] if t[1] < 0 or t[3] > m["h"] or t[0] < 0 or t[2] > m["w"]]
    assert off == [], off
    assert not any(ks.overlaps(m["title"], t) for t in m["tiles"]), "a tile lies over the title"
