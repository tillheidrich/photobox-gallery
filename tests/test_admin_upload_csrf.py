"""Security review G3, finding S-10 (15.11.2026): the admin upload needs the form token.

`/api/admin-upload` sits outside the admin blueprint, so the blueprint's
CSRF guard never ran for it: any page the logged-in admin visited could post
pictures into any event. It now checks the same token as every admin form.
"""
import io
import secrets

from PIL import Image


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (10, 120, 200)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _event():
    from app import db
    return db.create_event(name="CSRF", slug="csrf-" + secrets.token_hex(3), upload_token=secrets.token_hex(6))


def _admin(client):
    from app import accounts
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "tok"


def test_without_the_token_nothing_is_uploaded(client):
    client.get("/healthz")
    eid = _event()
    _admin(client)
    r = client.post("/api/admin-upload", data={"event_id": str(eid), "file": (_jpeg(), "a.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    r = client.post("/api/admin-upload", data={"event_id": str(eid), "file": (_jpeg(), "a.jpg"),
                                                "csrf_token": "falsch"}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_with_the_token_it_works_as_before(client):
    client.get("/healthz")
    eid = _event()
    _admin(client)
    r = client.post("/api/admin-upload", data={"event_id": str(eid), "file": (_jpeg(), "a.jpg")},
                    headers={"X-CSRF-Token": "tok"}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["ok"]


def test_the_event_page_sends_the_token():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "app" / "templates" / "admin" / "events" / "edit.html").read_text()
    i = page.index("/api/admin-upload")
    assert "X-CSRF-Token" in page[i - 300:i + 300]
