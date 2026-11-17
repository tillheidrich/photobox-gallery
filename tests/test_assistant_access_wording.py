"""The old name "KI-Zugang" is gone from what the operator reads: the page
and its pointers say "Assistenten-Zugang (MCP)", like the settings tab."""


def test_the_mcp_settings_page_title_uses_the_new_name(client):
    client.get("/healthz")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    page = client.get("/admin/settings/mcp").get_data(as_text=True)
    assert "KI-Zugang" not in page
    assert "<title>Assistenten-Zugang (MCP)" in page


def test_the_setup_hint_of_the_mcp_door_uses_the_new_name():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "app" / "mcp.py"
    text = src.read_text(encoding="utf-8")
    assert "Einstellungen → KI-Zugang" not in text
    assert "Einstellungen → Assistenten-Zugang (MCP)" in text
