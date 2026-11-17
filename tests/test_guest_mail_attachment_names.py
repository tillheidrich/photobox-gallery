"""Mail attachments sent by the gallery for a box follow the event's file-name rule.

Rule ``original`` (default): the attachment keeps the stored file's name, as
before. Rule ``event_datetime``: the same name a download or a ZIP gives the
picture (``app.dateinamen.auslieferungsname``), shots of the same second
``_2``, ``_3``. The name is encoded in the MIME header by the standard
library (RFC 2231 when needed). The SMTP client is a fake: no mail leaves.
"""
import email
import email.policy
import io
import json
import secrets

import pytest
from PIL import Image

from tests.test_guest_mail import _FakeSMTP, postausgang  # noqa: F401 — the fake mailbox fixture


@pytest.fixture(autouse=True)
def schema(client):
    # Before the fake mailbox fixture: it writes a setting.
    client.get("/healthz")
    from app import db, ecosystem
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (400, 500), (190, 90, 60)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _box(client, rule):
    client.get("/healthz")
    from app import db, ecosystem, gallery_settings as gsm
    db.init_db()
    ecosystem.init_schema()
    slug = "mailname-" + secrets.token_hex(3)
    eid = db.create_event(name="Hochzeit Müller", slug=slug, upload_token=secrets.token_hex(8))
    gs = {"file_naming": rule} if rule else {}
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
    made = db.create_device(name="Box Namen")
    ecosystem.save_device_profile(made["id"], {"modes": ["photo"]}, eid)
    return db.get_event_by_id(eid), made["api_key"]


def _upload(client, key, event, uid, session_uid):
    r = client.post("/api/v2/booth/media", headers={"X-API-Key": key}, content_type="multipart/form-data",
                    data={"uid": uid, "session_uid": session_uid, "kind": "photo", "variant": "web",
                          "event_slug": event["slug"], "file": (_jpeg(), "result_web.jpg"),
                          "meta": json.dumps({"taken_at": "2026-09-20T20:00:00"})})
    assert r.status_code in (200, 201), r.get_data(as_text=True)


def _mail(client, postausgang, rule):
    event, key = _box(client, rule)
    run = "nam" + secrets.token_hex(4)
    _upload(client, key, event, run, run)
    _upload(client, key, event, run + ".f1", run)
    r = client.post("/api/v2/booth/mail", json={"to": "gast@example.de", "session_uid": run},
                    headers={"X-API-Key": key})
    assert r.status_code == 200 and r.get_json()["attached"] == 2, r.get_data(as_text=True)
    raw = postausgang.sent[-1].as_bytes()
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    return run, sorted(p.get_filename() for p in parsed.iter_attachments())


def test_event_datetime_names_the_attachments_like_a_download(client, postausgang):
    _, names = _mail(client, postausgang, "event_datetime")
    # The gallery attaches its web copy (WebP); the extension stays the file's own.
    assert names == ["Hochzeit-Mueller_2026-09-20_20-00-00.webp", "Hochzeit-Mueller_2026-09-20_20-00-00_2.webp"]


def test_original_keeps_the_stored_names(client, postausgang):
    from app import ecosystem
    run, names = _mail(client, postausgang, "")
    stored = sorted((m.get("path_webp") or m.get("path_original") or "").rsplit("/", 1)[-1]
                    for m in ecosystem.session_media(run) if m.get("variant") == "web")
    assert names == stored and all(stored)


def test_the_account_default_counts_when_the_event_has_no_rule(client, postausgang):
    from app import db
    db.set_setting("file_naming", "event_datetime")
    try:
        _, names = _mail(client, postausgang, "")
    finally:
        db.set_setting("file_naming", "")
    assert names[0] == "Hochzeit-Mueller_2026-09-20_20-00-00.webp"


def test_the_gallery_mailer_encodes_a_non_ascii_name_the_rfc_2231_way(client, postausgang):
    from app import emailer
    emailer.send_text_mail(to_email="gast@example.de", subject="s", body="b",
                           attachments=[("Grüße_2026.jpg", b"\xff\xd8\xff0", "image/jpeg")])
    raw = postausgang.sent[-1].as_bytes()
    assert b"filename*=utf-8''Gr%C3%BC%C3%9Fe_2026.jpg" in raw
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    assert [p.get_filename() for p in parsed.iter_attachments()] == ["Grüße_2026.jpg"]
