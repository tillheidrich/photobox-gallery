"""Was ein langsamer Sublimationsdrucker für die Box bedeutet.

Eine SELPHY CP1300 braucht laut Canon rund 47 Sekunden je Postkarte. Bei fünf
Leuten in der Schlange wartet der letzte vier Minuten — das gehört gesagt, und
das Papier gehört gezählt, weil der Drucker seinen Vorrat nicht meldet.
"""
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-selphy-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def test_the_queue_turns_into_a_waiting_time(booth, monkeypatch):
    booth.store.profile["print"]["seconds_per_print"] = 47
    monkeypatch.setattr(type(booth.printer), "pending", property(lambda self: 5))
    allow = booth.print_allowance(None)
    assert allow["queue"] == 5
    assert allow["wait_seconds"] == 235          # fünf Bilder vor einem: knapp vier Minuten


def test_without_a_speed_there_is_no_invented_waiting_time(booth, monkeypatch):
    booth.store.profile["print"]["seconds_per_print"] = 0
    monkeypatch.setattr(type(booth.printer), "pending", property(lambda self: 3))
    assert booth.print_allowance(None)["wait_seconds"] == 0


def test_paper_is_counted_not_measured(booth):
    """Der Drucker sagt nicht, wie viel Papier drin ist. Die Box zählt mit."""
    booth.store.profile["print"]["sheets_per_cassette"] = 18
    booth.printer.printed = 0
    assert booth.media_left()["sheets_left"] == 18

    booth.printer.printed = 15
    assert booth.media_left()["sheets_left"] == 3

    # Nach der Kassette fängt die Zählung von vorn an.
    booth.printer.printed = 18
    assert booth.media_left()["sheets_left"] == 18


def test_no_cassette_size_means_no_claim(booth):
    booth.store.profile["print"]["sheets_per_cassette"] = 0
    assert booth.media_left() == {"sheets_left": None}


def test_the_doctor_warns_before_the_paper_runs_out(booth):
    from boothcore.doctor import Doctor

    booth.store.profile["print"]["sheets_per_cassette"] = 18
    booth.printer.printed = 16
    check = next(c for c in Doctor(booth).run() if c.name == "Papiervorrat")
    assert check.state == "warn" and "2 Blatt" in check.message
    assert "zurücksetzen" in check.hint

    booth.printer.printed = 2
    check = next(c for c in Doctor(booth).run() if c.name == "Papiervorrat")
    assert check.state == "ok" and "16 von 18" in check.message


def test_the_doctor_stays_quiet_without_a_cassette_size(booth):
    from boothcore.doctor import Doctor
    check = next(c for c in Doctor(booth).run() if c.name == "Papiervorrat")
    assert check.state == "skip"


def test_the_postcard_format_matches_the_selphy():
    """148 x 100 mm laut Canon — nicht 10 x 15, das ist DNP."""
    from boothcore.printing.layout import PAPERS
    assert PAPERS["postcard"] == (148.0, 100.0)
    assert PAPERS["card"] == (86.0, 54.0)


def _two_counters(booth, monkeypatch, fach, band, gesamt=18):
    monkeypatch.setattr(booth, "sheets", lambda: [{
        "slot": 1, "label": "SELPHY", "quelle": "gezählt", "rest": min(fach, band), "gesamt": gesamt,
        "fach": {"rest": fach, "gesamt": gesamt}, "farbband": {"rest": band, "gesamt": gesamt}, "leer": False}])
    booth.store.profile["print"]["enabled"] = True
    booth.store.profile["print"]["sheets_per_cassette"] = 0
    from boothcore.doctor import Doctor
    return next(c for c in Doctor(booth).run() if c.name == "Papiervorrat")


def test_the_doctor_names_what_runs_out_first_on_a_printer_with_tray_and_ribbon(booth, monkeypatch):
    check = _two_counters(booth, monkeypatch, fach=2, band=11)
    assert check.state == "warn"
    assert "Kassettenwechsel" not in check.message
    assert "Papierfach 2 von 18" in check.message and "Farbband 11 von 18" in check.message
    assert "Papier ist zuerst" in check.message
    assert "noch 2 Blatt" in check.message and "gezählt" in check.message
    check = _two_counters(booth, monkeypatch, fach=15, band=7)
    assert check.state == "ok" and "Farbband ist zuerst" in check.message and "noch 7 Blatt" in check.message


def test_the_other_printers_keep_their_paper_text(booth, monkeypatch):
    monkeypatch.setattr(booth, "sheets", lambda: [{"slot": 1, "label": "DNP", "quelle": "gezählt", "rest": 2, "gesamt": 18, "leer": False}])
    booth.store.profile["print"]["enabled"] = True
    from boothcore.doctor import Doctor
    check = next(c for c in Doctor(booth).run() if c.name == "Papiervorrat")
    assert check.state == "warn" and "Noch 2 Blatt bis zum Kassettenwechsel." == check.message
