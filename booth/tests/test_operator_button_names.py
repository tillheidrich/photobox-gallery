"""The operator reads a button's name in a notice and must find that button.

The booth admin's button for action ``paper_changed`` is called "Papier
nachgelegt" since the paper tray and the ink ribbon are counted apart; the
notice about held prints still said "Papier gewechselt". And since a print is
also held when the printer was switched off or unplugged, the notice names
the button that retries without claiming new paper.
"""
import re
from pathlib import Path
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"
ROOT = Path(__file__).resolve().parents[2]


def _admin():
    return page(WEB / "admin.html")


def test_no_operator_text_names_the_old_button():
    for f in (WEB / "admin.html", WEB / "static" / "admin-page.js", WEB / "hilfe.html", WEB / "static" / "i18n.js",
              ROOT / "booth" / "boothcore" / "app.py", ROOT / "booth" / "boothcore" / "doctor.py",
              ROOT / "app" / "booth_texts.py", ROOT / "app" / "ecosystem.py",
              ROOT / "app" / "templates" / "admin" / "booth_profile.html"):
        text = f.read_text(encoding="utf-8")
        assert "„Papier gewechselt“" not in text and ">Papier gewechselt<" not in text, f.name
        assert "Paper changed" not in text, f.name


def test_the_held_prints_notice_names_buttons_that_exist():
    i18n = (WEB / "static" / "i18n.js").read_text(encoding="utf-8")
    de = re.search(r'"op\.prints_held\.text": "([^"]*)"', i18n).group(1)
    assert "„Papier nachgelegt“" in de and "„Wartende Drucke erneut“" in de
    assert re.search(r'data-act="paper_changed"[^>]*>Papier nachgelegt<', _admin())
    assert re.search(r'data-act="release_prints"[^>]*>Wartende Drucke erneut<', _admin())


def test_the_help_page_names_both_buttons():
    html = page(WEB / "hilfe.html")
    assert "„Papier nachgelegt“" in html and "„Farbband gewechselt“" in html
    assert "„Wartende Drucke erneut“" in html


def test_the_ribbon_can_be_changed_remotely_too(tmp_path, monkeypatch):
    """The gallery sends job ``ribbon_changed`` like ``paper_changed``; the booth must know it.

    That the gallery's job list and the sync agent's allowed list agree is
    checked in the gallery and sync suites (they must be equal sets).
    """
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    booth = Booth(base=tmp_path, start_threads=False)
    try:
        assert "Farbband" in booth.agent.job_handlers["ribbon_changed"]({})
    finally:
        booth.mail.stop()
