"""The booth explains itself: one help page, German and English.

Till, 14.11.2026: "das Programm braucht natürlich ne Funktion in der dem
Nutzer alle Funktionen erklärt werden … De und en". The page ships with the
program, so it works without internet, and the admin links to it.
"""
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"

TOPICS = ["start", "lock", "settings", "modes", "print", "templates", "look", "gallery", "offline",
          "usb", "folder", "camera", "printer", "update", "check", "trouble"]


@pytest.fixture()
def client(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-help-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    b = app_mod.Booth(base=d, start_threads=False)
    yield TestClient(app_mod.create_app(b))
    b.mail.stop()


def test_the_page_is_served_by_the_booth_itself(client):
    r = client.get("/admin/hilfe")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "http://" not in r.text and "https://" not in r.text, "nothing is loaded from the internet"


def test_every_topic_exists_in_both_languages():
    html = page(WEB / "hilfe.html")
    for lang in ("de", "en"):
        block = re.search(rf'<main lang="{lang}"[^>]*>(.*?)</main>', html, re.S)
        assert block, lang
        for topic in TOPICS:
            assert f'id="{lang}-{topic}"' in block.group(1), f"{lang}: {topic}"
    assert html.count("<section") % 2 == 0, "the same number of sections in both languages"


def test_what_was_learned_the_hard_way_is_in_it():
    html = page(WEB / "hilfe.html")
    for needle in ("aus- und wieder einschalten", "Laufend auf den USB-Stick sichern", "Moduswahlrad",
                   "switch it off and on", "mode dial", "Daten nicht lesbar"):
        assert needle in html, needle


def test_the_admin_links_to_it():
    admin = page(WEB / "admin.html")
    assert "/admin/hilfe" in admin


def test_the_template_editor_can_be_reached_from_the_admin():
    """It was served at /admin/vorlagen and nothing linked to it."""
    admin = page(WEB / "admin.html")
    assert 'data-href="/admin/vorlagen"' in admin
