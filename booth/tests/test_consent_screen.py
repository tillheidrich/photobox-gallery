"""The kiosk side of the consent choice: one screen, equal buttons, no preselection.

Read from the files, like test_readable_at_full_hd.py: what matters here is
that the buttons carry no default, that the buzzer has no say on this screen,
and that the text is large enough at 1920x1080.
"""
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"
HTML = (WEB / "index.html").read_text("utf-8")
JS = (WEB / "static" / "app.js").read_text("utf-8")
CSS = (WEB / "static" / "app.css").read_text("utf-8")
I18N = (WEB / "static" / "i18n.js").read_text("utf-8")


def _rule(selector):
    m = re.search(r"(?m)^" + re.escape(selector) + r"\{([^}]*)\}", CSS)
    assert m, f"no rule for {selector}"
    return m.group(1)


def _px(value, vh):
    def one(term):
        term = term.strip()
        if term.endswith("vh"):
            return float(term[:-2]) * vh / 100
        return float(term[:-2])
    m = re.fullmatch(r"clamp\(([^,]+),([^,]+),([^,]+)\)", value.strip())
    if not m:
        return one(value)
    lo, ideal, hi = (one(t) for t in m.groups())
    return max(lo, min(ideal, hi))


def _font(selector, vh):
    return _px(re.search(r"font-size:([^;]+)", _rule(selector)).group(1), vh)


def test_there_is_a_screen_with_a_title_choices_and_a_note():
    section = re.search(r'<section class="screen" id="s-consent">(.*?)</section>', HTML, re.S)
    assert section, "no consent screen"
    body = section.group(1)
    assert 'data-t="consent.title"' in body and 'id="consentChoices"' in body and 'id="consentNote"' in body
    # The first focus lands on the title (barrierefrei), never on a button.
    assert re.search(r"<h1[^>]*data-t=\"consent.title\"", body)


def test_the_buttons_are_built_alike_and_none_is_preselected():
    build = re.search(r"function askConsent\(mode\) \{(.*?)\n  \}", JS, re.S).group(1)
    assert 'class="consent-choice"' in build
    assert "aria-pressed" not in build and "autofocus" not in build and "btn-primary" not in build


def test_the_buzzer_chooses_nothing_on_this_screen():
    trigger = re.search(r"function triggerPrimary\(\) \{(.*?)\n  \}", JS, re.S).group(1)
    line = next(l for l in trigger.splitlines() if 'sc === "consent"' in l)
    assert 'toast(t("consent.use_screen"))' in line and "openSetup" not in line and "runCapture" not in line
    # A free tap is not a choice either: the screen is not one where a tap triggers.
    assert '"consent"' not in re.search(r"FREE_TAP_SCREENS = \[([^\]]*)\]", JS).group(1)


def test_a_tap_on_a_button_is_that_button():
    assert "if (b.dataset.consent) return openSetup(flow.pendingMode, b.dataset.consent);" in JS
    assert "consent: flow.consent || undefined" in JS


@pytest.mark.parametrize("key", ["consent.title", "consent.gallery", "consent.print", "consent.use_screen"])
def test_texts_exist_in_both_languages(key):
    assert I18N.count(f'"{key}"') == 2, key


def test_print_only_is_called_print_only_in_english():
    assert '"consent.print": "Print only"' in I18N and '"consent.print": "Nur drucken"' in I18N


def test_readable_at_full_hd():
    assert _font(".consent-note", 1080) >= 18.9           # clamp(16px,1.75vh,20px)
    assert _font(".consent-note", 768) >= 16
    assert _font(".consent-choice small", 1080) >= 20
    assert _font(".consent-choice", 1080) >= 36
    assert _px(re.search(r"min-height:([^;]+)", _rule(".consent-choice")).group(1), 1080) >= 260


def test_both_buttons_have_the_same_weight():
    # One rule for every option: no modifier class that would make one louder.
    assert not re.search(r"\.consent-choice(\[|\.|:first|:last|:nth)", CSS.replace(".consent-choice:active", ""))
    assert "repeat(var(--n,2),minmax(0,1fr))" in _rule(".consent-choices")


def test_the_screen_is_shown_at_all():
    """Screens are hidden (opacity 0, no taps) unless named in the show rule.

    Found by looking at 1920x1080 in a browser: the screen was there, with
    its buttons, and invisible."""
    rule = CSS[:CSS.index("{opacity:1;pointer-events:auto}")]
    rule = rule[rule.rindex("}") + 1:]
    assert '#app[data-screen="consent"] #s-consent' in rule
