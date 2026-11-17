"""A German macOS answers ``lpstat`` in German, also with ``LC_ALL=C``.

Seen on 14.11.2026 with the SELPHY CP1300 on a Mac: the queue was idle and
the booth called its state "unknown", because it only knew the English words.
"""
import subprocess

import pytest

from boothcore.printing.backends import CupsPrinter


def _lpstat(monkeypatch, text):
    class R:
        stdout = text
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    # The reasons come from the local CUPS over IPP. Without this the test
    # asked the real CUPS of the machine it runs on: on 14.11.2026 the Mac's
    # switched-off SELPHY answered "offline-report", and the queue-state
    # tests read that machine instead of the line under test.
    from boothcore.printing import backends
    monkeypatch.setattr(backends.IppPrinter, "status", lambda self: {"state": "ready", "name": self.uri})


@pytest.mark.parametrize("zeile,erwartet", [
    ("Drucker „Canon_SELPHY_CP1300“ ist inaktiv; aktiviert seit Sat Nov 14 09:46:11 2026", "ready"),
    ("Drucker „Canon_SELPHY_CP1300“ druckt jetzt Canon_SELPHY_CP1300-12; aktiviert seit Sat Nov 14", "printing"),
    ("Drucker „Canon_SELPHY_CP1300“ deaktiviert seit Sat Nov 14 09:46:11 2026 -", "error"),
    ("printer Canon_SELPHY_CP1300 is idle.  enabled since Sat Nov 14 09:46:11 2026", "ready"),
    ("printer Canon_SELPHY_CP1300 disabled since Sat Nov 14 09:46:11 2026 -", "error"),
])
def test_the_queue_state_is_understood_in_german_and_english(monkeypatch, zeile, erwartet):
    _lpstat(monkeypatch, zeile)
    assert CupsPrinter("Canon_SELPHY_CP1300").status()["state"] == erwartet


# ── Reasons come from the print service itself, not from translated text ──

def _reasons(monkeypatch, reasons):
    from boothcore.printing import backends
    _lpstat(monkeypatch, "Drucker „Canon_SELPHY_CP1300“ ist inaktiv; aktiviert seit Sat Nov 14")
    seen = {}

    def status(self):
        seen["uri"] = self.uri
        return {"state": "ready", "name": self.uri, "reasons": list(reasons)}
    monkeypatch.setattr(backends.IppPrinter, "status", status)
    return seen


def test_an_empty_paper_tray_is_an_error_in_plain_german(monkeypatch):
    seen = _reasons(monkeypatch, ["media-empty-error"])
    st = CupsPrinter("Canon_SELPHY_CP1300").status()
    assert st["state"] == "error"
    assert "Papier" in st["hinweis"]
    assert seen["uri"].endswith("/printers/Canon_SELPHY_CP1300")


def test_an_empty_ribbon_is_an_error_too(monkeypatch):
    _reasons(monkeypatch, ["marker-supply-empty-error"])
    st = CupsPrinter("Canon_SELPHY_CP1300").status()
    assert st["state"] == "error" and "Farbband" in st["hinweis"]


def test_an_unnamed_printer_error_is_shown_but_does_not_block(monkeypatch):
    """The SELPHY said "other-error" on 14.11.2026 and nobody could look at it.

    What that means is unknown, so the booth passes it on as a hint and keeps
    printing — a booth that refuses to print over a reason nobody understands
    is worse than one sheet that does not come.
    """
    _reasons(monkeypatch, ["other-error"])
    st = CupsPrinter("Canon_SELPHY_CP1300").status()
    assert st["state"] == "ready"
    assert "Anzeige" in st["hinweis"]


def test_no_reason_is_no_hint(monkeypatch):
    _reasons(monkeypatch, [])
    st = CupsPrinter("Canon_SELPHY_CP1300").status()
    assert st["state"] == "ready" and "hinweis" not in st


def test_several_requested_attributes_are_one_attribute_with_several_values(monkeypatch):
    """IPP repeats a value with an empty name. Sent as four attributes of the
    same name, CUPS answered only one of them — the reasons never arrived
    (seen against the CUPS of a Mac, 14.11.2026)."""
    from boothcore.printing.backends import IppPrinter
    sent = {}

    def request(self, op, attrs, *a, **k):
        sent["attrs"] = attrs
        raise RuntimeError("enough")
    monkeypatch.setattr(IppPrinter, "_request", request)
    IppPrinter("ipp://localhost:631/printers/x").status()
    assert sent["attrs"].count(b"requested-attributes") == 1
    for value in (b"printer-state", b"printer-state-reasons", b"marker-levels", b"marker-names"):
        assert value in sent["attrs"]
