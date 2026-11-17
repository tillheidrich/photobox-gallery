"""Booth log lines in the gallery carry no guest address and no secret.

Final review A4: the booth logged ``mail to <address> failed`` at WARNING,
its log buffer travels to the gallery, and ``booth_diagnostics`` handed the
line to any assistant with ``read``. The gallery now shortens addresses and
masks secrets when it stores a device log, again when it shows one (rows
stored before), and the MCP tool says its answer is untrusted text.
"""
import json
import secrets
from pathlib import Path

import pytest

from app import db, ecosystem, redact

ROOT = Path(__file__).resolve().parent.parent
GUEST = "anna.meier@example.org"


@pytest.mark.parametrize("raw, shown", [
    ("mail to anna.meier@example.org failed (attempt 1/5)", "mail to a***@example.org failed (attempt 1/5)"),
    ("550 5.1.1 <x.y+z@mail.example.co.uk>: unknown", "550 5.1.1 <x***@mail.example.co.uk>: unknown"),
    ("admin_pin=938271 gesetzt", "admin_pin=•••• gesetzt"),
    ("password: 'Geheim 12'", "password: '•••• 12'"),
    ("Authorization: Bearer pbk_abc.DEF-123", "Authorization: Bearer ••••"),
    ("Code PBX1-QUJDREVGR0g eingefügt", "Code •••• eingefügt"),
    ("https://g.example.org/gallery/x?k=s3cr3t&lang=de", "https://g.example.org/gallery/x?k=••••&lang=de"),
    ("Fotobox ist abgestürzt (Code 3)", "Fotobox ist abgestürzt (Code 3)"),
    # second review R2-U3: what the first rules let through
    ("Mail an jörg.müller@bäckerei-müller.de fehlgeschlagen", "Mail an j***@bäckerei-müller.de fehlgeschlagen"),
    ("user@xn--mller-kva.de", "u***@xn--mller-kva.de"),
    ("GET /api/mail?to=anna.meier%40example.org&x=1", "GET /api/mail?to=a***%40example.org&x=1"),
    ("doppelt kodiert anna%2540example.org", "doppelt kodiert a***%2540example.org"),
    ("QR WIFI:T:WPA;S:Fotobox Gäste;P:Sommer\\;fest2026;H:false;;", "QR WIFI:T:WPA;S:Fotobox Gäste;P:••••;H:false;;"),
    ("PIN 123456 eingegeben", "PIN •••• eingegeben"),
    ("Passwort ist Hochzeit2026!", "Passwort ist ••••"),
    ("the password is hunter2 ok", "the password is •••• ok"),
    ("password=geheim&user=x", "password=••••&user=x"),
    ("Kopplungscode ABCD-EF23 bestätigt", "Kopplungscode •••• bestätigt"),
    ("Version 1.2.3 vom 2026-11-15", "Version 1.2.3 vom 2026-11-15"),
    ("", ""),
])
def test_the_redaction_rules(raw, shown):
    assert redact.text(raw) == shown


def test_the_booth_has_the_same_rules():
    """The booth does not import the gallery; its copy must not drift."""
    gallery = (ROOT / "app" / "redact.py").read_text(encoding="utf-8")
    booth = (ROOT / "booth" / "boothcore" / "redact.py").read_text(encoding="utf-8")

    def body(text):
        return text[text.index("MASK = "):]
    assert body(gallery) == body(booth)
    sync = (ROOT / "sync" / "photobox_sync" / "redact.py").read_text(encoding="utf-8")
    assert body(gallery) == body(sync)


def test_the_sync_program_redacts_what_it_sends():
    import sys
    sys.path.insert(0, str(ROOT / "sync"))
    from photobox_sync.diagnostics import LogBuffer
    buf = LogBuffer()
    buf.add("warning", f"Upload für {GUEST} fehlgeschlagen, Passwort ist Geheim99", detail=f"to={GUEST}")
    sent = json.dumps(buf.take(), ensure_ascii=False)
    assert GUEST not in sent and "Geheim99" not in sent


def _device():
    return db.create_device(name="Log-Box-" + secrets.token_hex(3))


def test_a_device_log_is_stored_without_the_address(client):
    client.get("/healthz")
    ecosystem.init_schema()
    dev = _device()
    ecosystem.add_device_logs(dev["id"], [{"level": "warning", "message": f"mail to {GUEST} failed",
                                           "context": {"to": GUEST, "pin": "938271"}}])
    con = db.get_db()
    row = dict(con.execute("SELECT * FROM device_logs WHERE device_id=?", (dev["id"],)).fetchone())
    con.close()
    stored = json.dumps(row, ensure_ascii=False)
    assert GUEST not in stored and "938271" not in stored
    assert "a***@example.org" in row["message"]


def test_rows_stored_before_are_shown_without_the_address(client):
    client.get("/healthz")
    ecosystem.init_schema()
    dev = _device()
    con = db.get_db()
    con.execute("INSERT INTO device_logs(device_id, level, code, message, context_json, occurred_at) "
                "VALUES (?,?,?,?,?,datetime('now'))",
                (dev["id"], "warning", None, f"mail to {GUEST} failed", json.dumps({"to": GUEST})))
    con.commit()
    con.close()
    shown = json.dumps(ecosystem.device_logs(dev["id"]), ensure_ascii=False)
    assert GUEST not in shown and "a***@example.org" in shown


def test_booth_diagnostics_with_read_shows_no_guest_address_and_is_marked_untrusted(client):
    from app import accounts, mcp, oauth
    client.get("/healthz")
    ecosystem.init_schema()
    dev = _device()
    con = db.get_db()
    con.execute("INSERT INTO device_logs(device_id, level, code, message, context_json, occurred_at) "
                "VALUES (?,?,?,?,?,datetime('now'))",
                (dev["id"], "warning", None, f"mail to {GUEST} failed", "{}"))
    cid = "pbc_" + secrets.token_hex(6)
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    token = oauth._issue(con, cid, secrets.token_hex(6), "read", "http://localhost/mcp",
                         accounts.session_epoch())["access_token"]
    con.commit()
    con.close()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "booth_diagnostics", "arguments": {"booth_id": dev["id"]}}},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert GUEST not in text and "a***@example.org" in text
    assert mcp.TOOLS["booth_diagnostics"]["untrusted"] is True
