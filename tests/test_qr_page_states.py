"""The QR page knows what is going on with the run behind it.

Until 11.11.2026 ``/p/<code>`` said "on its way" whenever nothing visible was
there — also when the event has moderation on and the picture is waiting for
approval, after a rejection and after the guest deleted it. And it polled
``status.json`` forever.

Four states now, on the page and in ``status.json``: pending (nothing
uploaded yet), ready, review (uploaded, waiting for approval), gone (rejected
or deleted). A rejection is never shown as a rejection. The page polls for at
most ten minutes and then offers a button to check again. The delete and the
status route obey the same silent-mode and expiry rules as the page.
"""
import io
import json
import secrets

from PIL import Image


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (600, 750), (200, 140, 30)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _box(client, **gallery):
    client.get("/healthz")
    from app import db, ecosystem, gallery_settings as gsm
    ecosystem.init_schema()
    slug = "qrst-" + secrets.token_hex(3)
    eid = db.create_event(name="QR-Zustand", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=1, gallery_json=? WHERE id=?",
                (json.dumps(gsm.clean(gallery)), eid))
    con.commit()
    con.close()
    _made = db.create_device(name="Box QR")
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo"]}, eid)
    return db.get_event_by_id(eid), {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _upload(client, dev, event, uid):
    return client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                       content_type="multipart/form-data",
                       data={"uid": uid, "session_uid": uid, "kind": "photo", "variant": "web",
                             "event_slug": event["slug"], "file": (_jpeg(), "r.jpg"), "meta": "{}"})


def _code(event, uid):
    from app import ecosystem
    return f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"


def _state(client, event, uid):
    return client.get(f"/p/{_code(event, uid)}/status.json").get_json()


def _page(client, event, uid):
    return client.get(f"/p/{_code(event, uid)}").get_data(as_text=True)


def test_pending_before_anything_arrived(client):
    event, _dev = _box(client)
    uid = "pend" + secrets.token_hex(4)
    assert _state(client, event, uid) == {"ok": True, "state": "pending", "ready": False}
    assert "unterwegs" in _page(client, event, uid)


def test_ready_once_it_is_visible(client):
    event, dev = _box(client)
    uid = "redy" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    assert _state(client, event, uid) == {"ok": True, "state": "ready", "ready": True}
    assert "Eure Aufnahmen" in _page(client, event, uid)


def test_review_while_the_organiser_has_not_approved_it(client):
    event, dev = _box(client, moderation=True)
    uid = "revw" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    assert _state(client, event, uid)["state"] == "review"
    page = _page(client, event, uid)
    assert "Eure Aufnahme ist angekommen — der Veranstalter gibt sie noch frei." in page
    assert "unterwegs" not in page


def test_gone_after_the_guest_deleted(client):
    event, dev = _box(client)
    uid = "gone" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    client.post(f"/p/{_code(event, uid)}/delete")
    assert _state(client, event, uid) == {"ok": True, "state": "gone", "ready": False}
    page = _page(client, event, uid)
    assert "Diese Aufnahmen sind nicht mehr verfügbar." in page
    assert "unterwegs" not in page and "status.json" not in page     # nothing left to wait for


def test_a_rejection_looks_like_any_other_deletion(client):
    event, dev = _box(client, moderation=True)
    uid = "rjqr" + secrets.token_hex(4)
    media_id = _upload(client, dev, event, uid).get_json()["id"]
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/events/{event['id']}/review/{media_id}/reject", data={"csrf_token": "x"})
    with client.session_transaction() as s:
        s.pop("admin_logged_in", None)
    state = _state(client, event, uid)
    assert state["state"] == "gone" and "reject" not in json.dumps(state)
    page = _page(client, event, uid)
    assert "Diese Aufnahmen sind nicht mehr verfügbar." in page
    assert "abgelehnt" not in page.lower() and "reject" not in page.lower()


def test_the_new_texts_exist_in_both_languages_and_can_be_overridden():
    from app import texts
    from app.i18n import de, en
    assert de.STRINGS["session.review_text"] == "Eure Aufnahme ist angekommen — der Veranstalter gibt sie noch frei."
    assert de.STRINGS["session.gone_text"] == "Diese Aufnahmen sind nicht mehr verfügbar."
    assert de.STRINGS["session.check_again"] == "Noch einmal nachsehen"
    for key in ("session.review_text", "session.gone_text", "session.check_again"):
        assert en.STRINGS[key]
    keys = {k for k, _label in texts.KEYS}
    assert {"session.review_text", "session.gone_text", "session.check_again"} <= keys


def test_the_page_stops_polling_after_ten_minutes(client):
    event, _dev = _box(client)
    page = _page(client, event, "poll" + secrets.token_hex(4))
    assert "600000" in page                       # ten minutes, in milliseconds
    assert "Noch einmal nachsehen" in page
    assert 'id="checkAgain"' in page and "hidden" in page.split('id="checkAgain"')[1][:80]


# ── The side routes follow the same rules as the page ──────────────────────

def test_silent_mode_closes_the_status_route_and_the_delete(client):
    event, dev = _box(client, silent=True)
    uid = "slnt" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    code = _code(event, uid)
    assert client.get(f"/p/{code}").status_code == 404
    assert client.get(f"/p/{code}/status.json").status_code == 404
    assert client.post(f"/p/{code}/delete").status_code == 404
    from app import ecosystem
    assert ecosystem.session_known_elsewhere(uid)      # nothing was deleted


def test_an_expired_gallery_closes_the_status_route_and_the_delete(client):
    event, dev = _box(client, expires_at="2020-01-01T00:00")
    uid = "expd" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    code = _code(event, uid)
    assert client.get(f"/p/{code}").status_code == 410
    status = client.get(f"/p/{code}/status.json")
    assert status.status_code == 410 and status.get_json()["ok"] is False
    assert client.post(f"/p/{code}/delete").status_code == 410
    from app import ecosystem
    assert ecosystem.session_known_elsewhere(uid)


def test_the_admin_still_gets_through_in_silent_mode(client):
    event, dev = _box(client, silent=True)
    uid = "admq" + secrets.token_hex(4)
    _upload(client, dev, event, uid)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    assert client.get(f"/p/{_code(event, uid)}/status.json").get_json()["state"] == "ready"
