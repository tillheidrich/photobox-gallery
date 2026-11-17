"""The small line above the start title can be worded freely.

It showed the name of the event and nothing else ("GENERALPROBE"). It is a
text like every other now: empty = the name of the event as before, "-" = no
line at all, anything else = that text.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "booth" / "boothcore" / "web" / "static" / "app.js").read_text(encoding="utf-8")
I18N = (ROOT / "booth" / "boothcore" / "web" / "static" / "i18n.js").read_text(encoding="utf-8")


def test_the_key_exists_on_both_sides_with_an_empty_default():
    assert I18N.count('"start.eyebrow": ""') == 2          # de and en
    texts = (ROOT / "app" / "booth_texts.py").read_text(encoding="utf-8")
    assert '("start.eyebrow",' in texts and '"start.eyebrow": ""' in texts


def test_the_start_screen_uses_it():
    assert 'const eyebrow = t("start.eyebrow")' in JS
    assert 'eyebrow === "-" ? "" : (eyebrow || p.event.name || S.booth_name || "")' in JS
