"""Which certificate authorities outgoing TLS of the booth and Photobox Sync trusts.

One place for it, because the obvious ``ssl.create_default_context()`` is not
enough on Windows: there it reads only the roots already in the Windows
certificate store. Windows fetches a root it has not seen yet lazily, and only
for its own TLS stack (browser, Edge, .NET) — Python never triggers that. A
booth PC that never visited a site under, say, a newer Sectigo root then
refuses an FTPS server whose chain ends there ("unable to get local issuer
certificate"), while the gallery upload on the same PC works, because httpx
brings the certifi bundle.

So: the system's roots (an in-house CA the operator installed keeps working)
plus the certifi bundle (the same roots as the gallery upload). certifi comes
with httpx and is packed into both Windows programs; if it is missing anyway,
the system store alone is used, as before. Certificate and host name are
always checked — there is deliberately no switch to turn that off.
"""
from __future__ import annotations

import os
import ssl
from typing import Optional


def _system_context() -> ssl.SSLContext:
    # Looked up at call time, so a test can stand in for "this machine's store".
    return ssl.create_default_context()


def _certifi_bundle() -> Optional[str]:
    try:
        import certifi
    except ImportError:
        return None
    try:
        where = certifi.where()
    except Exception:                       # noqa: BLE001 - a broken bundle must not stop the upload
        return None
    return where if where and os.path.isfile(where) else None


def verify_context() -> ssl.SSLContext:
    """A client context for outgoing TLS: system roots plus certifi, always verifying."""
    ctx = _system_context()
    bundle = _certifi_bundle()
    if bundle:
        try:
            ctx.load_verify_locations(cafile=bundle)
        except (ssl.SSLError, OSError):
            # A damaged or unreadable bundle must not stop the upload: the
            # system store alone, as before certifi was added. OpenSSL may have
            # taken the certificates before the damage; they are valid ones.
            pass
    # Stated, not assumed: whatever built the base context, this one verifies.
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    return ctx
