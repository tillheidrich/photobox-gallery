"""Selections that stay: a logged-in client keeps named picks per gallery.

Without client access nothing changes (likes per browser, "Meine Auswahl"
by ids in the address). With it, picks live on the client: named
selections per gallery ("Album", "Abzüge"), add and remove from grid and
large view, an optional cap from the gallery settings. The photographer
reads them in the admin and exports the file names for Lightroom.
"""
import json
import re
import secrets

import pytest

from app import clients, db, folders, gallery_settings as gsm, picks


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    db.set_setting("silent_mode", "0")
    yield


def _event(password="geheim1234", **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Wahl {k}", slug=f"wahl-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
    if password:
        db.set_event_password(eid, password)
    return db.get_event_by_id(eid)


def _media(event, filename, hidden=0, review_state=None):
    con = db.get_db()
    cur = con.execute("INSERT INTO media(event_id, kind, filename, path_original, size_bytes, sha256, share_token,"
                      " hidden, review_state) VALUES (?,?,?,?,?,?,?,?,?)",
                      (event["id"], "photo", filename, f"storage/{event['slug']}/orig/{filename}", 1,
                       secrets.token_hex(4), secrets.token_hex(8), hidden, review_state))
    con.commit()
    con.close()
    return cur.lastrowid


def _login(client, *events):
    cid = clients.create("Paar " + secrets.token_hex(2))
    code = clients.new_code(cid)
    clients.set_events(cid, [e["id"] for e in events])
    page = client.get("/kunde").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)
    client.post("/kunde", data={"code": code, "csrf_token": token})
    return cid


def _api(client, event, path="", body=None):
    url = f"/kunde/api/{event['slug']}/selections{path}"
    return client.post(url, json=body) if body is not None else client.get(url)


def test_a_client_keeps_named_selections(client):
    ev = _event()
    a, b = _media(ev, "IMG_0001.CR3"), _media(ev, "IMG_0002.CR3")
    cid = _login(client, ev)
    sel = _api(client, ev, "", {"name": "Album"}).get_json()
    assert _api(client, ev, f"/{sel['id']}/items", {"media_id": a, "on": True}).get_json()["count"] == 1
    _api(client, ev, f"/{sel['id']}/items", {"media_id": b, "on": True})
    _api(client, ev, f"/{sel['id']}/items", {"media_id": a, "on": False})
    listed = _api(client, ev).get_json()
    assert listed["selections"][0]["name"] == "Album" and listed["selections"][0]["media_ids"] == [b]
    # stays after logout and a new login on another device
    assert picks.for_client(cid, ev["id"])[0]["media_ids"] == [b]


def test_the_cap_from_the_settings_holds(client):
    ev = _event(selection_max="1")
    a, b = _media(ev, "A.jpg"), _media(ev, "B.jpg")
    _login(client, ev)
    sel = _api(client, ev, "", {"name": "Abzüge"}).get_json()
    assert _api(client, ev, f"/{sel['id']}/items", {"media_id": a, "on": True}).status_code == 200
    r = _api(client, ev, f"/{sel['id']}/items", {"media_id": b, "on": True})
    assert r.status_code == 409 and r.get_json()["max"] == 1


def test_only_pictures_the_client_can_see_can_be_picked(client):
    ev, other = _event(), _event()
    hidden = _media(ev, "H.jpg", hidden=1, review_state="pending")
    foreign = _media(other, "F.jpg")
    _login(client, ev)
    sel = _api(client, ev, "", {"name": "Album"}).get_json()
    assert _api(client, ev, f"/{sel['id']}/items", {"media_id": hidden, "on": True}).status_code == 404
    assert _api(client, ev, f"/{sel['id']}/items", {"media_id": foreign, "on": True}).status_code == 404
    assert _api(client, other, "", {"name": "x"}).status_code == 403


def test_without_client_access_the_api_is_closed_and_the_gallery_unchanged(client):
    ev = _event(password="")
    _media(ev, "A.jpg")
    assert _api(client, ev).status_code == 403
    assert _api(client, ev, "", {"name": "x"}).status_code == 403
    page = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'id="clientPicks"' not in page and 'id="selectionBar"' in page


def test_the_api_wants_json(client):
    ev = _event()
    _login(client, ev)
    r = client.post(f"/kunde/api/{ev['slug']}/selections", data={"name": "x"})
    assert r.status_code == 415


def test_the_gallery_shows_the_picks_bar_to_a_client(client):
    ev = _event()
    _media(ev, "A.jpg")
    _login(client, ev)
    page = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'id="clientPicks"' in page and "/static/js/client-picks.js" in page
    assert 'id="lbPick"' in page


def test_a_client_of_the_parent_picks_in_its_folders(client):
    parent = _event()
    child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    m = _media(child, "P.jpg")
    _login(client, parent)
    sel = _api(client, child, "", {"name": "Album"}).get_json()
    assert _api(client, child, f"/{sel['id']}/items", {"media_id": m, "on": True}).status_code == 200


# ── Export for Lightroom ──────────────────────────────────────

def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
        s["admin_epoch"] = accounts.session_epoch()


def test_the_photographer_exports_the_file_names(client):
    ev = _event()
    a, b = _media(ev, "IMG_0001.CR3"), _media(ev, "DSC 7 (2).jpg")
    cid = _login(client, ev)
    sel = picks.create(cid, ev["id"], "Album")
    picks.toggle(sel, a, True)
    picks.toggle(sel, b, True)
    _admin(client)
    base = f"/admin/events/{ev['id']}/selections/{sel}"
    assert client.get(base + ".comma").get_data(as_text=True).strip() == "IMG_0001, DSC 7 (2)"
    assert client.get(base + ".or").get_data(as_text=True).strip() == "IMG_0001 OR DSC 7 (2)"
    txt = client.get(base + ".txt")
    assert txt.get_data(as_text=True).splitlines() == ["IMG_0001", "DSC 7 (2)"]
    assert "attachment" in txt.headers["Content-Disposition"]
    rows = client.get(base + ".csv").get_data(as_text=True).splitlines()
    assert rows[0].startswith("datei") and "IMG_0001.CR3" in rows[1]
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert "Album" in page and base + ".or" in page


def test_the_export_needs_the_admin_and_the_right_event(client):
    ev, other = _event(), _event()
    cid = _login(client, ev)
    sel = picks.create(cid, ev["id"], "Album")
    assert client.get(f"/admin/events/{ev['id']}/selections/{sel}.txt").status_code == 302
    _admin(client)
    assert client.get(f"/admin/events/{other['id']}/selections/{sel}.txt").status_code == 404


def test_mcp_reads_the_selections(client):
    from app import accounts, oauth
    ev = _event()
    m = _media(ev, "IMG_9.jpg")
    cid = _login(client, ev)
    picks.toggle(picks.create(cid, ev["id"], "Album"), m, True)
    oc = "pbc_" + secrets.token_hex(6)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (oc, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    token = oauth._issue(con, oc, secrets.token_hex(6), "read", "http://localhost/mcp", accounts.session_epoch())
    con.commit()
    con.close()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "list_selections", "arguments": {"event_id": ev["id"]}}},
                    headers={"Authorization": f"Bearer {token['access_token']}"})
    data = json.loads(r.get_json()["result"]["content"][0]["text"])
    assert data[0]["name"] == "Album" and data[0]["files"] == ["IMG_9"]


# ── One bar, and picking on the tile (phone) ──────────────────

def test_a_client_gets_one_bar_with_the_zip_as_an_action(client):
    ev = _event()
    _media(ev, "A.jpg")
    _login(client, ev)
    page = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'id="selectionBar"' not in page
    assert 'id="clientPicksZip"' in page
    assert page.count('class="pick-dot"') == 1


def test_no_zip_action_when_the_switches_forbid_it(client):
    ev = _event(watermark=True)
    _media(ev, "A.jpg")
    _login(client, ev)
    page = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'id="clientPicksZip"' not in page and 'id="clientPicks"' in page


def test_guests_keep_the_old_bar_and_get_no_dot(client):
    ev = _event(password="")
    _media(ev, "A.jpg")
    page = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'id="selectionBar"' in page and 'class="pick-dot"' not in page
