"""Text on the start picture stays readable in the light scheme (18.11.2026).

At the booth: theme "hell" and a dark photo (ocean from above) as start
picture. Kicker and title took the theme's dark text colour and stood dark
on dark. ``start.text_tone`` decides: "auto" (default) = light text and a
dark shade whenever an own start picture lies behind the title and no
colour is set - exactly the dark scheme's look - and the theme's colours
without start media; "hell"/"dunkel" force it. Only the title block, the
shade and the status line follow it; tiles, buttons and the instructions
box keep the theme.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "booth" / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")


def _leuchte(h):
    t = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    t = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in t]
    return 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2]


def _kontrast(a, b):
    hi, lo = sorted((_leuchte(a), _leuchte(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_key_exists_on_both_sides_and_is_checked():
    import sys
    sys.path.insert(0, str(ROOT))
    from boothcore import config, profile_rules
    from app import ecosystem
    assert config.DEFAULT_PROFILE["start"]["text_tone"] == "auto"
    assert ecosystem.DEFAULT_PROFILE["start"]["text_tone"] == "auto"
    assert profile_rules.ENUM_VALUES["start.text_tone"] == ("auto", "hell", "dunkel")


def test_the_kiosk_sets_the_tone_from_what_lies_behind_the_title():
    m = re.search(r"// <start-tone>(.*?)// </start-tone>", JS, re.S)
    assert m, "the rule is one marked block"
    block = m.group(1)
    assert "function startTone(" in block
    assert 'dataset.tone = startTone(' in JS
    # forced values win; auto: an own start picture and no colour = light,
    # no start media = the theme's text
    assert '"hell"' in block and '"dunkel"' in block and "mediaCount > 0 && !colour" in block


def test_the_tone_rule_behaves_as_described():
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not installed")
    block = re.search(r"// <start-tone>(.*?)// </start-tone>", JS, re.S).group(1)
    probe = "//" + block + """
    console.log(JSON.stringify([
      startTone("auto", 1, ""), startTone("auto", 0, ""), startTone("auto", 1, "#ffffff"),
      startTone(undefined, 2, ""), startTone("dunkel", 1, ""), startTone("hell", 0, "")]));
    """
    out = subprocess.run([node, "-e", probe], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == '["hell","","","hell","dunkel","hell"]'


def test_light_text_on_the_picture_is_white_on_a_dark_shade():
    rule = re.search(r'#s-start\[data-tone="hell"\]\s*:is\(\.start-copy,\s*\.statusline\)\s*\{([^}]*)\}', CSS)
    assert rule, "light tone rule for title block and status line"
    assert "--text:#ffffff" in rule.group(1).replace(" ", "")
    # the shade and the status line's own backdrop turn dark too
    shade = re.search(r'#s-start\[data-tone="hell"\]\s*:is\(\.start-shade,\s*\.statusline\)\s*\{([^}]*)\}', CSS)
    assert shade and "--bg-rgb" in shade.group(1)
    # white on the dark shade's colour at full strength: well over 4.5:1
    assert _kontrast("#ffffff", "#0c0c10") > 4.5


def test_tiles_and_buttons_keep_the_theme():
    for sel in re.findall(r'#s-start\[data-tone="(?:hell|dunkel)"\][^{]*\{', CSS):
        assert ".mode" not in sel and ".pill" not in sel and ".start-steps" not in sel


def test_both_forms_offer_the_setting():
    form = (ROOT / "app" / "templates" / "admin" / "booth_profile.html").read_text(encoding="utf-8")
    assert 'name="start_text_tone"' in form
    settings = (WEB / "static" / "settings.js").read_text(encoding="utf-8")
    assert '"start.text_tone": ["auto", "hell", "dunkel"]' in settings
    assert '"theme.mode": ["dunkel", "hell", "eigene"]' in settings
    admin = (WEB / "admin.html").read_text(encoding="utf-8")
    assert 'id="start_text_tone"' in admin
    page = (WEB / "static" / "admin-page.js").read_text(encoding="utf-8")
    assert 'text_tone: $("start_text_tone").value' in page
