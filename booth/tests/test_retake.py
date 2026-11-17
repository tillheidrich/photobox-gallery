"""Nochmal — aber nur das eine Bild.

Bei einer Collage aus vier Aufnahmen hat regelmäßig genau eine nicht gepasst.
Bisher blieb nur, die ganze Serie zu wiederholen. Geprüft wird deshalb, ob
sich eine einzelne Aufnahme an ihrer Stelle ersetzen lässt — und ob der
Durchsehen-Schirm die Gäste überhaupt danach fragt.
"""
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import HTTPException


@pytest.fixture()
def booth():
    d = Path(tempfile.mkdtemp(prefix="booth-retake-"))
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "collage", "gif"]
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _serie(booth, mode="collage", vorlage="grid4"):
    s = booth.start_session(mode, "4:5", vorlage, None)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    return s


def test_a_single_shot_can_be_replaced_where_it_is(booth):
    s = _serie(booth)
    assert len(s.shots) >= 3, "Für den Test braucht es eine echte Serie"
    vorher = [p.name for p in s.shots]
    alt = s.shots[1].read_bytes()

    booth.take_shot(s, ersetzt=2)

    assert [p.name for p in s.shots] == vorher, "Die Reihenfolge muss bleiben"
    assert len(s.shots) == len(vorher), "Es darf keine Aufnahme dazukommen"
    assert s.shots[1].read_bytes() != alt, "Das zweite Bild muss ein neues sein"
    assert all(p.exists() for p in s.shots)


def test_the_replaced_shot_ends_up_in_the_collage(booth):
    """Die Reihenfolge auf dem Abzug entscheidet — nicht die des Auslösens."""
    s = _serie(booth)
    booth.take_shot(s, ersetzt=1)
    booth.finalize(s, "original")
    eintrag = booth.library.get(s.uid)
    assert eintrag and Path(eintrag["result"]).exists()


def test_a_shot_that_does_not_exist_is_refused(booth):
    s = booth.start_session("collage", "4:5", "grid4", None)
    booth.take_shot(s)
    with pytest.raises(HTTPException) as e:
        booth.take_shot(s, ersetzt=3)
    assert e.value.status_code == 404


def test_the_kiosk_asks_for_the_right_picture(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    s = _serie(booth)
    r = client.post(f"/api/session/{s.uid}/shot", json={"replace": 2})
    assert r.status_code == 200
    daten = r.json()
    assert daten["shot"] == 2
    assert "/shot/2.jpg" in daten["shot_url"], "Der Link muss auf die ersetzte Stelle zeigen"
    assert client.get(f"/api/session/{s.uid}/shot/{len(s.shots)}.jpg").status_code == 200


def test_the_old_file_is_not_left_behind(booth):
    s = _serie(booth)
    booth.take_shot(s, ersetzt=2)
    dateien = sorted(p.name for p in s.folder.glob("shot*.jpg"))
    assert dateien == sorted(p.name for p in s.shots), "Keine Karteileiche im Ordner"


# ── Der Schirm, der danach fragt ────────────────────────────────────────
WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


def test_there_is_a_screen_to_look_at_the_series():
    html = (WEB / "index.html").read_text()
    assert 'id="s-review"' in html and 'id="reviewShots"' in html
    assert 'data-action="review-ok"' in html


def test_it_only_shows_up_where_it_helps():
    js = (WEB / "static" / "app.js").read_text()
    assert "flow.needed > 1 && S.profile.retake" in js, \
        "Bei einer einzelnen Aufnahme gibt es nichts durchzusehen"
    assert 'replace: ersetzt || 0' in js


def test_nobody_gets_stuck_on_it():
    js = (WEB / "static" / "app.js").read_text()
    assert "reviewTimerStart" in js and "review_seconds" in js, \
        "Ohne Zeitschaltung steht die Box, sobald jemand weggeht"


def test_both_languages_know_the_words():
    i18n = (WEB / "static" / "i18n.js").read_text()
    for key in ("review.title", "review.hint", "review.ok", "review.again", "screen.review"):
        assert i18n.count(f'"{key}"') == 2, f"{key} fehlt in einer Sprache"


def test_the_profile_carries_the_waiting_time():
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["review_seconds"] == 12


def test_the_screen_is_actually_shown():
    """Ein Bildschirm, den das Stylesheet nicht kennt, bleibt schwarz."""
    css = (WEB / "static" / "app.css").read_text()
    assert '#app[data-screen="review"] #s-review' in css


def test_the_buzzer_means_carry_on():
    js = (WEB / "static" / "app.js").read_text()
    assert 'if (sc === "review") return reviewFertig();' in js
    assert '|| sc === "review") return;' in js, \
        "Andere Knöpfe dürfen die Serie nicht mitten im Durchsehen verlassen"


def test_going_home_does_not_come_back_later():
    js = (WEB / "static" / "app.js").read_text()
    assert "clearTimeout(reviewTimer); reviewGo = null;" in js


# ── Abbrechen ───────────────────────────────────────────────────────────
def test_a_running_series_can_be_thrown_away(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    s = _serie(booth)
    ordner = s.folder
    assert client.post(f"/api/session/{s.uid}/abort").status_code == 200
    assert not ordner.exists(), "Die Aufnahmen müssen weg sein"
    assert booth.library.get(s.uid) is None
    assert booth.outbox.counts()["pending"] == 0, "Nichts darf noch zur Galerie unterwegs sein"


def test_the_way_out_does_not_depend_on_the_delete_switch(booth):
    """``allow_delete`` regelt fertige Bilder — nicht den Ausstieg aus einer Serie."""
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["allow_delete"] = False
    client = TestClient(create_app(booth))
    s = _serie(booth)
    assert client.post(f"/api/session/{s.uid}/abort").status_code == 200


def test_a_finished_run_is_not_aborted_but_deleted(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    s = _serie(booth)
    booth.finalize(s, "original")
    assert client.post(f"/api/session/{s.uid}/abort").status_code == 409


def test_the_screen_offers_the_way_out():
    html = (WEB / "index.html").read_text()
    js = (WEB / "static" / "app.js").read_text()
    assert 'data-action="review-cancel"' in html
    assert '/abort' in js and 'review.cancel_confirm' in js


def test_saying_no_does_not_leave_the_box_standing():
    js = (WEB / "static" / "app.js").read_text()
    assert 'if (app.dataset.screen === "review") reviewTimerStart();' in js


def test_the_words_for_it_exist_in_both_languages():
    i18n = (WEB / "static" / "i18n.js").read_text()
    for key in ("review.cancel", "review.cancel_confirm", "review.cancel_yes"):
        assert i18n.count(f'"{key}"') == 2, f"{key} fehlt in einer Sprache"


# ── Funde der Durchsicht vom 19.09.2026 ───────────────────────────────

def test_a_failed_retake_leaves_the_old_shot_standing(booth):
    """Das alte Bild wurde gelöscht, bevor das neue existierte."""
    s = _serie(booth)
    vorher = [p.read_bytes() for p in s.shots]
    anzahl = len(s.shots)
    assert anzahl >= 3

    def kaputt(ziel):
        raise RuntimeError("USB weg")

    alt = booth.camera.capture
    booth.camera.capture = kaputt
    try:
        with pytest.raises(Exception):
            booth.take_shot(s, ersetzt=2)
    finally:
        booth.camera.capture = alt

    assert len(s.shots) == anzahl, "Eine Aufnahme ist verschwunden"
    assert all(p.exists() for p in s.shots), "Eine Datei ist weg"
    assert [p.read_bytes() for p in s.shots] == vorher


def test_a_working_retake_still_replaces_the_shot(booth):
    """Die Gegenprobe: Wenn die Kamera antwortet, wird ersetzt."""
    s = _serie(booth)
    anzahl = len(s.shots)
    booth.take_shot(s, ersetzt=2)
    assert len(s.shots) == anzahl
    assert s.shots[1].name == "shot2.jpg"
    assert not (s.folder / "shot2.neu.jpg").exists(), "Zwischendatei liegengeblieben"
    dateien = sorted(p.name for p in s.folder.glob("shot*.jpg"))
    assert dateien == sorted(p.name for p in s.shots), "Karteileiche im Ordner"


def test_only_one_wipe_timer_per_session(booth):
    """„Nochmal" legte einen zweiten Zeitgeber an, ohne den ersten abzubrechen."""
    booth.store.profile["privacy"] = {**booth.profile.get("privacy", {}),
                                      "mode": "print_only", "delete_seconds": 3600}
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    erster = booth._wipe_timers.get(s.uid)
    assert erster is not None

    booth.take_shot(s, retake=True)         # „Nochmal"
    booth.finalize(s, "original")
    zweiter = booth._wipe_timers.get(s.uid)
    assert zweiter is not None and zweiter is not erster
    assert not erster.is_alive() or erster.finished.is_set(), "Der alte Zeitgeber läuft weiter"
