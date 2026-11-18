"""Forward a gallery to another address - the event stays, the guests go there.

The operator moves finished galleries to an FTP-fed proofing gallery and wants every printed link
to keep working: ``/gallery/<slug>``, its old names, the password page and the
share links of the event answer with a 302 to the address stored at the event
(``events.redirect_url``). The column existed since release 2.1 for exactly
this ("archived = optional redirect") and was never read; this module is what
reads it.

Two rules make it safe:

* Only ``http`` and ``https`` with a host are stored, and the address is
  checked again when it is read, so a value written by an older version or by
  hand (``javascript:...``) is never followed.
* The target comes from the event row only. No route reads a target from the
  request, so there is no open redirect to abuse.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlsplit

MAX_LEN = 500


def clean_url(raw: Optional[str]) -> str:
    """The address if it is a plain http(s) URL with a host, otherwise ``""``.

    Refused: other schemes (``javascript:``, ``data:``, ``ftp:``), addresses
    without a scheme (``//host`` would be read relative to us), blanks and
    control characters anywhere, a backslash (browsers read it as a slash),
    and anything longer than :data:`MAX_LEN`.
    """
    url = str(raw or "").strip()
    if not url or len(url) > MAX_LEN:
        return ""
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url) or "\\" in url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""
    return url


def host(url: Optional[str]) -> str:
    """The host to show in the admin ("leitet weiter auf galerie.example.com")."""
    try:
        return urlsplit(clean_url(url)).hostname or ""
    except ValueError:
        return ""


def target(event: Optional[Dict[str, Any]]) -> str:
    """Where guests of this event are sent, or ``""`` for "stay here".

    A folder without a forward of its own follows its parent: the parent's
    gallery has moved, its folders went with it.
    """
    if not event:
        return ""
    own = clean_url(event.get("redirect_url"))
    if own:
        return own
    if event.get("parent_id"):
        from app import folders
        parent = folders.parent_of(event)
        if parent:
            return clean_url(parent.get("redirect_url"))
    return ""
