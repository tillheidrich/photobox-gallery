"""Der Gruß des Gastes auf dem Abzug.

„Anna & Ben, 12.7." statt der Unterzeile des Anlasses — das steht auf jedem
zweiten Hochzeitsabzug und ist der Unterschied zwischen einem Foto und einem
Andenken. Gefragt wird vor der Aufnahme: Danach warten alle auf das Bild,
und da tippt niemand mehr gern.
"""
import tempfile
import time
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text()
HTML = (WEB / "index.html").read_text()


@pytest.fixture()
def booth(monkeypatch):
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=Path(tempfile.mkdtemp(prefix="booth-gruss-")), start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _an(booth, **mehr):
    booth.store.profile["guest_text"] = {"enabled": True, "max_len": 40, "required": False,
                                         "modes": ["photo", "collage"], **mehr}


def test_it_is_off_until_someone_wants_it():
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["guest_text"]["enabled"] is False


def test_gallery_and_booth_ship_the_same_block():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import ecosystem
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["guest_text"] == ecosystem.DEFAULT_PROFILE["guest_text"]


def test_the_text_reaches_the_print(booth):
    _an(booth)
    s = booth.start_session("photo", "4:5", None, None)
    s.guest_text = "Anna & Ben"
    werte = booth._texts(s)
    assert werte["gast"] == "Anna & Ben"
    assert werte["subline"] == "Anna & Ben", "Er steht dort, wo sonst die Unterzeile steht"


def test_without_a_greeting_the_date_stays(booth):
    _an(booth)
    s = booth.start_session("photo", "4:5", None, None)
    werte = booth._texts(s)
    assert "subline" not in werte, "Ohne Gruß entscheidet weiter der Stil des Anlasses"


def test_the_single_print_uses_the_same_two_lines():
    """Sonst stünde der Gruß nur auf Collagen und nicht auf dem Einzelabzug."""
    from boothcore.media import compose
    tpl = compose.single_print_template("4:5", False)
    werte = [t["value"] for t in tpl["texts"]]
    assert "{headline}" in werte and "{subline}" in werte


def test_too_long_is_cut_where_it_is_taken(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    _an(booth, max_len=12)
    client = TestClient(create_app(booth))
    s = booth.start_session("photo", "4:5", None, None)
    r = client.post(f"/api/session/{s.uid}/text", json={"text": "Das ist viel zu lang für einen Abzug"})
    assert r.status_code == 200
    assert len(r.json()["text"]) == 12, "Auf dem Abzug wäre der Rest ohnehin nicht lesbar"


def test_whitespace_is_tidied(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    _an(booth)
    client = TestClient(create_app(booth))
    s = booth.start_session("photo", "4:5", None, None)
    r = client.post(f"/api/session/{s.uid}/text", json={"text": "  Anna   und\n  Ben "})
    assert r.json()["text"] == "Anna und Ben"


def test_switched_off_the_box_takes_no_text(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    s = booth.start_session("photo", "4:5", None, None)
    assert client.post(f"/api/session/{s.uid}/text", json={"text": "hallo"}).status_code == 403


def test_the_question_comes_before_the_countdown():
    """Danach warten alle auf das Bild — da tippt niemand mehr gern."""
    frage = JS.index("if (gastTextNoetig()) {")
    countdown = JS.index("for (let i = 1; i <= flow.needed; i++) await captureShot")
    assert frage < countdown


def test_it_is_not_asked_where_no_text_appears():
    """Bei GIF und Video steht kein Text auf dem Ergebnis."""
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["guest_text"]["modes"] == ["photo", "collage"]
    assert "modi.includes(flow.mode)" in JS


def test_there_is_a_keyboard_and_a_way_past_it():
    assert 'id="textOverlay"' in HTML and 'id="textKeyboard"' in HTML
    assert 'data-action="text-skip"' in HTML, "Ohne Gruß muss eine gültige Antwort sein"
    assert 'cfg.required' in JS, "…außer der Betreiber macht ihn zur Pflicht"


def test_no_key_is_blank():
    assert 'k === " " ? "␣"' in JS, "Eine Taste ohne Beschriftung ist keine Taste"
