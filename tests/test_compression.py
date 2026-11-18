"""gzip in the app (app/compression.py, 18.11.2026): text yes, media and streams no."""
from __future__ import annotations

import gzip

from flask import Response

GZ = {"Accept-Encoding": "gzip, deflate, br"}


def _app():
    from app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    big = "<p>" + "Hochzeit " * 400 + "</p>"

    @app.get("/_t/html")
    def _html():
        return big

    @app.get("/_t/small")
    def _small():
        return "<p>klein</p>"

    @app.get("/_t/jpeg")
    def _jpeg():
        return Response(b"\xff\xd8" + b"\x00" * 5000, mimetype="image/jpeg")

    @app.get("/_t/zip")
    def _zip():
        return Response(b"PK" + b"\x00" * 5000, mimetype="application/zip")

    @app.get("/_t/stream")
    def _stream():
        return Response((b"a" * 2000 for _ in range(3)), mimetype="text/plain")

    @app.get("/_t/encoded")
    def _encoded():
        r = Response(gzip.compress(big.encode()), mimetype="text/html")
        r.headers["Content-Encoding"] = "gzip"
        return r

    @app.get("/_t/etag")
    def _etag():
        r = Response(big, mimetype="text/html")
        r.set_etag("abc")
        return r.make_conditional(__import__("flask").request)

    return app


def test_html_is_gzipped_only_for_a_client_that_asks():
    c = _app().test_client()
    r = c.get("/_t/html", headers=GZ)
    assert r.headers["Content-Encoding"] == "gzip" and "Accept-Encoding" in r.headers["Vary"]
    assert gzip.decompress(r.data).startswith(b"<p>Hochzeit")
    assert c.get("/_t/html").headers.get("Content-Encoding") is None
    assert c.get("/_t/html", headers={"Accept-Encoding": "gzip;q=0"}).headers.get("Content-Encoding") is None


def test_small_media_archives_streams_ranges_and_encoded_answers_are_left_alone():
    c = _app().test_client()
    assert c.get("/_t/small", headers=GZ).headers.get("Content-Encoding") is None
    for path in ("/_t/jpeg", "/_t/zip", "/_t/stream"):
        r = c.get(path, headers=GZ)
        assert r.headers.get("Content-Encoding") is None, path
    r = c.get("/_t/html", headers={**GZ, "Range": "bytes=0-10"})
    assert r.headers.get("Content-Encoding") is None
    r = c.get("/_t/encoded", headers=GZ)
    assert gzip.decompress(r.data).startswith(b"<p>Hochzeit")         # packed once, not twice


def test_a_strong_etag_becomes_weak_and_still_answers_304():
    c = _app().test_client()
    r = c.get("/_t/etag", headers=GZ)
    assert r.headers["ETag"] == 'W/"abc"'
    again = c.get("/_t/etag", headers={**GZ, "If-None-Match": r.headers["ETag"]})
    assert again.status_code == 304


def test_static_css_and_js_are_compressed_and_revalidate():
    c = _app().test_client()
    r = c.get("/static/css/public.css", headers=GZ)
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip"
    assert b".lb-nav" in gzip.decompress(r.data)
    etag = r.headers.get("ETag")
    if etag:
        assert etag.startswith("W/")
        assert c.get("/static/css/public.css", headers={**GZ, "If-None-Match": etag}).status_code == 304
    r.close()
