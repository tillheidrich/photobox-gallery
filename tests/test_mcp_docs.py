"""docs/MCP.md is the contract with whoever connects an assistant: it must name
every tool with its scope, and the business-logic register must carry the
rules of the door. A tool added without a line here fails the suite.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "MCP.md"


def test_every_tool_is_documented_with_its_scope():
    from app import mcp
    text = DOC.read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| `([a-z_]+)` \| ([a-z+]+) \|", text, flags=re.M))
    for name, spec in mcp.TOOLS.items():
        expected = "+".join((spec["scope"],) + tuple(spec.get("also", ())))
        assert rows.get(name) == expected, f"docs/MCP.md: {name} missing or with the wrong scope"
    assert set(rows) == set(mcp.TOOLS), "docs/MCP.md lists tools that do not exist"


def test_the_doc_names_the_spec_revision_and_how_to_withdraw():
    text = DOC.read_text(encoding="utf-8")
    assert "2026-07-28" in text and "Withdrawing access" in text
    assert "/.well-known/oauth-protected-resource" in text


def test_the_business_logic_register_carries_the_rules_of_the_door():
    index = (ROOT / "business_logic" / "index.md").read_text(encoding="utf-8")
    assert "work/mcp-oauth" in index and "docs/MCP.md" in index
    for rule in ("PKCE", "refresh", "insufficient_scope", "set_local_config", "change log"):
        assert rule in index, rule
