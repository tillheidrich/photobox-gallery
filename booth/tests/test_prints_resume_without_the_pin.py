"""A day without the operator: paper refilled by a helper who has no PIN.

The SELPHY's tray holds 18 sheets, so on a wedding day it runs empty several
times. The person who refills it is the host or a guest — they put paper in
and press nothing in the admin page, because they cannot open it. Until
15.11.2026 two things then stayed broken for the rest of the evening:

* prints that had failed three times sat on "held" until somebody pressed
  "Papier nachgelegt" behind the PIN — the guests' cards never came out;
* the tray count stayed at "rechnerisch leer" and the critical notice never
  went away, so the next real "paper low" was never said.

Now a held print is tried again by itself once the printer reports ready,
at most every ``HELD_RETRY_SECONDS``; and a sheet that goes through while the
count says "empty" proves the tray was refilled, so the count starts over.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.printing.service import PrintJob, PrintService


class Selphy:
    """Says "ready" even without paper — the case where jobs end up held."""

    name = "test"

    def __init__(self):
        self.paper = True
        self.out = []

    def status(self):
        return {"state": "ready"}

    def print_file(self, path, copies=1):
        if not self.paper:
            raise RuntimeError("Kein Papier")
        self.out.append(Path(path).name)


def _bild(ordner: Path, name: str) -> Path:
    p = ordner / name
    Image.new("RGB", (60, 90), (20, 100, 160)).save(p)
    return p


def _warte(bedingung, sekunden=12.0):
    ende = time.time() + sekunden
    while time.time() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def ordner():
    return Path(tempfile.mkdtemp(prefix="held-"))


def test_a_held_print_comes_out_after_a_refill_without_any_button(ordner, monkeypatch):
    monkeypatch.setattr(PrintService, "HELD_RETRY_SECONDS", 0.5)
    drucker = Selphy()
    drucker.paper = False
    dienst = PrintService(drucker, ordner / "counter.txt")
    dienst.submit(PrintJob(path=_bild(ordner, "gast.jpg"), copies=1, session_uid="u1"))
    assert _warte(lambda: dienst.spool.counts()["held"] == 1), "the print should end up held first"

    drucker.paper = True                         # a helper put paper in, pressed nothing
    assert _warte(lambda: drucker.out == ["gast.jpg"]), "the held print must come out by itself"
    assert dienst.spool.counts()["held"] == 0


def test_held_prints_are_not_hammered_while_the_paper_is_still_missing(ordner, monkeypatch):
    monkeypatch.setattr(PrintService, "HELD_RETRY_SECONDS", 3600)
    drucker = Selphy()
    drucker.paper = False
    dienst = PrintService(drucker, ordner / "counter.txt")
    dienst.submit(PrintJob(path=_bild(ordner, "gast.jpg"), copies=1, session_uid="u1"))
    assert _warte(lambda: dienst.spool.counts()["held"] == 1)
    # The first automatic retry waits its interval; nothing is retried in between.
    time.sleep(6)
    assert dienst.spool.counts()["held"] == 1
    assert drucker.out == []


def test_a_sheet_through_an_empty_counted_tray_means_it_was_refilled(ordner):
    drucker = Selphy()
    dienst = PrintService(drucker, ordner / "counter.txt",
                          options=[{"model": "selphy-cp", "tray_total": 3, "ribbon_total": 6}])
    station = dienst.stations[0]
    for i in range(3):
        dienst.submit(PrintJob(path=_bild(ordner, f"a{i}.jpg"), copies=1, session_uid=f"u{i}"))
    assert _warte(lambda: len(drucker.out) == 3)
    assert station.sheets()["fach"]["rest"] == 0       # counted empty, nobody pressed anything

    dienst.submit(PrintJob(path=_bild(ordner, "b.jpg"), copies=1, session_uid="u9"))
    assert _warte(lambda: len(drucker.out) == 4)
    assert station.sheets()["fach"]["rest"] == 2       # the fourth sheet is the first of a new tray
    assert station.sheets()["farbband"]["rest"] == 2   # the ribbon was not empty: it counts on
