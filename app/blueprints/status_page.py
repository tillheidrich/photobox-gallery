"""The public, read-only status page of a booth (``GET /status/<token>``).

No login. Every token that does not open a page — unknown, malformed, of a
switched-off or deactivated booth — answers with the gallery's ordinary 404, the same body
as any mistyped address, so a stranger cannot tell whether a booth exists.
The token is looked up first and a valid one is served without counting
against the rate limit; only a token that opens nothing is counted per
address, and once an address is over the limit such requests get 429 instead
of 404. See app/status_page.py for what the page shows and why.
"""
from __future__ import annotations

from flask import Blueprint, abort, g, make_response, render_template, request

from app import db, sharelinks, status_page

bp = Blueprint("status_page", __name__)


@bp.after_request
def _private(response):
    # On the 404 too: a cache or a search engine should not keep even the
    # address of a dead link, and no page here may hand its address to the
    # next site in a Referer header.
    h = response.headers
    h["Cache-Control"] = "no-store"
    h["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    h["Referrer-Policy"] = "no-referrer"
    return response


@bp.get("/status/<token>")
def booth_status(token: str):
    device = status_page.device_for(token)
    if not device:
        # Only a token that opens nothing costs an attempt. Behind a reverse
        # proxy every visitor may share one address, and a valid 192-bit token
        # needs no brake. retry_after() records nothing; record_failure() does,
        # and only below the limit, so a blocked address does not extend its own block.
        key = status_page.rate_key(request.remote_addr)
        if sharelinks.retry_after(key, status_page.RATE_LIMIT, status_page.RATE_WINDOW):
            abort(429)
        sharelinks.record_failure(key, status_page.RATE_WINDOW)
        abort(404)
    from app.blueprints.public import _inject_globals
    _inject_globals()
    # The address carries the token: analytics would record it.
    g.umami_enabled = False
    # Language from the address, not the session: the lang switch of the
    # public pages redirects to the Referer, which this page does not send.
    wanted = request.args.get("lang")
    if wanted in ("de", "en") and db.languages() == "both":
        g.lang = wanted
    page = render_template("public/booth_status.html", s=status_page.view(device), noindex=True)
    return make_response(page, 200)
