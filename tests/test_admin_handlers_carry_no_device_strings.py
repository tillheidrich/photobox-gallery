"""Second review, gallery side of R2-U1: device strings never land in a JS handler string.

The sync page's folder picker built ``onclick="openPath('${esc(target)}',
'${esc(dir)}')"``. Folder names come from the sync PC or the remote server;
HTML escaping does not protect a JavaScript string inside an attribute (the
browser decodes ``&#39;`` back to ``'`` before the script runs). Since then
such values go into data attributes and a listener reads them.

Jinja autoescape covers every other place where booth or device text is
shown; ``|safe`` is only used for operator Markdown after app/safe_markdown.py.
"""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def test_no_template_puts_an_interpolated_value_into_an_inline_handler_string():
    bad = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"""\son[a-z]+="[^"]*'\$\{[^}]*\}'""", text):
            bad.append(f"{path.relative_to(TEMPLATES)}: {m.group(0)[:80]}")
    assert bad == []


def test_safe_is_used_only_for_sanitised_markdown():
    uses = []
    for path in TEMPLATES.rglob("*.html"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"\|\s*safe\b", line):
                uses.append((path.name, line.strip()))
    allowed = {"impressum.html", "datenschutz.html", "faq.html"}
    assert uses and all(name in allowed for name, _ in uses), uses


def test_the_folder_picker_reads_data_attributes():
    page = (TEMPLATES / "admin" / "sync_device.html").read_text(encoding="utf-8")
    assert "onclick=\"openPath(" not in page
    assert "data-open-path" in page and "b.dataset.openPath" in page
