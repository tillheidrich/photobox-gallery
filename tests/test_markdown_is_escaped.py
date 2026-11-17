"""Operator text rendered as Markdown for guests runs no script (final review A5).

``_markdown_to_html`` let raw HTML through while the content security policy
allows inline script, and the legal texts are writable from the admin form
and through MCP ``set_legal_texts``: a stored script on /impressum and
/datenschutz. The same converter path renders the FAQ intro and answers.
Now every such text goes through ``app.safe_markdown``: raw HTML is escaped
before the conversion and the result is filtered by an allow-list of tags
and attributes (standard library only); links keep http, https, mailto and
relative addresses. Ordinary Markdown still renders.
"""
import json
import secrets

import pytest

from app import db, safe_markdown

ATTACKS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "<a href=\"javascript:alert(1)\">x</a>",
    "[klick](javascript:alert(1))",
    "[klick](JaVaScRiPt:alert(1))",
    "[klick](  javascript:alert(1))",
    "[klick](java&#x73;cript:alert(1))",
    "[daten](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "[vb](vbscript:msgbox(1))",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    "<svg onload=alert(1)>",
    "<iframe src=\"https://evil.example\"></iframe>",
    "![x](https://example.org/x.png\" onerror=\"alert(1))",
    "<div style=\"background:url(javascript:alert(1))\">x</div>",
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_no_script_survives(attack):
    import re
    out = safe_markdown.render("Text davor\n\n" + attack + "\n\nText danach").lower()
    # What a browser would act on lives in tags; the attack may stay as visible text.
    tags = " ".join(re.findall(r"<[^>]*>", out))
    for bad in ("<script", "onerror", "onload", "javascript", "vbscript", "data:", "<iframe",
                "<svg", "<img", "style=", "<div"):
        assert bad not in tags, (attack, out)
    assert re.findall(r"<([a-z0-9]+)", out) and set(re.findall(r"<([a-z0-9]+)", out)) <= safe_markdown.ALLOWED_TAGS
    assert "text davor" in out and "text danach" in out


def test_ordinary_markdown_still_renders():
    md = ("## Überschrift\n\n**fett** und *kursiv*\n\n- eins\n- zwei\n\nDazwischen\n\n1. a\n2. b\n\n"
          "[Datenschutz](/datenschutz) · [Projekt](https://example.org/x?a=1&b=2) · "
          "[Mail](mailto:info@example.org) · [Anker](#oben)\n\n"
          "| Spalte | Wert |\n|---|---|\n| A | 1 |\n\n> Zitat\n\nZeile 1\nZeile 2 & mehr")
    out = safe_markdown.render(md)
    assert "<h2>Überschrift</h2>" in out
    assert "<strong>fett</strong>" in out and "<em>kursiv</em>" in out
    assert "<li>eins</li>" in out and "<ol>" in out
    assert 'href="/datenschutz"' in out and 'href="https://example.org/x?a=1&amp;b=2"' in out
    assert 'href="mailto:info@example.org"' in out and 'href="#oben"' in out
    assert "<table>" in out and "<td>A</td>" in out
    assert "<blockquote>" in out
    assert "<br" in out and "&amp; mehr" in out


def test_a_tag_typed_as_text_is_shown_as_text():
    out = safe_markdown.render("Wir nutzen <b>kein</b> Tracking")
    assert "&lt;b&gt;kein&lt;/b&gt;" in out


def test_the_built_in_legal_templates_render_as_before(client):
    from app import legal
    client.get("/healthz")
    for kind in ("impressum", "datenschutz"):
        md = legal.page_markdown(kind, "de", host="galerie.example.org")
        out = safe_markdown.render(md)
        assert out.count("<h2>") + out.count("<h3>") >= 1
        assert "&lt;" not in out, kind


def _token(scope):
    from app import accounts, oauth
    cid = "pbc_" + secrets.token_hex(6)
    con = db.get_db()
    con.execute("INSERT INTO oauth_clients (client_id, name, redirect_uris, consented_at) "
                "VALUES (?,?,?,datetime('now'))", (cid, "Claude", json.dumps(["https://claude.ai/api/mcp/auth_callback"])))
    token = oauth._issue(con, cid, secrets.token_hex(6), scope, "http://localhost/mcp",
                         accounts.session_epoch())["access_token"]
    con.commit()
    con.close()
    return token


@pytest.mark.parametrize("field, path", [("impressum_text", "/impressum"), ("datenschutz_text", "/datenschutz")])
def test_the_reviewers_proof_a_script_in_a_legal_text_is_escaped(client, field, path):
    from app import ecosystem
    client.get("/healthz")
    ecosystem.init_schema()
    payload = "## Seite\n\nHallo <script>alert('xss')</script> [x](javascript:alert(2))"
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "set_legal_texts", "arguments": {field: payload}}},
                    headers={"Authorization": f"Bearer {_token('read secure')}"})
    assert r.status_code == 200 and not r.get_json()["result"].get("isError")
    page = client.get(path).get_data(as_text=True)
    assert "<script>alert('xss')" not in page
    assert "javascript:alert(2)" not in page
    assert "&lt;script&gt;" in page


def test_faq_answers_and_intro_are_escaped(client):
    client.get("/healthz")
    db.set_setting("faq_intro_text", "Intro <script>alert(1)</script>")
    original = db.get_faq_items
    try:
        db.get_faq_items = lambda: [{"q": "Frage", "a": "Antwort <img src=x onerror=alert(1)> **fett**"}]
        page = client.get("/faq").get_data(as_text=True)
    finally:
        db.get_faq_items = original
    assert "<script>alert(1)" not in page and "<img src=x" not in page
    assert "<strong>fett</strong>" in page
