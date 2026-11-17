"""Account-wide default "Schlichte Galerie für neue Events".

Setting ``new_event_gallery_default`` (normal | schlicht, default normal).
Schlicht: a new booth event starts with the clean preset (the extras off,
downloads unchanged). Client galleries do not follow it; existing events
are never touched.
"""
import json
import re

import pytest

from app import db, ecosystem, gallery_settings as gsm


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("new_event_gallery_default", "normal")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
    yield
    with client.application.app_context():
        db.set_setting("new_event_gallery_default", "normal")


def _new(client, name, kind="booth"):
    r = client.post("/admin/events/new", data={"name": name, "csrf_token": "t", "gallery_kind": kind})
    eid = int(re.search(r"/events/(\d+)/edit", r.headers["Location"]).group(1))
    with client.application.app_context():
        return db.get_event_by_id(eid)


def test_the_helper_returns_nothing_by_default(client):
    with client.application.app_context():
        assert gsm.new_event_defaults("booth") == {}


def test_by_default_a_new_event_has_every_extra_on(client):
    ev = _new(client, "Normal Neu")
    gs = gsm.for_event(ev)
    assert all(gs[k] for k in gsm.PRESET_CLEAN)


def test_schlicht_switches_the_extras_off_and_keeps_downloads(client):
    with client.application.app_context():
        db.set_setting("new_event_gallery_default", "schlicht")
    ev = _new(client, "Schlicht Neu")
    gs = gsm.for_event(ev)
    assert not any(gs[k] for k in gsm.PRESET_CLEAN)
    assert gs["download_single"] and gs["download_original"]


def test_client_galleries_do_not_follow(client):
    with client.application.app_context():
        db.set_setting("new_event_gallery_default", "schlicht")
    ev = _new(client, "Kunde Neu", kind="client")
    assert all(gsm.for_event(ev)[k] for k in gsm.PRESET_CLEAN)


def test_existing_events_are_untouched(client):
    before = _new(client, "Alt")
    with client.application.app_context():
        db.set_setting("new_event_gallery_default", "schlicht")
        assert gsm.for_event(db.get_event_by_id(before["id"]))["likes"] is True


def test_mcp_creation_follows(client):
    from app import mcp
    with client.application.test_request_context("/mcp"):
        db.set_setting("new_event_gallery_default", "schlicht")
        made = mcp._create_event("MCP Schlicht")
        assert gsm.for_event(db.get_event_by_id(made["id"]))["likes"] is False
        booth = mcp._setup_event("MCP Box", kind="booth")
        assert gsm.for_event(db.get_event_by_id(booth["id"]))["share_sharepic"] is False
        client_gal = mcp._setup_event("MCP Kunde", kind="client")
        assert gsm.for_event(db.get_event_by_id(client_gal["id"]))["share_sharepic"] is True
        # What the assistant names explicitly wins over the default.
        explicit = mcp._setup_event("MCP Likes", kind="booth", settings={"likes": True})
        assert gsm.for_event(db.get_event_by_id(explicit["id"]))["likes"] is True


def test_the_setting_persists_from_the_defaults_page(client):
    client.post("/admin/settings/galerie", data={"csrf_token": "t", "new_event_gallery_default": "schlicht"})
    with client.application.app_context():
        assert db.get_setting("new_event_gallery_default") == "schlicht"
    client.post("/admin/settings/galerie", data={"csrf_token": "t", "new_event_gallery_default": "quatsch"})
    with client.application.app_context():
        assert db.get_setting("new_event_gallery_default") == "normal"


def test_a_folder_inherits_from_its_clean_parent(client):
    with client.application.app_context():
        db.set_setting("new_event_gallery_default", "schlicht")
    parent = _new(client, "Eltern Schlicht")
    from app import mcp
    with client.application.app_context():
        folder = mcp._create_subgallery(parent["id"], "Ordner")
        fid = folder.get("id") or folder.get("event_id")
        assert gsm.for_event(db.get_event_by_id(int(fid)))["likes"] is False
