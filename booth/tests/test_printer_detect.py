"""Die Box findet selbst heraus, was für ein Drucker dranhängt.

Ein Betreiber weiß, dass ein DS620 dasteht. Welche Kennung unsere Tabelle
dafür führt und wie viele Blatt auf einer vollen Kassette sind, weiß er nicht
— und soll es auch nicht nachschlagen müssen. Beides steht im Namen, den das
Betriebssystem ohnehin nennt, und im Papier, das der Treiber eingestellt hat.
"""
import tempfile
from pathlib import Path

import pytest

from boothcore.printing import models
from boothcore.printing.backends import PrinterBackend
from boothcore.printing.service import erkennen, printer_configs, uebernehmen


@pytest.mark.parametrize("name, erwartet", [
    ("Canon SELPHY CP1300", "selphy-cp"),
    ("SELPHY CP1500 (Kopie 1)", "selphy-cp"),
    ("Canon SELPHY QX10", "selphy-square"),
    ("DS620 (Copy 1)", "dnp-ds620"),
    ("DNP DS-RX1HS", "dnp-rx1"),
    ("DNP QW410", "dnp-qw410"),
    ("Mitsubishi CP-D70DW", "mitsubishi-d70"),
    ("EPSON SureLab D700", "epson-d700"),
    ("HP OfficeJet Pro 9010", "hp-tinte"),
    ("HP Sprocket", "hp-sprocket"),
    ("Sony UP-DR80MD", "sony-updr80"),
])
def test_the_name_says_which_machine_it_is(name, erwartet):
    assert models.erkenne(name) == erwartet


@pytest.mark.parametrize("name", ["", "Brother HL-2030", "Microsoft Print to PDF", None])
def test_what_it_does_not_know_it_does_not_claim(name):
    """„Ich weiß es nicht" muss sich von „ich habe nachgesehen" unterscheiden."""
    assert models.erkenne(name) is None


def test_the_specific_beats_the_general():
    # „HP Sprocket" darf nicht als Bürodrucker durchgehen — andere Blattzahl.
    assert models.erkenne("HP Sprocket 2x3") == "hp-sprocket"
    assert models.erkenne("Canon SELPHY Square QX20") == "selphy-square"


@pytest.mark.parametrize("text, erwartet", [
    ("4x6.Fullbleed", "10x15"),
    ("Postcard (4x6in)", "10x15"),
    ("2x6 strip", "10x15-cut"),
    ("15x20", "15x20"),
])
def test_the_paper_the_driver_reports_becomes_our_medium(text, erwartet):
    assert models.erkenne_medium("dnp-ds620", text) == erwartet


def test_a_machine_with_one_medium_needs_no_guessing():
    """Ein SELPHY kann genau ein Format — da gibt es nichts zu erkennen."""
    assert models.erkenne_medium("selphy-cp", "") == "postcard"


def test_unreadable_paper_stays_unknown():
    assert models.erkenne_medium("dnp-ds620", "") is None
    assert models.erkenne_medium("dnp-ds620", "Sonderformat 3") is None


# ── Übernehmen ─────────────────────────────────────────────────────────
class _Erfundener(PrinterBackend):
    """Ein Drucker, den es nur im Test gibt — sonst bräuchte es Hardware."""
    name = "test"

    def __init__(self, drucker, papier="", standard=None):
        self._drucker = drucker
        self._papier = papier
        # Was das System als Standarddrucker nennt — bei Windows genau das,
        # worauf ohne eingetragenen Namen tatsächlich gedruckt würde.
        self._standard = standard if standard is not None else (drucker[0] if drucker else "")

    def list_printers(self):
        return list(self._drucker)

    def papier(self):
        return self._papier

    def status(self):
        return {"state": "ready", "name": self._standard}


@pytest.fixture()
def falscher_drucker(monkeypatch):
    def setze(drucker, papier="", standard=None):
        monkeypatch.setattr("boothcore.printing.service.create_printer",
                            lambda cfg, data: _Erfundener(drucker, papier, standard))
    return setze


def test_it_fills_in_what_the_operator_would_have_had_to_look_up(falscher_drucker):
    falscher_drucker(["DS620 (Copy 1)"], "2x6 strip")
    ergebnis = uebernehmen({"backend": "windows"}, Path(tempfile.mkdtemp()))
    cfg = ergebnis["config"]
    assert cfg["model"] == "dnp-ds620"
    assert cfg["medium"] == "10x15-cut"
    assert cfg["name"] == "DS620 (Copy 1)"
    assert set(ergebnis["geaendert"]) == {"model", "medium", "name"}


def test_a_setting_someone_made_by_hand_is_a_decision(falscher_drucker):
    """Die Box darf sich nicht über den Betreiber hinwegsetzen."""
    falscher_drucker(["DS620"], "4x6")
    vorher = {"backend": "windows", "name": "DS620", "model": "andere", "medium": "frei"}
    ergebnis = uebernehmen(dict(vorher), Path(tempfile.mkdtemp()))
    assert ergebnis["config"]["model"] == "andere"
    assert ergebnis["config"]["medium"] == "frei"
    assert ergebnis["geaendert"] == []


def test_with_two_printers_it_follows_the_system_not_the_list(falscher_drucker):
    """Hängen zwei Geräte dran, gilt der Standarddrucker des Systems.

    Das ist keine Wahl der Box: Ohne eingetragenen Namen wird ohnehin genau
    dorthin gedruckt. Aufgelistet werden beide, damit der Betreiber umstellen
    kann — erkannt wird der, der tatsächlich drucken würde.
    """
    falscher_drucker(["DS620", "Canon SELPHY CP1300"], standard="Canon SELPHY CP1300")
    fund = erkennen({"backend": "windows"}, Path(tempfile.mkdtemp()))
    assert fund["name"] == "Canon SELPHY CP1300"
    assert fund["model"] == "selphy-cp"
    assert len(fund["drucker"]) == 2, "Aufgelistet werden aber beide"


def test_an_unknown_default_leaves_the_model_open(falscher_drucker):
    falscher_drucker(["Brother HL-2030"], standard="Brother HL-2030")
    fund = erkennen({"backend": "windows"}, Path(tempfile.mkdtemp()))
    assert fund["name"] == "Brother HL-2030" and fund["model"] is None


def test_the_sheet_count_follows_from_the_detection(falscher_drucker):
    falscher_drucker(["DNP DS-RX1HS"], "4x6")
    cfg = uebernehmen({"backend": "windows"}, Path(tempfile.mkdtemp()))["config"]
    fertig = printer_configs({"printers": [cfg]})[0]
    assert fertig["sheets_total"] == 700, "700 Blatt je Rolle — das schlägt sonst niemand nach"


def test_a_box_that_starts_knows_its_printer(falscher_drucker, monkeypatch):
    falscher_drucker(["Canon SELPHY CP1300"])
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    d = Path(tempfile.mkdtemp(prefix="booth-detect-"))
    b = Booth(base=d, start_threads=False)
    try:
        assert b.store.local["printer"]["model"] == "selphy-cp"
        assert b.store.local["printer"]["medium"] == "postcard"
        # und es steht auf der Platte, nicht nur im Speicher
        from boothcore.config import Store
        assert Store(d).local["printer"]["model"] == "selphy-cp"
    finally:
        b.mail.stop()


def test_looking_again_is_possible_while_it_runs(falscher_drucker, monkeypatch):
    """Drucker getauscht, Kassette gewechselt — dafür der Knopf in der Verwaltung."""
    falscher_drucker(["DNP QW410"], "10x20")
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=Path(tempfile.mkdtemp(prefix="booth-detect2-")), start_threads=False)
    try:
        fund = b.drucker_suchen()
        assert fund["printer"]["model"] == "dnp-qw410"
        assert fund["printer"]["medium"] == "10x20"
    finally:
        b.mail.stop()
