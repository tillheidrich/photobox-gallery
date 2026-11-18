"""The photo count in a gallery's link preview is the count the page shows (18.11.2026).

og:description / twitter:description said "981 Fotos" where the page said
568: it counted every row of ``db.list_media`` - the unframed captures next to
their framed pictures, red pictures the gallery hides - instead of the tiles a
guest sees (plus the pictures of open folders, as the page's count does).
"""
import re

from app import db, gallery_settings as gsm, link_preview
from tests.test_link_preview import BLUE_PIC, GREEN_PIC, RED_PIC, _event, _og, _photo, schema  # noqa: F401


def _page_count(client, slug):
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    return int(re.search(r'<span id="photoCount">(\d+) ', html).group(1)), _og(html)


def _gallery_with_extras():
    import json
    ev = _event(hide_red_in_presentation=True)
    db.update_event(ev["id"], description="")
    for _ in range(3):
        _photo(ev, BLUE_PIC)
    _photo(ev, RED_PIC, color_tag="red")                      # hidden from guests by the setting
    for _ in range(4):
        _photo(ev, GREEN_PIC, kind="original")                # captures shown only under their own filter
    _photo(ev, BLUE_PIC, hidden=1)                            # hidden
    db.update_event(ev["id"], gallery_json=json.dumps(gsm.clean({"hide_red_in_presentation": True})))
    return db.get_event_by_id(ev["id"])


def test_the_preview_counts_what_the_page_counts(client):
    ev = _gallery_with_extras()
    shown, og = _page_count(client, ev["slug"])
    assert shown == 3
    meta = link_preview.meta(db.get_event_by_id(ev["id"]), "https://x.test")
    assert meta["mode"] == "full"
    assert re.search(rf"\b{shown}\b", meta["description"]), meta["description"]
    assert og["og:description"] == meta["description"] == og["twitter:description"]


def test_open_folders_count_like_on_the_page(client):
    from app import folders
    ev = _gallery_with_extras()
    child = _event()
    folders.set_parent(child["id"], ev["id"])
    _photo(child, BLUE_PIC)
    _photo(child, BLUE_PIC)
    shown, og = _page_count(client, ev["slug"])
    assert shown == 5
    assert re.search(r"\b5\b", og["og:description"]), og
