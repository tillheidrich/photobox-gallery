"""Saving the event form keeps the gallery password (17.11.2026).

Only a hash is stored, so the form could never show the password, and its
text field came back empty — which meant "no password": every save of the
event page removed it. Now the field is a password input that is never
filled in, its placeholder says whether one is stored, an empty field keeps
it, and only the checkbox "Passwort entfernen" removes it (a typed password
wins over the checkbox). The MCP tool keeps its explicit meaning: a given
``password: ""`` removes it, an omitted field never touches it.
"""
import json
import re
import secrets
from html.parser import HTMLParser

import pytest

from app import db, ecosystem
from app.i18n import de


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"
        s["admin_epoch"] = accounts.session_epoch()
        s["lang"] = "de"


def _event(password=""):
    return db.create_event(name="Demo Sommerfest", slug="pw-" + secrets.token_hex(3),
                           upload_token=secrets.token_hex(8), password=password)


class _Form(HTMLParser):
    """What a browser sends for the form #eventForm, untouched."""

    def __init__(self):
        super().__init__()
        self.inside, self.data, self._ta, self._sel = False, [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "eventForm":
            self.inside = True
        if tag == "option" and self._sel is not None:
            if self._sel[1] is None or "selected" in a:      # first option unless one is selected
                self._sel[1] = a.get("value", "")
            return
        if not self.inside or not a.get("name") or "disabled" in a:
            return
        name = a["name"]
        if tag == "input":
            kind = (a.get("type") or "text").lower()
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self.data.append((name, a.get("value", "on")))
            elif kind not in ("submit", "button", "file", "image"):
                self.data.append((name, a.get("value") or ""))
        elif tag == "textarea":
            self._ta = [name, ""]
        elif tag == "select":
            self._sel = [name, None]

    def handle_data(self, text):
        if self._ta is not None:
            self._ta[1] += text

    def handle_endtag(self, tag):
        if tag == "textarea" and self._ta is not None:
            self.data.append(tuple(self._ta))
            self._ta = None
        elif tag == "select" and self._sel is not None:
            if self._sel[1] is not None:
                self.data.append(tuple(self._sel))
            self._sel = None
        elif tag == "form" and self.inside:
            self.inside = False


def _as_rendered(client, eid, **change):
    p = _Form()
    p.feed(client.get(f"/admin/events/{eid}/edit").get_data(as_text=True))
    data = [(k, v) for k, v in p.data if k not in change]
    data += list(change.items())
    from werkzeug.datastructures import MultiDict
    return client.post(f"/admin/events/{eid}/edit", data=MultiDict(data))


def test_saving_the_form_exactly_as_rendered_keeps_the_password(client):
    _admin(client)
    eid = _event("demo-pass")
    before = db.get_event_by_id(eid)["password_hash"]
    assert before
    r = _as_rendered(client, eid)
    assert r.status_code == 302
    assert db.get_event_by_id(eid)["password_hash"] == before
    _as_rendered(client, eid)
    assert db.get_event_by_id(eid)["password_hash"] == before


def test_the_field_is_never_filled_and_says_whether_one_is_stored(client):
    _admin(client)
    with_pw, without = _event("demo-pass"), _event()
    html = client.get(f"/admin/events/{with_pw}/edit").get_data(as_text=True)
    field = re.search(r'<input type="password" id="password" name="password"[^>]*>', html, re.S).group(0)
    assert 'value=""' in field and de.STRINGS["event.password.stored"] in field
    assert 'name="remove_password"' in html and de.STRINGS["event.password.remove_hint"] in html
    html = client.get(f"/admin/events/{without}/edit").get_data(as_text=True)
    field = re.search(r'<input type="password" id="password" name="password"[^>]*>', html, re.S).group(0)
    assert de.STRINGS["event.password.new"] in field
    assert 'name="remove_password"' not in html
    assert 'type="password" id="password"' in client.get("/admin/events/new").get_data(as_text=True)


def test_set_change_and_remove(client):
    _admin(client)
    eid = _event()
    _as_rendered(client, eid, password="erstes-pw")
    ev = db.get_event_by_id(eid)
    assert db.check_event_password(ev, "erstes-pw")
    _as_rendered(client, eid, password="zweites-pw")
    ev = db.get_event_by_id(eid)
    assert db.check_event_password(ev, "zweites-pw") and not db.check_event_password(ev, "erstes-pw")
    _as_rendered(client, eid, password="drittes-pw", remove_password="1")      # typed wins
    assert db.check_event_password(db.get_event_by_id(eid), "drittes-pw")
    _as_rendered(client, eid, remove_password="1")
    assert db.get_event_by_id(eid)["password_hash"] is None


def test_a_new_event_takes_the_password_typed_into_the_create_form(client, formular):
    _admin(client)
    slug = "neu-" + secrets.token_hex(3)
    formular("/admin/events/new", {"name": "Demo Neu", "slug": slug, "password": "neu-pw", "retention_days": "30"})
    assert db.check_event_password(db.get_event_by_slug(slug), "neu-pw")


def test_mcp_configure_event_clears_only_when_asked(client):
    from tests.test_mcp_visibility_needs_photos import PHOTOS, _data
    from app import accounts, oauth
    cid = "pbc_" + secrets.token_hex(6)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    token = oauth._issue(con, cid, secrets.token_hex(6), PHOTOS, "http://localhost/mcp",
                         accounts.session_epoch())["access_token"]
    con.commit()
    con.close()

    def call(**arguments):
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": "configure_event", "arguments": arguments}},
                           headers={"Authorization": f"Bearer {token}"})
    eid = _event("demo-pass")
    before = db.get_event_by_id(eid)["password_hash"]
    _data(call(event_id=eid, name="Demo umbenannt"))
    _data(call(event_id=eid, published=True))
    assert db.get_event_by_id(eid)["password_hash"] == before
    _data(call(event_id=eid, password=""))
    assert db.get_event_by_id(eid)["password_hash"] is None


def test_the_publish_switch_warns_about_unsaved_changes_and_sits_with_the_badge(client):
    _admin(client)
    eid = _event("demo-pass")
    html = client.get(f"/admin/events/{eid}/edit").get_data(as_text=True)
    head = re.search(r'<span class="pub-head">(.*?)</span>\s*</div>', html, re.S).group(1)
    assert "badge" in head and "pub-switch" in head
    assert f'data-dirty-note="{de.STRINGS["publish.dirty_note"]}"' in html
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "admin-phone.js").read_text(encoding="utf-8")
    assert "function guardPublish()" in js and "form[data-dirty-watch][data-dirty]" in js
    assert "confirm(" not in js.split("function guardPublish()")[1].split("function init()")[0]
