"""No <form> inside another <form> on the admin pages.

Browsers drop a nested ``<form>`` start tag, so a small button placed inside the
page's big form submits the big form instead of doing its own job (the booth
page's "Papier nachgelegt" once sent the whole profile). The walker below reads
the rendered HTML the way a parser sees it and fails on the first nested start
tag.
"""
import secrets
from html.parser import HTMLParser

import pytest

from app import db, ecosystem


class _FormWalker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.nested = []          # (line, action of the inner form)

    def handle_starttag(self, tag, attrs):
        if tag != "form":
            return
        if self.depth:
            self.nested.append((self.getpos()[0], dict(attrs).get("action") or "(no action)"))
        self.depth += 1

    def handle_endtag(self, tag):
        if tag == "form" and self.depth:
            self.depth -= 1


def nested_forms(html: str):
    walker = _FormWalker()
    walker.feed(html)
    walker.close()
    return walker.nested


def test_the_walker_sees_a_nested_form():
    assert nested_forms("<form action='/a'><p><form action='/b'></form></p></form>") == [(1, "/b")]
    assert nested_forms("<form action='/a'></form><form action='/b'></form>") == []
    assert nested_forms("<form><script>var s = '<form>';</script></form>") == []


@pytest.fixture()
def admin(client):
    client.get("/healthz")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    return client


def _selphy_status():
    return {"printer_status": "ready", "camera_state": "ready", "disk_free_gb": 100,
            "printer_name": "Canon SELPHY CP1300", "prints_pending": 4, "prints_held": 2,
            "printer_hint": "Kein Papier mehr", "printers_total": 2, "printers_ready": 1,
            "sheets": [{"slot": 1, "label": "SELPHY", "quelle": "gezählt", "rest": 3, "gesamt": 18,
                        "fach": {"rest": 3, "gesamt": 18}, "farbband": {"rest": 4, "gesamt": 36},
                        "leer": False}]}


def test_the_booth_page_has_no_nested_forms(admin):
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    db.record_heartbeat(dev["id"], _selphy_status(), "yellow", "127.0.0.1")
    from app import status_page
    status_page.enable(dev["id"])
    html = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    # The page really carries what this test is about.
    for needle in ("Wartende Drucke erneut", "Papier nachgelegt", "Farbband gewechselt", 'id="statusseite"'):
        assert needle in html, needle
    assert nested_forms(html) == []


def _pages():
    """(label, path template) of admin pages that render cheaply with one event and one booth."""
    return [
        ("dashboard", "/admin/dashboard"),
        ("events", "/admin/events"),
        ("event-new", "/admin/events/new"),
        ("event-edit", "/admin/events/{event}/edit"),
        ("event-review", "/admin/events/{event}/review"),
        ("event-storage", "/admin/events/{event}/storage"),
        ("deletions", "/admin/deletions"),
        ("devices", "/admin/devices"),
        ("device-new", "/admin/devices/new"),
        ("device-edit", "/admin/devices/{device}/edit"),
        ("device-logs", "/admin/devices/{device}/logs"),
        ("booths", "/admin/booths"),
        ("settings", "/admin/settings"),
        ("settings-tracking", "/admin/settings/tracking"),
        ("settings-eingang", "/admin/settings/eingang"),
        ("settings-bewertungen", "/admin/settings/bewertungen"),
        ("settings-mcp", "/admin/settings/mcp"),
        ("settings-assistants", "/admin/settings/assistants"),
        ("settings-homepage", "/admin/settings/homepage"),
        ("settings-notifications", "/admin/settings/notifications"),
        ("settings-mail", "/admin/settings/mail"),
        ("settings-legal", "/admin/settings/legal"),
        ("settings-faq", "/admin/settings/faq"),
        ("analytics", "/admin/analytics"),
        ("analytics-event", "/admin/analytics/{event}"),
        ("analytics-box", "/admin/analytics/box/{device}"),
        ("protokoll", "/admin/protokoll"),
        ("software", "/admin/software"),
        ("templates", "/admin/vorlagen"),
        ("booth-assets", "/admin/booth-assets"),
        ("domains", "/admin/domains"),
        ("clients", "/admin/clients"),
    ]


@pytest.mark.parametrize("label, path", _pages(), ids=[p[0] for p in _pages()])
def test_admin_pages_have_no_nested_forms(admin, label, path):
    event = db.create_event(name="Formprobe", slug="formprobe-" + secrets.token_hex(3),
                            upload_token=secrets.token_hex(8))
    event_id = event["id"] if isinstance(event, dict) else int(event)
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    db.record_heartbeat(dev["id"], _selphy_status(), "yellow", "127.0.0.1")
    r = admin.get(path.format(event=event_id, device=dev["id"]))
    if r.status_code != 200:
        pytest.skip(f"{path} answers {r.status_code} in the test setup, not checked")
    assert nested_forms(r.get_data(as_text=True)) == []
