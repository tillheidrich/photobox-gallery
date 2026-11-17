"""Selbstdiagnose: sagt sie das Richtige, wenn etwas fehlt?"""
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-doctor-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})  # the operator has replaced the factory PIN (see test_factory_pin.py)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def states(checks):
    return {c.name: c.state for c in checks}


def test_healthy_booth_has_nothing_to_complain_about(booth):
    from boothcore.doctor import Doctor, FAIL, summary
    checks = Doctor(booth).run()
    assert summary(checks)["worst"] != FAIL
    assert states(checks)["Kamera"] == "ok"


def test_missing_second_folder_and_stick_are_named(booth, tmp_path):
    from boothcore.doctor import Doctor
    booth.store.save_local({"storage": {"folder": str(tmp_path / "weg"),
                                        "usb": {"enabled": True, "path": str(tmp_path / "stick")}}})
    check = next(c for c in Doctor(booth).run() if c.name == "Zweite Ablage")
    assert check.state == "fail" and "fehlt" in check.message and "Stick" in check.message
    assert "Warteschlange" in check.hint            # sagt auch, dass nichts verloren geht


def test_house_look_that_is_not_on_the_box_is_a_failure(booth):
    from boothcore.doctor import Doctor
    booth.store.profile["luts"].update({"baked": "gibt-es-nicht"})
    check = next(c for c in Doctor(booth).run() if c.name == "LUT-Filter")
    assert check.state == "fail" and "gibt-es-nicht" in check.message


def _mailpruefung(booth):
    from boothcore.doctor import Doctor
    return next(c for c in Doctor(booth).run() if c.name == "E-Mail")


def test_mail_over_smtp_without_a_server_is_a_failure(booth):
    booth.store.profile["mail"].update({"enabled": True, "transport": "smtp",
                                        "host": "", "from_addr": ""})
    assert _mailpruefung(booth).state == "fail"


def test_mail_over_the_gallery_needs_no_mailbox_on_the_box(booth):
    """Genau dafür gibt es diesen Weg — die Prüfung verlangte trotzdem SMTP.

    Bis zum 19.09.2026 meldete die Selbstprüfung einer korrekt über die
    Galerie eingestellten Box „Server oder Absender fehlt" und schickte den
    Betreiber in eine Einrichtung, die er nicht braucht.
    """
    booth.store.profile["mail"].update({"enabled": True, "transport": "gallery",
                                        "host": "", "from_addr": ""})
    assert booth.mail.relay is not None, "Diese Box ist mit einer Galerie verbunden"
    assert _mailpruefung(booth).state == "ok"


def test_mail_over_the_gallery_without_a_gallery_is_a_failure(booth):
    booth.store.profile["mail"].update({"enabled": True, "transport": "gallery",
                                        "host": "", "from_addr": ""})
    booth.mail.set_relay(None)
    assert _mailpruefung(booth).state == "fail"


def test_printing_without_a_printer_warns_instead_of_failing(booth, monkeypatch):
    from boothcore.doctor import Doctor
    booth.store.profile["print"]["enabled"] = True
    monkeypatch.setattr(type(booth.printer), "backend", property(lambda self: None))
    check = next(c for c in Doctor(booth).run() if c.name == "Drucker")
    assert check.state == "warn" and "Knopf" in check.hint


def test_a_broken_check_does_not_take_down_the_run(booth, monkeypatch):
    from boothcore import doctor as mod
    def boom(self):
        raise RuntimeError("Platte zerbröselt")

    monkeypatch.setattr(mod.Doctor, "check_disk", boom)
    checks = mod.Doctor(booth).run()
    broken = next(c for c in checks if c.name == "Speicherplatz")
    assert broken.state == "fail" and "Platte zerbröselt" in broken.message
    assert len(checks) == len(mod.Doctor.CHECKS)                        # alle anderen liefen trotzdem


def test_report_reads_like_a_sentence(booth):
    from boothcore.doctor import Doctor, report
    text = report(Doctor(booth).run())
    assert "Kamera" in text and text.strip().endswith((".", "an."))


def test_admin_endpoint_needs_the_pin(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    from boothcore.doctor import Doctor
    client = TestClient(create_app(booth))
    assert client.get("/api/admin/doctor").status_code == 401
    token = client.post("/api/admin/login", json={"pin": booth.store.local["admin_pin"]}).json()["token"]
    data = client.get("/api/admin/doctor", headers={"X-Admin-Token": token}).json()
    assert data["worst"] in ("ok", "warn", "fail") and len(data["checks"]) == len(Doctor.CHECKS)


def test_remote_job_returns_a_short_verdict(booth):
    """Die Galerie soll per Auftrag prüfen können, ohne dass jemand hinfährt."""
    booth.store.profile["mail"].update({"enabled": True, "transport": "smtp",
                                        "host": "", "from_addr": ""})
    verdict = booth.agent.job_handlers["doctor"]({})
    assert "E-Mail" in verdict and len(verdict) < 400


def test_mirror_defaults_to_off(booth):
    assert booth.profile["mirror"] == {"horizontal": False, "vertical": False}


def test_a_failed_admin_action_travels_to_the_gallery(booth, monkeypatch):
    """Die Generalprobe hat gezeigt: fehlgeschlagene Wartungsaktionen waren aus der Ferne unsichtbar."""
    from fastapi.testclient import TestClient
    from boothcore.app import Booth, create_app

    def kaputt(self):
        raise RuntimeError("kein Drucker verbunden")

    monkeypatch.setattr(Booth, "test_print", kaputt)
    client = TestClient(create_app(booth))
    token = client.post("/api/admin/login", json={"pin": booth.store.local["admin_pin"]}).json()["token"]
    booth.logbuf.take(500)                       # Puffer leeren
    r = client.post("/api/admin/action/test_print", headers={"X-Admin-Token": token})
    assert r.status_code == 500
    messages = [e["message"] for e in booth.logbuf.peek(20)]
    assert any("test_print" in m for m in messages)


def test_canon_sdk_check_stays_quiet_without_canon(booth):
    from boothcore.doctor import Doctor
    booth.store.local["camera"]["backend"] = "webcam"
    check = next(c for c in Doctor(booth).run() if c.name == "Canon-SDK")
    assert check.state == "skip"


def test_canon_sdk_check_names_a_missing_file(booth, tmp_path, monkeypatch):
    from boothcore import edsdk_setup
    from boothcore.doctor import Doctor
    monkeypatch.setattr(edsdk_setup.sys, "platform", "win32")
    booth.store.local["camera"]["backend"] = "edsdk"
    booth.store.local["camera"]["edsdk_dll"] = str(tmp_path / "weg" / "EDSDK.dll")
    check = next(c for c in Doctor(booth).run() if c.name == "Canon-SDK")
    assert check.state == "fail" and "--install-edsdk" in check.hint


def test_canon_sdk_check_is_happy_when_both_files_are_there(booth, tmp_path, monkeypatch):
    import struct

    from boothcore import edsdk_setup
    from boothcore.doctor import Doctor
    monkeypatch.setattr(edsdk_setup.sys, "platform", "win32")
    head = bytearray(b"\0" * 0x40)
    head[0:2] = b"MZ"
    head[0x3C:0x40] = struct.pack("<I", 0x40)
    body = bytes(head) + b"PE\0\0" + struct.pack("<H", 0x8664) + b"\0" * 512
    (tmp_path / "EDSDK.dll").write_bytes(body)
    (tmp_path / "EdsImage.dll").write_bytes(body)
    booth.store.local["camera"]["backend"] = "edsdk"
    booth.store.local["camera"]["edsdk_dll"] = str(tmp_path / "EDSDK.dll")
    check = next(c for c in Doctor(booth).run() if c.name == "Canon-SDK")
    assert check.state == "ok"


def test_canon_sdk_check_points_at_gphoto2_on_linux(booth, monkeypatch):
    """Auf Linux ist die Abwesenheit des SDK kein Fehler, sondern der Normalfall."""
    from boothcore import edsdk_setup
    from boothcore.doctor import Doctor
    monkeypatch.setattr(edsdk_setup.sys, "platform", "linux")
    booth.store.local["camera"]["backend"] = "edsdk"
    booth.store.local["camera"]["edsdk_dll"] = ""
    check = next(c for c in Doctor(booth).run() if c.name == "Canon-SDK")
    assert check.state == "skip" and "gphoto2" in check.hint


def test_flash_check_stays_quiet_without_a_flash(booth):
    from boothcore.doctor import Doctor
    check = next(c for c in Doctor(booth).run() if c.name == "Blitz und Belichtung")
    assert check.state == "skip"


def test_flash_check_warns_when_the_dial_is_elsewhere(booth, monkeypatch):
    """Die Frage „Av oder M?" soll die Box beantworten, nicht der Betreiber raten."""
    from boothcore.doctor import Doctor
    booth.store.profile["flash"].update({"enabled": True, "expected_mode": "M"})
    booth.store.profile["modes"] = ["photo", "collage"]
    monkeypatch.setattr(type(booth.camera.backend), "shooting_mode", lambda self: "Av")
    check = next(c for c in Doctor(booth).run() if c.name == "Blitz und Belichtung")
    assert check.state == "warn" and "Av" in check.message and "M" in check.message
    assert "Moduswahlrad" in check.hint


def test_flash_check_is_content_when_the_dial_matches(booth, monkeypatch):
    from boothcore.doctor import Doctor
    booth.store.profile["flash"].update({"enabled": True, "expected_mode": "Av", "recycle_seconds": 2})
    booth.store.profile["modes"] = ["photo", "collage"]
    monkeypatch.setattr(type(booth.camera.backend), "shooting_mode", lambda self: "Av")
    check = next(c for c in Doctor(booth).run() if c.name == "Blitz und Belichtung")
    assert check.state == "ok" and "Av" in check.message


def test_flash_check_says_when_the_mode_cannot_be_read(booth):
    """Lieber „nicht auslesbar" als eine erfundene Angabe."""
    from boothcore.doctor import Doctor
    booth.store.profile["flash"].update({"enabled": True, "expected_mode": "any"})
    booth.store.profile["modes"] = ["photo"]
    check = next(c for c in Doctor(booth).run() if c.name == "Blitz und Belichtung")
    assert check.state == "ok" and "nicht auslesbar" in check.message


def test_flash_check_flags_series_modes(booth):
    from boothcore.doctor import Doctor
    booth.store.profile["flash"].update({"enabled": True, "recycle_seconds": 2.5})
    booth.store.profile["modes"] = ["photo", "boomerang"]
    check = next(c for c in Doctor(booth).run() if c.name == "Blitz und Belichtung")
    assert check.state == "warn" and "Boomerang" in check.message


def test_a_test_picture_nobody_asked_for_is_a_failure(monkeypatch):
    """No camera found -> "auto" silently falls back to the mock camera.

    The booth then shows a test picture and looks healthy. At an event that
    is the worst case: nobody notices until the photos are looked at.
    """
    d = Path(tempfile.mkdtemp(prefix="booth-doctor-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.delenv("BOOTH_CAMERA", raising=False)
    import boothcore.camera.service as service
    from boothcore.camera.mock import MockCamera
    monkeypatch.setattr(service, "create_backend", lambda cfg: MockCamera())
    from boothcore.app import Booth
    from boothcore.doctor import Doctor, FAIL
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    try:
        for _ in range(50):
            if b.camera.state == "ready":
                break
            time.sleep(0.1)
        check = next(c for c in Doctor(b).run() if c.name == "Kamera")
        assert check.state == FAIL
        assert "Testbild" in check.message
    finally:
        b.camera.stop()
        b.mail.stop()


def test_a_chosen_test_picture_says_so(booth):
    from boothcore.doctor import Doctor
    check = next(c for c in Doctor(booth).run() if c.name == "Kamera")
    assert check.state == "ok"
    assert "Testbild" in check.message


def test_a_file_the_profile_names_but_the_booth_lacks_is_named(booth):
    """A logo that never arrived shows nothing at the booth; the self-check says which file."""
    from boothcore.doctor import Doctor
    booth.assets_dir.mkdir(parents=True, exist_ok=True)
    (booth.assets_dir / "da.png").write_bytes(b"x")
    booth.store.profile["start"]["logo"] = "logo-fehlt.png"
    booth.store.profile["sounds"]["shutter"] = "klick-fehlt.mp3"
    booth.store.profile["backdrops"]["setup"]["image"] = "da.png"
    check = next(c for c in Doctor(booth).run() if c.name == "Medien aus dem Profil")
    assert check.state == "warn"
    assert "logo-fehlt.png" in check.message and "klick-fehlt.mp3" in check.message
    assert "da.png" not in check.message
    assert check.detail["missing"] == {"logo-fehlt.png": "start.logo", "klick-fehlt.mp3": "sounds.shutter"}


def test_all_profile_media_present_is_ok(booth):
    from boothcore.doctor import Doctor
    check = next(c for c in Doctor(booth).run() if c.name == "Medien aus dem Profil")
    assert check.state == "ok"


# ── Druckmodul: what printing on Windows imports, tried for real ────────
# On the booth PC (15.11.2026) win32ui would not load without the Visual C++
# runtime and EnumJobs failed for want of win32timezone. The check imports
# what printing needs and opens the configured queue, reporting the text.
def _print_module(booth):
    from boothcore.doctor import Doctor
    return next(c for c in Doctor(booth).run() if c.name == "Druckmodul")


def test_print_module_check_is_skipped_off_windows(booth):
    assert _print_module(booth).state == "skip"


def _fake_windows_modules(monkeypatch, enum_jobs=None, missing=(), driver="Canon SELPHY CP1300"):
    import sys
    import types
    from boothcore import doctor
    monkeypatch.setattr(doctor, "_is_windows", lambda: True)
    win32print = types.ModuleType("win32print")
    win32print.OpenPrinter = lambda name: name
    win32print.ClosePrinter = lambda h: None
    win32print.EnumJobs = enum_jobs or (lambda h, first, count, level: ())
    win32print.GetPrinter = lambda h, level: {"pPrinterName": h, "pDriverName": driver}
    for name, module in (("win32print", win32print), ("win32gui", types.ModuleType("win32gui")),
                         ("win32timezone", types.ModuleType("win32timezone"))):
        monkeypatch.setitem(sys.modules, name, None if name in missing else module)


def _windows_station(booth, monkeypatch):
    class Win:
        name = "windows"
        printer = "Canon SELPHY CP1300"

        def _name(self):
            return self.printer
    monkeypatch.setattr(booth.printer.stations[0], "backend", Win())


def test_print_module_names_a_missing_module(booth, monkeypatch):
    _fake_windows_modules(monkeypatch, missing=("win32timezone",))
    check = _print_module(booth)
    assert check.state == "fail" and "win32timezone" in check.message


def test_print_module_reports_why_the_queue_cannot_be_read(booth, monkeypatch):
    def kaputt(*a):
        raise RuntimeError("(1801, 'OpenPrinter', 'Der Druckername ist ungültig.')")
    _fake_windows_modules(monkeypatch, enum_jobs=kaputt)
    _windows_station(booth, monkeypatch)
    check = _print_module(booth)
    assert check.state == "fail"
    assert "Canon SELPHY CP1300" in check.message and "Druckername ist ungültig" in check.message


def test_print_module_ok_when_the_queue_reads(booth, monkeypatch):
    _fake_windows_modules(monkeypatch)
    _windows_station(booth, monkeypatch)
    check = _print_module(booth)
    assert check.state == "ok" and "Canon SELPHY CP1300" in check.message


# ── the driver Windows 11 picks by itself (booth PC, 15.11.2026) ─────────
# With "Microsoft IPP Class Driver" the SELPHY CP1300 accepted no job; the
# Canon driver from canon.de fixed it. The check reads the driver name.
def test_print_module_warns_about_the_microsoft_ipp_class_driver(booth, monkeypatch):
    _fake_windows_modules(monkeypatch, driver="Microsoft IPP Class Driver")
    _windows_station(booth, monkeypatch)
    check = _print_module(booth)
    assert check.state == "warn"
    assert "Microsoft IPP Class Driver" in check.message and "Canon SELPHY CP1300" in check.message
    assert "canon.de" in check.hint


def test_print_module_names_the_driver_when_all_is_well(booth, monkeypatch):
    _fake_windows_modules(monkeypatch, driver="Canon SELPHY CP1300")
    _windows_station(booth, monkeypatch)
    check = _print_module(booth)
    assert check.state == "ok" and check.detail["drivers"] == {"Canon SELPHY CP1300": "Canon SELPHY CP1300"}


def test_an_unreadable_driver_name_is_no_failure(booth, monkeypatch):
    import sys
    _fake_windows_modules(monkeypatch)
    _windows_station(booth, monkeypatch)

    def kaputt(h, level):
        raise OSError("level 2 refused (fake)")
    monkeypatch.setattr(sys.modules["win32print"], "GetPrinter", kaputt)
    assert _print_module(booth).state == "ok"
