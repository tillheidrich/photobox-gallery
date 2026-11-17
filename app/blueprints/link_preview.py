"""The link-preview image of a gallery: /gallery/<slug>/og.jpg.

A blueprint of its own, on purpose: the public blueprint's ``before_request``
writes the language into the session, and a link-preview bot must get the
same image without a cookie and without being handed one. Nothing here reads
the session - the admin sees exactly what a chat app sees. The decision is in
app/link_preview.py.
"""
from __future__ import annotations

from flask import Blueprint, Response, request

from app import db, link_preview

bp = Blueprint("link_preview", __name__)


@bp.get("/gallery/<slug>/og.jpg")
def og_image(slug: str):
    event, _canonical, _alias = db.resolve_event_slug(slug)
    if event:
        from app import folders
        event = folders.apply_parent(dict(event))
    # An unknown gallery and a silent one answer with the same neutral card:
    # a 404 for one and a picture for the other would tell a stranger which
    # names exist.
    data, key, mode = link_preview.card_for_request(event, request.args.get("b") == "1")
    resp = Response(data, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=86400" if mode == "neutral" else "public, max-age=3600"
    resp.headers["ETag"] = f'"{key}"'
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Content-Length"] = str(len(data))
    return resp
