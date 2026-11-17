"""The templates page: tile pictures come fast, from a cache, and never during a guest's run.

Seen on 17.11.2026 with ~45 layouts: the pictures came two at a time and
took seconds, every time the page was opened; on an event-locked tile
"Vom Event vorgegeben · Sichtbar" broke onto two lines inside the switch.

Now each tile names its picture with the booth's cache key (template content,
style, frame, texts, day), the answer for that key may be kept for good, the
booth renders every tile's picture in the background once the list is asked
for, and the page shows a placeholder of the sheet's exact size, loads only
what is near the screen, four at a time, and asks again after a 503 (a run
is going). The browser part loads the real page in headless Chrome against a
real booth (kiosk_stage.py) and skips without Chrome.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import kiosk_stage as ks

JS = (ks.WEB / "static" / "vorlagen-page.js").read_text(encoding="utf-8")
HTML = (ks.WEB / "vorlagen.html").read_text(encoding="utf-8")

EINFACH = {"name": "Abendvorlage", "paper": "10x15",
           "slots": [{"x": 60, "y": 60, "w": 1080, "h": 700, "shot": 0},
                     {"x": 60, "y": 800, "w": 1080, "h": 700, "shot": 1}]}


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.profile["access"]["pin"] = "135790"
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    c.headers.update({"X-Admin-Token": c.post("/api/admin/login", json={"pin": "135790"}).json()["token"]})
    return c


def _tiles(client):
    return {x["key"]: x for x in client.get("/api/admin/templates").json()["layouts"]}


def test_every_tile_names_its_picture_with_the_cache_key(client):
    tiles = _tiles(client)
    for x in tiles.values():
        assert x["picture_key"] and f"v={x['picture_key']}" in x["picture"], x["key"]
        assert x["picture"].startswith("/api/admin/templates/picture?kind=")


def test_with_its_key_the_picture_is_kept_for_good_without_it_only_revalidated(client):
    tile = _tiles(client)["b:grid4"]
    r = client.get(tile["picture"])
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert "immutable" in r.headers["cache-control"] and "max-age=31536000" in r.headers["cache-control"]
    old = client.get("/api/admin/templates/picture", params={"kind": "builtin", "id": "grid4", "v": "0" * 16})
    assert old.status_code == 200 and "no-cache" in old.headers["cache-control"]


def test_a_changed_template_gets_a_new_key_and_a_new_picture_on_disk(client, booth):
    datei = client.post("/api/admin/templates", json=EINFACH).json()["asset"]["file"]
    before = _tiles(client)["t:" + datei]
    assert client.get(before["picture"]).status_code == 200
    changed = dict(EINFACH, slots=[dict(EINFACH["slots"][0], w=900)] + EINFACH["slots"][1:],
                   id=before["id"])
    client.post("/api/admin/templates", json=changed)
    after = _tiles(client)["t:" + datei]
    assert after["picture_key"] != before["picture_key"]
    assert client.get(after["picture"]).status_code == 200
    on_disk = list((booth.layoutbilder.ordner).glob(f"t-*{after['picture_key']}.jpg"))
    assert on_disk, "the booth keeps the picture under the template's key"


def test_during_a_guests_run_nothing_is_rendered_and_the_page_is_told_to_ask_later(client, booth, monkeypatch):
    monkeypatch.setattr(booth.layoutbilder, "lauf_aktiv", lambda: True)
    r = client.get("/api/admin/templates/picture", params={"kind": "builtin", "id": "schlicht-2-schwarz"})
    assert r.status_code == 503 and r.headers.get("retry-after")


def test_opening_the_page_renders_every_tiles_picture_in_the_background(client, booth):
    booth._threads = True                      # as on a running booth
    try:
        tiles = _tiles(client)
        deadline = time.time() + 90
        want = {x["picture_key"] for x in tiles.values()}
        while time.time() < deadline:
            have = {p.stem.rsplit("-", 1)[-1] for p in booth.layoutbilder.ordner.glob("t-*.jpg")}
            if want <= have:
                break
            time.sleep(0.3)
        assert want <= have, f"{len(want - have)} of {len(want)} pictures missing"
    finally:
        booth._threads = False
        faden = getattr(booth.layoutbilder, "_waerm_faden", None)
        if faden:
            booth.layoutbilder._waerm_liste = []
            faden.join(timeout=30)


def test_the_page_loads_pictures_lazily_four_at_a_time_and_retries_a_503():
    assert "IntersectionObserver" in JS and "PARALLEL = 4" in JS
    assert "r.status !== 503" in JS and "Retry-After" in JS
    assert 'locked_tile") + " · "' not in JS, "the lock reason stays out of the switch"
    assert ".schalter{" in HTML and "white-space:nowrap" in HTML.split(".schalter{", 1)[1].split("}", 1)[0]


# ── in Chrome, against a real booth ─────────────────────────────────────────

VORLAGEN_JS = """
(function () {
  function lines(el) {
    if (!el) return 0;
    var r = document.createRange(), tops = {};
    var w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) { if (!w.currentNode.textContent.trim() || w.currentNode.parentNode.tagName === 'I') continue;
      r.selectNodeContents(w.currentNode);
      [].forEach.call(r.getClientRects(), function (b) { if (b.width) tops[Math.round(b.top)] = 1; }); }
    return Object.keys(tops).length;
  }
  function boxes() { return [].slice.call(document.querySelectorAll('.kachel .bild > *')).slice(0, 8).map(function (e) {
    var b = e.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]; }); }
  function login() {
    var pad = [].slice.call(document.querySelectorAll('#pad button'));
    '135790'.split('').forEach(function (d) { pad.filter(function (b) { return b.textContent === d; })[0].click(); });
    pad.filter(function (b) { return b.textContent === 'OK'; })[0].click();
  }
  setTimeout(login, 300);
  var first = null;
  setTimeout(function () { first = boxes(); }, 1500);
  // Rendering takes real time on a busy machine: wait until a few pictures
  // are there (or give up late), then measure.
  var started = Date.now();
  function ready() {
    if (document.querySelectorAll('.blatt[data-fertig="1"]').length >= 4 || Date.now() - started > 25000) return done();
    setTimeout(ready, 250);
  }
  setTimeout(ready, 4000);
  function done() {
    var tiles = document.querySelectorAll('.kachel');
    var pics = performance.getEntriesByType('resource').filter(function (e) { return /templates\\/picture|layout-thumb/.test(e.name); });
    var out = {w: innerWidth, h: innerHeight, tiles: tiles.length,
      loaded: document.querySelectorAll('.blatt[data-fertig="1"]').length,
      fetched: pics.length, first: first, later: boxes(),
      switchLines: [].map.call(document.querySelectorAll('.schalter'), lines),
      lockLines: [].map.call(document.querySelectorAll('.gesperrt'), lines),
      locked: document.querySelectorAll('.schalter:disabled').length};
    document.documentElement.setAttribute('data-measure', JSON.stringify(out));
  }
})();
"""


@pytest.fixture()
def locked_stage(client, booth):
    if not ks.CHROME:
        pytest.skip("needs Google Chrome or Chromium")
    booth.store.save_local({"server": {"url": "https://galerie.example.org", "api_key": "k"}})
    booth.profile["event"]["layouts_fixed"] = True
    booth.profile["event"]["name"] = "Sommerfest"
    assert booth.layout_visibility()["locked"]
    from boothcore.app import create_app
    with ks.serve(None, page_name="vorlagen.html", api=TestClient(create_app(booth)), measure_js=VORLAGEN_JS) as url:
        yield url


def test_in_chrome_locked_tiles_keep_one_line_and_pictures_do_not_move_anything(locked_stage):
    m = ks.chrome(locked_stage, "1280x800", budget_ms=32000)
    assert m["tiles"] >= 40 and m["locked"] == m["tiles"], m
    assert set(m["switchLines"]) == {1}, m["switchLines"][:6]
    assert set(m["lockLines"]) == {1}, m["lockLines"][:6]
    assert m["first"] and m["first"] == m["later"][:len(m["first"])], (m["first"], m["later"])
    assert m["loaded"] >= 4, m
    assert m["fetched"] < m["tiles"], "pictures far below the screen are not fetched yet"


def save_pictures(out: Path, tag: str, booth, sizes=("1280x1300", "1920x1400")) -> None:   # by hand
    # Tall windows: a headless screenshot of a scrolled page draws it wrongly.
    from boothcore.app import create_app
    with ks.serve(None, page_name="vorlagen.html", api=TestClient(create_app(booth)), measure_js=VORLAGEN_JS) as url:
        for size in sizes:
            ks.chrome(url, size, out / f"{tag}-vorlagen-{size}.png", budget_ms=32000)
