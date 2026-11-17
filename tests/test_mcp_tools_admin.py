"""The tools that make the MCP door cover the whole administration.

Each goes through the same code as the admin form it stands in for: event
settings through ``gallery_settings.clean``, share links through
``sharelinks.create``, print templates through ``vorlagen.pruefe``, booth
texts against the catalogue in ``booth_texts``, hardware settings through
the allowlist the booth itself enforces again.
"""
import ast
import json
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def mcp(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()

    def token(scope="read manage"):
        from app import accounts, db, oauth
        cid = "pbc_" + secrets.token_hex(6)
        con = db.get_db()
        con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                    "VALUES (?,?,?,datetime('now'))", (cid, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
        answer = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp", accounts.session_epoch())
        con.commit()
        con.close()
        return answer["access_token"]

    full = token()

    def tool(name, arguments=None, credential=None):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments or {}}},
                        headers={"Authorization": f"Bearer {credential or full}"})
        if r.status_code != 200:
            return SimpleNamespace(status=r.status_code, error=True, data=None)
        result = r.get_json()["result"]
        text = result["content"][0]["text"]
        error = bool(result.get("isError"))
        return SimpleNamespace(status=200, error=error, data=text if error else json.loads(text))

    return SimpleNamespace(tool=tool, token=token)


def test_every_new_tool_has_its_scope():
    from app import mcp
    expected = {"setup_event": "manage", "configure_event": "manage",
                "create_share_link": "manage", "list_share_links": "read", "revoke_share_link": "manage",
                "get_template": "read", "validate_template": "read", "save_template": "manage",
                "get_booth_texts": "read", "set_booth_texts": "manage", "set_booth_hardware": "manage"}
    for name, scope in expected.items():
        assert mcp.TOOLS[name]["scope"] == scope, name


# ── Events ────────────────────────────────────────────────────────────

def test_a_customer_gallery_is_set_up_in_one_call(mcp):
    from app import db, gallery_settings as gsm
    name = "Hochzeit Kunde " + secrets.token_hex(2)
    r = mcp.tool("setup_event", {"name": name, "published": True, "password": "Torte-2026",
                                 "expires_at": "2026-12-31T23:00", "visible_from": "2026-12-01T18:00",
                                 "mode": "proofing", "retention_days": 14,
                                 "settings": {"watermark": True, "download_zip": True}},
                 credential=mcp.token("read manage photos"))   # an open gallery needs "photos" (A1)
    assert not r.error, r.data
    event = db.get_event_by_id(r.data["id"])
    assert event["published"] and event["status"] == "live" and event["retention_days"] == 14
    assert event["password_hash"] and db.check_event_password(event, "Torte-2026")
    assert event["scheduled_live_at"] == "2026-12-01T18:00"
    gs = gsm.parse(event["gallery_json"])
    assert gs["mode"] == "proofing" and gs["expires_at"] == "2026-12-31T23:00"
    assert gs["watermark"] is True and gs["download_zip"] is True
    assert r.data["url"].endswith("/gallery/" + event["slug"])
    assert r.data["password"] is True
    text = json.dumps(r.data)
    assert "Torte-2026" not in text and event["upload_token"] not in text


def test_a_second_event_with_the_same_name_gets_its_own_address(mcp):
    name = "Sommerfest " + secrets.token_hex(2)
    first = mcp.tool("setup_event", {"name": name}).data
    second = mcp.tool("setup_event", {"name": name}).data
    assert first["slug"] != second["slug"]


@pytest.mark.parametrize("bad", [{"mode": "chaos"}, {"expires_at": "irgendwann"}, {"visible_from": "bald"},
                                 {"settings": {"expires_at": "2020-01-01"}}, {"retention_days": 0}])
def test_setup_refuses_what_the_admin_form_would_not_store(mcp, bad):
    r = mcp.tool("setup_event", {"name": "Falsch " + secrets.token_hex(2), **bad})
    assert r.error


def test_an_existing_event_can_be_configured(mcp):
    from app import db, gallery_settings as gsm
    photos = mcp.token("read manage photos")       # password and visibility are "photos" (A1)
    created = mcp.tool("setup_event", {"name": "Umbau " + secrets.token_hex(2), "password": "alt-123"},
                       credential=photos).data
    r = mcp.tool("configure_event", {"event_id": created["id"], "password": "", "published": False,
                                     "expires_at": "", "mode": "presentation", "name": "Neuer Name"},
                 credential=photos)
    assert not r.error, r.data
    event = db.get_event_by_id(created["id"])
    assert not event["password_hash"] and not event["published"] and event["name"] == "Neuer Name"
    assert gsm.parse(event["gallery_json"])["mode"] == "presentation"
    assert r.data["password"] is False


def test_configure_changes_only_what_it_is_given(mcp):
    from app import db
    created = mcp.tool("setup_event", {"name": "Teilweise " + secrets.token_hex(2), "password": "bleibt-1",
                                       "published": True}, credential=mcp.token("read manage photos")).data
    mcp.tool("configure_event", {"event_id": created["id"], "mode": "proofing"})
    event = db.get_event_by_id(created["id"])
    assert db.check_event_password(event, "bleibt-1") and event["published"]


# ── Share links ───────────────────────────────────────────────────────

def test_a_share_link_is_made_without_handing_out_photos(mcp):
    # Share links open the photos: since 15.11.2026 they need "photos" too.
    links = mcp.token("read manage photos")
    created = mcp.tool("setup_event", {"name": "Freigabe " + secrets.token_hex(2)}).data
    r = mcp.tool("create_share_link", {"event_id": created["id"], "days": 7, "title": "Für die Eltern",
                                       "password": "Passphrase-9"}, links)
    assert not r.error, r.data
    assert "/l/" in r.data["url"] and r.data["expires_at"] and r.data["password"] is True
    assert not {"media", "media_ids", "files"} & set(r.data)
    listed = mcp.tool("list_share_links", {"event_id": created["id"]}, links).data
    assert [l["slug"] for l in listed] == [r.data["slug"]]
    assert not any(k in json.dumps(listed) for k in ("password_hash", "ip_hash"))
    gone = mcp.tool("revoke_share_link", {"event_id": created["id"], "link_id": listed[0]["id"]})
    assert not gone.error
    assert mcp.tool("list_share_links", {"event_id": created["id"]}, links).data[0]["status"] == "revoked"


def test_a_link_without_end_is_not_made_through_a_chat(mcp):
    created = mcp.tool("setup_event", {"name": "Ewig " + secrets.token_hex(2)}).data
    assert mcp.tool("create_share_link", {"event_id": created["id"], "days": 0},
                    mcp.token("read manage photos")).error


# ── Print templates ───────────────────────────────────────────────────

TEMPLATE = {"name": "MCP-Streifen", "paper": "10x15",
            "slots": [{"x": 60, "y": 60, "w": 500, "h": 500, "shot": 0},
                      {"x": 60, "y": 600, "w": 500, "h": 500, "shot": 2}],
            "texts": [{"value": "Anna & Ben", "x": 60, "y": 1600, "size": 60}]}


def test_a_template_is_checked_without_being_saved(mcp):
    from app import ecosystem
    before = len(ecosystem.list_assets("template"))
    r = mcp.tool("validate_template", {"template": TEMPLATE})
    assert not r.error, r.data
    assert r.data["template"]["shots"] == 2 and r.data["hints"]        # shots renumbered, and said so
    assert len(ecosystem.list_assets("template")) == before


def test_a_template_is_saved_through_the_editor_rules_and_read_back(mcp):
    r = mcp.tool("save_template", {"template": TEMPLATE})
    assert not r.error, r.data
    file = r.data["file"]
    back = mcp.tool("get_template", {"file": file})
    assert not back.error and back.data["name"] == "MCP-Streifen" and len(back.data["slots"]) == 2
    listed = mcp.tool("list_templates").data
    assert any(e["file"] == file for e in listed["eigene"])


@pytest.mark.parametrize("file", ["../../etc/passwd", "nicht-da.json", "x.png"])
def test_only_own_templates_can_be_read(mcp, file):
    assert mcp.tool("get_template", {"file": file}).error


def test_saving_a_template_needs_manage(mcp):
    r = mcp.tool("save_template", {"template": TEMPLATE}, credential=mcp.token("read"))
    assert r.status == 403


# ── Booth texts ───────────────────────────────────────────────────────

def test_booth_texts_are_read_with_their_catalogue(mcp):
    from app import db
    dev = db.create_device(name="Text-Box")
    # The catalogue is large: since 17.11.2026 only on request.
    assert "catalogue" not in mcp.tool("get_booth_texts", {"booth_id": dev["id"]}).data
    r = mcp.tool("get_booth_texts", {"booth_id": dev["id"], "include_catalogue": True})
    assert not r.error
    keys = {e["key"] for e in r.data["catalogue"]}
    assert "start.title" in keys and r.data["texts"] == {}
    entry = next(e for e in r.data["catalogue"] if e["key"] == "start.title")
    assert entry["default"]["de"] == "Tippen & lächeln" and entry["default"]["en"]


def test_booth_texts_are_set_and_cleared(mcp):
    from app import db, ecosystem
    dev = db.create_device(name="Text-Box 2")
    r = mcp.tool("set_booth_texts", {"booth_id": dev["id"], "lang": "de",
                                     "texts": {"start.title": "Lächeln, bitte!", "start.subtitle": "Hochzeit"}})
    assert not r.error, r.data
    profile = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert profile["texts"]["de"]["start.title"] == "Lächeln, bitte!"
    mcp.tool("set_booth_texts", {"booth_id": dev["id"], "lang": "de", "texts": {"start.title": ""}})
    profile = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert "start.title" not in profile["texts"]["de"] and profile["texts"]["de"]["start.subtitle"] == "Hochzeit"


@pytest.mark.parametrize("bad", [{"lang": "de", "texts": {"no.such.key": "x"}},
                                 {"lang": "xx", "texts": {"start.title": "x"}},
                                 {"lang": "de", "texts": {"start.title": "x" * 401}}])
def test_unknown_text_keys_languages_and_novels_are_refused(mcp, bad):
    from app import db
    dev = db.create_device(name="Text-Box 3")
    assert mcp.tool("set_booth_texts", {"booth_id": dev["id"], **bad}).error


# ── Hardware settings at the booth ────────────────────────────────────

def _booth_allowlist():
    """The booth's list, read as text: the suites do not share an import path."""
    tree = ast.parse((ROOT / "booth" / "boothcore" / "config.py").read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (node.targets[0] if isinstance(node, ast.Assign) else None)
        if getattr(target, "id", "") == "REMOTE_LOCAL_KEYS":
            return ast.literal_eval(node.value)
    raise AssertionError("REMOTE_LOCAL_KEYS not found in booth/boothcore/config.py")


def test_gallery_and_booth_agree_on_what_may_be_set_remotely():
    from app import ecosystem
    assert ecosystem.REMOTE_LOCAL_KEYS == _booth_allowlist()


def test_nothing_on_the_list_opens_a_server_a_key_a_pin_or_a_path():
    from app import ecosystem
    for key in ecosystem.REMOTE_LOCAL_KEYS:
        assert not key.startswith(("server.", "targets", "admin_pin", "port", "standalone", "event_claim"))
        assert not any(part in key for part in ("dll", "url", "uri", "folder", "path", "key", "pin", "password"))


def test_hardware_settings_go_to_the_booth_as_a_job(mcp):
    from app import db, ecosystem
    dev = db.create_device(name="Hardware-Box")
    r = mcp.tool("set_booth_hardware", {"booth_id": dev["id"],
                                        "settings": {"camera.rotate": 90, "printer.backend": "none",
                                                     "storage.usb.eject_hint": False}})
    assert not r.error, r.data
    job = ecosystem.recent_jobs(dev["id"], 1)[0]
    assert job["type"] == "set_local_config"
    assert json.loads(job["payload_json"]) == {"camera.rotate": 90, "printer.backend": "none",
                                               "storage.usb.eject_hint": False}


@pytest.mark.parametrize("settings", [{"server.url": "https://evil.example"}, {"admin_pin": "123456"},
                                      {"camera.edsdk_dll": "C:/evil.dll"}, {"storage.folder": "/etc"},
                                      {"storage.usb.enabled": True},
                                      {"camera.rotate": 45}, {"printer.backend": "hotfolder"},
                                      {"printer.sheets_total": "viele"}, {}])
def test_hardware_settings_outside_the_allowlist_are_refused(mcp, settings):
    from app import db, ecosystem
    dev = db.create_device(name="Hardware-Box 2")
    assert mcp.tool("set_booth_hardware", {"booth_id": dev["id"], "settings": settings}).error
    assert not ecosystem.recent_jobs(dev["id"], 5)
