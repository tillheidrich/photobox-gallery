"""Settings → Rechtliches on a phone: the "not finished" box stays inside the screen.

Final review U1: the box holds a heading and paragraphs with long lists of
placeholders; as the flex row every alert is, they stood side by side and ran
520 px wide on a 360 px phone (tools/admin_phone_check.mjs, hard gate). The
box is a block now and breaks long words. Checked with the phone tool at 360
and 390 px (light and dark) after the change; this test pins the rule.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_unfinished_box_is_a_block_that_breaks_long_words():
    css = (ROOT / "app" / "static" / "css" / "admin.css").read_text(encoding="utf-8")
    rule = css[css.index(".admin-content .alert.legal-unfinished"):]
    rule = rule[:rule.index("}")]
    assert "display: block" in rule and "overflow-wrap: anywhere" in rule


def test_the_template_marks_the_box():
    html = (ROOT / "app" / "templates" / "admin" / "settings" / "legal.html").read_text(encoding="utf-8")
    assert 'class="alert alert--error legal-unfinished"' in html
