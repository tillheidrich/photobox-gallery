"""One obvious way to take a gallery live, and back (17.11.2026).

Until now the only way was the switch "Gemeinsame Galerie" deep in the event
form, then saving. Now the event list and the event page carry a button:
"Live schalten" for a draft, "Zurück auf Entwurf" for a live gallery. Every
way in (list, page, form switch, MCP ``configure_event``) ends in
``app/publishing.set_published``: ``published`` and ``status`` move
together and the "published" notification goes out once. Start date,
expiry, silent mode and a folder's parent still decide what a guest sees;
the flash says so, nothing blocks.
"""
import json
import re
import secrets

import pytest

from app import db, ecosystem
from app.i18n import de, en


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


@pytest.fixture()
def notified(monkeypatch):
    calls = []
    from app import notifier
    monkeypatch.setattr(notifier, "notify_event_published", lambda name, url="": calls.append((name, url)))
    return calls


def _admin(client, lang="de"):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"
        s["admin_epoch"] = accounts.session_epoch()
        s["lang"] = lang


def _event(published=0, password="", **fields):
    eid = db.create_event(name="Demo Sommerfest", slug="live-" + secrets.token_hex(3),
                          upload_token=secrets.token_hex(8), password=password,
                          status="live" if published else "draft")
    db.update_event(eid, published=published, **fields)
    return eid


def _post(client, eid, live, back="list", token="tok"):
    return client.post(f"/admin/events/{eid}/publish",
                       data={"csrf_token": token, "live": "1" if live else "0", "back": back})


def _flash(client):
    with client.session_transaction() as s:
        return " ".join(str(m[1]) for m in s.get("_flashes", []))


# ── where the buttons are ────────────────────────────────────────

def _list_item(html, eid):
    i = html.index(f'/admin/events/{eid}/edit"')
    j = html.find('class="event-list-item"', i)
    return html[i: j if j > 0 else len(html)]


@pytest.mark.parametrize("lang,s", [("de", de.STRINGS), ("en", en.STRINGS)])
def test_the_list_offers_go_live_for_a_draft_and_back_to_draft_for_a_live_gallery(client, lang, s):
    _admin(client, lang)
    draft, live = _event(0, password="geheim"), _event(1)
    html = client.get("/admin/events").get_data(as_text=True)
    d, li = _list_item(html, draft), _list_item(html, live)
    assert f'action="/admin/events/{draft}/publish"' in d and 'name="live" value="1"' in d
    assert re.search(r'class="btn btn--primary btn--sm pub-btn">' + re.escape(s["publish.go_live"]), d)
    assert 'name="csrf_token" value="tok"' in d
    # Back to draft: secondary, and only after a confirmation that says what happens.
    assert re.search(r'<details class="pub-confirm">\s*<summary class="btn btn--secondary btn--sm pub-btn">'
                     + re.escape(s["publish.go_draft"]), li)
    assert s["publish.confirm_draft_booth"] in li and 'name="live" value="0"' in li
    assert s["publish.confirm_draft_yes"] in li


def test_without_a_password_the_confirmation_says_so_and_links_the_password_field(client):
    _admin(client)
    eid = _event(0)
    html = _list_item(client.get("/admin/events").get_data(as_text=True), eid)
    assert '<details class="pub-confirm">' in html
    assert "Ohne Passwort: jeder mit dem Link sieht die Bilder." in html
    assert f'href="/admin/events/{eid}/edit#password"' in html
    assert "Trotzdem live schalten" in html            # offered, not blocked


def test_a_client_gallery_says_the_client_loses_the_link_when_it_goes_back_to_draft(client):
    _admin(client)
    eid = _event(1, kind="client")
    html = _list_item(client.get("/admin/events").get_data(as_text=True), eid)
    assert de.STRINGS["publish.confirm_draft_client"] in html
    assert de.STRINGS["publish.confirm_draft_booth"] not in html


def test_the_event_page_has_the_same_button_next_to_its_badge(client):
    _admin(client)
    eid = _event(0, password="geheim")
    html = client.get(f"/admin/events/{eid}/edit").get_data(as_text=True)
    badge = html.index('<span class="badge badge--secondary">Entwurf</span>')
    button = html.index(f'action="/admin/events/{eid}/publish"')
    form = html.index('id="eventForm"')
    assert badge < button < form, "next to the badge, outside the big form (no nested form)"
    assert 'name="back" value="edit"' in html


def test_the_buttons_are_touch_sized_on_a_phone():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "admin.css").read_text(encoding="utf-8")
    phone = "".join(m.group(1) for m in re.finditer(r"@media \(max-width: 768px\) \{(.*?)\n\}", css, flags=re.S))
    rest = css.split("@media (max-width: 768px)")
    phone += "".join(rest[1:])            # the phone rules sit in the later phone blocks
    assert re.search(r"\.pub-btn \{[^}]*min-height: 44px", phone)
    assert re.search(r"\.event-list-actions \.pub-switch \{[^}]*flex: 1 1 100%", phone)
    assert not re.search(r"\.pub-btn \{[^}]*min-height: 44px", rest[0]), "a desk keeps its small buttons"


# ── what the switch does ─────────────────────────────────────────

def test_going_live_sets_published_and_status_notifies_once_and_shows_the_link(client, notified):
    _admin(client)
    eid = _event(0, password="geheim")
    r = _post(client, eid, True)
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/events")
    ev = db.get_event_by_id(eid)
    assert ev["published"] == 1 and ev["status"] == "live"
    msg = _flash(client)
    assert "„Demo Sommerfest“ ist live." in msg
    assert f'/gallery/{ev["slug"]}' in msg and "<a href=" in msg
    assert "Ohne Passwort" not in msg
    assert len(notified) == 1 and notified[0][1].endswith(f'/gallery/{ev["slug"]}')
    _post(client, eid, True)                                   # already live: no second notice
    assert len(notified) == 1
    assert "war schon live" in _flash(client)


def test_going_live_without_a_password_says_so_plainly_and_offers_the_field(client, notified):
    _admin(client)
    eid = _event(0)
    _post(client, eid, True, back="edit")
    assert db.get_event_by_id(eid)["published"] == 1
    msg = _flash(client)
    assert "Ohne Passwort: jeder mit dem Link sieht die Bilder." in msg
    assert f'/admin/events/{eid}/edit#password' in msg


def test_back_to_draft_and_back_to_the_page_it_came_from(client, notified):
    _admin(client)
    eid = _event(1)
    r = _post(client, eid, False, back="edit")
    assert r.headers["Location"].endswith(f"/admin/events/{eid}/edit")
    ev = db.get_event_by_id(eid)
    assert ev["published"] == 0 and ev["status"] == "draft"
    assert "ist wieder ein Entwurf" in _flash(client)
    assert notified == []


def test_a_start_date_and_an_expiry_still_hold_the_gallery_back_and_the_flash_says_it(client, notified):
    _admin(client)
    soon = _event(0, password="x", scheduled_live_at="2099-06-01T18:00")
    _post(client, soon, True)
    assert db.get_event_by_id(soon)["published"] == 1
    assert "Startet erst am 01.06.2099 18:00" in _flash(client)
    over = _event(0, password="x", gallery_json=json.dumps({"expires_at": "2020-01-01T00:00"}))
    _post(client, over, True)
    assert db.get_event_by_id(over)["published"] == 1
    assert "Abgelaufen seit 01.01.2020" in _flash(client)


def test_a_folder_of_a_draft_gallery_is_told_it_stays_closed(client, notified):
    _admin(client)
    parent = _event(0, password="x")
    from app import folders
    child = _event(0)
    folders.set_parent(child, parent)
    _post(client, child, True)
    assert "das noch ein Entwurf ist" in _flash(client)


def test_english_flash(client, notified):
    _admin(client, "en")
    eid = _event(0)
    _post(client, eid, True)
    msg = _flash(client)
    assert "“Demo Sommerfest” is live." in msg and "No password" in msg


def test_only_an_admin_with_the_form_token_can_switch(client, notified):
    eid = _event(0)
    r = _post(client, eid, True)                 # not logged in: refused (form token or login)
    assert r.status_code in (302, 400) and db.get_event_by_id(eid)["published"] == 0
    _admin(client)
    assert _post(client, eid, True, token="wrong").status_code == 400
    assert db.get_event_by_id(eid)["published"] == 0
    assert client.get(f"/admin/events/{eid}/publish").status_code == 405
    assert _post(client, 999999, True).status_code == 404


# ── the other ways in behave the same ────────────────────────────

def test_the_form_switch_moves_status_and_notifies_through_the_same_function(client, formular, notified):
    _admin(client)
    eid = _event(0, password="geheim")
    ev = db.get_event_by_id(eid)
    formular(f"/admin/events/{eid}/edit", {"name": ev["name"], "slug": ev["slug"], "published": "1",
                                           "password": "geheim", "retention_days": "30"})
    ev = db.get_event_by_id(eid)
    assert ev["published"] == 1 and ev["status"] == "live"
    assert len(notified) == 1
    formular(f"/admin/events/{eid}/edit", {"name": ev["name"], "slug": ev["slug"],
                                           "password": "geheim", "retention_days": "30"})
    ev = db.get_event_by_id(eid)
    assert ev["published"] == 0 and ev["status"] == "draft"


def test_a_new_event_with_the_switch_off_is_a_draft(client, formular):
    _admin(client)
    slug = "neu-" + secrets.token_hex(3)
    formular("/admin/events/new", {"name": "Demo Neu", "slug": slug, "retention_days": "30"})
    ev = db.get_event_by_slug(slug)
    assert ev["published"] == 0 and ev["status"] == "draft"


def test_mcp_configure_event_publishes_the_same_way(client, notified):
    from tests.test_mcp_visibility_needs_photos import PHOTOS, _data
    import json as _json
    from app import accounts, oauth
    ecosystem.init_schema()
    cid = "pbc_" + secrets.token_hex(6)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Claude", _json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    answer = oauth._issue(con, cid, secrets.token_hex(6), PHOTOS, "http://localhost/mcp", accounts.session_epoch())
    con.commit()
    con.close()
    eid = _event(0)

    def call(**arguments):
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": "configure_event", "arguments": arguments}},
                           headers={"Authorization": f"Bearer {answer['access_token']}"})
    out = _data(call(event_id=eid, published=True))
    ev = db.get_event_by_id(eid)
    assert ev["published"] == 1 and ev["status"] == "live"
    assert len(notified) == 1
    assert any("Ohne Passwort" in n for n in out["publish_notes"])
    _data(call(event_id=eid, published=False))
    ev = db.get_event_by_id(eid)
    assert ev["published"] == 0 and ev["status"] == "draft"
