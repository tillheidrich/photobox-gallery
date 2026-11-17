"""An assistant can make a frame for boomerang and video and assign it (MCP).

The sequence the docs promise: add_asset (the background picture),
validate_template and save_template with kind "motion_frame", then
update_booth_profile or configure_event with frames. Same checks as the
admin page; unknown names are refused; the rights stay as they were.
"""
import base64
import io
import json
import secrets
from types import SimpleNamespace

import pytest
from PIL import Image


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


def _png_b64(colour=(20, 60, 110)):
    buf = io.BytesIO()
    Image.new("RGB", (1080, 1920), colour).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _frame(background):
    return {"kind": "motion_frame", "name": "MCP Rahmen " + secrets.token_hex(2), "format": "9:16",
            "background": background, "window": {"x": 80, "y": 140, "w": 920, "h": 1320, "radius": 30},
            "texts": [{"value": "{event_name}", "x": 540, "y": 1500, "size": 72, "align": "center",
                       "color": "#ffffff"}]}


def test_upload_a_background_then_validate_save_and_assign(mcp):
    from app import db, ecosystem
    up = mcp.tool("add_asset", {"kind": "background", "filename": "rahmen-hintergrund.png",
                                "content_base64": _png_b64()})
    assert not up.error, up.data
    frame = _frame(up.data["file"])
    checked = mcp.tool("validate_template", {"template": frame})
    assert not checked.error and checked.data["template"]["kind"] == "motion_frame"
    saved = mcp.tool("save_template", {"template": frame})
    assert not saved.error and saved.data["file"].startswith("rahmen-")
    assert mcp.tool("get_template", {"file": saved.data["file"]}).data["window"]["w"] == 920
    listed = mcp.tool("list_templates").data
    assert any(e["file"] == saved.data["file"] for e in listed["rahmen"]["eigene"])
    assert {e["id"] for e in listed["rahmen"]["mitgeliefert"]} >= {"titel-unten", "titel-links"}
    assert all(e["file"] != saved.data["file"] for e in listed["eigene"])      # no print template

    dev = db.create_device(name="MCP-Rahmenbox")
    r = mcp.tool("update_booth_profile", {"booth_id": dev["id"], "changes": {"boomerang": {
        "frames": True, "frame_templates": [saved.data["file"]], "frame_builtins": ["titel-unten"]}}})
    assert not r.error, r.data
    p = ecosystem.device_profile(db.get_device_by_id(dev["id"]))["boomerang"]
    assert p["frames"] is True and p["frame_templates"] == [saved.data["file"]]
    files = {a["file"] for a in ecosystem.assets_for_profile(ecosystem.device_profile(db.get_device_by_id(dev["id"])))}
    assert {saved.data["file"], up.data["file"]} <= files


def test_a_frame_with_a_missing_picture_is_refused_in_german(mcp):
    r = mcp.tool("validate_template", {"template": _frame("nicht-hochgeladen.png")})
    assert r.error and "fehlt in den Medien" in r.data and "add_asset" in r.data


def test_unknown_frame_names_are_refused(mcp):
    from app import db
    dev = db.create_device(name="MCP-Rahmenbox 2")
    r = mcp.tool("update_booth_profile", {"booth_id": dev["id"], "changes": {"video": {
        "frames": True, "frame_builtins": ["gibt-es-nicht"]}}})
    assert r.error and "Unbekannte Rahmen" in r.data


def test_the_event_sets_frames_through_configure_event(mcp):
    created = mcp.tool("setup_event", {"name": "Sommerfest 2027 " + secrets.token_hex(2), "kind": "booth"})
    assert not created.error, created.data
    eid = created.data["id"]
    r = mcp.tool("configure_event", {"event_id": eid, "frames": {"video": {"frames": ["titel-links"],
                                                                            "none": False}}})
    assert not r.error, r.data
    assert r.data["frames"] == {"video": {"frames": ["titel-links"], "none": False, "default": ""}}
    bad = mcp.tool("configure_event", {"event_id": eid, "frames": {"video": {"frames": ["erfunden"]}}})
    assert bad.error and "Unbekannte Rahmen" in bad.data
    cleared = mcp.tool("configure_event", {"event_id": eid, "frames": {"video": {"frames": []}}})
    assert not cleared.error and cleared.data["frames"] == {}


def test_saving_a_frame_needs_manage(mcp):
    r = mcp.tool("save_template", {"template": _frame("x.png")}, credential=mcp.token("read"))
    assert r.status == 403
