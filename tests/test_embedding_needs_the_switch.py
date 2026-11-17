"""A gallery is framed by other sites only when its event says so (final review A11).

``?embed=1`` let any site put any gallery in an iframe as long as the event
had no allow-list. Now an event has the switch ``embed`` ("Einbetten
erlauben"), off for new events; without it the embed page answers
``frame-ancestors 'self'`` like every other page. An existing event that
already named allowed sites (``embed_origins``) keeps embedding for them; any
other existing event is off.
"""
import json
import secrets

import pytest

from app import db, ecosystem, gallery_settings as gsm


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    ecosystem.init_schema()
    yield


def _event(raw_gallery: dict):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Embed {k}", slug=f"emb-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(raw_gallery)})
    return db.get_event_by_id(eid)


def _csp(client, slug, embed=True):
    r = client.get(f"/gallery/{slug}" + ("?embed=1" if embed else ""))
    return r.headers["Content-Security-Policy"], r.headers.get("X-Frame-Options")


def test_without_the_switch_no_site_may_frame_the_gallery(client):
    ev = _event({})
    csp, xfo = _csp(client, ev["slug"])
    assert "frame-ancestors 'self'" in csp and "frame-ancestors *" not in csp
    assert xfo == "SAMEORIGIN"


def test_switched_on_without_a_list_any_site_may(client):
    ev = _event({"embed": True})
    csp, xfo = _csp(client, ev["slug"])
    assert "frame-ancestors *" in csp and xfo is None


def test_switched_on_with_a_list_only_those_sites(client):
    ev = _event({"embed": True, "embed_origins": "https://example.org"})
    csp, _ = _csp(client, ev["slug"])
    assert "frame-ancestors 'self' https://example.org" in csp


def test_an_existing_event_with_a_list_keeps_embedding_for_it(client):
    ev = _event({"embed_origins": "https://hochzeit.example.org"})        # stored before the switch existed
    assert gsm.for_event(ev)["embed"] is True
    csp, _ = _csp(client, ev["slug"])
    assert "frame-ancestors 'self' https://hochzeit.example.org" in csp


def test_an_existing_event_without_a_list_is_off(client):
    ev = _event({"watermark": False})                                      # stored before the switch existed
    assert gsm.for_event(ev)["embed"] is False
    csp, _ = _csp(client, ev["slug"])
    assert "frame-ancestors *" not in csp


def test_switched_off_explicitly_the_list_does_not_open_it(client):
    ev = _event({"embed": False, "embed_origins": "https://example.org"})
    csp, xfo = _csp(client, ev["slug"])
    assert "https://example.org" not in csp and xfo == "SAMEORIGIN"


def test_new_events_start_with_embedding_off():
    assert gsm.DEFAULTS["embed"] is False
    assert gsm.clean(gsm.new_event_defaults("booth"))["embed"] is False
    assert gsm.clean(gsm.new_event_defaults("client"))["embed"] is False


def test_saving_another_setting_keeps_the_old_events_choice(client):
    ev = _event({"embed_origins": "https://example.org"})
    gs = gsm.for_event(ev)
    gs["watermark"] = True
    db.update_event(ev["id"], gallery_json=gsm.store(ev, gs))
    assert gsm.for_event(db.get_event_by_id(ev["id"]))["embed"] is True


def test_the_admin_embed_box_has_the_switch_and_explains_it(client):
    ev = _event({})
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert 'name="embed"' in page
    assert "Einbetten erlauben" in page
