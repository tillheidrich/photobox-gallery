"""Sub-galleries: a gallery can hold folders, one level deep.

Rules (page 4, section 13): a sub-gallery inherits every setting it does not
set itself, from one place (``gallery_settings.for_event``); whoever may open
the parent (password, link key, client access) may open its sub-galleries; a
sub-gallery can be stricter, never looser; two levels, no cycles.
"""
import io
import json
import re
import secrets
import zipfile

import pytest
from PIL import Image

from app import clients, db, folders, gallery_settings as gsm, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    db.set_setting("silent_mode", "0")
    yield


def _event(password="", published=1, name=None, **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=name or f"Ober {k}", slug=f"ob-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": published, "gallery_json": json.dumps(gsm.clean(gs))})
    if password:
        db.set_event_password(eid, password)
    return db.get_event_by_id(eid)


def _photo(event):
    d = media.event_storage_dir(event["slug"])
    stem = secrets.token_hex(5)
    orig, webp, thumb = d / "orig" / f"{stem}.jpg", d / "webp" / f"{stem}.webp", d / "thumb" / f"{stem}.webp"
    Image.new("RGB", (40, 30)).save(orig, "JPEG")
    Image.new("RGB", (40, 30)).save(webp, "WEBP")
    Image.new("RGB", (20, 15)).save(thumb, "WEBP")
    rel = lambda p: p.relative_to(media.data_dir()).as_posix()  # noqa: E731
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb, size_bytes, sha256,"
        " share_token) VALUES (?,?,?,?,?,?,?,?,?)",
        (event["id"], "photo", f"IMG_{stem}.jpg", rel(orig), rel(webp), rel(thumb), 1, stem, secrets.token_hex(8)))
    con.commit()
    con.close()
    return {"id": cur.lastrowid, "thumb": "/data/" + rel(thumb)}


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
        s["admin_epoch"] = accounts.session_epoch()


# ── Structure ─────────────────────────────────────────────────

def test_a_folder_is_created_under_a_gallery(client):
    parent = _event()
    cid = folders.create_child(parent["id"], "Trauung")
    child = db.get_event_by_id(cid)
    assert child["parent_id"] == parent["id"]
    assert child["slug"].startswith(parent["slug"])
    assert [c["id"] for c in folders.children_of(parent["id"])] == [cid]


def test_two_levels_and_no_cycles(client):
    a, b = _event(), _event()
    child = folders.create_child(a["id"], "Feier")
    with pytest.raises(ValueError):
        folders.set_parent(a["id"], a["id"])
    with pytest.raises(ValueError):
        folders.create_child(child, "Zu tief")          # a folder in a folder
    with pytest.raises(ValueError):
        folders.set_parent(a["id"], b["id"])            # a has folders itself
    with pytest.raises(ValueError):
        folders.set_parent(b["id"], child)              # under a folder
    folders.set_parent(child, b["id"])                  # moving is fine
    assert db.get_event_by_id(child)["parent_id"] == b["id"]
    folders.set_parent(child, None)
    assert db.get_event_by_id(child)["parent_id"] is None


# ── Settings inherit from one place ───────────────────────────

def test_a_folder_inherits_what_it_does_not_set(client):
    parent = _event(layout="grid", likes=False, download_zip=True)
    child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    gs = gsm.for_event(child)
    assert gs["layout"] == "grid" and gs["likes"] is False and gs["download_zip"] is True
    db.update_event(child["id"], gallery_json=gsm.store(child, {**gs, "layout": "list"}))
    child = db.get_event_by_id(child["id"])
    assert json.loads(child["gallery_json"]) == {"layout": "list"}
    db.update_event(parent["id"], gallery_json=json.dumps(gsm.clean({"likes": True, "layout": "grid"})))
    gs = gsm.for_event(child)
    assert gs["layout"] == "list" and gs["likes"] is True


def test_a_folder_is_never_looser_than_its_parent(client):
    parent = _event(silent=True, expires_at="2030-01-01T00:00", moderation=True)
    child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    db.update_event(child["id"], gallery_json=json.dumps(
        {"silent": False, "expires_at": "2040-01-01T00:00", "moderation": False}))
    gs = gsm.for_event(db.get_event_by_id(child["id"]))
    assert gs["silent"] is True and gs["expires_at"] == "2030-01-01T00:00" and gs["moderation"] is True


def test_uploads_into_a_folder_of_a_moderated_gallery_wait(client):
    parent = _event(moderation=True)
    child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    p = _photo(child)
    assert db.get_media(p["id"])["review_state"] == "pending"


# ── Access ────────────────────────────────────────────────────

def test_the_parent_password_opens_its_folders(client):
    parent = _event(password="geheim1234")
    child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    p = _photo(child)
    r = client.get(f"/gallery/{child['slug']}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/gallery/{parent['slug']}")
    assert client.get(p["thumb"]).status_code == 403
    page = client.get(f"/gallery/{parent['slug']}").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)
    client.post(f"/gallery/{parent['slug']}", data={"password": "geheim1234", "csrf_token": token})
    assert client.get(f"/gallery/{child['slug']}").status_code == 200
    assert client.get(p["thumb"]).status_code == 200


def test_a_folder_with_its_own_password_stays_closed(client):
    parent = _event(password="geheim1234")
    child_id = folders.create_child(parent["id"], "Privat")
    db.set_event_password(child_id, "anders5678")
    child = db.get_event_by_id(child_id)
    cid = clients.create("Paar")
    code = clients.new_code(cid)
    clients.set_events(cid, [parent["id"]])
    page = client.get("/kunde").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)
    client.post("/kunde", data={"code": code, "csrf_token": token})
    assert client.get(f"/gallery/{parent['slug']}").status_code == 200
    assert 'name="password"' in client.get(f"/gallery/{child['slug']}").get_data(as_text=True)


def test_the_client_code_of_the_parent_opens_the_folders(client):
    parent = _event(password="geheim1234")
    child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    cid = clients.create("Firma")
    code = clients.new_code(cid)
    clients.set_events(cid, [parent["id"]])
    page = client.get("/kunde").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)
    client.post("/kunde", data={"code": code, "csrf_token": token})
    assert client.get(f"/gallery/{child['slug']}").status_code == 200


def test_an_unpublished_parent_closes_its_folders(client):
    parent = _event(published=0)
    child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    assert child["published"]
    assert client.get(f"/gallery/{child['slug']}").status_code == 404


# ── The page ──────────────────────────────────────────────────

def test_the_parent_shows_its_folders_as_tiles_and_the_folder_a_breadcrumb(client):
    parent = _event(name="Hochzeit Meier")
    child = db.get_event_by_id(folders.create_child(parent["id"], "Trauung"))
    _photo(child)
    page = client.get(f"/gallery/{parent['slug']}").get_data(as_text=True)
    assert 'class="subgallery-grid"' in page and f'href="/gallery/{child["slug"]}"' in page
    sub = client.get(f"/gallery/{child['slug']}").get_data(as_text=True)
    crumbs = re.search(r'<nav class="gallery-crumbs".*?</nav>', sub, flags=re.S).group(0)
    assert f'href="/gallery/{parent["slug"]}"' in crumbs and "Hochzeit Meier" in crumbs


def test_folders_are_not_on_the_homepage(client):
    db.set_setting("homepage_show_galleries", "1")
    parent = _event(name="Startseite Ober")
    _photo(parent)
    _photo(db.get_event_by_id(folders.create_child(parent["id"], "Startseite Unter")))
    page = client.get("/").get_data(as_text=True)
    assert "Startseite Ober" in page and "Startseite Unter" not in page


# ── ZIP ───────────────────────────────────────────────────────

def test_the_parent_zip_takes_folders_along_only_on_request_and_when_allowed(client):
    parent = _event(download_zip=True)
    _photo(parent)
    open_child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    _photo(open_child)
    closed_child = db.get_event_by_id(folders.create_child(parent["id"], "Intern"))
    db.update_event(closed_child["id"], gallery_json=json.dumps({"download_zip": False}))
    _photo(closed_child)
    names = zipfile.ZipFile(io.BytesIO(client.get(f"/gallery/{parent['slug']}/download.zip").data)).namelist()
    assert len(names) == 1
    names = zipfile.ZipFile(io.BytesIO(
        client.get(f"/gallery/{parent['slug']}/download.zip?folders=1").data)).namelist()
    assert len(names) == 2 and any(n.startswith("Party/") for n in names)
    assert not any(n.startswith("Intern/") for n in names)


# ── Admin ─────────────────────────────────────────────────────

def test_the_admin_creates_and_moves_folders(client):
    _admin(client)
    parent, other = _event(), _event()
    r = client.post(f"/admin/events/{parent['id']}/folders", data={"csrf_token": "test-token", "name": "Empfang"})
    assert r.status_code == 302
    child = folders.children_of(parent["id"])[0]
    r = client.post(f"/admin/events/{child['id']}/parent", data={"csrf_token": "test-token",
                                                                 "parent_id": str(other["id"])})
    assert r.status_code == 302 and db.get_event_by_id(child["id"])["parent_id"] == other["id"]
    r = client.post(f"/admin/events/{other['id']}/parent", data={"csrf_token": "test-token",
                                                                 "parent_id": str(parent["id"])})
    assert db.get_event_by_id(other["id"])["parent_id"] is None      # refused: other has a folder
    assert "Empfang" in client.get(f"/admin/events/{other['id']}/edit").get_data(as_text=True)


def test_the_folder_settings_form_stores_only_what_differs(client):
    _admin(client)
    parent = _event(layout="grid")
    child = db.get_event_by_id(folders.create_child(parent["id"], "Party"))
    form = {k: ("on" if v is True else v) for k, v in gsm.for_event(child).items() if v not in (False, "")}
    form["thumb_size"] = "l"
    form["csrf_token"] = "test-token"
    client.post(f"/admin/events/{child['id']}/gallery-settings", data=form)
    assert json.loads(db.get_event_by_id(child["id"])["gallery_json"]) == {"thumb_size": "l"}


# ── MCP ───────────────────────────────────────────────────────

def test_mcp_creates_a_folder(client):
    from app import accounts, oauth
    parent = _event()
    cid = "pbc_" + secrets.token_hex(6)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    token = oauth._issue(con, cid, secrets.token_hex(6), "read manage", "http://localhost/mcp", accounts.session_epoch())
    con.commit()
    con.close()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "create_subgallery",
                                             "arguments": {"parent_id": parent["id"], "name": "Sektempfang"}}},
                    headers={"Authorization": f"Bearer {token['access_token']}"})
    data = json.loads(r.get_json()["result"]["content"][0]["text"])
    assert db.get_event_by_id(data["id"])["parent_id"] == parent["id"]
