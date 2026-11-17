"""Second review R2-U5: operator Markdown cannot tie up a worker.

10,000 times ``[a](`` in an imprint took 17 s to render: the Markdown
library looks for each link's end with a backtracking pattern. Links are
found by app/safe_markdown.py now (one pattern that cannot backtrack), texts
are limited to MAX_CHARS in the form, the MCP tool and the renderer, and a
rendered text is cached by its content.
"""
import json
import secrets
import time

import pytest

from app import safe_markdown

PATHOLOGICAL = ["[a](", "![a](", "[a](((", "[a](x(", "](", "[", "[a][", "*a", "_a", "**a", "`a", "\\",
                "<a", "&a", "&lt;", "> ", ">\n", "a\n", "[*a](_", "|a", "- - - "]


@pytest.mark.parametrize("unit", PATHOLOGICAL)
def test_50_kb_of_a_pathological_pattern_renders_in_under_200_ms(unit):
    text = unit * (50_000 // len(unit))
    took = []
    for _ in range(3):                  # best of three: the full suite shares the CPU
        safe_markdown._render_cached.cache_clear()
        start = time.perf_counter()
        safe_markdown.render(text)
        took.append(time.perf_counter() - start)
    assert min(took) < 0.2, took


def test_rendering_time_grows_linearly_with_the_text():
    """Many blocks cost the library time per block, but never more than linear."""
    def took(n):
        safe_markdown._render_cached.cache_clear()
        start = time.perf_counter()
        safe_markdown.render("# a\n\n" * n)
        return time.perf_counter() - start
    took(200)                                        # warm up
    small, large = took(1000), took(2000)
    assert large < small * 3


def test_a_rendered_text_is_cached():
    text = "Hallo **Welt** " + secrets.token_hex(8)
    safe_markdown._render_cached.cache_clear()
    first = safe_markdown.render(text)
    info = safe_markdown._render_cached.cache_info()
    assert safe_markdown.render(text) == first
    assert safe_markdown._render_cached.cache_info().hits == info.hits + 1


def test_the_renderer_cuts_at_the_limit():
    out = safe_markdown.render("a" * (safe_markdown.MAX_CHARS + 500))
    assert out.count("a") == safe_markdown.MAX_CHARS


def test_links_still_work_and_unsafe_ones_lose_their_target():
    out = safe_markdown.render("Siehe [Datenschutz & mehr](https://example.org/d?a=1&b=2), "
                               "[Wiki](https://de.wikipedia.org/wiki/Foo_(Bar)) und [x](javascript:alert(1))")
    assert '<a href="https://example.org/d?a=1&amp;b=2">Datenschutz &amp; mehr</a>' in out
    assert 'href="https://de.wikipedia.org/wiki/Foo_(Bar)"' in out
    assert "javascript" not in out and "<a>x</a>" in out


def test_line_breaks_inside_a_paragraph_stay():
    assert safe_markdown.render("eins\nzwei") == "<p>eins<br />\nzwei</p>"


_MADE = []


def _token(scope):
    from app import accounts, db, oauth
    cid = "pbc_" + secrets.token_hex(6)
    _MADE.append(cid)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    token = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp",
                         accounts.session_epoch())["access_token"]
    con.commit()
    con.close()
    return token


def test_the_mcp_tool_refuses_a_text_over_the_limit(client):
    from app import db, ecosystem
    client.get("/healthz")
    ecosystem.init_schema()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "set_legal_texts",
                                             "arguments": {"impressum_text": "[a](" * 10_000}}},
                    headers={"Authorization": f"Bearer {_token('read secure')}"})
    result = r.get_json()["result"]
    con = db.get_db()
    con.executemany("DELETE FROM oauth_clients WHERE client_id=?", [(c,) for c in _MADE])
    con.commit()
    con.close()
    assert result.get("isError") and "20000" in result["content"][0]["text"]
    assert (db.get_setting("impressum_text") or "") == ""


def test_the_admin_form_refuses_a_text_over_the_limit(client):
    from app import db
    client.get("/healthz")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    client.get("/admin/settings/legal")
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    r = client.post("/admin/settings/legal", data={"csrf_token": token, "impressum_text": "x" * 20_001},
                    follow_redirects=True)
    assert "höchstens 20000 Zeichen" in r.get_data(as_text=True)
    assert (db.get_setting("impressum_text") or "") == ""
