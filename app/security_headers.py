"""Browser-side protections on every answer of the gallery (review S6/S9).

The content security policy is the strictest one the current templates
survive. They carry about forty inline ``<script>`` blocks and close to a
hundred inline event handlers (``onclick="…"``). A nonce cannot cover
handlers, and a policy with a nonce switches ``'unsafe-inline'`` off, so a
nonce policy would break the admin and the gallery today. What the policy
does enforce: no plug-ins, no foreign ``<base>``, forms only to this origin,
scripts, styles, fonts and connections only from this origin (plus the
operator's analytics host, if one is configured), no framing by others.
Moving the inline handlers into files is the step that makes
``script-src 'self' 'nonce-…'`` possible; it is listed as open.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from flask import Flask, g, request


def _origin(url: str) -> Optional[str]:
    parts = urlsplit((url or "").strip())
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return None


def _analytics_origin() -> Optional[str]:
    src = getattr(g, "umami_src", None)
    if src is None:
        try:
            import os

            from app import db
            src = db.get_setting("umami_src") or os.environ.get("UMAMI_SRC", "")
        except Exception:                    # noqa: BLE001 — no database, no analytics
            src = ""
    return _origin(src)


def _embeddable() -> bool:
    """The gallery's embed mode is meant to sit in an iframe on the operator's site."""
    return request.path.startswith("/gallery/") and request.args.get("embed") == "1"


def _embed_permission() -> Optional[list]:
    """``None`` when the gallery in the path may not be framed by other sites;
    otherwise its ``embed_origins`` ([] = any site). Final review A11: only an
    event with "Einbetten erlauben" on may be framed."""
    try:
        slug = request.path.split("/")[2]
        from app import db, folders, gallery_settings as gsm
        event = db.get_event_by_slug(slug)
        if not event:
            return None
        folders.apply_parent(event)
        gs = gsm.for_event(event)
        if not gs.get("embed"):
            return None
        return gsm.embed_origins(gs["embed_origins"])
    except Exception:                        # noqa: BLE001 — in doubt: not framed
        return None


def policy(frame_ancestors: str, extra_origin: Optional[str] = None) -> str:
    extra = f" {extra_origin}" if extra_origin else ""
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'unsafe-inline'{extra}",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self' data:",
        f"connect-src 'self'{extra}",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        f"frame-ancestors {frame_ancestors}",
    ])


PERMISSIONS_POLICY = ("camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
                      "serial=(), bluetooth=()")


def install(app: Flask) -> None:
    @app.after_request
    def _security_headers(response):
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        allowed = _embed_permission() if _embeddable() else None
        if allowed is not None:
            # The event allows embedding; with named sites (no. 66) only those.
            ancestors = ("'self' " + " ".join(allowed)) if allowed else "*"
        else:
            ancestors = "'self'"
            h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Content-Security-Policy", policy(ancestors, _analytics_origin()))
        # S-11: no page of the gallery uses these; an injected script must not either.
        h.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        # HSTS only on a request that really came over HTTPS — directly, or
        # through a proxy the operator trusts (TRUST_PROXY=1 makes
        # X-Forwarded-Proto count). On a plain-HTTP NAS it would lock the
        # browser out of the gallery's own address.
        if request.is_secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response
