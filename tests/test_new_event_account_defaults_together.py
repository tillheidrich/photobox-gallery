"""Every account default for new events applies at creation, together.

Two branches added account defaults for new events: the clean gallery
(``new_event_gallery_default``, Paket D) and the deletion period, automatic
deletion and file-name rule (``retention_days_default``,
``retention_auto_default``, ``file_naming``, event-wahl). Both must reach an
event created by the admin form and by the MCP tools ``create_event`` and
``setup_event``; before the merge ``create_event`` ignored the period
default and always wrote 30 days.
"""
import re
import secrets

import pytest

from app import db, dateinamen, ecosystem, gallery_settings as gsm, retention

SETTINGS = {"new_event_gallery_default": "schlicht", "retention_days_default": "45",
            "retention_auto_default": "0", "file_naming": "event_datetime"}


@pytest.fixture(autouse=True)
def defaults(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        for k, v in SETTINGS.items():
            db.set_setting(k, v)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"
    yield
    with client.application.app_context():
        db.set_setting("new_event_gallery_default", "normal")
        db.set_setting("retention_days_default", "")
        db.set_setting("retention_auto_default", "1")
        db.set_setting("file_naming", "")


def _all_defaults_hold(event):
    assert gsm.for_event(event)["likes"] is False                     # clean gallery
    assert int(event["retention_days"]) == 45                         # period default
    assert retention.auto_delete(event) is False                      # no automatic deletion
    assert dateinamen.regel_fuer(event, gsm.for_event(event)) == "event_datetime"


def test_the_admin_form_applies_every_default(client):
    # A name of its own: the suite shares one database, a taken slug re-renders the form.
    name = f"Formular {secrets.token_hex(3)}"
    r = client.post("/admin/events/new", data={"name": name, "csrf_token": "t", "gallery_kind": "booth"})
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    eid = int(re.search(r"/events/(\d+)/edit", r.headers["Location"]).group(1))
    with client.application.app_context():
        _all_defaults_hold(db.get_event_by_id(eid))


def test_mcp_create_event_and_setup_event_apply_every_default(client):
    from app import mcp
    with client.application.test_request_context("/mcp"):
        made = mcp._create_event("MCP Vorgaben")
        _all_defaults_hold(db.get_event_by_id(made["id"]))
        booth = mcp._setup_event("MCP Box Vorgaben", kind="booth")
        _all_defaults_hold(db.get_event_by_id(booth["id"]))


def test_what_the_assistant_names_still_wins(client):
    from app import mcp
    with client.application.test_request_context("/mcp"):
        made = mcp._create_event("MCP 14 Tage", retention_days=14)
        assert int(db.get_event_by_id(made["id"])["retention_days"]) == 14
        booth = mcp._setup_event("MCP explizit", kind="booth", retention_days=7, auto_delete=True,
                                 file_naming="original", settings={"likes": True})
        event = db.get_event_by_id(booth["id"])
        assert int(event["retention_days"]) == 7 and retention.auto_delete(event) is True
        assert dateinamen.regel_fuer(event, gsm.for_event(event)) == "original"
        assert gsm.for_event(event)["likes"] is True
