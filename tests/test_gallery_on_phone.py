"""The gallery on a phone shows pictures, not buttons.

On a touch device there is no hover, so the three round buttons (share,
download, heart) used to sit on every tile all the time. In the two-column
grid they covered a third of each landscape photo - the lower third, where the
front row of a group photo has its faces. This was the weakest screen of the
whole product and the one every guest sees.

Now a tile on a touch device is the picture. Share and download were already
in the large view; the heart moved there too, and a liked photo carries a
small heart in its corner so the grid still shows what was chosen. The colour
flag stays on the tile in voting mode, because its menu cannot open inside the
large view yet.

These tests read the page and the stylesheet. Whether it looks right on a
phone is checked by eye; the screenshots are in the wiki note of 2026-11-11.
"""
import json
import re
import secrets
from pathlib import Path

import pytest

from app import db, ecosystem, gallery_settings as gsm

CSS = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "public.css"


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _gallery(client, **settings):
    key = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name="Phone", slug=f"phone-{key}", upload_token=f"tok-{key}")
        db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(settings))})
        con = db.get_db()
        cur = con.execute(
            "INSERT INTO media (event_id, filename, path_original, path_webp, kind, "
            "share_token, created_at) VALUES (?,?,?,?,?,?,datetime('now'))",
            (eid, "f.jpg", f"storage/phone-{key}/orig/f.jpg",
             f"storage/phone-{key}/webp/f.jpg", "photo", secrets.token_hex(8)))
        con.commit()
        mid = int(cur.lastrowid)
        con.close()
        return db.get_event_by_id(eid), mid


def _touch_rules() -> str:
    """Everything the stylesheet says inside ``@media (hover: none)`` blocks."""
    css = CSS.read_text(encoding="utf-8")
    return "\n".join(re.findall(r"@media \(hover: none\) \{(.*?)\n\}", css, flags=re.S))


def test_touch_devices_no_longer_force_the_buttons_onto_every_tile():
    rules = _touch_rules()
    assert rules, "the stylesheet has no rules for touch devices at all"
    assert "opacity: 1 !important" not in rules, "the overlay is still forced visible on touch"
    assert re.search(r"\.photo-item-overlay \.btn:not\(\.tag-btn\)\s*\{\s*display: none", rules), \
        "share, download and heart are not taken off the tile on touch"


def test_the_heart_is_in_the_large_view(client):
    event, _ = _gallery(client, likes=True)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert 'id="lbLike"' in page
    # Taken off the tile on touch, so it must be reachable there without hover.
    assert page.index('id="lbLike"') > page.index('id="lightbox"')


def test_no_heart_in_the_large_view_when_hearts_are_off(client):
    event, _ = _gallery(client, likes=False)
    page = client.get(f"/gallery/{event['slug']}").get_data(as_text=True)
    assert 'id="lbLike"' not in page


def test_a_liked_photo_is_marked_on_its_tile(client):
    event, mid = _gallery(client, likes=True)
    slug = event["slug"]
    assert 'data-liked="1"' not in client.get(f"/gallery/{slug}").get_data(as_text=True)
    answer = client.post(f"/api/photo/{mid}/like", json={"event_id": event["id"]})
    assert answer.status_code == 200 and answer.get_json()["liked"] is True
    assert 'data-liked="1"' in client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert "[data-liked]" in CSS.read_text(encoding="utf-8"), "the mark has no style"


def test_a_long_title_stays_on_one_line_on_a_phone():
    """Changed 15.11.2026 (owner's brief, finding M9): two lines made the nav
    taller than 56 px at 360 and 390 px. One line with an ellipsis; the full
    name stands on the page itself."""
    css = CSS.read_text(encoding="utf-8")
    narrow = "\n".join(re.findall(r"@media \(max-width: 480px\) \{(.*?)\n\}", css, flags=re.S))
    assert ".nav-logo-text" in narrow and "text-overflow: ellipsis" in narrow
    assert "-webkit-line-clamp" not in narrow


def test_a_wrong_address_gets_our_own_page(client):
    answer = client.get("/there-is-no-such-page")
    page = answer.get_data(as_text=True)
    assert answer.status_code == 404
    assert "The requested URL was not found on the server" not in page
    assert "nav-logo" in page, "the 404 page does not use the site's own layout"


def test_the_interface_for_programs_keeps_its_plain_404(client):
    answer = client.get("/api/there-is-no-such-thing")
    assert answer.status_code == 404
    assert "nav-logo" not in answer.get_data(as_text=True)
