"""The owner's two views of a gallery (Picdrop round, 17.11.2026).

* ``?ansicht=vorschau`` - the page exactly as a guest gets it, also for a
  draft or a password gallery, under a slim bar "Vorschau · <state>".
* ``?ansicht=verwalten`` - the same page plus hiding, colour marks, cover,
  delete and bulk actions through ``/admin/events/<id>/manage``.

And the calm guest page: no WhatsApp button, nothing on the tiles, the
description card, one primary "Alle herunterladen".
"""
import json
import re
import secrets

import pytest
from PIL import Image

from app import db, ecosystem, gallery_settings as gsm, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    db.set_setting("silent_mode", "0")
    yield


def _event(published=1, password="", description="", **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Galerie {k}", slug=f"ov-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": published, "gallery_json": json.dumps(gsm.clean(gs)),
                          "description": description})
    if password:
        db.update_event(eid, password=password)
    return db.get_event_by_id(eid)


def _photo(event, **cols):
    d = media.event_storage_dir(event["slug"])
    stem = secrets.token_hex(5)
    orig, webp, thumb = d / "orig" / f"{stem}.jpg", d / "webp" / f"{stem}.webp", d / "thumb" / f"{stem}.webp"
    Image.new("RGB", (60, 40), (90, 90, 90)).save(orig, "JPEG")
    Image.new("RGB", (60, 40), (90, 90, 90)).save(webp, "WEBP")
    Image.new("RGB", (30, 20), (90, 90, 90)).save(thumb, "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
        " share_token) VALUES (?,?,?,?,?,?,?,?,?)",
        (event["id"], "photo", f"IMG_{stem}.jpg", rel(orig), rel(webp), rel(thumb), 1, stem, secrets.token_hex(8)))
    mid = cur.lastrowid
    for k, v in cols.items():
        con.execute(f"UPDATE media SET {k}=? WHERE id=?", (v, mid))
    con.commit()
    con.close()
    return mid


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"


def _ids(html):
    return {int(i) for i in re.findall(r'class="photo-item pswp-gallery"[^>]*?data-id="(\d+)"', html, flags=re.S)}


# ── B: no WhatsApp ────────────────────────────────────────────

def test_the_gallery_has_no_whatsapp_button_even_for_old_settings(client):
    ev = _event(share_whatsapp=True)
    _photo(ev)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert "wa.me" not in html and ">\n        WhatsApp\n" not in html
    assert gsm.DEFAULTS["share_whatsapp"] is False
    assert "share_whatsapp" not in gsm.PRESET_CLEAN


def test_the_event_form_no_longer_offers_the_whatsapp_switch(client):
    ev = _event()
    _admin(client)
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert 'name="share_whatsapp"' not in page
    assert "WhatsApp-Button" not in page


# ── A: the calm guest page ────────────────────────────────────

def test_tiles_carry_no_share_download_or_like_buttons(client):
    ev = _event(likes=True, download_single=True, share_native=True)
    _photo(ev)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    grid = html[html.index('id="photoGrid"'):html.index('id="lightbox"')]
    assert "downloadPhoto(" not in grid and "sharePhoto(" not in grid and "likePhoto(" not in grid
    # ... the large view has them
    assert 'id="lbDownload"' in html and 'id="lbLike"' in html and 'id="lbShare"' in html


def test_the_description_is_a_card_with_more_and_less(client):
    ev = _event(description="Ein langer Text über den Abend. " * 12)
    _photo(ev)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'id="gDescText"' in html and "Ein langer Text" in html
    assert 'data-more="mehr lesen"' in html and 'data-less="weniger"' in html


def test_download_all_is_the_one_primary_button(client):
    ev = _event(download_zip=True)
    _photo(ev)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert re.search(r'download\.zip"\s+class="btn btn--primary g-dl-all"', html)


def test_ios_gets_a_save_sheet_with_the_long_press_hint(client):
    ev = _event()
    _photo(ev)
    html = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'id="dlSave"' in html and "Datei-Download" in html
    assert "Tippe lange auf die Datei, um diese zu speichern." in html
    assert "if (IS_IOS) { dlSaveSheet(" in html


# ── D: preview ────────────────────────────────────────────────

def test_preview_opens_a_draft_and_a_password_gallery_for_the_owner(client):
    for ev in (_event(published=0), _event(password="geheim")):
        _photo(ev)
        _admin(client)
        r = client.get(f"/gallery/{ev['slug']}?ansicht=vorschau")
        html = r.get_data(as_text=True)
        assert r.status_code == 200 and 'id="pvBar"' in html and 'name="password"' not in html
        assert ("Entwurf" in html) if not ev["published"] else ("mit Passwort" in html)
        assert f'href="/admin/events/{ev["id"]}/edit"' in html


def test_preview_is_nothing_for_a_guest(client):
    ev = _event(published=0)
    assert client.get(f"/gallery/{ev['slug']}?ansicht=vorschau").status_code == 404
    ev2 = _event()
    html = client.get(f"/gallery/{ev2['slug']}?ansicht=vorschau").get_data(as_text=True)
    assert 'id="pvBar"' not in html and 'id="mgData"' not in html


def test_preview_shows_what_a_guest_sees(client):
    ev = _event(hide_red_in_presentation=True)
    shown = _photo(ev)
    red = _photo(ev, color_tag="red")
    hidden = _photo(ev, hidden=1, review_state="pending")
    _admin(client)
    html = client.get(f"/gallery/{ev['slug']}?ansicht=vorschau").get_data(as_text=True)
    assert _ids(html) == {shown}
    # guest's rights: no colour flags, no manage tools
    assert "openTagMenu(" not in html and 'id="mgData"' not in html
    assert red not in _ids(html) and hidden not in _ids(html)


def test_the_event_list_and_page_link_to_the_preview(client):
    ev = _event()
    _admin(client)
    assert f"/gallery/{ev['slug']}?ansicht=vorschau" in client.get("/admin/events").get_data(as_text=True)
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert f"/gallery/{ev['slug']}?ansicht=vorschau" in page and f"/gallery/{ev['slug']}?ansicht=verwalten" in page


# ── E/F: manage ───────────────────────────────────────────────

def test_manage_shows_hidden_and_red_pictures_marked(client):
    ev = _event(hide_red_in_presentation=True)
    a = _photo(ev)
    red = _photo(ev, color_tag="red")
    hidden = _photo(ev, hidden=1, review_state="pending")
    _admin(client)
    html = client.get(f"/gallery/{ev['slug']}?ansicht=verwalten").get_data(as_text=True)
    assert _ids(html) == {a, red, hidden}
    assert re.search(rf'data-id="{hidden}"[^>]*?data-hidden="1"', html, flags=re.S)
    assert 'id="mgData"' in html and "gallery-manage.js" in html
    assert 'data-mgfilter="liked"' in html and 'data-mgfilter="hidden"' in html


def test_manage_shows_like_counts(client):
    ev = _event(likes=True)
    pid = _photo(ev)
    db.toggle_like(ev["id"], pid, "dev-a")
    db.toggle_like(ev["id"], pid, "dev-b")
    _admin(client)
    html = client.get(f"/gallery/{ev['slug']}?ansicht=verwalten").get_data(as_text=True)
    assert re.search(rf'data-id="{pid}"[^>]*?data-likes="2"', html, flags=re.S)
    assert 'class="mg-likes"' in html


def _manage(client, ev, **body):
    return client.post(f"/admin/events/{ev['id']}/manage", json=body, headers={"X-CSRF-Token": "tok"})


def test_hide_show_tag_and_cover_through_the_manage_endpoint(client):
    ev = _event()
    a, b = _photo(ev), _photo(ev)
    _admin(client)
    assert _manage(client, ev, action="hide", ids=[a, b]).get_json()["ok"]
    assert {m["id"] for m in db.list_media(ev["id"])} == set()
    assert _manage(client, ev, action="show", ids=[a]).get_json()["ok"]
    assert {m["id"] for m in db.list_media(ev["id"])} == {a}
    assert _manage(client, ev, action="tag", ids=[a], tag="green").get_json()["tag"] == "green"
    assert db.get_media(a)["color_tag"] == "green"
    assert _manage(client, ev, action="tag", ids=[a], tag="purple").status_code == 400
    assert _manage(client, ev, action="cover", ids=[a]).get_json()["ok"]
    assert db.get_event_by_id(ev["id"])["cover_path"] == db.get_media(a)["path_webp"]


def test_delete_goes_to_the_trash_and_comes_back(client):
    from app import trash
    db.set_setting("trash_days", "14")
    try:
        ev = _event()
        a = _photo(ev)
        _admin(client)
        res = _manage(client, ev, action="delete", ids=[a]).get_json()
        assert db.get_media(a) is None and res["trash"]
        assert trash.enabled()
        back = _manage(client, ev, action="restore", ids=res["trash"]).get_json()
        assert back["restored"] == 1 and db.get_media(a) is not None
    finally:
        db.set_setting("trash_days", "0")


def test_the_manage_endpoint_touches_only_this_events_pictures(client):
    ev, other = _event(), _event()
    foreign = _photo(other)
    _admin(client)
    assert _manage(client, ev, action="hide", ids=[foreign]).status_code == 400
    assert db.get_media(foreign)["hidden"] in (0, None)


def test_the_manage_endpoint_needs_the_admin_and_the_token(client):
    ev = _event()
    a = _photo(ev)
    assert _manage(client, ev, action="hide", ids=[a]).status_code in (400, 401)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"
    r = client.post(f"/admin/events/{ev['id']}/manage", json={"action": "hide", "ids": [a]})
    assert r.status_code == 400                      # no X-CSRF-Token
    assert db.get_media(a)["hidden"] in (0, None)


def test_the_hide_red_label_says_what_it_does(client):
    ev = _event()
    _admin(client)
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert "Rot markierte Bilder für Gäste ausblenden" in page


@pytest.mark.parametrize("view", ["", "?ansicht=vorschau", "?ansicht=verwalten"])
def test_every_tile_sits_in_the_grid(client, view):
    """A stray </div> once closed the grid after the first tile (found in the self-test)."""
    from html.parser import HTMLParser
    ev = _event(color_tags=True)
    made = {_photo(ev) for _ in range(3)}
    if view:
        _admin(client)
    html = client.get(f"/gallery/{ev['slug']}{view}").get_data(as_text=True)

    class P(HTMLParser):
        depth, grid, inside = 0, None, set()

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "div":
                self.depth += 1
                if a.get("id") == "photoGrid":
                    self.grid = self.depth
                elif self.grid and self.depth == self.grid + 1 and "photo-item" in (a.get("class") or ""):
                    self.inside.add(int(a["data-id"]))

        def handle_endtag(self, tag):
            if tag == "div":
                if self.grid == self.depth:
                    self.grid = None
                self.depth -= 1

    p = P()
    p.feed(html)
    assert p.inside == made


def test_the_owners_colour_marks_are_not_shown_to_guests(client):
    """Marks are the owner's sorting tool: guests (and the preview) see clean tiles."""
    ev = _event(hide_red_in_presentation=False)
    _photo(ev, color_tag="green")
    guest = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'class="photo-tag"' not in guest
    _admin(client)
    assert 'class="photo-tag"' not in client.get(f"/gallery/{ev['slug']}?ansicht=vorschau").get_data(as_text=True)
    assert 'class="photo-tag"' in client.get(f"/gallery/{ev['slug']}?ansicht=verwalten").get_data(as_text=True)
