"""Die Galerie an der Box: ansehen ja, wegräumen nein.

Der Ablauf ist der von dslrBooth und genau so gewollt: Wer gerade ausgelöst
hat, sieht sein Bild und darf es verwerfen. Wer später durch die Galerie
blättert, sieht fremde Bilder — und kann sie nicht löschen. Sonst räumt im
Kiosk irgendwann jemand den Abend eines anderen weg.
"""
import tempfile
import time
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text()
HTML = (WEB / "index.html").read_text()
CSS = (WEB / "static" / "app.css").read_text()


@pytest.fixture()
def booth(monkeypatch):
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=Path(tempfile.mkdtemp(prefix="booth-gal-")), start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def test_deleting_is_only_offered_on_the_fresh_shot():
    assert '$("#deleteBtn").hidden = !(fresh && S.profile.allow_delete);' in JS, \
        "Aus der Galerie heraus darf der Löschen-Knopf nicht auftauchen"


def test_the_operator_can_switch_deleting_off_entirely():
    from boothcore.config import DEFAULT_PROFILE
    assert "allow_delete" in DEFAULT_PROFILE


def test_the_box_refuses_deleting_when_it_is_switched_off(booth):
    """Nicht nur der Knopf verschwindet — die Box nimmt den Auftrag nicht an."""
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["allow_delete"] = False
    client = TestClient(create_app(booth))
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    assert client.delete(f"/api/session/{s.uid}").status_code == 403
    assert booth.library.get(s.uid) is not None


def test_there_is_a_way_to_look_at_a_picture_properly():
    assert 'id="fullOverlay"' in HTML and 'id="fullMedia"' in HTML
    assert "function openFull(" in JS and "function closeFull(" in JS
    assert 'data-action="full"' in JS, "Das Bild selbst ist der Knopf"


def test_the_full_picture_fits_on_the_screen():
    """Ein Vollbild, das über den Rand steht, ist kein Vollbild."""
    assert ".voll-media{position:absolute;inset:3vh 3vw;display:flex" in CSS
    assert "max-height:100%" in CSS


def test_nothing_gets_deleted_from_the_full_view():
    stelle = JS.index("function openFull(")
    ende = JS.index("function closeFull(")
    assert "delete" not in JS[stelle:ende].lower()


def test_the_timer_waits_while_someone_is_looking():
    assert "clearTimeout(resultTimer);      // wer schaut, soll nicht hinausgeworfen werden" in JS


def test_the_back_key_closes_the_picture_first():
    """Sonst landet man vom Vollbild aus auf dem Startbildschirm.

    Geprüft wurde hier früher eine Zeile im Wortlaut. Seit dem 19.09.2026 gilt
    dasselbe für jedes offene Fenster, nicht nur fürs Vollbild — deshalb prüft
    der Test jetzt die Reihenfolge und nicht die Schreibweise.
    """
    stelle = JS.index("back.includes(e.code) && overlayOpen()")
    assert "return zurueckTaste();" in JS[stelle:stelle + 200]
    # since 15.11.2026 one function for the back key (and Backspace with arrow navigation)
    block = JS[JS.index("function zurueckTaste()"):][:700]
    assert "closeFull()" in block
    assert block.index("closeFull()") < block.index("closeMail()"), \
        "Das Vollbild wird zuerst geschlossen"
    heimweg = JS.index("} else if (back.includes(e.code)")
    assert stelle < heimweg, "Der Heimweg darf erst nach den Fenstern kommen"


def test_the_picture_is_reachable_without_a_mouse():
    """Die Box läuft auch an Kisten ohne Touch — dann zählt die Tastatur."""
    assert 'role="button" tabindex="0"' in JS
    assert "app.addEventListener(\"keydown\"" in JS
