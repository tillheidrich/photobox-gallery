"""Link preview of a gallery a stranger may not know about (18.11.2026).

A draft (or a folder under a draft) answers 404 on its page, but its
``og.jpg`` answered 200 with the event title drawn on it, while an unknown
name got the neutral card: a guesser could confirm that a name exists and read
its title. A draft and a gallery not yet visible (``scheduled_live_at`` in the
future, MCP ``visible_from``) now answer exactly like an unknown name - same
status, bytes and headers. Password-protected and expired galleries are
published and keep the brand card (title, colour, no photo, no description).
"""
import secrets
from datetime import datetime, timedelta

import pytest

from app import db, link_preview
from tests.test_link_preview import RED_PIC, _event, _og, _photo, schema  # noqa: F401

SAME = ("Content-Type", "Content-Length", "Cache-Control", "ETag", "X-Robots-Tag", "Set-Cookie", "Vary",
        "Last-Modified", "Content-Disposition")


def _draft():
    return _event(published=0)


def _scheduled():
    ev = _event()
    start = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    db.update_event(ev["id"], scheduled_live_at=start)
    return db.get_event_by_id(ev["id"])


def _folder_of_a_draft():
    from app import folders
    parent, child = _draft(), _event()
    folders.set_parent(child["id"], parent["id"])
    return db.get_event_by_id(child["id"])


def _compare(client, slug, query=""):
    unknown = "gibt-es-nicht-" + secrets.token_hex(3)
    a = client.get(f"/gallery/{slug}/og.jpg{query}")
    b = client.get(f"/gallery/{unknown}/og.jpg{query}")
    assert a.status_code == b.status_code
    assert a.data == b.data, "the card differs from an unknown name's"
    for h in SAME:
        assert a.headers.get(h) == b.headers.get(h), h
    assert sorted(k for k, _ in a.headers) == sorted(k for k, _ in b.headers)
    return a


@pytest.mark.parametrize("make", [_draft, _scheduled, _folder_of_a_draft], ids=["draft", "not_yet_visible", "folder_of_draft"])
def test_a_hidden_gallery_answers_exactly_like_an_unknown_name(client, make):
    ev = make()
    _photo(ev, RED_PIC)
    _compare(client, ev["slug"])
    # The key a page would name, with and without the run-page marker, too.
    meta = link_preview.meta(db.get_event_by_id(ev["id"]), "https://fotos.example.test", force_brand=True)
    assert meta["mode"] == "neutral" and ev["name"] not in meta["title"]
    _compare(client, ev["slug"], "?" + meta["image"].split("?", 1)[1])
    _compare(client, ev["slug"], "?b=1")


@pytest.mark.parametrize("make", [_draft, _scheduled])
def test_meta_tags_of_a_hidden_gallery_name_nothing_of_it(client, make):
    ev = make()
    _photo(ev, RED_PIC)
    meta = link_preview.meta(db.get_event_by_id(ev["id"]), "https://fotos.example.test")
    unknown = link_preview.meta(None, "https://fotos.example.test")
    assert meta["title"] == unknown["title"] and meta["description"] == unknown["description"]
    assert meta["url"] == unknown["url"]
    assert meta["image"].split("?v=")[1] == unknown["image"].split("?v=")[1]     # same cache key


def test_password_and_expired_galleries_keep_the_brand_card(client):
    for ev in (_event(password="geheim"), _event(expires_at="2020-01-01T00:00")):
        _photo(ev, RED_PIC)
        assert link_preview.meta(db.get_event_by_id(ev["id"]), "https://x.test")["mode"] == "brand"
