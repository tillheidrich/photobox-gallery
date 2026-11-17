"""What the kiosk looked like at 1920x1080, 1280x800 and 1024x768 (UI review, 12.11.2026).

The run was driven in headless Chrome with the mock camera; these tests pin
the four things that were wrong on the screenshots:

* 1024x768: on the setup screen the 44vh picture lay over the format
  choices (the one-row grid of the wide layout, stacked into one column);
* English: the built-in looks were named in German ("Kühl", "Schwarzweiß",
  the latter broken as "Schwarzwei / ß");
* English: the status line stayed "Kamera · Drucker" until the next poll
  after the language button;
* the booth's own admin drew the "Download im WLAN" checkbox as a 58 px white
  field with its sentence squeezed beside it.
"""
import re
from pathlib import Path
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
I18N = (WEB / "static" / "i18n.js").read_text(encoding="utf-8")
ADMIN = page(WEB / "admin.html")


def _media(head: str) -> str:
    m = re.search(re.escape(head) + r"\{(.*?)\n\}", CSS, flags=re.S)
    assert m, head
    return m.group(1)


def test_on_a_small_landscape_screen_setup_is_side_by_side():
    small = _media("@media (max-width:1100px) and (orientation:landscape)")
    rule = re.search(r"\.setup\{([^}]*)\}", small).group(1)
    assert "grid-template-columns:1fr minmax(0,440px)" in rule
    assert re.search(r"\.setup-preview\{height:auto\}", small)


def test_stacked_setup_gives_picture_and_choices_their_own_rows():
    narrow = _media("@media (max-width:1100px)")
    rule = re.search(r"\.setup\{([^}]*)\}", narrow).group(1)
    assert "grid-template-rows:auto auto" in rule


def test_built_in_looks_are_named_in_the_guests_language():
    from boothcore.media.looks import LABELS
    de = I18N[I18N.index("de: {"):I18N.index("en: {")]
    en = I18N[I18N.index("en: {"):]
    for look, label in LABELS.items():
        assert f'"look.{look}": "{label}"' in de
        assert f'"look.{look}":' in en
    assert '"look.mono": "Black & white"' in en
    assert "esc(lookLabel(l))" in JS and "esc(l.label)" not in JS


def test_the_status_line_follows_the_language_button():
    case = re.search(r'case "lang": \{(.*?)\}', JS, flags=re.S).group(1)
    assert "renderStatus()" in case


def test_the_wifi_download_switch_is_a_checkbox_row():
    assert '<label class="check"><input id="gs_allowed" type="checkbox">' in ADMIN
    row = re.search(r'<div class="row">\s*<label>Adresse im WLAN', ADMIN)
    assert row, "address and port stay in their row; the switch is outside it"
