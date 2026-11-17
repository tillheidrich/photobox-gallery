"""Security review G3, finding S-11 (15.11.2026): HSTS, Permissions-Policy, no Server banner.

* HSTS only when the request really is HTTPS (directly, or behind a trusted
  proxy that says so). On plain HTTP — a NAS in the LAN — it would lock the
  browser out of the gallery's own address.
* Permissions-Policy: the gallery never asks for camera, microphone or
  location; saying so stops an injected script from asking either.
* gunicorn's ``Server: gunicorn`` banner is dropped (gunicorn.conf.py).
"""
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_permissions_policy_on_every_answer(client):
    r = client.get("/healthz")
    pp = r.headers.get("Permissions-Policy", "")
    for feature in ("camera=()", "microphone=()", "geolocation=()"):
        assert feature in pp


def test_no_hsts_over_plain_http(client):
    assert "Strict-Transport-Security" not in client.get("/healthz").headers


def test_hsts_over_https(client):
    r = client.get("/healthz", base_url="https://localhost")
    assert r.headers.get("Strict-Transport-Security", "").startswith("max-age=")


def test_hsts_behind_a_trusted_proxy_only(monkeypatch):
    from app import create_app
    monkeypatch.setenv("TRUST_PROXY", "1")
    c = create_app().test_client()
    r = c.get("/healthz", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" in r.headers
    monkeypatch.setenv("TRUST_PROXY", "0")
    c = create_app().test_client()
    r = c.get("/healthz", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" not in r.headers, "a forged header is no HTTPS"


def test_gunicorn_drops_its_server_banner():
    conf = runpy.run_path(str(ROOT / "gunicorn.conf.py"))
    conf["post_worker_init"](None)
    from gunicorn.http import wsgi

    class _Req:
        version = (1, 1)

    resp = wsgi.Response.__new__(wsgi.Response)
    resp.req, resp.status, resp.version = _Req(), "200 OK", "gunicorn"
    resp.upgrade, resp.chunked, resp.must_close, resp.headers = False, False, True, []
    lines = resp.default_headers()
    assert not any(h.lower().startswith("server:") for h in lines), lines
