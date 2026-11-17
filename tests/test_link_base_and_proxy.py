"""Security review G3, finding S-5 (15.11.2026): proxy trust and links from the Host header.

* ``TRUST_PROXY`` is an explicit opt-in. Trusting ``X-Forwarded-For`` on a
  container that is reachable directly lets anyone pick the address the
  login brakes count.
* Links that leave the gallery (password reset mail, pairing codes) are built
  from the configured base address. Without one, the Host header is used only
  when it is trusted (loopback or listed in ``ALLOWED_HOSTS``); otherwise the
  gallery refuses instead of mailing a link to whatever host the request named.
* The default compose file publishes nothing on all interfaces.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIL = "evil.example"


@pytest.fixture()
def no_base(monkeypatch):
    from app import db
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    old = db.get_setting("app_base_url")
    db.set_setting("app_base_url", "")
    yield
    db.set_setting("app_base_url", old or "")


def test_trust_proxy_is_off_unless_switched_on(monkeypatch):
    from werkzeug.middleware.proxy_fix import ProxyFix

    from app import create_app
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    assert not isinstance(create_app().wsgi_app, ProxyFix)
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert isinstance(create_app().wsgi_app, ProxyFix)


def test_a_forged_forwarded_for_does_not_change_the_address_by_default(monkeypatch):
    from app import create_app
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    app = create_app()
    seen = {}

    @app.get("/_ip_probe")
    def _probe():
        from flask import request
        seen["ip"] = request.remote_addr
        return "ok"

    app.test_client().get("/_ip_probe", headers={"X-Forwarded-For": "203.0.113.9"})
    assert seen["ip"] != "203.0.113.9"


def test_link_base_fails_closed_for_an_untrusted_host(client, no_base, monkeypatch):
    from app import create_app, db
    app = create_app()
    with app.test_request_context("/", base_url=f"https://{EVIL}"):
        assert db.link_base() == ""
    with app.test_request_context("/", base_url="http://localhost:5000"):
        assert db.link_base() == "http://localhost:5000"
    monkeypatch.setenv("ALLOWED_HOSTS", "galerie.example.de, fotos.example.de")
    with app.test_request_context("/", base_url="https://fotos.example.de"):
        assert db.link_base() == "https://fotos.example.de"
    monkeypatch.setenv("BASE_URL", "https://galerie.example.de")
    with app.test_request_context("/", base_url=f"https://{EVIL}"):
        assert db.link_base() == "https://galerie.example.de"


def test_no_reset_mail_with_a_link_to_a_foreign_host(client, no_base, monkeypatch):
    from app import accounts, db, emailer
    sent = []
    monkeypatch.setattr(accounts, "mail_reset_possible", lambda: True)
    monkeypatch.setattr(emailer, "send_text_mail", lambda **kw: sent.append(kw))
    db.set_setting("admin_email", "admin@example.de")
    try:
        with client.session_transaction(base_url=f"https://{EVIL}") as s:
            s["csrf_token"] = "t"
        client.post("/admin/recover", data={"csrf_token": "t", "send_link": "1"},
                    base_url=f"https://{EVIL}")
        assert sent == [], "a reset link must not be built from the request's host"
        monkeypatch.setenv("BASE_URL", "https://galerie.example.de")
        client.post("/admin/recover", data={"csrf_token": "t", "send_link": "1"},
                    base_url=f"https://{EVIL}")
        assert len(sent) == 1 and "https://galerie.example.de/admin/recover?token=" in sent[0]["body"]
        assert EVIL not in sent[0]["body"]
    finally:
        db.set_setting("admin_email", "")


def test_pairing_links_never_name_a_foreign_host(client, no_base):
    import hashlib
    import secrets
    from app.blueprints import booth_v2
    booth_v2._PAIR_OPENS.clear()
    client.get("/healthz")
    r = client.post("/api/v2/pair/request", base_url=f"https://{EVIL}",
                    json={"verifier_hash": hashlib.sha256(b"x").hexdigest(), "booth_name": "Box",
                          "install_id": secrets.token_hex(4)})
    assert EVIL not in r.get_data(as_text=True)
    assert r.status_code == 503
    booth_v2._PAIR_OPENS.clear()


def test_the_admin_pairing_code_needs_a_trusted_address(client, no_base):
    from app import accounts, db, ecosystem
    client.get("/healthz")
    ecosystem.init_schema()
    dev = db.create_device(name="Box Basis")
    with client.session_transaction(base_url=f"https://{EVIL}") as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "t"
    r = client.post(f"/admin/devices/{dev['id']}/pairing", headers={"X-CSRF-Token": "t"},
                    base_url=f"https://{EVIL}")
    assert r.status_code == 409 and EVIL not in r.get_data(as_text=True)


def test_the_default_compose_file_publishes_nothing_to_the_world():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    ports = re.findall(r'^\s*-\s*"?([^"\n]+:5000)"?\s*$', text, re.M)
    for p in ports:
        assert p.startswith("127.0.0.1:"), p
    assert "TRUST_PROXY=${TRUST_PROXY:-0}" in text
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "TRUST_PROXY" in env and "ALLOWED_HOSTS" in env


def test_the_env_template_does_not_trust_a_proxy_and_the_nas_port_is_a_choice():
    """Second review R2-U6: TRUST_PROXY=1 in the template next to a port published in the LAN."""
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    active = re.findall(r"^TRUST_PROXY=(\S*)", env, re.M)
    assert active == ["0"]
    nas = (ROOT / "docker-compose.nas.yml").read_text(encoding="utf-8")
    assert '"${BIND_ADDR:-0.0.0.0}:${PORT:-5000}:5000"' in nas
    assert "TRUST_PROXY=${TRUST_PROXY:-0}" in nas
    doc = (ROOT / "docs" / "INSTALL-NAS.md").read_text(encoding="utf-8")
    assert "BIND_ADDR=127.0.0.1" in doc and "TRUST_PROXY=0" in doc
