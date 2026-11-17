"""MCP additions the owner asked for (15.11.2026): legal texts (scope
``secure``, logged) and guests' access requests (``manage``; deciding also ``photos``)."""
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
    yield


def test_the_tools_carry_the_scopes_asked_for():
    from app.mcp import TOOLS
    import app.mcp_more  # noqa: F401 — registers the tools
    assert TOOLS["set_legal_texts"]["scope"] == "secure"
    assert TOOLS["get_legal_texts"]["scope"] == "read"
    assert TOOLS["list_access_requests"]["scope"] == "manage"
    assert TOOLS["decide_access_request"]["scope"] == "manage"
    assert TOOLS["decide_access_request"]["also"] == ("photos",)   # final review A2


def test_legal_texts_are_set_and_logged(client):
    from flask import g
    from app import mcp_more
    with client.application.test_request_context("/mcp"):
        out = mcp_more._set_legal_texts(impressum_text="Muster GmbH\nMusterweg 1",
                                        facts={"legal_contact_email": "info@example.org", "legal_role": "chef"})
        assert out["changed"] == ["impressum_text", "legal_contact_email", "legal_role"]
        assert "Rechtstexte" in g._protokoll_ergebnis
        assert db.get_setting("impressum_text").startswith("Muster GmbH")
        assert db.get_setting("legal_role") == "controller"
        assert mcp_more._get_legal_texts()["legal_contact_email"] == "info@example.org"
        with pytest.raises(ValueError):
            mcp_more._set_legal_texts(facts={"admin_password": "x"})
        with pytest.raises(ValueError):
            mcp_more._set_legal_texts(geheim="x")
        db.set_setting("legal_contact_email", "")
        db.set_setting("impressum_text", "")


def test_every_legal_field_is_reachable_by_get_and_set(client):
    from flask import g
    from app import legal, mcp_more
    with client.application.test_request_context("/mcp"):
        facts = {k: f"Wert {k}" for k in legal.LEGAL_FIELDS if k != "legal_role"}
        assert {"legal_address", "legal_phone", "legal_register", "legal_tax", "legal_editorial",
                "legal_dispute", "legal_server_logs", "legal_assistant"} <= set(facts)
        old = {k: db.get_setting(k) or "" for k in legal.LEGAL_FIELDS}
        try:
            out = mcp_more._set_legal_texts(facts=facts)
            assert set(out["changed"]) == set(facts)
            got = mcp_more._get_legal_texts()
            assert all(got[k] == v for k, v in facts.items())
            assert set(legal.LEGAL_FIELDS) <= set(got)
        finally:
            for k, v in old.items():
                db.set_setting(k, v)


def _gated_event():
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Zugang {k}", slug=f"zg-{k}", upload_token=f"t-{k}")
    db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean({"access_request": True}))})
    db.set_event_password(eid, "geheim-123")
    return db.get_event_by_id(eid)


def test_access_requests_are_listed_with_a_shortened_address(client):
    from app import access, mcp_more
    with client.application.test_request_context("/mcp"):
        ev = _gated_event()
        access.create(ev, email="gast@example.org", name="Gast", note="Tisch 4")
        listed = mcp_more._list_access_requests(ev["id"])["requests"]
        assert listed[0]["email"] == "g***@example.org" and listed[0]["note"] == "Tisch 4"


def test_granting_sends_a_link_that_opens_without_the_password(client, monkeypatch):
    from app import access, mcp_more
    sent = {}
    monkeypatch.setattr(access, "grant", lambda event, req, url: sent.update(url=url) or True)
    with client.application.test_request_context("/mcp"):
        ev = _gated_event()
        req = access.create(ev, email="gast@example.org")
        out = mcp_more._decide_access_request(ev["id"], req["id"], "grant")
    assert out["mail_sent"] is True and "access_token=" in sent["url"]


def test_denying_closes_the_request_and_a_second_decision_is_refused(client):
    from app import access, mcp_more
    with client.application.test_request_context("/mcp"):
        ev = _gated_event()
        req = access.create(ev, email="zwei@example.org")
        mcp_more._decide_access_request(ev["id"], req["id"], "deny")
        assert access.get(req["id"], ev["id"])["state"] == access.DENIED
        with pytest.raises(ValueError):
            mcp_more._decide_access_request(ev["id"], req["id"], "grant")


def test_a_request_of_another_event_is_not_found(client):
    from app import access, mcp_more
    with client.application.test_request_context("/mcp"):
        a, b = _gated_event(), _gated_event()
        req = access.create(a, email="drei@example.org")
        with pytest.raises(ValueError):
            mcp_more._decide_access_request(b["id"], req["id"], "deny")
