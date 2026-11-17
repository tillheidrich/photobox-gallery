"""Look & feel (no. 66): design presets, embed code with an allow-list of
embedding origins per event, and a trust line in the gallery footer."""
import json
import secrets

import pytest

from app import db, ecosystem, gallery_settings as gsm


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("gallery_trust_line", "")
    yield


def _event(client, **gs):
    k = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"Look {k}", slug=f"look-{k}", upload_token=f"t-{k}")
        db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs))})
        return db.get_event_by_id(eid)


def _login(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "t"


# ── Presets ───────────────────────────────────────────────────────────

def test_every_preset_uses_only_settings_the_theme_system_has():
    assert set(gsm.DESIGN_PRESETS) == {"hell", "dunkel", "warm", "schlicht"}
    for values in gsm.DESIGN_PRESETS.values():
        cleaned = gsm.clean(values)
        assert {k: cleaned[k] for k in values} == values


def test_the_admin_form_offers_the_presets(client):
    ev = _event(client)
    _login(client)
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    for key in gsm.DESIGN_PRESETS:
        assert f'data-design-preset="{key}"' in page


# ── Embedding ─────────────────────────────────────────────────────────

def test_switched_on_without_a_list_any_site_may_embed(client):
    # Since the final review (A11) embedding needs the event's switch "embed";
    # without it see tests/test_embedding_needs_the_switch.py.
    ev = _event(client, embed=True)
    embedded = client.get(f"/gallery/{ev['slug']}?embed=1")
    assert "frame-ancestors *" in embedded.headers["Content-Security-Policy"]
    page = client.get(f"/gallery/{ev['slug']}")
    assert "frame-ancestors 'self'" in page.headers["Content-Security-Policy"]
    assert page.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_with_a_list_only_those_origins_may_embed(client):
    ev = _event(client, embed=True, embed_origins="https://example.org https://www.example.org/pfad")
    embedded = client.get(f"/gallery/{ev['slug']}?embed=1")
    csp = embedded.headers["Content-Security-Policy"]
    assert "frame-ancestors 'self' https://example.org https://www.example.org" in csp
    assert "frame-ancestors *" not in csp
    # The normal gallery page stays closed to framing.
    page = client.get(f"/gallery/{ev['slug']}")
    assert page.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_only_web_origins_go_on_the_list():
    cleaned = gsm.clean({"embed_origins": "javascript:alert(1) ftp://a.b https://ok.example *  http://x.example"})
    assert cleaned["embed_origins"] == "https://ok.example http://x.example"


def test_the_admin_shows_an_embed_snippet(client):
    ev = _event(client)
    _login(client)
    page = client.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert f"/gallery/{ev['slug']}?embed=1" in page and "&lt;iframe" in page


# ── Trust line ────────────────────────────────────────────────────────

def test_no_trust_line_by_default(client):
    ev = _event(client)
    assert 'class="g-trust"' not in client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)


def test_the_trust_line_is_shown_and_escaped(client):
    _login(client)
    client.post("/admin/settings/galerie", data={"csrf_token": "t",
                                                  "gallery_trust_line": "Server in Deutschland <b>sicher</b>"})
    ev = _event(client)
    client.get("/admin/logout")
    with client.session_transaction() as s:
        s.clear()
    page = client.get(f"/gallery/{ev['slug']}").get_data(as_text=True)
    assert 'class="g-trust"' in page
    assert "Server in Deutschland &lt;b&gt;sicher&lt;/b&gt;" in page
    with client.application.app_context():
        db.set_setting("gallery_trust_line", "")
