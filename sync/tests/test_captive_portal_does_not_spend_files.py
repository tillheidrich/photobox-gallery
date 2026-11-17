"""Hotel and venue Wi-Fi with a login page must not cost a single picture.

A captive portal answers every request itself: a redirect to its login page,
or its login page with 200. The gallery target read that answer as JSON,
failed, and counted it as the file's fault; after eight tries (about twenty
minutes of portal) every file queued in that time was "failed" for good and
never uploaded once the Wi-Fi was logged in again. An answer that is not the
gallery's is the network's trouble: wait, do not write the file off.
"""
from __future__ import annotations

import httpx
import pytest

from photobox_sync.targets import GalleryTarget, TemporaryError


def _item(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 10)
    return {"path": str(f), "uid": "u", "session_uid": "s", "event_slug": "e", "kind": "photo", "variant": "web"}


@pytest.mark.parametrize("answer", [
    httpx.Response(302, headers={"Location": "http://login.hotel-wifi.example/"}),
    httpx.Response(200, text="<html><body>Bitte im WLAN anmelden</body></html>",
                   headers={"Content-Type": "text/html"}),
    httpx.Response(200, text="", headers={"Content-Type": "application/json"}),
])
def test_a_login_page_instead_of_the_gallery_stalls_the_queue(tmp_path, answer):
    t = GalleryTarget("https://gallery.invalid", "key")
    t.client = httpx.Client(transport=httpx.MockTransport(lambda req: answer))
    with pytest.raises(TemporaryError):
        t.upload(_item(tmp_path))


def test_the_gallerys_own_answer_is_still_read(tmp_path):
    t = GalleryTarget("https://gallery.invalid", "key")
    t.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})))
    assert t.upload(_item(tmp_path)) == {"ok": True}


def test_the_heartbeat_behind_a_login_page_is_offline_not_broken():
    t = GalleryTarget("https://gallery.invalid", "key")
    t.client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text="<html>Login</html>", headers={"Content-Type": "text/html"})))
    with pytest.raises(TemporaryError):
        t.sync({"status": {}})
