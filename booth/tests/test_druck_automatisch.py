"""„Jedes Bild automatisch drucken" — die Einstellung, auf der ein Abend steht.

Der Betreiber hakt das an, stellt die Box hin und geht. Wenn es dann nicht
tut, merkt es niemand, bis die ersten Gäste ohne Abzug dastehen. Genau diese
Sorte Funktion braucht Tests, und sie hatte bis zum 20.09.2026 keinen —
nur eine Testdatei, die sie zur Vorbereitung auf ``False`` setzte.

Geprüft wird über die Warteschlange des Druckdienstes, nicht über einen
echten Drucker: Was zählt, ist, dass genau ein Auftrag entsteht.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-autoprint-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


class Zaehler:
    """Ein Drucker, der nichts druckt und nur mitschreibt."""

    name = "zaehler"

    def __init__(self):
        self.auftraege = []

    def status(self):
        return {"state": "ready", "name": "Zähler"}

    def print_file(self, path, copies: int = 1):
        self.auftraege.append((Path(path).name, copies))


@pytest.fixture()
def drucker(booth):
    z = Zaehler()
    booth.printer.stations[0].backend = z
    return z


def _durchgang(booth, modus="photo"):
    s = booth.start_session(modus, "4:5", None, None)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    return s


def _warte(drucker, sekunden=6.0):
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende and not drucker.auftraege:
        time.sleep(0.05)
    return drucker.auftraege


# ── Der Normalfall ────────────────────────────────────────────────────

def test_switched_on_every_picture_prints_by_itself(booth, drucker):
    booth.store.profile["print"] = {**booth.profile["print"], "enabled": True, "auto": True}
    s = _durchgang(booth)
    booth.finalize(s, "original")
    assert _warte(drucker), "Der Abzug kam nicht von allein"
    assert len(drucker.auftraege) == 1
    assert drucker.auftraege[0][1] == 1, "Automatisch heißt genau ein Exemplar"


def test_switched_off_nothing_prints(booth, drucker):
    """Die Gegenprobe — sonst beweist der Test oben nur, dass gedruckt wird."""
    booth.store.profile["print"] = {**booth.profile["print"], "enabled": True, "auto": False}
    s = _durchgang(booth)
    booth.finalize(s, "original")
    time.sleep(1.0)
    assert not drucker.auftraege, "Es wurde gedruckt, obwohl der Haken aus ist"


# ── Die Fälle, die einen Abend kosten ─────────────────────────────────

def test_taking_it_again_does_not_print_twice(booth, drucker):
    """„Nochmal" ruft finalize ein zweites Mal — das Papier ist dann doppelt weg.

    Bei 400 Blatt in der Kassette und einer Box, die den ganzen Abend
    automatisch druckt, ist das der Unterschied zwischen „reicht" und
    „nach der Hälfte leer".
    """
    booth.store.profile["print"] = {**booth.profile["print"], "enabled": True, "auto": True}
    s = _durchgang(booth)
    booth.finalize(s, "original")
    assert _warte(drucker)
    booth.take_shot(s, retake=True)
    booth.finalize(s, "original")
    time.sleep(1.0)
    assert len(drucker.auftraege) == 1, f"Zweiter Abzug für dieselbe Sitzung: {drucker.auftraege}"


def test_a_different_look_does_not_print_again(booth, drucker):
    """Wer am Ergebnisschirm den Filter wechselt, löst finalize erneut aus."""
    booth.store.profile["print"] = {**booth.profile["print"], "enabled": True, "auto": True}
    s = _durchgang(booth)
    booth.finalize(s, "original")
    assert _warte(drucker)
    booth.finalize(s, booth.look_names()[-1])
    time.sleep(1.0)
    assert len(drucker.auftraege) == 1, "Jeder Filterwechsel kostet ein Blatt"


def test_a_video_does_not_stop_the_session(booth, drucker):
    """Ein Clip hat keine Druckdatei — das darf den Durchgang nicht abbrechen."""
    booth.store.profile["print"] = {**booth.profile["print"], "enabled": True, "auto": True}
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    s.print_file = None
    s.finalized = False
    booth.finalize(s, "original")            # darf nicht werfen
    assert True


def test_a_printer_that_refuses_does_not_break_the_session(booth):
    """Drucker aus, Kassette leer, Kontingent erreicht: Die Sitzung läuft trotzdem.

    Das Foto gehört in die Galerie, auch wenn kein Abzug herauskommt. Eine
    Ausnahme an dieser Stelle würde den ganzen Durchgang verlieren.
    """
    booth.store.profile["print"] = {**booth.profile["print"], "enabled": True,
                                    "auto": True, "limit_event": 0, "copies_max": 1}

    class Kaputt:
        name = "kaputt"

        def status(self):
            return {"state": "error", "name": "kaputt"}

        def print_file(self, path, copies: int = 1):
            raise OSError("Drucker offline")

    booth.printer.stations[0].backend = Kaputt()
    s = _durchgang(booth)
    fertig = booth.finalize(s, "original")   # darf nicht werfen
    assert fertig.result and fertig.result.exists(), "Die Aufnahme ging verloren"


def test_the_setting_the_operator_ticks_is_the_one_that_acts():
    """Der Haken in der Verwaltung und der Schlüssel im Code müssen derselbe sein."""
    vorlage = (Path(__file__).resolve().parents[2] / "app" / "templates" / "admin"
               / "booth_profile.html").read_text()
    assert 'name="print_auto"' in vorlage, "Der Haken fehlt in der Verwaltung"
    quelle = (Path(__file__).resolve().parents[1] / "boothcore" / "app.py").read_text()
    assert 'self.profile["print"].get("auto")' in quelle, \
        "Die Verwaltung schreibt einen Schalter, den niemand ausliest"


# ── Dieselbe Wurzel, zwei weitere Symptome ────────────────────────────

def test_taking_it_again_is_not_a_second_session(booth):
    """``sessions_total`` zählte jede Wiederholung als weitere Sitzung.

    Bekannt und benannt, aber nie behoben — es war derselbe Fehler wie beim
    Doppeldruck: ``finalized`` beantwortet „ist das Ergebnis aktuell", nicht
    „ist dieser Durchgang neu".
    """
    vorher = booth.sessions_total
    s = _durchgang(booth)
    booth.finalize(s, "original")
    booth.take_shot(s, retake=True)
    booth.finalize(s, "original")
    assert booth.sessions_total == vorher + 1, "Eine Wiederholung zählt als zweite Sitzung"


def test_two_sessions_are_two_sessions(booth):
    """Die Gegenprobe: Der Zähler darf nicht einfach stehenbleiben."""
    vorher = booth.sessions_total
    for _ in range(2):
        booth.finalize(_durchgang(booth), "original")
    assert booth.sessions_total == vorher + 2
