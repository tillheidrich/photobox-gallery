"""A read-only status page per booth, for rentals (/status/<token>).

The owner rents the booth out. During the rental the customer, or the owner
on his phone without logging into the admin, opens one link and sees whether
the booth is fine: one light, one sentence, last contact, camera, printer,
today's numbers. Nothing else. The link is a secret: off by default, a random
token of which only the hash is stored, and every token that does not open a
page looks exactly like any other unknown address.
"""
import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def _fresh_brake(client):
    """All tests share one database and come from 127.0.0.1: start each with a clean count."""
    client.get("/healthz")
    from app import sharelinks, status_page
    sharelinks.forget(status_page.rate_key("127.0.0.1"))
    yield
    sharelinks.forget(status_page.rate_key("127.0.0.1"))


def _device(name=None):
    from app import db, ecosystem
    ecosystem.init_schema()
    _made = db.create_device(name=name or f"Mietbox {secrets.token_hex(2)}")
    did = _made["id"]
    return {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"


def _beat(client, dev, **status):
    body = {"status": {"booth_running": True, "camera_state": "ready", "disk_free_gb": 80.0, **status}}
    r = client.post("/api/v2/booth/sync", json=body, headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200, r.get_data(as_text=True)


def _switch(client, dev, action):
    """Press a button of the card; returns the link shown, if one is shown."""
    _admin(client)
    r = client.post(f"/admin/devices/{dev['id']}/status-page", data={"action": action, "csrf_token": "x"})
    assert r.status_code == 302
    page = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    found = re.search(r"/status/([A-Za-z0-9_-]{20,})", page)
    return found.group(1) if found else None


def _unknown_page(client):
    r = client.get(f"/gibt-es-nicht-{secrets.token_hex(4)}")
    assert r.status_code == 404
    return r.get_data(as_text=True)


# ── Switch ───────────────────────────────────────────────────

def test_off_by_default_a_link_is_a_404(client):
    from app import status_page
    dev = _device()
    _beat(client, dev)
    assert not status_page.is_enabled(dev["id"])
    _admin(client)
    page = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert 'id="statusseite"' in page
    assert client.get(f"/status/{secrets.token_urlsafe(24)}").status_code == 404


def test_switching_on_shows_the_link_once_and_the_page_answers(client):
    dev = _device("Mietbox Nord")
    _beat(client, dev)
    token = _switch(client, dev, "on")
    assert token and len(token) >= 32                       # 24 bytes from secrets = 192 bit
    # Shown once: the next visit of the booth page no longer has it.
    again = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert token not in again and "/status/" not in again
    r = client.get(f"/status/{token}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Mietbox Nord" in html
    assert "Alles in Ordnung" in html
    assert "Kamera" in html and "bereit" in html
    assert 'http-equiv="refresh"' in html


def test_the_token_is_stored_only_as_its_hash(client, app):
    from app import db
    dev = _device()
    token = _switch(client, dev, "on")
    con = db.get_db()
    try:
        dump = "\n".join(con.iterdump())
    finally:
        con.close()
    assert token not in dump
    assert hashlib.sha256(token.encode()).hexdigest() in dump


def test_wrong_malformed_and_switched_off_tokens_look_like_any_unknown_page(client):
    dev = _device()
    _beat(client, dev)
    token = _switch(client, dev, "on")
    reference = _unknown_page(client)
    tries = [secrets.token_urlsafe(24), "abc", "x" * 200, token[:-1] + ("A" if token[-1] != "A" else "B")]
    for bad in tries:
        r = client.get(f"/status/{bad}")
        assert r.status_code == 404, bad
        assert r.get_data(as_text=True) == reference, bad
    assert _switch(client, dev, "off") is None
    r = client.get(f"/status/{token}")
    assert r.status_code == 404
    assert r.get_data(as_text=True) == reference


def test_a_new_link_ends_the_old_one(client):
    dev = _device()
    _beat(client, dev)
    first = _switch(client, dev, "on")
    second = _switch(client, dev, "renew")
    assert first and second and first != second
    assert client.get(f"/status/{first}").status_code == 404
    assert client.get(f"/status/{second}").status_code == 200


def test_a_valid_link_is_never_braked(client):
    """Behind a proxy all visitors may share one address; phones refreshing every minute must not lock each other out."""
    from app import sharelinks, status_page
    dev = _device()
    _beat(client, dev)
    token = _switch(client, dev, "on")
    addr = {"REMOTE_ADDR": "10.9.8.7"}
    try:
        codes = [client.get(f"/status/{token}", environ_base=addr).status_code for _ in range(200)]
        assert 200 > status_page.RATE_LIMIT
        assert set(codes) == {200}
    finally:
        sharelinks.forget(status_page.rate_key("10.9.8.7"))


def test_wrong_tokens_are_braked_per_address_404_then_429(client):
    from app import sharelinks, status_page
    addr = {"REMOTE_ADDR": "10.9.8.8"}
    try:
        codes = [client.get(f"/status/{secrets.token_urlsafe(24)}", environ_base=addr).status_code
                 for _ in range(status_page.RATE_LIMIT)]
        assert set(codes) == {404}
        assert client.get(f"/status/{secrets.token_urlsafe(24)}", environ_base=addr).status_code == 429
        assert client.get("/status/abc", environ_base=addr).status_code == 429
        # Another address is not affected.
        assert client.get(f"/status/{secrets.token_urlsafe(24)}").status_code == 404
    finally:
        sharelinks.forget(status_page.rate_key("10.9.8.8"))


def test_a_valid_link_still_opens_while_wrong_ones_from_the_same_address_are_braked(client):
    from app import sharelinks, status_page
    dev = _device()
    _beat(client, dev)
    token = _switch(client, dev, "on")
    addr = {"REMOTE_ADDR": "10.9.8.9"}
    try:
        for _ in range(status_page.RATE_LIMIT):
            client.get(f"/status/{secrets.token_urlsafe(24)}", environ_base=addr)
        assert client.get(f"/status/{secrets.token_urlsafe(24)}", environ_base=addr).status_code == 429
        r = client.get(f"/status/{token}", environ_base=addr)
        assert r.status_code == 200
        assert client.get(f"/status/{secrets.token_urlsafe(24)}", environ_base=addr).status_code == 429
    finally:
        sharelinks.forget(status_page.rate_key("10.9.8.9"))


def test_headers_keep_the_token_out_of_caches_search_engines_and_referrers(client):
    dev = _device()
    _beat(client, dev)
    token = _switch(client, dev, "on")
    for url in (f"/status/{token}", f"/status/{secrets.token_urlsafe(24)}"):
        r = client.get(url)
        assert "no-store" in r.headers.get("Cache-Control", "")
        assert "noindex" in r.headers.get("X-Robots-Tag", "")
        assert r.headers.get("Referrer-Policy") == "no-referrer"
        # ... and the protections every answer of the gallery carries.
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert "frame-ancestors" in r.headers.get("Content-Security-Policy", "")


def test_read_only(client):
    dev = _device()
    _beat(client, dev)
    token = _switch(client, dev, "on")
    assert client.post(f"/status/{token}").status_code == 405
    html = client.get(f"/status/{token}").get_data(as_text=True)
    assert "<form" not in html


def test_the_page_shows_none_of_what_a_leaked_link_must_not_reveal(client):
    from app import db, ecosystem
    slug = "geheim-" + secrets.token_hex(3)
    eid = db.create_event(name="Hochzeit Geheimname", slug=slug, upload_token=secrets.token_hex(8))
    dev = _device("Box Diskret")
    ecosystem.save_device_profile(dev["id"], {"modes": ["photo"]}, eid)
    dev = {**db.get_device_by_id(dev["id"]), "api_key": dev["api_key"]}
    mail, ip, key = "gast.leak@example.com", "192.168.178.23", dev["api_key"]
    _beat(client, dev,
          printer_name=f"DNP DS620 @ {ip}", printer_status="error", prints_held=2,
          printer_hint=f"Fach leer {mail} {key}",
          sheets=[{"slot": 1, "label": f"\\\\{ip}\\DNP", "quelle": "gezählt", "rest": 3, "gesamt": 400}],
          notices=[{"stufe": "kritisch", "titel": f"Mail an {mail} hängt", "text": "C:\\Users\\box\\x.log",
                    "quelle": "drucker", "at": 1},
                   {"stufe": "warnung", "titel": "Datei fehlt", "text": "C:\\Photobox\\assets\\logo.png",
                    "quelle": "box", "at": 1}],
          doctor={"worst": "fail", "at": 1, "findings": [
              {"name": "Medien", "state": "fail", "message": "Datei fehlt: C:\\Photobox\\assets\\logo.png"}]},
          booth_stats={"event_slug": slug, "day": date.today().isoformat(),
                       "today": {"runs.photo": 41, "prints": 38, "consent.gallery": 39},
                       "event_total": {"runs.photo": 41}})
    token = _switch(client, dev, "on")
    html = client.get(f"/status/{token}").get_data(as_text=True)
    for secret in (mail, ip, key, "127.0.0.1", "C:\\", "C:\\\\", "Photobox\\assets", "Hochzeit Geheimname", slug,
                   "/gallery/", "/data/", "<img", "camera_state", "printer_name", "{&#34;", '{"'):
        assert secret not in html, secret
    assert "Box Diskret" in html
    # Still useful: red, the printer needs attention, today's numbers are there.
    assert "Drucker" in html and "braucht Aufmerksamkeit" in html
    assert ">41<" in html and ">38<" in html


def test_silence_shows_since_when_there_is_no_connection(client):
    from app import db
    dev = _device()
    _beat(client, dev)
    token = _switch(client, dev, "on")
    old = (datetime.now(timezone.utc) - timedelta(minutes=25)).strftime("%Y-%m-%d %H:%M:%S")
    con = db.get_db()
    con.execute("UPDATE devices SET last_heartbeat_at=? WHERE id=?", (old, dev["id"]))
    con.commit()
    con.close()
    html = client.get(f"/status/{token}").get_data(as_text=True)
    assert "Keine Verbindung seit 25 Minuten" in html
    assert "Alles in Ordnung" not in html
    en = client.get(f"/status/{token}?lang=en").get_data(as_text=True)
    assert "No connection for 25 minutes" in en


def test_a_fresh_contact_reads_as_relative_time(client):
    dev = _device()
    _beat(client, dev)
    token = _switch(client, dev, "on")
    assert "gerade eben" in client.get(f"/status/{token}").get_data(as_text=True)
    assert "just now" in client.get(f"/status/{token}?lang=en").get_data(as_text=True)


def test_both_languages_render(client):
    dev = _device("Box Zweisprachig")
    _beat(client, dev, booth_stats={"event_slug": "x", "day": date.today().isoformat(),
                                    "today": {"runs.photo": 12, "prints": 9}, "event_total": {}})
    token = _switch(client, dev, "on")
    de = client.get(f"/status/{token}").get_data(as_text=True)
    en = client.get(f"/status/{token}?lang=en").get_data(as_text=True)
    assert '<html lang="de"' in de and '<html lang="en"' in en
    assert "Kamera" in de and "Aufnahmen heute" in de
    assert "Camera" in en and "Photo sessions today" in en and "All good" in en
    for page in (de, en):
        assert "Box Zweisprachig" in page and ">12<" in page and ">9<" in page


def test_no_mcp_tool_carries_the_status_link(client):
    from app import mcp as mcp_mod, status_page
    dev = _device()
    _beat(client, dev)
    token = _switch(client, dev, "on")
    digest = hashlib.sha256(token.encode()).hexdigest()
    key = mcp_mod.new_key()

    def call(name, args):
        r = client.post("/mcp", headers={"X-API-Key": key}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        return json.dumps(r.get_json())

    listed = client.post("/mcp", headers={"X-API-Key": key},
                         json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).get_data(as_text=True)
    answers = [listed, call("list_booths", {}), call("get_booth_profile", {"booth_id": dev["id"]}),
               call("booth_diagnostics", {"booth_id": dev["id"]})]
    for text in answers:
        assert token not in text and digest not in text
    assert status_page.TABLE not in (mcp_mod.__file__ and open(mcp_mod.__file__, encoding="utf-8").read())


# ── The admin route: CSRF, change log, deactivated booths ─────

@pytest.mark.parametrize("sent", [None, "falsch"])
def test_the_switch_without_a_valid_csrf_token_changes_nothing(client, sent):
    from app import status_page
    dev = _device()
    _admin(client)
    data = {"action": "on"}
    if sent is not None:
        data["csrf_token"] = sent
    assert client.post(f"/admin/devices/{dev['id']}/status-page", data=data).status_code == 400
    assert not status_page.is_enabled(dev["id"])
    # Same for "renew" and "off" on a booth that is switched on.
    token = _switch(client, dev, "on")
    for action in ("renew", "off"):
        data["action"] = action
        assert client.post(f"/admin/devices/{dev['id']}/status-page", data=data).status_code == 400
    assert status_page.is_enabled(dev["id"])
    assert client.get(f"/status/{token}").status_code == 200


def test_the_card_forms_carry_the_csrf_token(client):
    dev = _device()
    _admin(client)
    for before in (False, True):
        if before:
            _switch(client, dev, "on")
            _admin(client)
        page = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
        card = page[page.index('id="statusseite"'):]
        card = card[:card.index("</section>")]
        assert 'name="csrf_token"' in card and 'value="x"' in card


def test_on_new_link_and_off_are_in_the_change_log_without_the_token(client):
    from app import protokoll
    dev = _device()
    from app import db as _db
    con = _db.get_db()
    con.execute("DELETE FROM admin_aenderungen")
    con.execute("DELETE FROM admin_vorgaenge")
    con.commit()
    con.close()
    tokens = [_switch(client, dev, "on"), _switch(client, dev, "renew")]
    _switch(client, dev, "off")
    rows = [v for v in protokoll.liste(limit=20) if v["pfad"].endswith("/status-page")]
    assert len(rows) == 3
    whats = [(v["was"], v["ergebnis"]) for v in reversed(rows)]
    assert [w for w, _ in whats] == ["Statusseite eingeschaltet", "Statusseite: neuer Link", "Statusseite ausgeschaltet"]
    dump = json.dumps(protokoll.liste(limit=50), default=str)
    for token in tokens:
        assert token not in dump and hashlib.sha256(token.encode()).hexdigest() not in dump


def test_a_deactivated_booth_has_no_status_page(client):
    from app import db as _db
    dev = _device()
    _beat(client, dev)
    token = _switch(client, dev, "on")
    assert client.get(f"/status/{token}").status_code == 200
    _db.set_device_active(dev["id"], False)
    try:
        assert client.get(f"/status/{token}").status_code == 404
    finally:
        _db.set_device_active(dev["id"], True)
    assert client.get(f"/status/{token}").status_code == 200
