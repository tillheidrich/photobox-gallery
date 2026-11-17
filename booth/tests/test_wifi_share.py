"""Download without internet: Wi-Fi code, then a page the booth serves itself.

The booth does not set up a Wi-Fi; a router or hotspot stands next to it.
With ``wifi_share`` on, the result screen shows "1. join the Wi-Fi" (a code
in the WIFI: format) and "2. load your photo" (a link to the booth). The link
is signed per run with a key that stays on the booth and expires (24 h by
default). It is served by a second listener that knows nothing but that page:
from the Wi-Fi, "/", "/api/…", "/admin" and every other address are a 404,
and the booth's own interface stays on 127.0.0.1.
"""
import tempfile
import time
from pathlib import Path

import pytest

from boothcore import config, guest_share

# ── the WIFI: code ──────────────────────────────────────────────────────────


def test_the_wifi_code_has_the_standard_form():
    assert guest_share.wifi_qr_text("Fotobox", "geheim123") == "WIFI:T:WPA;S:Fotobox;P:geheim123;;"


@pytest.mark.parametrize("raw, escaped", [
    ('a;b', r'a\;b'), ('a,b', r'a\,b'), ('a:b', r'a\:b'), ('a\\b', 'a\\\\b'), ('a"b', 'a\\"b'),
])
def test_special_characters_are_escaped(raw, escaped):
    assert guest_share.wifi_qr_text(raw, raw) == f"WIFI:T:WPA;S:{escaped};P:{escaped};;"


def test_an_open_network_has_no_password_field():
    assert guest_share.wifi_qr_text("Gaeste", "") == "WIFI:T:nopass;S:Gaeste;;"
    assert guest_share.wifi_qr_text("Gaeste", "x", "nopass") == "WIFI:T:nopass;S:Gaeste;;"


def test_wep_and_hidden_networks():
    assert guest_share.wifi_qr_text("Alt", "12345", "WEP", hidden=True) == "WIFI:T:WEP;S:Alt;P:12345;H:true;;"
    assert guest_share.wifi_qr_text("X", "pw", "something").startswith("WIFI:T:WPA;")


# ── defaults change nothing ─────────────────────────────────────────────────

def test_the_function_is_off_by_default():
    w = config.DEFAULT_PROFILE["wifi_share"]
    assert w == {"enabled": False, "mode": "auto", "ssid": "", "password": "", "security": "WPA",
                 "hidden": False, "link_hours": 24}
    assert config.DEFAULT_LOCAL["guest_share"] == {"allowed": False, "address": "", "port": 8766}
    assert config.DEFAULT_LOCAL["port"] != config.DEFAULT_LOCAL["guest_share"]["port"]


# ── signed links ────────────────────────────────────────────────────────────

KEY = "k" * 64


def test_a_link_carries_its_expiry_and_its_signature():
    code = guest_share.make_code(KEY, "abcdefghjk", int(time.time()) + 60)
    assert guest_share.check_code(KEY, code) == "abcdefghjk"


def test_a_wrong_or_expired_signature_is_nothing():
    now = time.time()
    code = guest_share.make_code(KEY, "abcdefghjk", int(now) + 60)
    uid, exp, sig = code.split("-")
    assert guest_share.check_code("x" * 64, code) is None, "other key"
    assert guest_share.check_code(KEY, f"{uid}-{exp}-{'a' * len(sig)}") is None, "forged signature"
    assert guest_share.check_code(KEY, f"{uid}-{format(int(now) + 99999, 'x')}-{sig}") is None, "stretched expiry"
    assert guest_share.check_code(KEY, f"zzzzzzzzzz-{exp}-{sig}") is None, "other run"
    assert guest_share.check_code(KEY, code, now=now + 61) is None, "expired"
    assert guest_share.check_code(KEY, "") is None and guest_share.check_code(KEY, "a-b") is None


# ── auto | always | off ─────────────────────────────────────────────────────

def _prof(**w):
    return {"wifi_share": {**config.DEFAULT_PROFILE["wifi_share"], "enabled": True, "ssid": "Box", **w}}


def test_auto_shows_the_local_steps_only_while_the_gallery_is_out_of_reach():
    assert guest_share.offer_kind(_prof(), online=False, gallery_url="https://g/p/x") == "local"
    assert guest_share.offer_kind(_prof(), online=True, gallery_url="") == "local"
    assert guest_share.offer_kind(_prof(), online=True, gallery_url="https://g/p/x") == ""


def test_always_and_off():
    assert guest_share.offer_kind(_prof(mode="always"), online=True, gallery_url="https://g") == "local"
    assert guest_share.offer_kind(_prof(mode="off"), online=False, gallery_url="") == ""
    assert guest_share.offer_kind(_prof(enabled=False), online=False, gallery_url="") == ""
    assert guest_share.offer_kind(_prof(ssid=" "), online=False, gallery_url="") == "", "no network, no code"


def test_the_address_is_found_or_set_by_the_operator():
    assert guest_share.guest_address({"guest_share": {"address": "10.0.0.5"}}, lambda: ["192.168.1.2"]) == "10.0.0.5"
    assert guest_share.guest_address({}, lambda: ["192.168.1.2"]) == "192.168.1.2"
    assert guest_share.base_url({"guest_share": {"port": 9000}}, lambda: ["192.168.1.2"]) == "http://192.168.1.2:9000"
    assert guest_share.base_url({}, lambda: []) == ""
    for ip in guest_share.local_addresses():
        assert not ip.startswith("127.") and not ip.startswith("169.254.")


# ── the booth ───────────────────────────────────────────────────────────────

@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-wifi-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["wifi_share"].update({"enabled": True, "ssid": "Fotobox", "password": "geheim;1"})
    b.store.save_local({"guest_share": {"allowed": True, "address": "192.168.8.20", "port": 8766}})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    guest_share.for_booth(b).bound = True          # as if the listener were up
    yield b
    b.camera.stop()
    b.mail.stop()


def _shoot(booth, mode="photo"):
    s = booth.start_session(mode, "4:5", None, None)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _kiosk(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def _guests(booth, ip="192.168.8.77"):
    """A phone in the Wi-Fi: the guest app, seen from a non-loopback address."""
    from fastapi.testclient import TestClient
    return TestClient(guest_share.create_guest_app(booth), client=(ip, 50000))


def test_the_result_offers_both_steps_when_offline(booth):
    s = _shoot(booth)
    item = _kiosk(booth).get(f"/api/gallery/{s.uid}").json()
    local = item["local_share"]
    assert local["ssid"] == "Fotobox" and local["wifi"] == r"WIFI:T:WPA;S:Fotobox;P:geheim\;1;;"
    assert local["url"].startswith("http://192.168.8.20:8766/g/" + s.uid + "-")
    assert local["wifi_qr"].startswith("/api/qr.png?data=") and local["photo_qr"].startswith("/api/qr.png?data=")


def test_the_password_does_not_travel_in_the_kiosk_profile(booth):
    state = _kiosk(booth).get("/api/state").json()
    assert "password" not in state["profile"]["wifi_share"]


def test_online_with_a_gallery_the_usual_qr_stays(booth):
    booth.store.profile["event"].update({"slug": "fest", "share_key": "k", "base_url": "https://galerie.example"})
    booth.agent.online = True
    s = _shoot(booth)
    item = _kiosk(booth).get(f"/api/gallery/{s.uid}").json()
    assert item["local_share"] is None and item["qr_url"]


def test_no_local_link_without_a_listener_or_in_print_only_mode(booth):
    s = _shoot(booth)
    guest_share.for_booth(booth).bound = False
    assert _kiosk(booth).get(f"/api/gallery/{s.uid}").json()["local_share"] is None
    guest_share.for_booth(booth).bound = True
    booth.store.profile["privacy"]["mode"] = "print_only"
    s2 = _shoot(booth)
    assert guest_share.for_booth(booth).offer({"uid": s2.uid, "share_url": ""}) is None
    assert guest_share.for_booth(booth).link(s2.uid) and guest_share.for_booth(booth).item_for(
        guest_share.for_booth(booth).link(s2.uid).rsplit("/", 1)[1]) is None


def test_the_guest_link_shows_this_run_and_its_file(booth):
    s = _shoot(booth)
    code = guest_share.for_booth(booth).link(s.uid).rsplit("/", 1)[1]
    phone = _guests(booth)
    page = phone.get(f"/g/{code}")
    assert page.status_code == 200 and f"/g/{code}/file" in page.text
    assert "http" not in page.text.split("<main>")[1], "no address from anywhere else"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    f = phone.get(f"/g/{code}/file")
    assert f.status_code == 200 and f.headers["content-type"].startswith("image/")
    dl = phone.get(f"/g/{code}/file?dl=1")
    assert "attachment" in dl.headers["content-disposition"]


def test_english_for_an_english_phone(booth):
    s = _shoot(booth)
    code = guest_share.for_booth(booth).link(s.uid).rsplit("/", 1)[1]
    assert "Your photo" in _guests(booth).get(f"/g/{code}", headers={"Accept-Language": "en-GB"}).text


@pytest.mark.parametrize("path", ["/", "/api/state", "/api/gallery", "/api/admin/config", "/admin",
                                  "/media/x/result", "/assets/a.jpg", "/static/app.js", "/api/qr.png?data=x",
                                  "/docs", "/openapi.json", "/g", "/g/"])
def test_from_the_wifi_everything_but_the_guest_page_is_404(booth, path):
    phone = _guests(booth)
    assert phone.get(path).status_code == 404
    assert phone.post(path).status_code == 404


def test_another_run_and_a_broken_link_are_404(booth):
    a, b2 = _shoot(booth), _shoot(booth)
    gs = guest_share.for_booth(booth)
    code = gs.link(a.uid).rsplit("/", 1)[1]
    uid, exp, sig = code.split("-")
    phone = _guests(booth)
    assert phone.get(f"/g/{b2.uid}-{exp}-{sig}").status_code == 404
    assert phone.get(f"/g/{code}x").status_code == 404
    assert phone.get(f"/g/{code}/../{b2.uid}").status_code == 404


def test_an_expired_link_is_404(booth, monkeypatch):
    s = _shoot(booth)
    gs = guest_share.for_booth(booth)
    code = gs.link(s.uid, now=time.time() - 25 * 3600).rsplit("/", 1)[1]
    assert _guests(booth).get(f"/g/{code}").status_code == 404


def test_switched_off_the_old_links_stop_working(booth):
    s = _shoot(booth)
    code = guest_share.for_booth(booth).link(s.uid).rsplit("/", 1)[1]
    booth.store.profile["wifi_share"]["enabled"] = False
    assert _guests(booth).get(f"/g/{code}").status_code == 404


def test_the_brake_stops_trying_codes(booth):
    s = _shoot(booth)
    good = guest_share.for_booth(booth).link(s.uid).rsplit("/", 1)[1]
    phone = _guests(booth, "192.168.8.99")
    for i in range(guest_share.BRAKE_FAILS):
        assert phone.get(f"/g/aaaaaaaaaa-1-{'b' * 20}").status_code == 404
    blocked = phone.get(f"/g/{good}")
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert _guests(booth, "192.168.8.100").get(f"/g/{good}").status_code == 200, "only that address waits"


def test_the_key_stays_on_the_booth(booth):
    key = guest_share.for_booth(booth).key
    assert len(key) >= 32 and (booth.data / "guest_share.key").read_text().strip() == key
    assert guest_share.load_key(booth.data) == key, "the same key after a restart"
    state = _kiosk(booth).get("/api/state").text
    assert key not in state


def test_the_listener_binds_its_own_port_and_answers_only_guests(booth):
    import socket
    import httpx
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    booth.store.save_local({"guest_share": {"address": "127.0.0.1", "port": port}})
    gs = guest_share.GuestShare(booth)
    assert gs.ensure_running(host="127.0.0.1")
    try:
        for _ in range(50):
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/api/state", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        assert r.status_code == 404
        assert gs.bound and gs.port == port
        assert gs.ensure_running(host="127.0.0.1"), "a second call keeps the running listener"
    finally:
        gs.stop()


def test_the_listener_refuses_the_booths_own_port(booth):
    booth.store.save_local({"guest_share": {"port": int(booth.store.local["port"])}})
    gs = guest_share.GuestShare(booth)
    assert gs.ensure_running(host="127.0.0.1") is False and "Port" in gs.error and not gs.bound


# ── self-diagnosis ──────────────────────────────────────────────────────────

def _check(booth):
    from boothcore.doctor import Doctor
    return Doctor(booth).check_wifi_share()


def test_doctor_is_quiet_while_the_function_is_off(booth):
    booth.store.profile["wifi_share"]["enabled"] = False
    assert _check(booth).state == "skip"


def test_doctor_warns_without_an_address(booth, monkeypatch):
    booth.store.save_local({"guest_share": {"address": "", "port": 8766}})
    monkeypatch.setattr(guest_share, "local_addresses", lambda: [])
    c = _check(booth)
    assert c.state == "warn" and "Adresse" in c.message


def test_doctor_warns_when_the_port_is_not_bound(booth):
    gs = guest_share.for_booth(booth)
    gs.bound, gs.error = False, "Port 8766 ist belegt (Address already in use)."
    c = _check(booth)
    assert c.state == "warn" and "8766" in c.message


# ── the kiosk ───────────────────────────────────────────────────────────────

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


def test_the_result_screen_shows_two_steps_instead_of_the_gallery_code():
    src = JS.split("function openResult")[1].split("updatePrintBox(item.print);")[0]
    assert "item.local_share" in src and '$("#share").hidden = true;' in src
    assert 'data-qr="wifi"' in HTML and 'data-qr="photo"' in HTML
    assert HTML.count('data-action="qr-full"') == 3


def test_each_code_opens_large_on_its_own():
    assert 'case "qr-full": return openQr(b.dataset.qr);' in JS
    src = JS.split("function openQr(which)")[1].split("\n  }\n")[0]
    assert "local.wifi" in src and "local.url" in src


def test_every_step_has_its_own_customisable_line():
    i18n = (WEB / "static" / "i18n.js").read_text(encoding="utf-8")
    for key in ("result.wifi_join", "result.wifi_join_hint", "result.wifi_photo", "result.wifi_photo_hint",
                "result.wifi_ssid", "result.wifi_password"):
        assert i18n.count(f'"{key}"') == 2, key


# ── the lock on the booth itself ────────────────────────────────────────────

def test_the_profile_alone_never_opens_the_guest_page(monkeypatch):
    """The owner must be able to rule the function out for good (12.11.2026).

    The profile comes from the gallery and can be changed from afar (admin
    pages, MCP). A second listener on all addresses must not be something a
    remote change can switch on: behind the booth there may be an LTE stick
    nobody else is to reach. So the function needs a switch on the booth
    itself, off by default, and that switch is not on the remote list.
    """
    d = Path(tempfile.mkdtemp(prefix="booth-wifi-lock-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    try:
        assert config.DEFAULT_LOCAL["guest_share"]["allowed"] is False
        assert not any(k.startswith("guest_share") for k in config.REMOTE_LOCAL_KEYS)
        b.store.profile["wifi_share"].update({"enabled": True, "ssid": "Fotobox", "mode": "always"})
        gs = guest_share.for_booth(b)
        assert gs.enabled() is False                      # profile says yes, the booth says no
        b.store.save_local({"guest_share": {"allowed": True}})
        assert gs.enabled() is True
    finally:
        b.mail.stop()


def test_doctor_says_that_the_booth_itself_forbids_it(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-wifi-lock-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    from boothcore.doctor import Doctor
    b = Booth(base=d, start_threads=False)
    try:
        b.store.profile["wifi_share"].update({"enabled": True, "ssid": "Fotobox"})
        check = Doctor(b).check_wifi_share()
        assert check.state == "skip" and "an dieser Box gesperrt" in check.message
    finally:
        b.mail.stop()
