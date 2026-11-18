"""Guard: what a phone loads for a gallery page stays small (18.11.2026).

A gallery with 568 tiles was ~900 KB of uncompressed HTML, and at 390 px and
3x the grid took the web version of every picture (1200-1600 px) where the
thumbnail fits. Budgets per tile, compression on, and the candidate a phone
picks from ``srcset``/``sizes`` are pinned here. Compression itself is
app/compression.py.
"""
from __future__ import annotations

import gzip
import json
import re
import secrets

import pytest

from app import db, gallery_settings as gsm

N = 200
#: Uncompressed HTML per tile (measured 18.11.2026: see the report; a tile is
#: ~1.3 KB of markup). Ceiling with room, not a target.
RAW_PER_TILE = 2200
#: gzip per tile.
GZIP_PER_TILE = 400
#: The longest edge a phone in the two-column grid may fetch for a tile.
PHONE_TILE_MAX_PX = 720


def _gallery(n=N, **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Gewicht {k}", slug=f"gewicht-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
    con = db.get_db()
    for i in range(n):
        stem = f"{k}{i:04d}"
        con.execute(
            "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
            " share_token, width, height) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "photo", f"{stem}.jpg", f"x/orig/{stem}.jpg", f"x/webp/{stem}_web.webp",
             f"x/thumb/{stem}_web.webp", 120_000, stem, secrets.token_hex(6), 1200, 1800))
    con.commit()
    con.close()
    return db.get_event_by_id(eid)


@pytest.fixture()
def big(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
    db.set_setting("silent_mode", "0")
    return _gallery()


def test_the_page_is_compressed_and_stays_within_its_budget(client, big):
    plain = client.get(f"/gallery/{big['slug']}")
    packed = client.get(f"/gallery/{big['slug']}", headers={"Accept-Encoding": "gzip, br"})
    assert plain.status_code == packed.status_code == 200
    assert "Content-Encoding" not in plain.headers
    assert packed.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in packed.headers.get("Vary", "")
    assert "Accept-Encoding" in plain.headers.get("Vary", "")
    raw = plain.get_data()
    assert gzip.decompress(packed.get_data()) == raw
    assert int(packed.headers["Content-Length"]) == len(packed.get_data())
    print(f"\n{N} tiles: {len(raw)} bytes plain, {len(packed.get_data())} bytes gzip")
    assert len(raw) / N <= RAW_PER_TILE, len(raw) / N
    assert len(packed.get_data()) / N <= GZIP_PER_TILE, len(packed.get_data()) / N


def _pick(srcset: str, sizes: str, viewport: int, dpr: float) -> tuple:
    """The candidate a browser takes: the first ``sizes`` entry whose media
    query matches, times the pixel ratio, then the smallest wide enough."""
    slot = None
    for part in sizes.split(","):
        part = part.strip()
        m = re.match(r"\(max-width:\s*(\d+)px\)\s*(\d+)(vw|px)$", part)
        if m:
            if viewport <= int(m.group(1)):
                slot = int(m.group(2)) * (viewport / 100 if m.group(3) == "vw" else 1)
                break
            continue
        m = re.match(r"(\d+)(vw|px)$", part)
        slot = int(m.group(1)) * (viewport / 100 if m.group(2) == "vw" else 1)
        break
    cands = sorted((int(w), url) for url, w in re.findall(r"(\S+) (\d+)w", srcset))
    need = slot * dpr
    for w, url in cands:
        if w >= need:
            return w, url
    return cands[-1]


@pytest.mark.parametrize("layout", ["masonry", "grid", "polaroid"])
@pytest.mark.parametrize("viewport,dpr", [(320, 2), (390, 3), (430, 3)])
def test_a_phone_takes_the_thumbnail_in_the_grid(client, layout, viewport, dpr):
    client.get("/healthz")
    ev = _gallery(3, layout=layout)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    img = re.search(r'<img src="[^"]*" srcset="([^"]+)" sizes="([^"]+)"', html)
    assert img, "tiles carry srcset and sizes"
    w, url = _pick(img.group(1), img.group(2), viewport, dpr)
    assert "/thumb/" in url, (layout, viewport, dpr, url)
    assert w <= PHONE_TILE_MAX_PX


def test_a_desktop_still_takes_the_web_version_where_the_tile_is_large(client):
    client.get("/healthz")
    ev = _gallery(3, layout="list")
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    img = re.search(r'<img src="[^"]*" srcset="([^"]+)" sizes="([^"]+)"', html)
    _w, url = _pick(img.group(1), img.group(2), 1280, 2)
    assert "/webp/" in url


def test_chrome_on_a_390px_3x_phone_requests_the_thumbnail():
    """The same in a real engine (headless Chrome, mobile emulation)."""
    import socket
    import threading
    import time

    from werkzeug.serving import make_server

    from tests.chrome_cdp import CHROME, Browser
    if CHROME is None:
        pytest.skip("no Chrome on this machine")
    from app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    app.test_client().get("/healthz")
    with app.app_context():
        ev = _gallery(6)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with Browser() as b:
            b.viewport(390, 844, mobile=True, scale=3)
            b.goto(f"http://127.0.0.1:{port}/gallery/{ev['slug']}", settle=1.0)
            srcs = b.eval("Array.from(document.querySelectorAll('.photo-item img')).slice(0, 4).map(i => i.currentSrc)")
            time.sleep(0.1)
    finally:
        server.shutdown()
    assert srcs and all("/thumb/" in s for s in srcs), srcs
