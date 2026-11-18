"""Guard: the German guest pages speak to the guests as a group ("ihr") only (18.11.2026).

Guests are a wedding party, so the guest pages say "ihr/euch/eure" (see
test_guest_pages_on_a_phone.py::test_the_run_page_speaks_to_the_guests_as_a_group).
"Tippe lange", "Bitte gib", "Bitte wähle" had crept in next to "Tippt" and
"ladet". Every German string under a guest prefix, and the guest templates'
own text, is checked for singular "du" forms. Operator and client pages
(admin, layout check, comments of a client, manage mode) keep "du".
"""
from __future__ import annotations

import re
from pathlib import Path

from app.i18n import de

ROOT = Path(__file__).resolve().parents[1]

#: Keys a guest reads (gallery, its download and share sheets, the run page,
#: share links, "Meine Aufnahmen", access requests, guest upload, error pages).
GUEST_PREFIXES = ("gallery.", "dl.", "lb.", "session.", "share.", "access.", "mine.", "photo.", "soon.",
                  "notfound.", "home.", "device.", "print.", "kiosk_guests.", "about.", "password.", "qr.",
                  "zip.", "upl.", "picks.", "a11y.", "og.count", "og.neutral", "og.closed", "og.protected")
#: Guest prefixes that name the operator's side on purpose.
NOT_GUEST = ("gallery.admin", "share.admin", "upl.admin")

DU = re.compile(r"\b(du|dich|dir|dein|deine|deinen|deinem|deiner|deines|Du|Dich|Dir|Dein|Deine|Deinen|Deinem|Deiner|"
                r"Tippe|Wähle|wähle|Gib|gib|Scanne|scanne|Kopiere|kopiere|Schau|schau|Lade|Halte|Klicke|klicke|Probier)\b")


def _strings():
    for name in dir(de):
        val = getattr(de, name)
        if isinstance(val, dict) and len(val) > 100:
            return val
    raise AssertionError("German strings not found")


def test_german_guest_strings_use_ihr_only():
    bad = {k: v for k, v in _strings().items()
           if k.startswith(GUEST_PREFIXES) and not k.startswith(NOT_GUEST) and DU.search(v)}
    assert not bad, bad


def test_guest_templates_carry_no_du_of_their_own():
    files = list((ROOT / "app" / "templates" / "public").glob("*.html")) + [ROOT / "app" / "templates" / "layout_public.html"]
    bad = []
    for f in files:
        text = re.sub(r"\{#.*?#\}", "", f.read_text(encoding="utf-8"), flags=re.S)        # comments
        text = re.sub(r"<script.*?</script>", "", text, flags=re.S)                     # code
        text = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", text, flags=re.S)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
        for m in DU.finditer(text):                                                      # text and attributes
            bad.append((f.name, m.group(0)))
    assert not bad, bad


def test_the_ios_download_hint_speaks_to_the_group():
    assert _strings()["dl.sheet_hint"].startswith("Tippt lange")
