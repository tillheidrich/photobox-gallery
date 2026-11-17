"""Drucker: was sie können, was sie melden, und wie viel noch da ist.

Gedruckt wird überall über den Herstellertreiber — SELPHY, DNP, Citizen,
Mitsubishi, HiTi, Epson, HP. Der Unterschied liegt woanders: Ein SELPHY kann
genau ein Format, ein DS620 fünf, und keiner von beiden sagt, wie viele Blätter
noch auf der Kassette sind. Deshalb zählt die Box mit — und sagt dazu, dass sie
zählt statt zu wissen.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.printing import models
from boothcore.printing.backends import FolderPrinter
from boothcore.printing.service import PrintJob, PrintService, printer_configs


def _dienst(tmp: Path, **optionen):
    img = tmp / "blatt.jpg"
    Image.new("RGB", (1200, 1800), (120, 90, 60)).save(img)
    dienst = PrintService(FolderPrinter(tmp / "aus"), tmp / "zaehler.txt", options=[optionen])
    return dienst, img


@pytest.fixture()
def tmp():
    return Path(tempfile.mkdtemp(prefix="drucker-"))


# ── Modellkunde ───────────────────────────────────────────

def test_the_usual_event_printers_are_known():
    marken = {m["marke"] for m in models.MODELLE}
    assert {"Canon", "DNP", "Citizen", "Mitsubishi", "HiTi", "Epson", "HP", "Sony"} <= marken


def test_a_selphy_knows_it_can_do_exactly_one_format():
    selphy = models.modell("selphy-cp")
    assert len(selphy["medien"]) == 1
    assert models.blatt_je_kassette("selphy-cp", "postcard") == 108


def test_the_dnp_knows_its_cut_formats():
    """Der 2-Zoll-Schnitt verdoppelt die Ausbeute — das darf die Box wissen."""
    voll = models.blatt_je_kassette("dnp-ds620", "10x15")
    geschnitten = models.blatt_je_kassette("dnp-ds620", "10x15-cut")
    assert geschnitten == 2 * voll
    assert models.medium("dnp-ds620", "10x15-cut")["cut"] == 2


def test_an_unknown_printer_says_unknown_rather_than_guessing():
    assert models.blatt_je_kassette("gibtsnicht", "irgendwas") == 0
    assert models.modell("gibtsnicht")["id"] == "andere"


def test_only_printers_that_really_report_supplies_claim_to():
    assert models.meldet_bestand("epson-d700") is True
    assert models.meldet_bestand("selphy-cp") is False, "Thermosublimation meldet keinen Füllstand"


# ── Restblätter ───────────────────────────────────────────

def test_the_sheet_count_comes_from_the_model(tmp):
    dienst, img = _dienst(tmp, label="SELPHY", model="selphy-cp", medium="postcard")
    # printer_configs füllt die Blattzahl — hier direkt geprüft:
    cfgs = printer_configs({"printers": [{"backend": "folder", "model": "selphy-cp", "medium": "postcard"}]})
    assert cfgs[0]["sheets_total"] == 108


def test_printing_counts_down_and_says_it_is_counting(tmp):
    dienst, img = _dienst(tmp, model="selphy-cp", medium="postcard", sheets_total=108)
    dienst.submit(PrintJob(img, 3, "uid"))
    for _ in range(40):
        if not dienst.busy and not dienst.pending:
            break
        time.sleep(0.05)
    blatt = dienst.sheets()[0]
    assert blatt["quelle"] == "gezählt" and blatt["rest"] == 105 and blatt["gesamt"] == 108


def test_paper_changed_starts_from_full_again(tmp):
    dienst, img = _dienst(tmp, model="selphy-cp", medium="postcard", sheets_total=108)
    dienst.submit(PrintJob(img, 2, "uid"))
    time.sleep(0.5)
    dienst.paper_changed()
    assert dienst.sheets()[0]["rest"] == 108


def test_without_a_model_nothing_is_invented(tmp):
    dienst, _img = _dienst(tmp, label="Irgendeiner")
    assert dienst.sheets()[0]["quelle"] == "unbekannt"
    assert "rest" not in dienst.sheets()[0]


def test_the_count_survives_a_restart(tmp):
    dienst, img = _dienst(tmp, model="selphy-cp", medium="postcard", sheets_total=108)
    dienst.submit(PrintJob(img, 4, "uid"))
    for _ in range(40):
        if not dienst.busy and not dienst.pending:
            break
        time.sleep(0.05)
    zweiter = PrintService(FolderPrinter(tmp / "aus"), tmp / "zaehler.txt",
                           options=[{"model": "selphy-cp", "medium": "postcard", "sheets_total": 108}])
    assert zweiter.sheets()[0]["rest"] == 104, "der Blattzähler liegt auf der Platte, nicht im Arbeitsspeicher"


def test_a_reported_level_beats_a_counted_one(tmp):
    dienst, _img = _dienst(tmp, model="epson-d700", medium="10x15", sheets_total=0)
    dienst.stations[0]._status = {"state": "ready", "supply_percent": 42}
    blatt = dienst.sheets()[0]
    assert blatt["quelle"] == "gemeldet" and blatt["prozent"] == 42


def test_empty_paper_is_empty_whatever_the_state_says(tmp):
    dienst, _img = _dienst(tmp, model="dnp-ds620", medium="10x15", sheets_total=400)
    dienst.stations[0]._status = {"state": "ready", "reasons": ["media-empty"]}
    assert dienst.sheets()[0]["leer"] is True
