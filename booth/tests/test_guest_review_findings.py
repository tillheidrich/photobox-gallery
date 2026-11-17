"""Findings of the guest-flow review of 14 Nov 2026, pinned so they stay fixed.

These read the shipped files, as the other interface tests do: the kiosk has no
build step and no browser runs in the suite.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "boothcore" / "web" / "static"
CSS = (STATIC / "app.css").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
I18N = (STATIC / "i18n.js").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", CSS)
    assert m, f"no rule for {selector}"
    return m.group(1)


def test_a_toast_lies_above_every_overlay():
    z = re.search(r"z-index:(\d+)", _rule(".toast"))
    assert z and int(z.group(1)) > 30          # overlays are 20, the lock is 30


def test_countdown_digits_survive_reduced_motion():
    block = CSS
    assert re.search(r"\.countdown b[^{]*\{[^}]*animation:none!important[^}]*opacity:1!important", block)


def test_colours_follow_the_theme():
    assert "#1c1c20" not in _rule(".kb-row--quick .kb-key")
    assert "#d4d4d8" not in _rule(".lede")
    assert "color:#fff" in _rule(".look span")
    assert "var(--gefahr)" in _rule(".statusline i.bad")


def test_only_the_mail_field_shows_a_mail_placeholder():
    assert "name@beispiel.de" not in CSS
    assert "attr(data-placeholder)" in CSS
    assert 'dataset.placeholder = t("mail.placeholder")' in JS


def test_the_consent_screen_returns_to_start_when_left_alone():
    assert re.search(r'\["setup", "consent", "error", "gallery"\]\.includes\(sc\)', JS)


def test_deleting_is_asked_in_the_colour_of_retreat():
    assert 'confirm(doDelete, "result.delete_confirm", "result.delete", "common.cancel", true)' in JS


def test_a_print_says_so_until_it_is_out():
    assert 't("print.running")' in JS and 't("print.done")' in JS
    for key in ("print.running", "print.done", "mail.placeholder"):
        assert I18N.count(f'"{key}":') == 2, key          # German and English


def test_guests_are_addressed_as_a_group():
    de = I18N[:I18N.index('"lang.name": "English"')] if '"lang.name": "English"' in I18N else I18N
    for wrong in ("vor dir", "Nimm den Ausdruck", "Schau in dein", "Bitte Kamera einschalten", "Betreuer informieren"):
        assert wrong not in de, wrong


def test_printing_leads_until_a_print_is_made():
    """G13: 'Fertig' was the only accent button; guests looking for the print tapped themselves out."""
    assert "function printLead(" in JS
    assert re.search(r"printLead\(enabled && !view\.printedOnce", JS)
    assert "view.printedOnce = true" in JS


def test_the_screen_saver_says_what_a_touch_does():
    assert I18N.count("Bildschirm berühren, um Fotos zu machen") == 1
    assert I18N.count("Touch the screen to take photos") == 1


def test_a_greeting_starts_its_words_with_a_capital():
    """G11: the keyboard has small letters only, so the print read "anna & ben"."""
    import json, subprocess
    m = re.search(r"function grossAmWortanfang\(text, key\) \{.*?\n  \}", JS, re.S)
    assert m, "helper missing"
    code = m.group(0) + "\nconsole.log(JSON.stringify(['anna & ben', 'jörg', 'a-b', '12 uhr'].map(w => { let t=''; for (const k of w) t += grossAmWortanfang(t, k); return t; })))"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert json.loads(out.stdout) == ["Anna & Ben", "Jörg", "A-B", "12 Uhr"], out.stdout + out.stderr
    assert "gastText += grossAmWortanfang(gastText, key)" in JS
