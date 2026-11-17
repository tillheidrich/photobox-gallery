"""A word or a smiley before the shot, without having to make a picture.

countdown.smile_media shows a picture right before the trigger. Most hosts
have no such picture at hand: countdown.smile_text shows a short text or an
emoji instead ("Bitte lächeln!", "😀"). With the screen flash on, the text
stays on the white surface in dark letters -- the screen still lights the
guests, and they look up instead of down.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "booth" / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")


def test_both_sides_know_the_setting():
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["countdown"]["smile_text"] == ""
    assert '"smile_text": ""' in (ROOT / "app" / "ecosystem.py").read_text(encoding="utf-8")
    assert "cd_smile_text" in (ROOT / "app" / "blueprints" / "admin_booth.py").read_text(encoding="utf-8")
    assert 'name="cd_smile_text"' in (ROOT / "app" / "templates" / "admin" / "booth_profile.html").read_text(encoding="utf-8")


def test_the_text_is_shown_when_there_is_no_picture():
    assert "cd.smile_text" in JS
    assert "el.textContent = datei ? \"\" : text" in JS
    assert ".smile{" in CSS and "place-items:center" in CSS.split(".smile{")[1].split("}")[0]


def test_the_text_stays_on_the_white_flash_in_dark_letters():
    assert 'el.textContent = smileText()' in JS
    rule = CSS.split(".screen-flash{")[1].split("}")[0]
    assert "color:#111" in rule and "place-items:center" in rule


def test_it_is_set_as_text_never_as_markup():
    # the text comes from the profile: textContent, never innerHTML
    assert "smilePic\").innerHTML" not in JS and "screenFlash\").innerHTML" not in JS
