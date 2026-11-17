"""A line that says what to do next, between the pictures of a series.

14 Nov 2026: guests of every age stand in front of the booth, and in a
collage or GIF nobody knows what comes after the first picture. The big
booths solve it with a voice; this booth's speaker is switched off, so it is
a line of text — optional, off by default, the wording per event.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "booth" / "boothcore" / "web" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
I18N = (STATIC / "i18n.js").read_text(encoding="utf-8")


def test_it_is_a_switch_that_is_off_and_the_same_on_both_sides():
    sys.path.insert(0, str(ROOT))
    from app import ecosystem
    from boothcore import config
    assert config.DEFAULT_PROFILE["countdown"]["poses"] is False
    assert ecosystem.DEFAULT_PROFILE["countdown"]["poses"] is False


def test_there_are_five_lines_in_both_languages():
    for n in range(1, 6):
        assert I18N.count(f'"pose.{n}":') == 2, n


def test_the_kiosk_shows_one_from_the_second_picture_on():
    assert "function posenZeile(" in JS
    m = re.search(r"function posenZeile\(index, needed\) \{.*?\n  \}", JS, re.S)
    assert m and "countdown" in m.group(0) and "index < 2" in m.group(0)
    assert "posenZeile(index, needed)" in JS


def test_the_gallery_form_saves_the_switch():
    assert 'name="cd_poses"' in (ROOT / "app" / "templates" / "admin" / "booth_profile.html").read_text(encoding="utf-8")
    assert '"poses": bool(f.get("cd_poses"))' in (ROOT / "app" / "blueprints" / "admin_booth.py").read_text(encoding="utf-8")


def test_a_prompt_is_large_and_in_the_middle():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".shotcount.has-pose{" in css and 'classList.toggle("has-pose"' in JS
