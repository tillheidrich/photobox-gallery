"""Druckaufträge dürfen nicht verschwinden — auch nicht beim Neustart.

Der Abend, für den das gebaut ist: Farbband leer, zehn Karten stehen aus, der
Betreiber wechselt die Kassette, und dazwischen startet die Box neu. Lag die
Warteschlange nur im Arbeitsspeicher, warten zehn Gäste auf etwas, das nie
kommt.

Zweiter Punkt: Während gedruckt wird, muss weiter fotografiert werden können.
Der SELPHY braucht eine Minute pro Karte — solange darf die Box nicht stehen.
"""
from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.printing.service import PrintJob, PrintService
from boothcore.printing.spool import Spool


class Zaehler:
    """Ein Drucker, der zählt — und auf Wunsch kaputtgeht."""

    name = "test"

    def __init__(self):
        self.gedruckt = []
        self.kaputt = False
        self.langsam = 0.0

    def status(self):
        # Like the real backends: a printer in error says why. Since
        # 14.11.2026 a printer known to be in error gets no job, so the
        # operator's reason comes from here, not from a failed print.
        return {"state": "error", "hinweis": "Kein Papier mehr"} if self.kaputt else {"state": "ready"}

    def print_file(self, path, copies=1):
        if self.langsam:
            time.sleep(self.langsam)
        if self.kaputt:
            raise RuntimeError("Kein Papier mehr")
        self.gedruckt.append((Path(path).name, copies))


@pytest.fixture()
def ordner():
    return Path(tempfile.mkdtemp(prefix="spool-"))


def _bild(ordner: Path, name="a.jpg") -> Path:
    p = ordner / name
    Image.new("RGB", (60, 90), (200, 100, 60)).save(p)
    return p


def _warte(bedingung, sekunden=6.0):
    ende = time.time() + sekunden
    while time.time() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return False


def test_a_print_is_written_down_before_it_is_sent(ordner):
    drucker = Zaehler()
    dienst = PrintService(drucker, ordner / "counter.txt")
    dienst.submit(PrintJob(path=_bild(ordner), copies=1, session_uid="u1"))
    assert _warte(lambda: drucker.gedruckt)
    assert _warte(lambda: dienst.spool.counts()["done"] == 1)


def test_what_is_open_survives_a_restart(ordner):
    """Erster Dienst nimmt an und scheitert, zweiter Dienst holt es nach."""
    kaputt = Zaehler()
    kaputt.kaputt = True
    erst = PrintService(kaputt, ordner / "counter.txt")
    erst.max_attempts = 1
    erst.submit(PrintJob(path=_bild(ordner), copies=2, session_uid="u1"))
    assert _warte(lambda: erst.spool.counts()["held"] == 1)

    # „Neustart": neuer Dienst, gleicher Ordner, Drucker wieder in Ordnung
    heile = Zaehler()
    zweit = PrintService(heile, ordner / "counter.txt")
    assert zweit.spool.counts()["held"] == 1        # wartet, bis jemand Papier nachlegt
    assert zweit.release_held() == 1
    assert _warte(lambda: heile.gedruckt == [("a.jpg", 2)])


def test_a_job_that_was_printing_when_the_power_went_is_tried_again(ordner):
    """Niemand weiß, ob das Blatt durchlief — lieber einmal zu viel."""
    spool = Spool(ordner / "spool.sqlite")
    nummer = spool.add(path=str(_bild(ordner)), copies=1)
    spool.started(nummer, 0)
    assert spool.counts()["printing"] == 1

    neu = Spool(ordner / "spool.sqlite")             # wie nach einem Neustart
    assert neu.counts()["printing"] == 0 and neu.counts()["queued"] == 1


def test_a_failed_print_waits_for_the_operator_instead_of_vanishing(ordner):
    drucker = Zaehler()
    drucker.kaputt = True
    dienst = PrintService(drucker, ordner / "counter.txt")
    dienst.max_attempts = 1
    dienst.submit(PrintJob(path=_bild(ordner), copies=1, session_uid="u1"))
    assert _warte(lambda: dienst.spool.counts()["held"] == 1)
    wartend = dienst.spool.held()[0]
    assert "Papier" in (wartend["last_error"] or "")
    assert dienst.status()["held"] == 1
    assert "Papier" in dienst.status()["held_reason"]


def test_taking_photos_does_not_wait_for_the_printer(ordner):
    """Der SELPHY braucht eine Minute je Karte. Solange darf nichts stehen."""
    drucker = Zaehler()
    drucker.langsam = 0.8
    dienst = PrintService(drucker, ordner / "counter.txt")
    start = time.time()
    for i in range(3):
        dienst.submit(PrintJob(path=_bild(ordner, f"b{i}.jpg"), copies=1))
    # Das Einreihen selbst kostet nichts — die Box ist sofort wieder frei.
    assert time.time() - start < 0.5
    assert _warte(lambda: len(drucker.gedruckt) == 3, sekunden=10)


def test_deleting_a_session_stops_its_unprinted_jobs(ordner):
    drucker = Zaehler()
    drucker.kaputt = True
    dienst = PrintService(drucker, ordner / "counter.txt")
    dienst.max_attempts = 1
    dienst.submit(PrintJob(path=_bild(ordner), copies=1, session_uid="weg"))
    assert _warte(lambda: dienst.spool.counts()["held"] == 1)
    assert dienst.drop_session("weg") == 1
    assert dienst.spool.counts()["held"] == 0


def test_a_missing_file_is_not_retried_forever(ordner):
    spool = Spool(ordner / "spool.sqlite")
    spool.add(path=str(ordner / "gibtesnicht.jpg"), copies=1)
    dienst = PrintService(Zaehler(), ordner / "counter.txt", spool=spool)
    assert dienst.spool.counts()["lost"] == 1


def test_old_entries_are_cleared_but_open_ones_never(ordner):
    spool = Spool(ordner / "spool.sqlite")
    fertig = spool.add(path=str(_bild(ordner)), copies=1)
    spool.done(fertig)
    offen = spool.add(path=str(_bild(ordner, "c.jpg")), copies=1)
    spool.hold(offen, "Papier")
    import sqlite3
    with sqlite3.connect(spool.path) as con:        # künstlich altern
        con.execute("UPDATE spool SET changed=changed-20*86400")
    assert spool.cleanup(tage=14) == 1
    assert spool.counts()["held"] == 1


# ── Drei Funde der Durchsicht vom 19.09.2026 ──────────────────────────

class Waehlerisch(Zaehler):
    """Ein Drucker, der an einer bestimmten Datei scheitert und sonst wartet.

    Damit lässt sich der Abend nachstellen, um den es geht: Ein Auftrag liegt
    auf „held", zwei weitere stehen in der Reihe, und dann wechselt jemand
    das Papier.
    """

    def __init__(self):
        super().__init__()
        self.scheitert_an = set()
        self.tor = threading.Event()

    def print_file(self, path, copies=1):
        name = Path(path).name
        if name in self.scheitert_an:
            raise RuntimeError("Kein Papier mehr")
        self.tor.wait(10)
        self.gedruckt.append((name, copies))


def test_paper_changed_does_not_print_the_waiting_jobs_twice(ordner):
    """„Papier gewechselt" reihte alles ein zweites Mal ein.

    Ein Auftrag steht in der Datenbank auf ``queued``, bis ein Drucker ihn
    anfasst. ``release_held`` holte alles ``queued`` zurück in die Reihe —
    auch das, was längst im Speicher wartete. Der Gast bekam sein Bild
    doppelt. Gefunden bei der Durchsicht am 19.09.2026.
    """
    drucker = Waehlerisch()
    drucker.scheitert_an = {"a.jpg"}
    dienst = PrintService(drucker, ordner / "counter.txt")
    dienst.max_attempts = 1
    dienst.submit(PrintJob(path=_bild(ordner, "a.jpg"), copies=1, session_uid="u1"))
    assert _warte(lambda: dienst.spool.counts().get("held") == 1)

    dienst.submit(PrintJob(path=_bild(ordner, "b.jpg"), copies=1, session_uid="u2"))
    dienst.submit(PrintJob(path=_bild(ordner, "c.jpg"), copies=1, session_uid="u3"))
    assert _warte(lambda: dienst.spool.counts().get("printing") == 1)   # b hängt am Tor, c wartet

    drucker.scheitert_an = set()
    assert dienst.release_held() == 1        # der Betreiber drückt „Papier gewechselt"
    drucker.tor.set()

    assert _warte(lambda: len(drucker.gedruckt) >= 3, 12.0)
    time.sleep(1.0)                          # einem doppelten Druck Zeit geben, aufzutauchen
    namen = sorted(n for n, _ in drucker.gedruckt)
    assert namen == ["a.jpg", "b.jpg", "c.jpg"], f"doppelt gedruckt: {drucker.gedruckt}"


def test_a_job_that_really_waits_is_still_retried_after_paper_changed(ordner):
    """Die Gegenprobe: Was auf „held" liegt, muss weiterhin nachgeholt werden."""
    kaputt = Zaehler()
    kaputt.kaputt = True
    dienst = PrintService(kaputt, ordner / "counter.txt")
    dienst.max_attempts = 1
    dienst.submit(PrintJob(path=_bild(ordner), copies=1, session_uid="u1"))
    assert _warte(lambda: dienst.spool.counts().get("held") == 1)
    kaputt.kaputt = False
    assert dienst.release_held() == 1
    assert _warte(lambda: kaputt.gedruckt == [("a.jpg", 1)])


def test_a_deleted_session_does_not_come_out_of_the_printer(ordner):
    """Der Gast löscht, während der Drucker noch mit dem Vorgänger läuft."""
    drucker = Zaehler()
    drucker.langsam = 0.6
    dienst = PrintService(drucker, ordner / "counter.txt")
    dienst.submit(PrintJob(path=_bild(ordner, "erst.jpg"), copies=1, session_uid="u1"))
    dienst.submit(PrintJob(path=_bild(ordner, "geloescht.jpg"), copies=1, session_uid="u2"))
    assert dienst.drop_session("u2") == 1
    assert _warte(lambda: drucker.gedruckt == [("erst.jpg", 1)], 8.0)
    time.sleep(0.9)
    assert [n for n, _ in drucker.gedruckt] == ["erst.jpg"], f"gedruckt: {drucker.gedruckt}"
