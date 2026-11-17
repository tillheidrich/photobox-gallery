"""Every answer of the gallery carries the browser-side protections.

Security review of 11.11.2026, findings S6 and S9: no answer set nosniff, a
referrer policy, frame protection or a content security policy, and
password-protected pictures went out as ``Cache-Control: public``.
"""
import io
import json
import secrets

import pytest
from PIL import Image

from app import db, ecosystem, gallery_settings as gsm, media


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _event(passwort="", **gs):
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Hdr {k}", slug=f"hdr-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
    if passwort:
        db.set_event_password(eid, passwort)
    return db.get_event_by_id(eid)


def _thumb(event):
    pfad = media.event_storage_dir(event["slug"]) / "thumb" / "a.webp"
    Image.new("RGB", (20, 20)).save(pfad, "WEBP")
    return f"/data/storage/{event['slug']}/thumb/a.webp"


@pytest.mark.parametrize("pfad", ["/", "/healthz", "/admin/login", "/datenschutz"])
def test_every_answer_has_the_basic_headers(client, pfad):
    r = client.get(pfad)
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    csp = r.headers.get("Content-Security-Policy", "")
    for directive in ("default-src 'self'", "object-src 'none'", "base-uri 'self'",
                      "frame-ancestors 'self'", "form-action 'self'"):
        assert directive in csp, (directive, csp)


def test_the_gallery_page_has_them_too(client):
    event = _event()
    r = client.get(f"/gallery/{event['slug']}")
    assert r.status_code == 200
    assert "frame-ancestors 'self'" in r.headers.get("Content-Security-Policy", "")


def test_the_embed_mode_may_be_framed(client):
    """``?embed=1`` exists to put a gallery into the operator's own website —
    when the event allows it (final review A11, tests/test_embedding_needs_the_switch.py)."""
    event = _event(embed=True)
    r = client.get(f"/gallery/{event['slug']}?embed=1")
    assert r.status_code == 200
    assert "X-Frame-Options" not in r.headers
    assert "frame-ancestors 'self'" not in r.headers.get("Content-Security-Policy", "")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_the_analytics_script_is_allowed_when_configured(client):
    db.set_setting("umami_src", "https://stats.example.org/script.js")
    db.set_setting("umami_website_id", "abc")
    csp = client.get("/").headers.get("Content-Security-Policy", "")
    script = next(p for p in csp.split(";") if p.strip().startswith("script-src"))
    connect = next(p for p in csp.split(";") if p.strip().startswith("connect-src"))
    assert "https://stats.example.org" in script and "https://stats.example.org" in connect


def test_protected_pictures_are_not_publicly_cacheable(client):
    event = _event(passwort="geheim-123")
    url = _thumb(event)
    with client.session_transaction() as s:
        s[f"gallery_access_{event['id']}"] = True
    r = client.get(url)
    assert r.status_code == 200
    cc = r.headers.get("Cache-Control", "")
    assert "public" not in cc and "private" in cc


def test_open_pictures_stay_cacheable(client):
    event = _event()
    r = client.get(_thumb(event))
    assert r.status_code == 200
    assert "public" in r.headers.get("Cache-Control", "")
