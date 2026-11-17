"""Running the tests must never put a sheet into somebody's printer.

A booth with default settings picks its printer by itself ("auto"): the Windows
default printer on Windows, the CUPS default wherever ``lp`` exists. That is
right on the booth PC and wrong on a developer's machine - on 2026-11-11 one
run of this suite on a Mac queued 22 jobs on the photo printer that happened to
be the system default. Nothing came out only because the printer was switched
off.

``conftest.py`` closes that door for every test. These tests keep it closed.
"""
import subprocess

import pytest

from boothcore.printing import service
from boothcore.printing.backends import CupsPrinter, FolderPrinter


def test_a_booth_with_default_settings_prints_into_a_folder_during_tests(tmp_path):
    for cfg in ({}, {"backend": "auto"}, {"backend": ""}, {"backend": "AUTO", "name": "Some Printer"}):
        printer = service.create_printer(cfg, tmp_path)
        assert isinstance(printer, FolderPrinter), f"{cfg} chose {type(printer).__name__}"


def test_no_printing_at_all_stays_no_printing(tmp_path):
    assert service.create_printer({"backend": "none"}, tmp_path) is None


def test_the_cups_backend_refuses_to_print_during_tests(tmp_path, monkeypatch):
    # Even a test that asks for CUPS by name must not get as far as `lp`.
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"not a picture")
    printer = service.create_printer({"backend": "cups", "name": ""}, tmp_path)
    assert isinstance(printer, CupsPrinter)
    with pytest.raises(AssertionError, match="real printer"):
        printer.print_file(sheet)
    assert called == []


def test_a_whole_booth_with_default_settings_has_no_real_printer(tmp_path, monkeypatch):
    # The path that actually queued the jobs: Booth() builds its printers
    # through a name it imported, not through the service module.
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    booth = Booth(base=tmp_path, start_threads=False)
    try:
        kinds = [type(b).__name__ for b in (s.backend for s in booth.printer.stations) if b is not None]
        assert kinds and set(kinds) == {"FolderPrinter"}, kinds
    finally:
        booth.mail.stop()
