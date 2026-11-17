"""Hintergrundbetrieb: Autostart, Symbol im Infobereich, nur eine Instanz.

Der eigentliche Win32-Teil lässt sich hier nicht ausführen. Geprüft wird
deshalb, dass nichts davon auf anderen Systemen umfällt und dass die
Fernwartung einen ehrlichen Zustand zu sehen bekommt — „kein Windows" statt
einer Fehlermeldung.
"""
import socket
import threading
import time
from pathlib import Path

from photobox_sync import tray, winsetup
from photobox_sync.standalone import SyncApp


def test_autostart_reports_honestly_on_this_system():
    state = winsetup.autostart_state()
    assert set(state) == {"supported", "enabled", "detail"}
    if not winsetup.is_windows():
        assert state == {"supported": False, "enabled": False, "detail": "kein Windows"}
        ok, detail = winsetup.enable_autostart()
        assert not ok and "Windows" in detail
        assert winsetup.report(winsetup.install()).startswith("--  Windows")


def test_the_program_path_is_the_quiet_one():
    """Ein Konsolenfenster bei jeder Anmeldung ist kein Hintergrundbetrieb."""
    target = winsetup.program_path()
    assert "photobox_sync" in target or target.endswith('"')
    if winsetup.is_windows():
        assert "pythonw" in target or target.count('"') == 2


def test_no_tray_no_crash():
    import os

    assert tray.supported() is (os.name == "nt")
    t = tray.Tray(title="Test", on_open=lambda: None, on_sync=lambda: None, on_quit=lambda: None)
    assert t.run() is False          # kein Windows: sauber nein sagen, nicht fliegen
    t.remove()                        # darf auch ohne Symbol aufgerufen werden


def test_a_second_start_does_not_open_a_second_queue(tmp_path):
    """Zwei Instanzen auf denselben Ordnern wären doppelte Uploads."""
    from photobox_sync.__main__ import _already_running

    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert _already_running(port)
    assert not _already_running(port)


def test_the_report_carries_the_autostart_state(tmp_path):
    app = SyncApp(base=tmp_path / "data", start_threads=False)
    report = app.report()
    assert report["autostart"] == winsetup.autostart_state()
    assert report["port"] == 8766 and "log_counts" in report


def test_housekeeping_warns_before_the_disk_is_full(tmp_path, monkeypatch, caplog):
    import logging
    import shutil as sh

    app = SyncApp(base=tmp_path / "data", start_threads=False)
    monkeypatch.setattr(sh, "disk_usage", lambda p: type("U", (), {"free": 1.1e9, "total": 5e11, "used": 1})())
    monkeypatch.setattr(time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    with caplog.at_level(logging.WARNING):
        try:
            app._housekeeping()
        except KeyboardInterrupt:
            pass
    assert any("frei" in r.message for r in caplog.records)
    # und die Warnung geht mit dem Heartbeat in die Galerie
    assert any("frei" in e["message"] for e in app.agent.logs())


def test_the_web_page_ships_with_the_program():
    """Ohne diese Datei startet die .exe, zeigt aber nichts an."""
    page = Path(__import__("photobox_sync").__file__).parent / "web" / "index.html"
    text = page.read_text(encoding="utf-8")
    assert "Photobox Sync" in text and "Sync-Aufträge" in text
    assert "<script" in text and "cdn" not in text.lower()      # nichts nachzuladen, auch ohne Internet
