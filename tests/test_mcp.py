"""MCP-Zugang: Protokoll, Werkzeuge, und was bewusst fehlt.

Der Zugang ist dafür da, dass ein KI-Assistent die Verwaltung bedienen kann,
wenn der Betreiber mit dem Handy in der Hand vor einer Box steht, die nicht
druckt. Er ist ausdrücklich nicht dafür da, an Gästefotos zu kommen.
"""
import base64
import json

import pytest


@pytest.fixture()
def mcp(client):
    """Galerie mit eingerichtetem MCP-Schlüssel."""
    client.get("/healthz")
    from app import ecosystem, mcp as mcp_mod
    ecosystem.init_schema()
    key = mcp_mod.new_key()

    def call(method, params=None, msg_id=1, headers=None):
        head = {"X-API-Key": key}
        if headers is not None:
            head = headers
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": msg_id, "method": method,
                                      "params": params or {}}, headers=head)
        return r

    def tool(name, arguments=None):
        r = call("tools/call", {"name": name, "arguments": arguments or {}})
        assert r.status_code == 200, r.get_data(as_text=True)
        payload = r.get_json()["result"]
        text = payload["content"][0]["text"]
        return payload.get("isError", False), (json.loads(text) if not payload.get("isError") else text)

    call.tool = tool
    call.key = key
    return call


def test_it_speaks_the_handshake(mcp):
    r = mcp("initialize")
    result = r.get_json()["result"]
    # A client that names no version gets the newest one spoken here.
    from app import mcp as mcp_mod
    assert result["protocolVersion"] == mcp_mod.PROTOCOL_VERSIONS[0]
    assert result["serverInfo"]["name"] == "photobox-gallery"
    assert "tools" in result["capabilities"]


def test_a_notification_gets_no_answer(mcp):
    r = mcp("notifications/initialized")
    assert r.status_code == 202 and not r.get_data()


def test_every_tool_describes_itself(mcp):
    tools = mcp("tools/list").get_json()["result"]["tools"]
    assert len(tools) >= 10
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"
        # Pflichtfelder müssen auch im Schema stehen, sonst rät die KI
        for name in t["inputSchema"].get("required", []):
            assert name in t["inputSchema"]["properties"], (t["name"], name)


def test_without_the_key_nothing_works(client):
    client.get("/healthz")
    from app import mcp as mcp_mod
    mcp_mod.new_key()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"X-API-Key": "falsch"})
    assert r.status_code == 401


def test_a_bearer_token_works_too(client):
    client.get("/healthz")
    from app import mcp as mcp_mod
    key = mcp_mod.new_key()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200


def test_an_event_can_be_created_and_listed(mcp):
    err, created = mcp.tool("create_event", {"name": "Hochzeit Meier", "retention_days": 14})
    assert not err and created["slug"]
    # The upload token is a secret: it stays in the admin (17.11.2026).
    from app import db
    assert db.get_event_by_id(created["id"])["upload_token"] not in json.dumps(created)

    err, events = mcp.tool("list_events")
    assert not err
    assert any(e["slug"] == created["slug"] and e["retention_days"] == 14 for e in events)


def test_a_booth_profile_can_be_changed_in_one_place(mcp):
    from app import db
    dev_id = db.create_device(name="MCP-Box")["id"]

    err, before = mcp.tool("get_booth_profile", {"booth_id": dev_id})
    assert not err and before["print"]["enabled"] is True

    err, result = mcp.tool("update_booth_profile",
                           {"booth_id": dev_id, "changes": {"print": {"enabled": False},
                                                            "flash": {"enabled": True}}, "return": "full"})
    assert not err
    # Nur das Angefasste ändert sich; der Rest bleibt stehen.
    assert result["profile"]["print"]["enabled"] is False
    assert result["profile"]["flash"]["enabled"] is True
    assert result["profile"]["print"]["copies_max"] == before["print"]["copies_max"]


def test_the_pin_never_leaves_through_this_door(mcp):
    """Der Schlüssel landet im Chatverlauf einer KI. Die PIN hat da nichts zu suchen."""
    from app import db, ecosystem
    dev_id = db.create_device(name="MCP-Box 2")["id"]
    ecosystem.save_device_profile(dev_id, {"access": {"pin": "424242"}}, None)

    err, profile = mcp.tool("get_booth_profile", {"booth_id": dev_id})
    assert not err and "pin" not in profile.get("access", {})

    err, result = mcp.tool("update_booth_profile", {"booth_id": dev_id, "changes": {"qr_on_screen": False},
                                                    "return": "full"})
    assert not err and "pin" not in result["profile"].get("access", {})


def test_jobs_can_be_sent_and_are_checked(mcp):
    from app import db
    dev_id = db.create_device(name="MCP-Box 3")["id"]
    err, result = mcp.tool("send_booth_job", {"booth_id": dev_id, "job": "test_print"})
    assert not err and result["job"] == "test_print"

    err, message = mcp.tool("send_booth_job", {"booth_id": dev_id, "job": "selbstzerstoerung"})
    assert err and "Unbekannter Auftrag" in message


def test_diagnostics_return_logs_and_jobs(mcp):
    from app import db
    dev_id = db.create_device(name="MCP-Box 4")["id"]
    err, data = mcp.tool("booth_diagnostics", {"booth_id": dev_id})
    assert not err and set(data) == {"status", "logs", "jobs", "error_groups_24h", "errors_acknowledged_at"}


def test_an_asset_can_be_added(mcp):
    png = base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a"
        "49444154789c6300010000050001` 0d0a2db40000000049454e44ae426082".replace("` ", "")
    )).decode()
    err, result = mcp.tool("add_asset", {"kind": "background", "filename": "wand.png",
                                         "content_base64": png})
    assert not err and result["kind"] == "background"

    err, assets = mcp.tool("list_assets", {"kind": "background"})
    assert not err and any(a["id"] == result["id"] for a in assets)


def test_a_wrong_file_type_is_refused_with_a_reason(mcp):
    err, message = mcp.tool("add_asset", {"kind": "background", "filename": "skript.exe",
                                          "content_base64": base64.b64encode(b"MZ").decode()})
    assert err and "nicht erlaubt" in message


def test_there_is_no_way_to_guest_photos(mcp):
    """Bewusste Lücke: kein Werkzeug gibt Aufnahmen heraus oder löscht sie."""
    names = {t["name"] for t in mcp("tools/list").get_json()["result"]["tools"]}
    for forbidden in ("get_media", "list_media", "download_media", "delete_media", "delete_event"):
        assert forbidden not in names


def test_a_broken_call_does_not_break_the_connection(mcp):
    err, message = mcp.tool("get_booth_profile", {})          # Pflichtfeld fehlt
    assert err and "Falsche Angaben" in message
    # Danach geht es normal weiter.
    assert mcp("ping").get_json()["result"] == {}


def test_an_unknown_method_is_a_protocol_error(mcp):
    r = mcp("tools/summon")
    assert r.get_json()["error"]["code"] == -32601


def test_the_admin_page_shows_the_key_and_what_it_can_do(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    from app import mcp as _mcp
    _mcp.revoke_key()                         # andere Tests teilen sich diese Datenbank
    with client.session_transaction() as s:
        s["admin_logged_in"] = True

    page = client.get("/admin/settings/mcp")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Noch kein Zugang eingerichtet" in body
    # Die Grenze des Zugangs steht auf der Seite, nicht nur im Code: Standard
    # ist Lesen und Verwalten, alles darüber ist ein Zusatzrecht mit Passwort.
    assert "Standard ist Lesen und Verwalten" in body and "nie auslesen" in body

    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    r = client.post("/admin/settings/mcp", data={"csrf_token": token, "action": "rotate"},
                    follow_redirects=True)
    assert r.status_code == 200
    from app import mcp as mcp_mod
    import re
    shown = re.findall(r"pbmcp_[A-Za-z0-9_\-]+", r.get_data(as_text=True))
    assert shown and mcp_mod._key_hash(shown[0]) == mcp_mod.current_key()   # shown once, stored as hash


def test_a_new_key_invalidates_the_old_one(client):
    client.get("/healthz")
    from app import ecosystem, mcp as mcp_mod
    ecosystem.init_schema()
    old = mcp_mod.new_key()
    new = mcp_mod.new_key()
    assert old != new

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"X-API-Key": old})
    assert r.status_code == 401
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"X-API-Key": new})
    assert r.status_code == 200


def test_revoking_closes_the_door(client):
    client.get("/healthz")
    from app import db, ecosystem, mcp as mcp_mod
    ecosystem.init_schema()
    key = mcp_mod.new_key()
    mcp_mod.revoke_key()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"X-API-Key": key})
    assert r.status_code == 401


# ── Deckt der Zugang ab, was die Galerie heute kann? ──────
# Diese Gruppe ist der Wächter: Wer eine Funktion baut und den MCP-Zugang
# vergisst, merkt es hier — und wer Felder umbenennt, auch.

def test_the_booth_fields_are_the_ones_the_booth_really_reports(mcp, client):
    """Vorher standen hier erfundene Namen, und jedes Feld war null."""
    from app import booth as booth_status, db
    dev = db.create_device(name="MCP-Box")
    client.post("/api/v2/booth/sync", json={"status": {
        "camera_state": "ready", "camera_backend": "mock", "printer_status": "ready",
        "printer_name": "SELPHY", "prints_today": 7, "prints_pending": 1, "prints_held": 0,
        "queue_pending": 2, "queue_failed": 0, "disk_free_gb": 40.0, "sessions_total": 12,
        "booth_version": "photobox-booth 0.2.0", "profile_version": 3,
        "sheets": [{"slot": 1, "quelle": "gezählt", "rest": 96, "gesamt": 108, "leer": False}],
    }}, headers={"X-API-Key": dev["api_key"]})

    fehler, boxen = mcp.tool("list_booths")
    assert not fehler
    meine = next(b for b in boxen if b["id"] == dev["id"])
    assert meine["camera_state"] == "ready" and meine["printer_name"] == "SELPHY"
    assert meine["prints_today"] == 7 and meine["sessions_total"] == 12
    assert (meine["sheets"] or [{}])[0].get("rest") == 96
    # Und die Namen stammen wirklich aus dem Statusschema der Box — sonst
    # steht hier wieder überall null.
    eigene = {"id", "name", "event_id", "last_heartbeat_at", "health", "reasons", "stale",
              # the light itself (app/booth.py light), shared with the admin page
              "findings", "age_seconds", "age_text", "event_running", "paper_text", "print_queue",
              "upload_queue", "doctor_worst"}
    assert set(meine) - eigene <= set(booth_status.ALLOWED_FIELDS)


def test_the_numbers_of_an_event_come_through(mcp, client):
    import secrets
    from app import db
    client.get("/healthz")
    eid = db.create_event(name="MCP-Fest", slug="mcp-" + secrets.token_hex(3),
                          upload_token=secrets.token_hex(8))
    db.add_stat(eid, "download")
    fehler, daten = mcp.tool("event_stats", {"event_id": eid})
    assert not fehler and daten["actions"]["download"] == 1
    assert "days" in daten and "devices" in daten


def test_a_box_can_be_looked_at_day_by_day(mcp, client):
    from app import db
    dev = db.create_device(name="MCP-Zählbox")
    client.post("/api/v2/booth/sync", json={"status": {"sessions_total": 10, "prints_today": 2}},
                headers={"X-API-Key": dev["api_key"]})
    client.post("/api/v2/booth/sync", json={"status": {"sessions_total": 40, "prints_today": 9}},
                headers={"X-API-Key": dev["api_key"]})
    fehler, daten = mcp.tool("booth_stats", {"booth_id": dev["id"]})
    assert not fehler and daten["sessions_total"] == 30 and daten["prints_total"] == 7


def test_silent_operation_can_be_switched_from_a_chat(mcp, client):
    """Der Fall, für den es den Zugang gibt: unterwegs, Handy in der Hand."""
    import secrets
    from app import db, gallery_settings as gsm
    client.get("/healthz")
    eid = db.create_event(name="Stiller Abend", slug="mcp-still-" + secrets.token_hex(3),
                          upload_token=secrets.token_hex(8))
    fehler, vorher = mcp.tool("get_event_settings", {"event_id": eid})
    assert not fehler and vorher["settings"]["silent"] is False

    fehler, nachher = mcp.tool("set_event_settings", {"event_id": eid, "changes": {"silent": True, "kiosk": True}})
    assert not fehler and nachher["settings"]["silent"] is True and nachher["settings"]["kiosk"] is True
    gespeichert = gsm.parse(db.get_event_by_id(eid)["gallery_json"])
    assert gespeichert["silent"] is True


def test_only_the_agreed_switches_can_be_changed(mcp, client):
    import secrets
    from app import db
    client.get("/healthz")
    eid = db.create_event(name="Grenzen", slug="mcp-grenz-" + secrets.token_hex(3),
                          upload_token=secrets.token_hex(8))
    # Since 17.11.2026 every gallery setting is reachable, but the expiry
    # decides who still sees the pictures: with read + manage it is refused.
    r = mcp("tools/call", {"name": "set_event_settings",
                           "arguments": {"event_id": eid, "changes": {"expires_at": "2099-01-01"}}})
    assert r.status_code == 403
    from app import gallery_settings as gsm
    assert gsm.for_event(db.get_event_by_id(eid))["expires_at"] == ""


def test_every_job_the_gallery_knows_can_be_sent(mcp):
    """Neue Aufträge (Papier gewechselt, Update holen) sollen nicht vergessen werden."""
    from app import ecosystem, mcp as mcp_mod
    schema = mcp_mod.TOOLS["send_booth_job"]["schema"]
    # Jobs that need a payload have their own tools (final review A14).
    assert set(schema["properties"]["job"]["enum"]) == (set(ecosystem.JOB_TYPES) - mcp_mod.NOT_FOR_ASSISTANTS
                                                       - mcp_mod.NEEDS_PAYLOAD - mcp_mod.NEEDS_CONTROL
                                                       - mcp_mod.NEEDS_SECURE)


def test_an_assistant_cannot_roll_out_software(mcp):
    """The module promises "no software distribution" -- and a job did it anyway.

    install_update puts a new version on a booth and restarts it. That is not
    something to happen in passing in a chat, least of all during an event.
    Updates stay with the admin pages.
    """
    from app import db, ecosystem, mcp as mcp_mod
    assert {"install_update", "fetch_update"} <= mcp_mod.NOT_FOR_ASSISTANTS <= set(ecosystem.JOB_TYPES)
    dev_id = db.create_device(name="MCP-Box Update")["id"]
    for job in ("install_update", "fetch_update"):
        err, message = mcp.tool("send_booth_job", {"booth_id": dev_id, "job": job})
        assert err and "Verwaltung" in str(message)
    assert ecosystem.jobs_waiting(dev_id) == 0


def test_templates_and_software_are_visible(mcp):
    fehler, vorlagen = mcp.tool("list_templates")
    assert not fehler and len(vorlagen["builtin"]) >= 14 and "hochzeit" in vorlagen["stile"]
    fehler, software = mcp.tool("list_software")
    assert not fehler and "versions" in software and "booths" in software


def test_guest_data_stays_out_of_the_chat(mcp):
    """Zugriffsanfragen enthalten E-Mail-Adressen von Gästen.

    Since 15.11.2026 the owner wants assistants to decide them
    (``list_access_requests``, ``decide_access_request``, scope manage); the
    address still never reaches the chat in full - it is shortened
    (tests/test_mcp_paket_d.py). No other tool may carry "access" or "mail".
    """
    from app import mcp as mcp_mod
    namen = " ".join(n for n in mcp_mod.TOOLS if n not in ("list_access_requests", "decide_access_request"))
    assert "access" not in namen and "mail" not in namen
    assert not any("media" == n or n.startswith("download") for n in mcp_mod.TOOLS)
