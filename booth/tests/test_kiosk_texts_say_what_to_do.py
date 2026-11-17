"""Kiosk texts: plain words a guest can act on, the same in the kiosk and in the gallery catalogue.

From the text review of 15.11.2026: "Die ganze Gruppe" under the landscape
format promised something the format does not do, the error title "Kurzer
Moment …" did not say that something failed, the PIN hint talked to guests
about breaking things, the English texts asked for "the hosts" in one place
and "someone" in another, and "Use it" / "Trigger" are not words a guest uses.
The catalogue in the gallery (app/booth_texts.py) shows the same defaults, so
both files are checked.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "booth/boothcore/web/static/i18n.js").read_text(encoding="utf-8")
PY = (ROOT / "app/booth_texts.py").read_text(encoding="utf-8")


def _block(lang: str) -> str:
    start = JS.index(f"  {lang}: {{")
    end = JS.index("\n  },", start)
    return JS[start:end]


def _js(lang: str, key: str) -> str:
    m = re.search(r'"' + re.escape(key) + r'":\s*"((?:[^"\\]|\\.)*)"', _block(lang))
    assert m, f"{lang} {key} missing"
    return m.group(1)


WANTED = {
    ("de", "format.3:2.hint"): "Klassisches Querformat",
    ("en", "format.3:2.hint"): "Classic landscape",
    ("de", "error.title"): "Das hat nicht geklappt",
    ("en", "error.title"): "That didn't work",
    ("de", "camera.off"): "Die Kamera ist nicht bereit. Bitte sagt dem Team Bescheid.",
    ("en", "camera.off"): "The camera is not ready. Please tell the booth staff.",
    ("de", "pin.hint"): "Nur für das Fotobox-Team.",
    ("en", "pin.hint"): "For booth staff only.",
    ("en", "guesttext.ok"): "Add",
    ("en", "draw.done"): "Done",
    ("en", "screen.setup"): "Ready. Start when you are.",
}


def test_the_reviewed_texts_are_the_defaults_at_the_kiosk():
    for (lang, key), text in WANTED.items():
        assert _js(lang, key) == text, (lang, key)


def test_the_gallery_catalogue_shows_the_same_defaults():
    for (lang, key), text in WANTED.items():
        if lang == "de":
            assert re.search(r'\("' + re.escape(key) + r'",\s*"[^"]*",\s*"' + re.escape(text) + r'"\)', PY), key
        else:
            assert f'"{key}": "{text}"' in PY, key


def test_guests_are_told_whom_to_ask_in_one_way():
    for source in (_block("en"), PY):
        assert "hosts know" not in source
        assert "let someone know" not in source
        assert "Use it" not in source
        assert "Trigger" not in source
    for source in (_block("de"), PY):
        assert "kurz Bescheid geben" not in source
        assert "kaputt machen" not in source
        assert "Die ganze Gruppe" not in source
        assert "Kurzer Moment" not in source
