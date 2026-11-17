"""Download without internet: the booth hands a guest their own run over Wi-Fi.

At many venues there is no internet. The gallery is out of reach, and the QR
code on the result screen leads nowhere until the booth has been online again.
The big booth programs solve this the same way, and so does this module: a
router or hotspot next to the booth spans a Wi-Fi (the booth does NOT set one
up and changes nothing on the system), the result screen shows two codes —
"1. join the Wi-Fi" and "2. load your photo" — and the booth itself serves the
picture of exactly that run.

What makes this safe to switch on:

* A second listener, on its own port, that knows nothing but the guest page.
  The booth's own interface stays bound to 127.0.0.1; nothing in it becomes
  reachable from the Wi-Fi. Every address the guest app does not know answers
  a bare 404, so a phone in the Wi-Fi cannot tell an admin route from nothing.
* One link per run, signed with a key that never leaves the booth
  (``<data>/guest_share.key``) and carrying its own expiry (24 h by default).
  The link is the only way in: no list, no other run, no admin.
* A brake per address on wrong links, so nobody can try codes in a loop.
* Data-minimal mode and "Nur drucken": no local link, as there is no QR code.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import secrets
import socket
import threading
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import share

log = logging.getLogger("booth.guest_share")

DEFAULT_PORT = 8766
SIG_LEN = 20                 # 100 bits: nobody types this link, so it can be longer than the gallery's 40
BRAKE_FAILS = 12             # wrong links per address …
BRAKE_WINDOW = 600           # … within ten minutes …
BRAKE_PAUSE = 600            # … close the door for ten minutes


# ── the Wi-Fi code ─────────────────────────────────────────────────────────

def _wifi_escape(value: str) -> str:
    """Escape for the WIFI: format (ZXing): backslash, semicolon, comma, colon, quote."""
    out = []
    for ch in str(value or ""):
        if ch in '\\;,:"':
            out.append("\\")
        out.append(ch)
    return "".join(out)


def wifi_qr_text(ssid: str, password: str = "", security: str = "WPA", hidden: bool = False) -> str:
    """``WIFI:T:WPA;S:<ssid>;P:<password>;;`` — what phone cameras understand.

    An open network (no password, or ``security`` "nopass") is written as
    ``T:nopass`` without a password field. WPA2 and WPA3 are both "WPA" in
    this format; "WEP" is accepted for old routers.
    """
    sec = str(security or "WPA").upper()
    if sec not in ("WPA", "WEP", "NOPASS"):
        sec = "WPA"
    if not password or sec == "NOPASS":
        sec, password = "nopass", ""
    parts = [f"T:{sec}", f"S:{_wifi_escape(ssid)}"]
    if password:
        parts.append(f"P:{_wifi_escape(password)}")
    if hidden:
        parts.append("H:true")
    return "WIFI:" + ";".join(parts) + ";;"


# ── signed links ───────────────────────────────────────────────────────────

def load_key(data_dir: Path) -> str:
    """The booth's own key for guest links; created once, never sent anywhere."""
    path = Path(data_dir) / "guest_share.key"
    try:
        key = path.read_text(encoding="utf-8").strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(key, encoding="utf-8")
    tmp.replace(path)
    return key


def make_code(key: str, uid: str, expires: int) -> str:
    """``<uid>-<expiry in hex>-<signature>``; the expiry is part of what is signed."""
    exp = format(int(expires), "x")
    return f"{uid}-{exp}-{share.signature(key, f'{uid}.{exp}', SIG_LEN)}"


def check_code(key: str, code: str, now: Optional[float] = None) -> Optional[str]:
    """The run id behind a valid, unexpired link — or None."""
    parts = str(code or "").split("-")
    if len(parts) != 3:
        return None
    uid, exp, sig = parts
    if not (6 <= len(uid) <= 24 and uid.isalnum() and 1 <= len(exp) <= 12 and len(sig) == SIG_LEN):
        return None
    try:
        expires = int(exp, 16)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, share.signature(key, f"{uid}.{exp}", SIG_LEN)):
        return None
    if expires < (time.time() if now is None else now):
        return None
    return uid


class Brake:
    """Wrong links per address; after too many the address waits."""

    def __init__(self, fails: int = BRAKE_FAILS, window: int = BRAKE_WINDOW, pause: int = BRAKE_PAUSE):
        self.fails, self.window, self.pause = fails, window, pause
        self._lock = threading.Lock()
        self._seen: Dict[str, List[float]] = {}
        self._closed: Dict[str, float] = {}

    def wait_seconds(self, who: str, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            until = self._closed.get(who, 0)
            if until > now:
                return int(until - now) + 1
            self._closed.pop(who, None)
            return 0

    def failed(self, who: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if len(self._seen) > 2000:                    # a flood of addresses must not eat the memory
                self._seen.clear()
            hits = [t for t in self._seen.get(who, []) if t > now - self.window] + [now]
            self._seen[who] = hits
            if len(hits) >= self.fails:
                self._closed[who] = now + self.pause
                self._seen.pop(who, None)


# ── where the booth can be reached in the Wi-Fi ────────────────────────────

def _usable(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local


def local_addresses() -> List[str]:
    """Private IPv4 addresses of this machine, best guess first.

    The UDP trick asks the system which address it would use for the default
    route; nothing is sent. Without a default route (a router with no uplink
    usually still gives one) the host name's addresses are the fallback.
    """
    found: List[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))              # TEST-NET-1, never routed anywhere
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out: List[str] = []
    for ip in found:
        if _usable(ip) and ip not in out:
            out.append(ip)
    return out


def guest_port(local: Dict[str, Any]) -> int:
    cfg = local.get("guest_share") or {}
    try:
        port = int(cfg.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return port if 1024 <= port <= 65535 else DEFAULT_PORT


def guest_address(local: Dict[str, Any], detect=local_addresses) -> str:
    """The operator's address if set, otherwise the first one found."""
    own = str((local.get("guest_share") or {}).get("address") or "").strip()
    if own:
        return own
    found = detect()
    return found[0] if found else ""


def base_url(local: Dict[str, Any], detect=local_addresses) -> str:
    addr = guest_address(local, detect)
    if not addr:
        return ""
    host = f"[{addr}]" if ":" in addr and not addr.startswith("[") else addr
    return f"http://{host}:{guest_port(local)}"


def offer_kind(profile: Dict[str, Any], online: bool, gallery_url: str) -> str:
    """"local" when the result screen should show the two Wi-Fi steps, else "".

    ``mode``: auto (only while the gallery cannot be reached: the booth is
    offline or there is no gallery link at all), always, off.
    """
    cfg = profile.get("wifi_share") or {}
    if not cfg.get("enabled") or not str(cfg.get("ssid") or "").strip():
        return ""
    mode = str(cfg.get("mode") or "auto").lower()
    if mode == "off":
        return ""
    if mode == "always":
        return "local"
    return "local" if (not online or not gallery_url) else ""


# ── the booth's side: link, offer, listener ────────────────────────────────

class GuestShare:
    """Everything the booth needs for the local guest page; one per booth."""

    def __init__(self, booth):
        self.b = booth
        self._key: Optional[str] = None
        self.bound = False          # the second listener is up
        self.port: Optional[int] = None
        self.error = ""
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._lock = threading.Lock()

    @property
    def key(self) -> str:
        if self._key is None:
            self._key = load_key(self.b.data)
        return self._key

    def cfg(self) -> Dict[str, Any]:
        return self.b.profile.get("wifi_share") or {}

    def allowed(self) -> bool:
        """The lock on the booth itself -- never set from afar."""
        return bool((self.b.store.local.get("guest_share") or {}).get("allowed"))

    def enabled(self) -> bool:
        return self.allowed() and bool(self.cfg().get("enabled")) and not self.b.ephemeral

    def hours(self) -> int:
        try:
            return max(1, min(168, int(self.cfg().get("link_hours") or 24)))
        except (TypeError, ValueError):
            return 24

    def link(self, uid: str, now: Optional[float] = None) -> str:
        base = base_url(self.b.store.local)
        if not base:
            return ""
        expires = int((time.time() if now is None else now) + self.hours() * 3600)
        return f"{base}/g/{make_code(self.key, uid, expires)}"

    def offer(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The two steps for the result screen, or None for the usual QR code."""
        uid = str(item.get("uid") or "")
        if not uid or not self.enabled() or not self.b.profile.get("qr_on_screen", True):
            return None
        if self.b.run_print_only(uid):
            return None                       # no link of any kind, as before
        if not offer_kind(self.b.profile, bool(self.b.agent.online), str(item.get("share_url") or "")):
            return None
        if not self.bound:
            return None                       # nobody would answer; the self-diagnosis says why
        url = self.link(uid)
        if not url:
            return None
        cfg = self.cfg()
        password = str(cfg.get("password") or "")
        return {"ssid": str(cfg.get("ssid") or ""), "password": password, "open": not password,
                "wifi": wifi_qr_text(str(cfg.get("ssid") or ""), password,
                                     str(cfg.get("security") or "WPA"), bool(cfg.get("hidden"))),
                "url": url}

    def item_for(self, code: str) -> Optional[Dict[str, Any]]:
        """The run behind a guest link, if the guest may see it right now."""
        if not self.enabled():
            return None
        uid = check_code(self.key, code)
        if not uid or self.b.run_print_only(uid):
            return None
        item = self.b.library.get(uid)
        if not item or item.get("aufgeraeumt") or not item.get("result") or not Path(item["result"]).exists():
            return None
        return item

    # The listener. Bound on all addresses, but it only ever answers the guest
    # page (see create_guest_app); the booth's interface keeps its loopback.
    def ensure_running(self, host: str = "0.0.0.0") -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            if not self.enabled():
                return False
            port = guest_port(self.b.store.local)
            if port == int(self.b.store.local.get("port", 8765)):
                self.error = f"Port {port} ist schon der Port der Box selbst."
                return False
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
            except OSError as e:
                sock.close()
                self.bound, self.error = False, f"Port {port} ist belegt ({e.strerror or e})."
                log.warning("Gäste-Seite: %s", self.error)
                return False
            import uvicorn
            server = uvicorn.Server(uvicorn.Config(create_guest_app(self.b), log_level="warning",
                                                   access_log=False))
            self._server = server
            self._thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]},
                                            name="guest-share", daemon=True)
            self._thread.start()
            self.bound, self.port, self.error = True, port, ""
            log.info("Gäste-Seite im WLAN auf Port %s", port)
            return True

    def stop(self, timeout: float = 5.0) -> None:
        server, thread = getattr(self, "_server", None), self._thread
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout)
        self.bound = False

    def watch(self, every: float = 5.0) -> None:
        """Start the listener as soon as the profile switches the function on."""
        while True:
            try:
                self.ensure_running()
            except Exception as e:                       # never take the booth down for this
                self.error = str(e)
                log.warning("Gäste-Seite: %s", e)
            time.sleep(every)


def for_booth(booth) -> GuestShare:
    gs = getattr(booth, "_guest_share", None)
    if gs is None:
        gs = GuestShare(booth)
        booth._guest_share = gs
    return gs


# ── the guest app ──────────────────────────────────────────────────────────

TEXTS = {
    "de": {"title": "Euer Foto", "save": "Speichern", "hint": "Auf dem iPhone: Bild lange drücken und "
           "„Zu Fotos hinzufügen“ wählen.", "until": "Dieser Link gilt bis {when}.",
           "gone": "Dieser Link gilt nicht (mehr).", "anim": "Bewegt ansehen"},
    "en": {"title": "Your photo", "save": "Save", "hint": "On an iPhone: press and hold the picture, then "
           "choose “Add to Photos”.", "until": "This link works until {when}.",
           "gone": "This link does not work (any more).", "anim": "See it move"},
}
VIDEO_MODES = ("boomerang", "video")
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # Nothing from anywhere else: no fonts, no scripts, no tracking.
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; "
                               "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
                               "frame-ancestors 'none'",
}


def _lang(request) -> str:
    want = (request.query_params.get("lang") or request.headers.get("accept-language") or "de").lower()
    return "de" if want.startswith("de") else "en"


def _page(t: Dict[str, str], lang: str, body: str) -> str:
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex">'
            f'<title>{t["title"]}</title><style>'
            ":root{color-scheme:dark}body{margin:0;background:#0a0806;color:#f5f3f0;"
            "font:18px/1.45 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}"
            "main{max-width:720px;margin:0 auto;padding:20px 16px 40px;display:grid;gap:18px}"
            "h1{margin:0;font-size:26px}img,video{width:100%;height:auto;border-radius:14px;background:#23201c}"
            "a.btn{display:block;text-align:center;padding:16px;border-radius:99px;background:#e7ad59;"
            "color:#0f0d09;font-weight:700;text-decoration:none;font-size:20px}"
            "a.alt{background:#23201c;color:#f5f3f0}p{margin:0;color:#c9c5bf}"
            f"</style></head><body><main>{body}</main></body></html>")


def create_guest_app(booth):
    """The whole app a phone in the Wi-Fi can reach: one page and its files."""
    gs = for_booth(booth)
    brake = Brake()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.brake = brake

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in _HEADERS.items():
            response.headers[k] = v
        return response

    def nothing(lang: str = "de", html: bool = False):
        if html:
            t = TEXTS[lang]
            return HTMLResponse(_page(t, lang, f"<h1>{t['title']}</h1><p>{t['gone']}</p>"), status_code=404)
        return Response("Not found", status_code=404, media_type="text/plain")

    def find(request: Request, code: str):
        """(item, response): the item, or the answer to send instead."""
        who = request.client.host if request.client else "?"
        wait = brake.wait_seconds(who)
        if wait:
            return None, Response("Too many attempts", status_code=429, media_type="text/plain",
                                  headers={"Retry-After": str(wait)})
        item = gs.item_for(code)
        if item is None:
            brake.failed(who)
            return None, None
        return item, None

    @app.get("/g/{code}")
    def page(code: str, request: Request):
        lang = _lang(request)
        item, refused = find(request, code)
        if refused:
            return refused
        if item is None:
            return nothing(lang, html=True)
        t = TEXTS[lang]
        c = escape(code)
        exp = datetime.fromtimestamp(int(code.split("-")[1], 16)).strftime("%d.%m. %H:%M")
        if item["mode"] in VIDEO_MODES:
            media = f'<video src="/g/{c}/file" controls autoplay muted loop playsinline></video>'
        else:
            media = f'<img src="/g/{c}/file" alt="">'
        anim = item.get("anim") and Path(item["anim"]).exists()
        body = (f"<h1>{t['title']}</h1>{media}"
                f'<a class="btn" href="/g/{c}/file?dl=1" download>{t["save"]}</a>'
                + (f'<a class="btn alt" href="/g/{c}/anim">{t["anim"]}</a>' if anim else "")
                + f"<p>{t['hint']}</p><p>{t['until'].format(when=exp)}</p>")
        return HTMLResponse(_page(t, lang, body))

    def _file(request: Request, code: str, which: str, dl: bool):
        item, refused = find(request, code)
        if refused:
            return refused
        path = item and item.get(which)
        if not path or not Path(path).exists():
            return nothing()
        name = f"foto-{item['uid']}{Path(path).suffix.lower()}"
        return FileResponse(path, filename=name if dl else None,
                            content_disposition_type="attachment" if dl else "inline")

    @app.get("/g/{code}/file")
    def file(code: str, request: Request, dl: int = 0):
        return _file(request, code, "result", bool(dl))

    @app.get("/g/{code}/anim")
    def anim(code: str, request: Request):
        return _file(request, code, "anim", False)

    # Everything else — "/", "/api/…", "/admin", "/media/…", "/assets/…" — is
    # not there. The same bare 404 for all of it, whatever the method.
    @app.api_route("/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def everything_else(rest: str):
        return nothing()

    return app
