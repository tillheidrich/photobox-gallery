"""gzip for text answers, in the app itself (18.11.2026).

A gallery page with 568 tiles is about 900 KB of HTML and went out
uncompressed: gunicorn does not compress, and neither the Coolify/Traefik set-up
nor a NAS without a proxy adds it unless somebody configures it. Done here,
with the standard library, it works for every self-hoster; a proxy that also
compresses sees ``Content-Encoding`` and leaves the answer alone.

Compressed: text/html, CSS, JavaScript, JSON, SVG, plain text and XML of at
least ``MIN_BYTES``, status 2xx (not 206), when the client accepts gzip.
Never: media and archives (other types), range requests, answers that already
carry ``Content-Encoding``, streamed answers (a ZIP or a NAS download is a
generator). Static files (``send_file``) are read once and compressed when
they are small text files.

ETag: a strong ETag is made weak (``W/"…"``), as nginx does. The gzip bytes
and the plain bytes are the same representation semantically, so a weak
comparison of ``If-None-Match`` still answers 304 for both, and no cache ever
treats the two byte streams as byte-identical.
"""
from __future__ import annotations

import gzip

MIN_BYTES = 1024
#: A static text file bigger than this is passed through untouched.
MAX_STATIC_BYTES = 4 * 1024 * 1024
TYPES = frozenset({
    "text/html", "text/css", "text/plain", "text/javascript", "application/javascript",
    "application/json", "application/manifest+json", "image/svg+xml", "application/xml", "text/xml",
})


def compressible(mimetype: str) -> bool:
    return (mimetype or "").split(";")[0].strip().lower() in TYPES


def _compress(response):
    from flask import request

    if not compressible(response.mimetype):
        return response
    # Whatever happens below, a cache must keep the two variants apart.
    response.vary.add("Accept-Encoding")
    if request.method == "HEAD" or not (200 <= response.status_code < 300) or response.status_code in (204, 206):
        return response
    if response.headers.get("Content-Encoding") or request.headers.get("Range"):
        return response
    if not request.accept_encodings["gzip"]:
        return response
    if response.is_streamed and not response.direct_passthrough:
        return response                       # a generator: ZIP, NAS stream, server-sent data
    if response.direct_passthrough:
        length = response.content_length
        if length is None or length > MAX_STATIC_BYTES:
            return response
        source = response.response
        try:
            data = b"".join(source)
        finally:
            if hasattr(source, "close"):
                source.close()
        response.direct_passthrough = False
        response.set_data(data)
    data = response.get_data()
    if len(data) < MIN_BYTES:
        return response
    packed = gzip.compress(data, compresslevel=6, mtime=0)
    if len(packed) >= len(data):
        return response
    response.set_data(packed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(packed))
    etag, weak = response.get_etag()
    if etag and not weak:
        response.set_etag(etag, weak=True)
    return response


def install(app) -> None:
    """Run after every other after_request hook (they run in reverse order of
    registration), so the body and headers are final when they are packed."""
    app.after_request_funcs.setdefault(None, []).insert(0, _compress)
