"""Security review G3, finding S-13 (15.11.2026): an assistant cannot move the QR code.

The QR code on the print is where every guest goes. With ``manage`` an
assistant could set the profile's ``qr.url`` or ``qr_url_template`` to any
address — a phishing page printed on every card of the evening. The two keys
are now read-only through MCP, like ``mail`` and ``webhooks``; the admin can
still set them.
"""
import pytest


def _call(client, key, name, args):
    return client.post("/mcp", headers={"X-API-Key": key},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": name, "arguments": args}}).get_json()


@pytest.mark.parametrize("changes", [
    {"qr": {"mode": "custom", "url": "https://evil.example/login"}},
    {"qr_url_template": "https://evil.example/{uid}"},
])
def test_manage_cannot_change_where_the_qr_code_points(client, changes):
    from app import db, ecosystem, mcp
    client.get("/healthz")
    ecosystem.init_schema()
    dev = db.create_device(name="Box QR-Ziel")
    key = mcp.new_key()
    before = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    out = _call(client, key, "update_booth_profile", {"booth_id": dev["id"], "changes": changes})
    assert out.get("error") or (out.get("result") or {}).get("isError"), out
    after = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert after.get("qr") == before.get("qr") and after.get("qr_url_template") == before.get("qr_url_template")


def test_the_lock_list_names_the_qr_keys():
    from app import mcp
    assert "qr" in mcp.PROFILE_LOCKED and "qr_url_template" in mcp.PROFILE_LOCKED
