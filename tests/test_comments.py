"""Comments on a picture and on a gallery - from logged-in clients only.

Rules (page 4, section 17): off by default (gallery setting ``comments``);
written only by a client logged in with their code, under the client's name,
no anonymous comments, no e-mail addresses; plain text, length-capped,
rate-limited; the photographer answers and deletes in the admin; a tile with
comments carries a small mark; the operator is told through the notifier,
without the text.
"""
import json
import re
import secrets

import pytest

from app import clients, comments, db, gallery_settings as gsm, notifier


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    db.set_setting("silent_mode", "0")
    yield


def _event(**gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Komm {k}", slug=f"km-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
    db.set_event_password(eid, "geheim1234")
    return db.get_event_by_id(eid)


def _media(event):
    con = db.get_db()
    cur = con.execute("INSERT INTO media(event_id, kind, filename, path_original, size_bytes, sha256, share_token)"
                      " VALUES (?,?,?,?,?,?,?)", (event["id"], "photo", "IMG_1.jpg", "x/IMG_1.jpg", 1,
                                                  secrets.token_hex(4), secrets.token_hex(8)))
    con.commit()
    con.close()
    return cur.lastrowid


def _login(client, *events, name="Familie Krause"):
    cid = clients.create(name)
    code = clients.new_code(cid)
    clients.set_events(cid, [e["id"] for e in events])
    page = client.get("/kunde").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)
    client.post("/kunde", data={"code": code, "csrf_token": token})
    return cid


def _post(client, ev, body, media_id=None):
    payload = {"body": body}
    if media_id:
        payload["media_id"] = media_id
    return client.post(f"/kunde/api/{ev['slug']}/comments", json=payload)


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
        s["admin_epoch"] = accounts.session_epoch()


def test_comments_are_off_by_default(client):
    ev = _event()
    _login(client, ev)
    assert gsm.DEFAULTS["comments"] is False
    assert _post(client, ev, "Schön!").status_code == 403


def test_a_client_comments_a_picture_and_the_gallery_under_their_name(client, monkeypatch):
    sent = []
    monkeypatch.setattr(notifier, "notify_comment", lambda **kw: sent.append(kw))
    ev = _event(comments=True)
    m = _media(ev)
    _login(client, ev)
    r = _post(client, ev, "Bitte <b>heller</b>", media_id=m)
    assert r.status_code == 200
    _post(client, ev, "Danke für alles")
    rows = comments.for_event(ev["id"])
    assert {c["author"] for c in rows} == {"Familie Krause"}
    assert sent and "heller" not in json.dumps(sent)
    page = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert "<b>heller</b>" not in page
    assert 'class="photo-comment-mark"' in page


def test_no_comment_without_client_access(client):
    ev = _event(comments=True)
    assert _post(client, ev, "anonym").status_code == 403


def test_length_and_brake(client):
    ev = _event(comments=True)
    _login(client, ev)
    assert _post(client, ev, "").status_code == 400
    assert _post(client, ev, "x" * (comments.BODY_MAX + 1)).status_code == 400
    codes = [_post(client, ev, f"Nr {i}").status_code for i in range(comments.BRAKE_LIMIT + 2)]
    assert codes[-1] == 429


def test_the_photographer_answers_and_deletes(client):
    ev = _event(comments=True)
    m = _media(ev)
    _login(client, ev)
    _post(client, ev, "Zu dunkel?", media_id=m)
    cid = comments.for_event(ev["id"])[0]["id"]
    _admin(client)
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert "Zu dunkel?" in page and 'id="kommentare"' in page
    client.post(f"/admin/events/{ev['id']}/comments/{cid}/reply", data={"csrf_token": "test-token",
                                                                         "body": "Mache ich heller."})
    thread = client.get(f"/kunde/api/{ev['slug']}/comments").get_json()["comments"]
    assert any(c["from_operator"] and c["body"] == "Mache ich heller." for c in thread)
    client.post(f"/admin/events/{ev['id']}/comments/{cid}/delete", data={"csrf_token": "test-token"})
    assert not [c for c in comments.for_event(ev["id"]) if c["id"] == cid]


def test_a_client_sees_only_their_own_threads(client):
    ev = _event(comments=True)
    _login(client, ev, name="A")
    _post(client, ev, "von A")
    client.post("/kunde/abmelden", data={"csrf_token": re.search(
        r'name="csrf_token" value="([^"]*)"', client.get("/kunde").get_data(as_text=True)).group(1)})
    _login(client, ev, name="B")
    thread = client.get(f"/kunde/api/{ev['slug']}/comments").get_json()["comments"]
    assert all(c["body"] != "von A" for c in thread)
