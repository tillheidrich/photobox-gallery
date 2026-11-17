"""What a look at the gallery on a 390 px phone found (UI review, 12.11.2026).

Each test is one thing that was visibly wrong on a screenshot:

* the colour flag was a 44 px white disc on every tile, over the picture and
  over the date line;
* the date line on a tile read "2026-11-12 12:05" while every other date on
  the page reads 12.11.2026;
* in the large view the counter broke into three lines ("2 / 17" stacked)
  and the last buttons ran off the right edge;
* a gallery made only of folders said "0 photos" and showed its date as
  2020-09-19, both in its header and on the client's overview.

The stylesheet tests read the CSS the way tests/test_gallery_on_phone.py does;
the screenshots are under /tmp/ui-review/A/ of the review run.
"""
import json
import re
import secrets
from pathlib import Path

import pytest
from PIL import Image

from app import clients, db, ecosystem, folders, gallery_settings as gsm, media

CSS = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "public.css"


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    db.set_setting("silent_mode", "0")
    yield


def _blocks(head: str) -> str:
    css = CSS.read_text(encoding="utf-8")
    return "\n".join(re.findall(re.escape(head) + r" \{(.*?)\n\}", css, flags=re.S))


def _event(name="Galerie", date="2020-09-19", **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=name, slug=f"ph-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "date": date, "gallery_json": json.dumps(gsm.clean(gs))})
    return db.get_event_by_id(eid)


def _photo(event, created="2026-11-12 12:05:00"):
    d = media.event_storage_dir(event["slug"])
    stem = secrets.token_hex(5)
    orig, webp, thumb = d / "orig" / f"{stem}.jpg", d / "webp" / f"{stem}.webp", d / "thumb" / f"{stem}.webp"
    Image.new("RGB", (40, 30)).save(orig, "JPEG")
    Image.new("RGB", (40, 30)).save(webp, "WEBP")
    Image.new("RGB", (20, 15)).save(thumb, "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
        " share_token, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (event["id"], "photo", f"IMG_{stem}.jpg", rel(orig), rel(webp), rel(thumb), 1, stem,
         secrets.token_hex(8), created))
    con.commit()
    con.close()


def test_the_colour_flag_on_a_touch_tile_is_a_small_mark_not_a_white_disc():
    rules = _blocks("@media (hover: none)")
    flag = re.search(r"\.photo-item-overlay \.btn\.tag-btn \{(.*?)\}", rules, flags=re.S).group(1)
    assert "background: none" in flag and "box-shadow: none" in flag
    assert "44px" in flag                       # the finger target stays 44 px
    mark = re.search(r"\.tag-btn::before \{(.*?)\}", rules, flags=re.S).group(1)
    inset = int(re.search(r"inset: (\d+)px", mark).group(1))
    assert 44 - 2 * inset <= 28                 # what is seen is no larger than the pick dot


def test_the_date_line_moves_aside_for_the_pick_dot_and_the_flag():
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"\.photo-item:has\(\.pick-dot\) \.photo-info \{[^}]*padding-left: 40px", css)
    assert ".photo-item:has(.tag-btn) .photo-info { padding-right: 44px; }" in _blocks("@media (hover: none)")


def test_the_large_view_toolbar_fits_a_phone():
    rules = _blocks("@media (max-width: 640px)")
    assert re.search(r"\.lb-counter \{[^}]*white-space: nowrap", rules)
    assert re.search(r"\.lb-tools \.lb-btn \{[^}]*width: 44px", rules)
    assert "#lbFull { display: none; }" in _blocks("@media (hover: none) and (max-width: 640px)")


def test_the_date_on_a_tile_reads_like_every_other_date(client):
    ev = _event(show_date=True)
    _photo(ev)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    info = re.findall(r'<span class="photo-info">(.*?)</span>', html, flags=re.S)
    assert info and "12.11.2026 12:05" in info[0]
    assert "2026-11-12" not in info[0]


def test_a_gallery_of_folders_counts_the_pictures_in_its_folders(client):
    top = _event(name="Hochzeit")
    for name, n in (("Trauung", 2), ("Feier", 3)):
        child = db.get_event_by_id(folders.create_child(top["id"], name))
        db.update_event(child["id"], {"published": 1})
        for _ in range(n):
            _photo(child)
    html = client.get(f"/gallery/{top['slug']}").get_data(as_text=True)
    meta = re.search(r'<div class="gallery-meta">(.*?)</div>', html, flags=re.S).group(1)
    assert re.search(r'id="photoCount">\s*5 ', meta)
    assert "19.09.2020" in meta and "2020-09-19" not in meta


def test_the_client_overview_counts_the_pictures_in_the_folders(client):
    top = _event(name="Hochzeit")
    child = db.get_event_by_id(folders.create_child(top["id"], "Trauung"))
    db.update_event(child["id"], {"published": 1})
    _photo(child)
    _photo(child)
    hidden = db.get_event_by_id(folders.create_child(top["id"], "Intern"))
    db.update_event(hidden["id"], {"published": 0})
    _photo(hidden)
    cid = clients.create("Anna")
    clients.set_events(cid, [top["id"]])
    tile = next(g for g in clients.overview(cid) if g["id"] == top["id"])
    assert tile["count"] == 2


def test_the_selection_bar_takes_two_rows_on_a_phone():
    rules = _blocks("@media (max-width: 560px)")
    assert re.search(r"\.client-picks-inner \{[^}]*grid-template-columns: minmax\(0, 1fr\) auto", rules)
    assert re.search(r"\.client-picks-state \{[^}]*flex-direction: column", rules)
    # the visible label goes; the list keeps its name for screen readers
    assert re.search(r"\.client-picks-label \{[^}]*clip: rect\(0 0 0 0\)", rules)


def test_folders_stand_two_by_two_on_a_phone():
    assert ".subgallery-grid { grid-template-columns: repeat(2, minmax(0, 1fr))" in _blocks("@media (max-width: 560px)")
