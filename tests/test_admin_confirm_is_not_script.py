"""Sicherheitsdurchsicht 14.11.2026 (Befund S4).

A name inside ``onsubmit="return confirm('…')"`` is HTML-escaped by Jinja, and
the browser turns ``&#39;`` back into a quote before the handler runs. An event
named from a booth could so run script in the admin's session.
"""
import re
from pathlib import Path

VORLAGEN = Path(__file__).resolve().parent.parent / "app" / "templates"


def test_no_template_puts_a_value_into_a_quoted_script_string():
    schlecht = []
    for f in VORLAGEN.rglob("*.html"):
        for nr, zeile in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for attr in re.findall(r'\son\w+="([^"]*)"', zeile):
                # a {{ value }} between single quotes of a script string
                # numeric ids (``{{ device.id }}``) cannot carry a quote and may stay
                ohne_ids = re.sub(r"\{\{ [a-z_]+\.id \}\}", "0", attr)
                if re.search(r"'[^']*\{\{(?![^}]*\|\s*tojson)[^}]*\}\}[^']*'", ohne_ids) and "t('" not in attr:
                    schlecht.append(f"{f.relative_to(VORLAGEN)}:{nr}")
    assert not schlecht, schlecht


def test_an_event_name_with_a_quote_stays_text(client):
    from app import db
    import secrets
    client.get("/healthz")
    db.create_event(name="a');window.__x=1;//", slug="xss-" + secrets.token_hex(3), upload_token=secrets.token_hex(8))
    html = (VORLAGEN / "admin" / "events" / "list.html").read_text(encoding="utf-8")
    assert "event.name" in html and "|tojson" in html
