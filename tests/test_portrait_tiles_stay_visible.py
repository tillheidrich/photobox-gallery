"""Portrait media in the guest gallery (reported 18.11.2026, wedding day).

"Hochkant-Sachen werden in der Galerie anscheinend nicht immer angezeigt."
The event in question: masonry, thumb size m, presentation mode; tiles of
portrait 9:16 boomerang and video MP4s, 4:5 GIFs, 1200x1800 collages and
landscape photos side by side. Every tile must stand in the grid with its
own height and show its picture, at a desktop and at a phone width.
"""
from __future__ import annotations

import json
import re
import secrets
import socket
import threading
from pathlib import Path

import pytest
from PIL import Image

from tests.chrome_cdp import CHROME, Browser

ROOT = Path(__file__).resolve().parents[1]

needs_chrome = pytest.mark.skipif(CHROME is None, reason="no Chrome on this machine")


def _jpeg(path, w, h, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), colour).save(path, "JPEG", quality=85)


def _add(con_db, eid, slug, kind, name, original, webp, thumb, w, h, uid):
    from app import db, media
    mid = db.add_media(event_id=eid, kind=kind, filename=name, path_original=original, path_webp=webp,
                       path_thumb=thumb, size_bytes=(media.data_dir() / original).stat().st_size,
                       sha256=secrets.token_hex(8), share_token=secrets.token_hex(6))
    con = db.get_db()
    con.execute("UPDATE media SET width=?, height=?, uid=? WHERE id=?", (w, h, uid, mid))
    con.commit()
    return mid


def _mixed_event(app):
    """The wedding's mix, stored the way the booth upload stores it (booth_v2.upload)."""
    from app import db, gallery_settings as gsm, media
    slug = "hoch-" + secrets.token_hex(3)
    with app.app_context():
        db.init_db()
        eid = db.create_event(slug=slug, name="Hochkant", upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
        con.commit()
        db.update_event(eid, {"gallery_json": json.dumps(gsm.clean({"layout": "masonry", "thumb_size": "m"}))})
        root = media.data_dir()
        base = media.event_storage_dir(slug).relative_to(root).as_posix()
        made = []
        for i in range(3):
            # A run of each kind, several times over, so the columns balance.
            r = f"{base}/orig/land{i}.jpg"; _jpeg(root / r, 1800, 1200, (90, 120, 160))
            made.append(_add(None, eid, slug, "photo", f"l{i}.jpg", r, r, r, 1800, 1200, f"land{i}"))
            r = f"{base}/orig/coll{i}.jpg"; _jpeg(root / r, 1200, 1800, (160, 90, 90))
            made.append(_add(None, eid, slug, "collage", f"c{i}.jpg", r, r, r, 1200, 1800, f"coll{i}"))
            g = f"{base}/orig/gif{i}.gif"
            Image.new("RGB", (800, 1000), (90, 160, 90)).save(root / g, "GIF")
            t = f"{base}/thumb/gif{i}.jpg"; _jpeg(root / t, 384, 480, (90, 160, 90))
            made.append(_add(None, eid, slug, "gif", f"g{i}.gif", g, g, t, 800, 1000, f"gif{i}"))
            # A boomerang: the clip, its 9:16 poster's web version and thumbnail.
            v = f"{base}/orig/boom{i}.mp4"; (root / v).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            p = f"{base}/webp/boom{i}.jpg"; _jpeg(root / p, 1080, 1920, (200, 170, 60))
            t = f"{base}/thumb/boom{i}.jpg"; _jpeg(root / t, 270, 480, (200, 170, 60))
            made.append(_add(None, eid, slug, "boomerang", f"b{i}.mp4", v, p, t, 1080, 1920, f"boom{i}"))
            # The GIF run's 9:16 film: its poster is the GIF's 4:5 thumbnail.
            v = f"{base}/orig/film{i}.mp4"; (root / v).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            t = f"{base}/thumb/film{i}.jpg"; _jpeg(root / t, 384, 480, (60, 170, 200))
            made.append(_add(None, eid, slug, "video", f"f{i}.mp4", v, t, t, 384, 480, f"gif{i}.mp4"))
            # A clip whose poster never arrived: no picture, no size.
            v = f"{base}/orig/bare{i}.mp4"; (root / v).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            made.append(_add(None, eid, slug, "video", f"v{i}.mp4", v, None, None, None, None, f"bare{i}"))
        return slug, made


@pytest.fixture(scope="module")
def gallery():
    from werkzeug.serving import make_server

    from app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    app.test_client().get("/healthz")
    slug, made = _mixed_event(app)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {"base": f"http://127.0.0.1:{port}", "slug": slug, "count": len(made)}
    finally:
        server.shutdown()


# Every tile as the guest meets it: scrolled to, then measured. ``ratio`` is
# the shape the tile has *before* its picture arrives (lazy loading, a slow
# phone connection, a picture that never comes).
TILES = """(async () => {
  for (let y = 0; y < document.body.scrollHeight; y += innerHeight / 2) {
    scrollTo({top: y, behavior: 'instant'}); await new Promise(r => setTimeout(r, 60));
  }
  await new Promise(r => setTimeout(r, 400));
  const grid = document.getElementById('photoGrid');
  const out = [];
  for (const el of document.querySelectorAll('#photoGrid .photo-item')) {
    el.scrollIntoView({block: 'center', behavior: 'instant'});
    await new Promise(r => setTimeout(r, 80));
    const m = el.querySelector('.photo-open img, .photo-open video');
    const r = el.getBoundingClientRect(), g = grid.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, Math.min(r.top + r.height / 2, innerHeight - 2));
    out.push({id: el.dataset.id, kind: el.dataset.kind, tag: m ? m.tagName : '',
              w: r.width, h: r.height, natural: m && m.tagName === 'IMG' ? m.naturalWidth : -1,
              ratio: m ? getComputedStyle(m).aspectRatio : '',
              inside: r.left >= g.left - 1 && r.right <= g.right + 1,
              shown: getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden',
              hit: !!(hit && el.contains(hit)), what: hit ? hit.tagName + "." + hit.className + "#" + hit.id : "", top: r.top});
  }
  return out;
})()"""


@needs_chrome
@pytest.mark.parametrize("width,height,mobile", [(1280, 900, False), (390, 844, True)])
def test_every_tile_of_a_mixed_wedding_gallery_stands_in_the_grid_with_its_picture(gallery, width, height, mobile):
    with Browser() as b:
        b.viewport(width, height, mobile=mobile, scale=3 if mobile else 1)
        b.goto(f"{gallery['base']}/gallery/{gallery['slug']}", settle=1.5)
        tiles = b.eval(TILES, await_promise=True)
        assert len(tiles) == gallery["count"], tiles
        for t in tiles:
            assert t["shown"] and t["inside"] and t["hit"], ("tile not visible", json.dumps(t))
            assert t["h"] >= 0.5 * t["w"], ("tile collapsed", t)
            # A picture, or for a clip without poster the clip itself.
            assert t["natural"] > 0 or t["tag"] == "VIDEO", ("no picture in the tile", t)
            # The tile's shape comes from the stored size, not from a loaded
            # picture: Chrome 133+ turned `aspect-ratio: attr(width) /
            # attr(height)` into `auto`, and every tile was 0 px high until
            # its picture had arrived.
            assert "/" in t["ratio"], ("tile has no shape before its picture loads", t)
        portrait = [t for t in tiles if t["kind"] in ("collage", "boomerang", "gif")]
        assert portrait and all(t["h"] > t["w"] for t in portrait), portrait


def test_a_clip_without_poster_gets_its_own_frame_as_tile(app, client):
    slug, _made = _mixed_event(app)
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    clips = re.findall(r'<video class="photo-clip" src="([^"]+)"', html)
    assert len(clips) == 3 and all(c.endswith(".mp4#t=0.1") for c in clips), clips
    assert '<img src=""' not in html


def test_the_tile_css_does_not_take_the_shape_from_attr():
    css = (ROOT / "app" / "static" / "css" / "public.css").read_text(encoding="utf-8")
    assert not re.search(r"aspect-ratio\s*:\s*attr\(", css)


# ── Follow-up 18.11.2026: "Videos sind schwarz", "immer weißer Hintergrund" ──

LB = """(async () => {
  const tile = document.querySelector('.photo-item[data-kind="boomerang"] .photo-open');
  tile.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
  await new Promise(r => setTimeout(r, 500));
  const lb = document.getElementById('lightbox') || document.querySelector('.lb');
  const v = document.getElementById('lbVideo');
  const c = document.createElement('canvas').getContext('2d');
  c.fillStyle = getComputedStyle(lb).backgroundColor; c.fillRect(0, 0, 1, 1);
  const [r, g, b] = c.getImageData(0, 0, 1, 1).data;
  return {bg: [r, g, b], theme: document.documentElement.getAttribute('data-theme'),
          videoShown: !v.hidden, poster: v.poster,
          btn: getComputedStyle(document.getElementById('lbClose')).color};
})()"""


@needs_chrome
def test_the_large_view_of_a_clip_is_light_in_the_light_theme_with_its_poster(gallery):
    with Browser() as b:
        b.viewport(1280, 900)
        b.goto(f"{gallery['base']}/gallery/{gallery['slug']}", settle=1.2)
        st = b.eval(LB, await_promise=True)
        assert st["theme"] != "dark", st
        assert st["videoShown"] and "/thumb/boom" in st["poster"], st
        assert min(st["bg"]) >= 230, ("large view behind the clip is not light", st)


def test_run_page_and_my_shots_show_a_frame_for_a_clip_without_poster():
    for name in ("session.html", "my_shots.html"):
        src = (ROOT / "app" / "templates" / "public" / name).read_text(encoding="utf-8")
        assert '{% if not it.poster %}#t=0.1{% endif %}' in src, name
    session = (ROOT / "app" / "templates" / "public" / "session.html").read_text(encoding="utf-8")
    assert 'preload="none" loop' not in session
