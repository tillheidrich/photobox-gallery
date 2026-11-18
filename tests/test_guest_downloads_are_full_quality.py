"""Guard: no guest download or share path hands out a thumbnail or less than
the best size the event allows unless the guest chose it (18.11.2026).

The lightbox downloaded the 640 px size after a sheet that put it on top
(~23 KB for a print), shared the display copy, and the run page and
"Meine Aufnahmen" offered 2048 px as "Herunterladen" next to a separate
"Original". Checked here for every guest page that downloads or shares.

Download = the best size the event allows (the original if offered).
Share = the best allowed size up to the 2048 px one (decision 18.11.2026):
iOS Safari drops the share sheet when fetching a several-MB original on a
weak network takes too long, so the share never fetches more than 2048 px;
the 640 px size only where it is all the event allows.
"""
from __future__ import annotations

import html as htmllib
import re
from pathlib import Path

from tests.test_my_shots import _event, _scanned, _shot

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "app" / "templates" / "public"

#: Guest pages that may share something other than the download route, and why.
EXCEPTIONS = {
    "kiosk_guests.html": "the booth's own guest screen; a guest takes the picture home by QR code",
    "share.html": "shares the gallery link page's picture as published by the operator",
    "share_link.html": "an operator's share link serves its file route /l/<slug>/file/<id> (full file)",
}


def test_no_guest_template_hard_codes_a_smaller_size():
    for f in PUBLIC.glob("*.html"):
        src = f.read_text(encoding="utf-8")
        assert not re.search(r"download\?size=(small|web|large)\b", src), f.name


def test_no_guest_share_or_download_reads_a_thumbnail():
    for f in PUBLIC.glob("*.html"):
        if f.name in EXCEPTIONS:
            continue
        for m in re.finditer(r"(fetch\([^)]*\)|\.download\s*=|download=\"[^\"]*\"|href=\"[^\"]*download[^\"]*\")", f.read_text(encoding="utf-8")):
            assert "thumb" not in m.group(0), (f.name, m.group(0))


def test_the_gallery_defaults_to_the_best_allowed_size():
    src = (PUBLIC / "gallery.html").read_text(encoding="utf-8")
    # Without a size sheet (choice off) the best comes from the server, never a fixed "original".
    assert "function dlBest() { return DL_ALLOWED.length ? DL_ALLOWED[DL_ALLOWED.length - 1] : DL_BEST; }" in src
    assert "const DL_BEST = {{ best_download_size(gs)|tojson }};" in src
    assert "if (!DL_SIZES_ON || !change) { resolve(dlCurrent()); return; }" in src      # never asks first
    # Share = the download route's file at the share size, never the display variant.
    assert "const SHARE_SIZE = {{ share_download_size(gs)|tojson }};" in src
    assert "photoFileUrl(mediaId, SHARE_SIZE) + '&share=1'" in src


def test_the_ios_save_sheet_preview_is_never_the_display_or_thumbnail_variant():
    """Most iPhone guests long-press the preview and choose "Zu Fotos
    hinzufügen": that picture is what they keep. It is the share file (download
    route, share=1, up to 2048 px JPEG), fetched once; "Als Datei laden" stays
    the best download (owner's complaint 18.11.2026: "far too small")."""
    src = (PUBLIC / "gallery.html").read_text(encoding="utf-8")
    sheet = re.search(r"function dlSaveSheet\(.*?\n\}", src, re.S).group(0)
    assert "img.src = photoFileUrl(mediaId, SHARE_SIZE) + '&share=1'" in sheet
    assert "dataset.url" not in sheet and "thumb" not in sheet and "webp" not in sheet.lower()
    assert "file.href = dlUrl" in sheet


def test_a_share_fetch_is_a_jpeg_and_not_counted_as_a_download(app, client):
    """The share file and the save-sheet preview come with share=1; the
    client counts those itself (share_photo / download on opening the sheet),
    so the route does not add a second "download"."""
    from app import db
    from tests.test_download_sizes import _event_with_photo
    slug, eid, mid = _event_with_photo(app, w=3000, h=2000)

    def downloads():
        con = db.get_db()
        n = con.execute("SELECT COUNT(*) FROM stats WHERE event_id=? AND action='download'", (eid,)).fetchone()[0]
        con.close()
        return n
    r = client.get(f"/gallery/{slug}/photo/{mid}/download?size=large&share=1")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert downloads() == 0
    client.get(f"/gallery/{slug}/photo/{mid}/download?size=original")
    assert downloads() == 1


#: (event switches, download size, share size)
CASES = (
    ({}, "original", "large"),
    ({"download_original": False}, "large", "large"),
    ({"download_original": False, "download_2048": False}, "web", "web"),
    ({"download_2048": False, "download_1024": False}, "original", "original"),
    ({"download_2048": False, "download_1024": False, "download_original": False}, "small", "small"),
)


def test_share_size_is_the_best_up_to_2048_and_640_only_when_alone():
    from app import media
    for cfg, best, share in CASES:
        assert media.best_download_size(cfg) == best, cfg
        assert media.share_download_size(cfg) == share, cfg


def _share_args(page: str) -> list:
    return [htmllib.unescape(a).replace("\\u0026", "&") for a in re.findall(r'onclick="(?:mineShare|shareItem)\(([^,]+),', page)]


def test_run_page_and_my_shots_download_the_best_and_share_up_to_2048(app, client):
    for i, (gallery, best, share) in enumerate(CASES):
        slug, eid = _event(app, gallery=gallery)
        uid = f"best{i}{best}"
        _shot(app, eid, slug, uid)
        _scanned(client, uid)
        mine = client.get("/meine").get_data(as_text=True)
        code = re.search(r'/p/(' + uid + r'-[^"?&]+)', mine).group(1)
        run = client.get(f"/p/{code}").get_data(as_text=True)
        for page, html in (("mine", mine), ("run", run)):
            hrefs = re.findall(r'href="[^"]*/download\?size=([a-z]+)&', html)
            assert hrefs and set(hrefs) == {best}, (page, gallery, hrefs)
            args = [a for a in _share_args(html) if slug in a]
            assert args and all(f"/download?size={share}&share=1&" in a for a in args), (page, gallery, args)


def test_the_share_request_gets_the_share_size_with_the_choice_off(app, client):
    """The share's size holds whatever the global size switch says: with the
    choice off a download is the best (here the original), a share 2048 px."""
    import io

    from PIL import Image

    from tests.test_download_sizes import _event_with_photo
    slug, _eid, mid = _event_with_photo(app, w=3000, h=2000)
    from app import db
    with app.app_context():
        db.set_setting("download_sizes_enabled", "0")
    edge = lambda r: max(Image.open(io.BytesIO(r.get_data())).size)  # noqa: E731
    assert edge(client.get(f"/gallery/{slug}/photo/{mid}/download?size=original")) == 3000
    assert edge(client.get(f"/gallery/{slug}/photo/{mid}/download?size=large&share=1")) == 2048
    assert edge(client.get(f"/gallery/{slug}/photo/{mid}/download?size=original&share=1")) == 2048


def _share_function(src: str, name: str) -> str:
    start = src.index(f"async function {name}(")
    end = min(i for i in (src.find("\n}\n", start), src.find("\n      }\n", start)) if i > 0)
    return src[start:end]


def test_share_never_hands_an_error_page_on_as_a_picture():
    """Review 18.11.2026: the run page and "Meine Aufnahmen" shared whatever
    the fetch returned - a 403 or 404 page went out as "foto.jpg". Like the
    gallery, a failed fetch falls back to the link."""
    for page, fn in (("gallery.html", "sharePhoto"), ("session.html", "shareItem"), ("my_shots.html", "mineShare")):
        body = _share_function((PUBLIC / page).read_text(encoding="utf-8"), fn)
        assert "(await fetch(" not in body, (page, "blob() of an unchecked response")
        ok = body.index(".ok)")
        assert ok < body.index(".blob()"), page
        assert "throw new Error" in body[ok - 60:ok + 80], page


# ── Animated stays animated (review 18.11.2026) ──────────────────────────
# share=1 scaled every picture to a JPEG, so a booth GIF was shared as its
# first frame (on the run page even named "...gif"); a scaled download did
# the same. A size that cannot be animated gives the animated file instead.

def _animated_gif(app, slug=None):
    import secrets

    from PIL import Image

    from app import db, media
    from tests.test_download_sizes import _event_with_photo
    slug, eid, _mid = _event_with_photo(app, slug=slug)
    with app.app_context():
        rel = f"orig/{slug}-anim.gif"
        full = media.data_dir() / rel
        frames = [Image.new("RGB", (2600, 120), c) for c in ((200, 0, 0), (0, 200, 0), (0, 0, 200))]
        frames[0].save(full, save_all=True, append_images=frames[1:], duration=120, loop=0)
        mid = db.add_media(event_id=eid, kind="gif", filename="loop_1.gif",
                           path_original=rel, path_webp=rel, path_thumb=rel,
                           size_bytes=full.stat().st_size, sha256=secrets.token_hex(8),
                           share_token=secrets.token_hex(6))
    return slug, eid, mid


def _frames(data: bytes) -> int:
    import io

    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return getattr(im, "n_frames", 1)


def test_an_animated_gif_is_never_handed_out_as_a_still(app, client):
    import io
    import zipfile
    slug, _eid, mid = _animated_gif(app)
    base = f"/gallery/{slug}/photo/{mid}/download"
    for query in ("", "?size=small", "?size=web", "?size=large", "?size=original",
                  "?size=large&share=1", "?share=1"):
        r = client.get(base + query)
        assert r.status_code == 200, query
        assert r.mimetype == "image/gif", (query, r.mimetype)
        assert _frames(r.get_data()) > 1, query
        assert ".gif" in (r.headers.get("Content-Disposition") or ""), query
    z = client.get(f"/gallery/{slug}/selection.zip?ids={mid}&size=web")
    zf = zipfile.ZipFile(io.BytesIO(z.get_data()))
    (name,) = zf.namelist()
    assert name.endswith(".gif") and _frames(zf.read(name)) > 1, name


def test_run_page_and_my_shots_count_a_share():
    """The gallery counts a share (trackAction 'share_photo'); the run page
    and "Meine Aufnahmen" count theirs the same way, through /api/track."""
    for page, fn, count in (("session.html", "shareItem", "countShare(mediaId)"),
                            ("my_shots.html", "mineShare", "mineCount(eventId, mediaId)")):
        src = (PUBLIC / page).read_text(encoding="utf-8")
        assert "fetch('/api/track'" in src and "action: 'share_photo'" in src, page
        body = _share_function(src, fn)
        assert body.count(count) == 2, page                # file shared, link shared


def test_a_still_photo_with_embedded_extra_pictures_is_not_animated(tmp_path):
    """MPO JPEGs (cameras, phone HDR) carry several pictures but are stills.

    Treated as animated they skipped scaling, and a requested small size or a
    share handed out the full original — also where originals are not allowed.
    """
    from PIL import Image

    from app import media

    mpo = tmp_path / "kamera.jpg"
    first = Image.new("RGB", (4000, 3000), "navy")
    second = Image.new("RGB", (640, 480), "gray")
    first.save(mpo, format="MPO", save_all=True, append_images=[second])
    with Image.open(mpo) as probe:
        assert probe.format == "MPO" and getattr(probe, "n_frames", 1) > 1
    assert media.is_animated(mpo) is False

    gif = tmp_path / "bewegt.gif"
    a = Image.new("RGB", (80, 60), "red")
    b = Image.new("RGB", (80, 60), "blue")
    a.save(gif, save_all=True, append_images=[b], duration=80, loop=0)
    assert media.is_animated(gif) is True
