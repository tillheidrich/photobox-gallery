"""Pairing a booth from the phone (device-authorisation flow).

Asked for on 15.11.2026: the booth shows a QR code, the operator scans it,
logs in to the gallery admin on the phone, confirms, and the booth is paired
without typing anything at the booth.

The whole way with a real booth object and the gallery in the test client
(httpx is bent to it). Rules pinned here: the request code (and so the QR
code) alone never yields a key — collecting needs the verifier only the
booth knows; a wrong verifier counts on the pair brake; a request is
confirmed once and collected once; it expires; a new request of the same
booth replaces the old one; the admin page never shows a key; the protocol
says who paired which box "per Handy"; nothing of this goes through MCP.
"""
import hashlib
import re
import secrets
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sync"))
sys.path.insert(0, str(ROOT / "booth"))


@pytest.fixture()
def gallery(client, monkeypatch):
    client.get("/healthz")
    from app import accounts, db, ecosystem
    from app.blueprints import booth_v2
    ecosystem.init_schema()
    booth_v2._PAIR_FAILS.clear()
    booth_v2._PAIR_OPENS.clear()
    monkeypatch.setattr(booth_v2.time, "sleep", lambda s: None)
    db.set_setting("admin_username", "chef")
    db.set_setting("admin_totp_enabled", "")
    accounts.set_password("ein-langes-start-passwort")

    def fake_post(url, json=None, **kw):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        r = client.post(path, json=json)
        return httpx.Response(r.status_code, json=r.get_json())
    monkeypatch.setattr(httpx, "post", fake_post)
    yield client
    booth_v2._PAIR_FAILS.clear()
    booth_v2._PAIR_OPENS.clear()


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-phone-pair-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"booth_name": "Box im Flur"})
    yield b
    b.mail.stop()


def _admin(app):
    c = app.test_client()
    from app import accounts
    with c.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "t"
    return c


def _code(started):
    return started["code"].replace("-", "")


def test_the_whole_way_from_the_qr_code_to_a_paired_booth(app, gallery, booth):
    from app import db
    started = booth.phone_pair_start("https://galerie.example.de")
    assert started["qr"].startswith("data:image/png;base64,")
    assert started["link"] == f"{started['link'].split('/admin/')[0]}/admin/devices/pair?c={_code(started)}"
    assert booth.phone_pair_poll()["state"] == "open"
    assert booth.store.local["server"]["url"] == "https://galerie.example.de"   # remembered

    phone = _admin(app)
    page = phone.get(f"/admin/devices/pair?c={_code(started)}").get_data(as_text=True)
    assert "Box im Flur" in page and started["code"] in page and "photobox-booth" in page
    r = phone.post(f"/admin/devices/pair?c={_code(started)}", data={
        "csrf_token": "t", "action": "confirm", "device_id": "new", "new_name": "Box Flur"})
    assert r.status_code == 200 and "Gekoppelt" in r.get_data(as_text=True)
    device = next(d for d in db.list_devices() if d["name"] == "Box Flur")
    assert device["api_key"] not in r.get_data(as_text=True), "the key never reaches the phone"

    done = booth.phone_pair_poll()
    assert done["state"] == "paired"
    # The key is made when the booth collects it (S-2): the one stored at
    # confirmation was never anybody's.
    assert db.get_device_by_api_key(booth.store.local["server"]["api_key"])["id"] == device["id"]
    assert booth.store.local["server"]["device_name"] == "Box Flur"
    assert booth.phone_pair_poll()["state"] == "none"

    # The protocol says who did it, and nothing secret
    con = db.get_db()
    rows = [dict(x) for x in con.execute("SELECT * FROM admin_vorgaenge ORDER BY id DESC LIMIT 5")]
    changes = " ".join(str(dict(x)) for x in con.execute("SELECT * FROM admin_aenderungen ORDER BY id DESC LIMIT 50"))
    con.close()
    assert any("Box Flur" in r["was"] and "per Handy" in r["was"] and "chef" in r["was"] for r in rows)
    assert device["api_key"] not in changes


def test_the_code_alone_never_yields_a_key(app, gallery, booth):
    from app.blueprints import booth_v2
    started = booth.phone_pair_start("https://galerie.example.de")
    phone = _admin(app)
    phone.post(f"/admin/devices/pair?c={_code(started)}", data={
        "csrf_token": "t", "action": "confirm", "device_id": "new", "new_name": "Box X"})
    stranger = gallery.post("/api/v2/pair/status", json={"code": _code(started), "verifier": "geraten"})
    assert stranger.status_code == 404 and "api_key" not in stranger.get_json()
    assert sum(n for n, _ in booth_v2._PAIR_FAILS.values()) == 1, "counted on the brake"
    assert booth.phone_pair_poll()["state"] == "paired", "the booth still gets it"
    again = gallery.post("/api/v2/pair/status", json={"code": _code(started), "verifier": "x"})
    assert again.status_code == 404


def test_declined_and_expired_requests_say_so(app, gallery, booth):
    from app import db
    started = booth.phone_pair_start("https://galerie.example.de")
    phone = _admin(app)
    phone.post(f"/admin/devices/pair?c={_code(started)}", data={"csrf_token": "t", "action": "decline"})
    assert booth.phone_pair_poll()["state"] == "declined"
    assert not booth.store.local["server"]["api_key"]

    started = booth.phone_pair_start("")
    con = db.get_db()
    con.execute("UPDATE pairing_requests SET expires_at=datetime('now','-1 minute') WHERE code=?", (_code(started),))
    con.commit()
    con.close()
    assert booth.phone_pair_poll()["state"] == "expired"
    r = phone.post(f"/admin/devices/pair?c={_code(started)}", data={
        "csrf_token": "t", "action": "confirm", "device_id": "new"})
    assert "Abgelaufen" in r.get_data(as_text=True)


def test_a_new_request_replaces_the_open_one(gallery, booth):
    first = booth.phone_pair_start("https://galerie.example.de")
    second = booth.phone_pair_start("https://galerie.example.de")
    from app import phone_pairing
    assert phone_pairing.get_request(_code(first)) is None
    assert phone_pairing.state_of(phone_pairing.get_request(_code(second))) == "open"


def test_opening_requests_is_braked_per_address(gallery):
    from app.blueprints import booth_v2
    body = lambda: {"verifier_hash": hashlib.sha256(secrets.token_bytes(8)).hexdigest(),  # noqa: E731
                    "booth_name": "x", "install_id": secrets.token_hex(4)}
    codes = [gallery.post("/api/v2/pair/request", json=body()).status_code for _ in range(booth_v2.PAIR_OPEN_MAX + 1)]
    assert codes[0] == 200 and codes[-1] == 429
    assert gallery.post("/api/v2/pair/request", json={"verifier_hash": "kurz"}).status_code in (400, 429)


def test_the_phone_lands_on_the_request_after_logging_in(gallery):
    from app import phone_pairing
    made = phone_pairing.open_request(hashlib.sha256(b"v").hexdigest(), "Box", "1.0", "abc", "127.0.0.1")
    r = gallery.get(f"/admin/devices/pair?c={made['code']}")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]
    with gallery.session_transaction() as s:
        s["csrf_token"] = "t"
    r = gallery.post("/admin/login", data={"csrf_token": "t", "username": "chef",
                                           "password": "ein-langes-start-passwort"})
    assert r.headers["Location"].endswith(f"/admin/devices/pair?c={made['code']}")


def test_only_exact_local_paths_come_back_after_login(gallery):
    with gallery.session_transaction() as s:
        s["after_login"] = "https://evil.example/admin/devices/pair?c=ABCDEFGH"
        s["csrf_token"] = "t"
    r = gallery.post("/admin/login", data={"csrf_token": "t", "username": "chef",
                                           "password": "ein-langes-start-passwort"})
    assert r.headers["Location"].endswith("/admin/dashboard")


def test_maintenance_removes_old_requests(gallery):
    from app import db, maintenance, phone_pairing
    made = phone_pairing.open_request(hashlib.sha256(b"w").hexdigest(), "Box", "1.0", "def", "127.0.0.1")
    con = db.get_db()
    con.execute("UPDATE pairing_requests SET expires_at=datetime('now','-2 days'), created_at=datetime('now','-2 days') "
                "WHERE code=?", (made["code"],))
    con.commit()
    con.close()
    phone_pairing.cleanup()
    assert phone_pairing.get_request(made["code"]) is None
    assert "phone_pairing.cleanup()" in (ROOT / "app" / "maintenance.py").read_text(encoding="utf-8")
    assert maintenance  # imported: the run calls the cleanup


def test_pairing_goes_through_mcp_only_with_secure_and_two_steps():
    """Until 15.11.2026 nothing of pairing went through MCP. The owner wants every
    booth action reachable there; confirming a request now needs the extra
    right ``secure`` and the two-step confirm, and no tool hands out a code or key."""
    from app import mcp
    text = (ROOT / "app" / "mcp.py").read_text(encoding="utf-8")
    assert not re.search(r"pairing_requests|phone_pairing|/api/v2/pair", text)
    for name in ("list_pairing_requests", "confirm_pairing_request", "decline_pairing_request"):
        assert mcp.TOOLS[name]["scope"] == "secure", name
    assert mcp.TOOLS["confirm_pairing_request"]["confirm"] is not None


# ── Security review G3, finding S-2 (15.11.2026): pairing phishing ──
# Anyone can open a request and send the QR link to the operator. Confirming
# it for an EXISTING box used to hand that box's current key to whoever polls.

def _open(gallery, ua="photobox-booth/9.9 (Fremder)"):
    verifier = secrets.token_hex(16)
    r = gallery.post("/api/v2/pair/request", headers={"User-Agent": ua},
                     json={"verifier_hash": hashlib.sha256(verifier.encode()).hexdigest(),
                           "booth_name": "Box im Flur", "install_id": secrets.token_hex(4)})
    return r.get_json()["code"], verifier


def test_confirming_for_an_existing_box_never_hands_out_its_current_key(app, gallery):
    from app import db
    dev = db.create_device(name="Laufende Box")
    old_key = dev["api_key"]
    code, verifier = _open(gallery)
    phone = _admin(app)
    page = phone.get(f"/admin/devices/pair?c={code}").get_data(as_text=True)
    assert "getrennt" in page, "the form warns that a running box is disconnected"
    phone.post(f"/admin/devices/pair?c={code}", data={
        "csrf_token": "t", "action": "confirm", "device_id": str(dev["id"])})
    # the running box is cut off the moment the request is confirmed
    assert db.get_device_by_api_key(old_key) is None
    got = gallery.post("/api/v2/pair/status", json={"code": code, "verifier": verifier}).get_json()
    assert got["state"] == "paired" and got["api_key"] != old_key
    assert db.get_device_by_api_key(got["api_key"])["id"] == dev["id"]


def test_the_confirm_page_shows_who_asked_and_warns(app, gallery):
    code, _ = _open(gallery, ua="Mozilla/5.0 Fremder-Browser")
    page = _admin(app).get(f"/admin/devices/pair?c={code}").get_data(as_text=True)
    assert "Nur bestätigen, wenn du den QR-Code selbst gerade an der Box geöffnet hast" in page
    assert "Fremder-Browser" in page
    assert "127.0.0.1" in page                    # the test client's address
    assert "Angefragt" in page


def test_a_request_lives_only_minutes_and_a_confirmed_one_expires_too(app, gallery):
    from app import db, phone_pairing
    assert phone_pairing.REQUEST_MINUTES <= 5
    code, verifier = _open(gallery)
    _admin(app).post(f"/admin/devices/pair?c={code}", data={
        "csrf_token": "t", "action": "confirm", "device_id": "new", "new_name": "Spät"})
    con = db.get_db()
    con.execute("UPDATE pairing_requests SET expires_at=datetime('now','-1 minute') WHERE code=?", (code,))
    con.commit()
    con.close()
    got = gallery.post("/api/v2/pair/status", json={"code": code, "verifier": verifier}).get_json()
    assert got["state"] == "expired" and "api_key" not in got


def test_a_confirmed_request_is_collected_exactly_once(app, gallery):
    code, verifier = _open(gallery)
    _admin(app).post(f"/admin/devices/pair?c={code}", data={
        "csrf_token": "t", "action": "confirm", "device_id": "new", "new_name": "Einmal"})
    first = gallery.post("/api/v2/pair/status", json={"code": code, "verifier": verifier}).get_json()
    second = gallery.post("/api/v2/pair/status", json={"code": code, "verifier": verifier}).get_json()
    assert first["state"] == "paired" and first["api_key"]
    assert second["state"] == "collected" and "api_key" not in second
