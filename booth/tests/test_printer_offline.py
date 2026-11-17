"""A switched-off or unplugged printer is not ready, and "printed" waits for evidence.

Review finding of 14.11.2026: with the SELPHY CP1300 switched off (or its USB
cable pulled) the booth on Windows still showed the printer as ready, took
print jobs and called each one "printed" the moment the Windows spooler had
accepted it. The guest was told the print was coming; nothing came.

Nothing here runs on Windows or against a real printer. ``win32print`` is a
fake that models the few things the booth asks the spooler: printer status
and attributes, the job list with each job's status bits, and deleting a job.
The bit values are those of winspool.h; whether a real SELPHY on a real
Windows sets them the way the fake does is the manual check on the booth PC.
"""
import sys
import time
import types
from pathlib import Path

import pytest

from boothcore.printing import backends
from boothcore.printing.backends import PrinterBackend, windows_job_outcome, windows_printer_state
from boothcore.printing.service import PrintJob, PrintService


# ── what the spooler's bits mean: pure functions, table tests ───────────
@pytest.mark.parametrize("status,attributes,jobs,state", [
    (0, 0, [], "ready"),
    (0x400, 0, [], "printing"),                 # PRINTER_STATUS_PRINTING
    (0, 0, [0x8], "printing"),                  # a job spooling
    (0, 0, [0x10], "printing"),                 # a job printing
    (0, 0x400, [], "offline"),                  # PRINTER_ATTRIBUTE_WORK_OFFLINE ("use printer offline")
    (0x80, 0, [], "offline"),                   # PRINTER_STATUS_OFFLINE
    (0x1000, 0, [], "offline"),                 # PRINTER_STATUS_NOT_AVAILABLE
    (0x800000, 0, [], "offline"),               # PRINTER_STATUS_SERVER_UNKNOWN
    (0, 0, [0x12], "offline"),                  # job ERROR|PRINTING: the port write failed (switched off, cable)
    (0, 0, [0x20], "offline"),                  # JOB_STATUS_OFFLINE
    (0, 0, [0x200], "offline"),                 # JOB_STATUS_BLOCKED_DEVQ
    (0, 0, [0x40], "error"),                    # JOB_STATUS_PAPEROUT
    (0, 0, [0x400], "error"),                   # JOB_STATUS_USER_INTERVENTION
    (0x10, 0, [], "error"),                     # PRINTER_STATUS_PAPER_OUT
    (0x8, 0, [], "error"),                      # PRINTER_STATUS_PAPER_JAM
    (0x2, 0, [], "error"),                      # PRINTER_STATUS_ERROR
    (0x40000, 0, [], "error"),                  # PRINTER_STATUS_NO_TONER: ribbon empty
    (0x400000, 0, [], "error"),                 # PRINTER_STATUS_DOOR_OPEN
    (0x1, 0, [], "error"),                      # PRINTER_STATUS_PAUSED: nothing comes out
    (0x20000, 0, [], "ready"),                  # PRINTER_STATUS_TONER_LOW: a hint, not a stop
    (0x4000, 0, [], "ready"),                   # PRINTER_STATUS_PROCESSING is not an open door
    # Seen on the booth PC (SELPHY CP1300, 15.11.2026; 0x2010 is a good print with the Canon driver):
    (0x400, 0, [0x2010], "printing"),           # printing, the job retained: still fine
    (0, 0, [0x2000], "printing"),               # RETAINED alone: neither failed nor done
    (0x2, 0, [0x2012], "offline"),              # after ~60 s: printer ERROR, job ERROR|PRINTING|RETAINED
    (0x2, 0, [0x12], "offline"),
    (0x2, 0, [0x2010], "error"),                # printer ERROR while the job still prints
])
def test_what_the_windows_spooler_bits_mean(status, attributes, jobs, state):
    assert windows_printer_state(status, attributes, jobs)["state"] == state


def test_the_reasons_are_named_right():
    assert windows_printer_state(0x40000, 0, [])["reasons"] == ["marker-supply-empty"]
    assert windows_printer_state(0x20000, 0, [])["reasons"] == ["marker-supply-low"]
    assert windows_printer_state(0x400000, 0, [])["reasons"] == ["door-open"]
    assert "door-open" not in windows_printer_state(0x4000, 0, []).get("reasons", [])
    assert windows_printer_state(0, 0, [0x12])["reasons"] == ["job-error"]
    assert "offline-report" in windows_printer_state(0, 0x400, [])["reasons"]


@pytest.mark.parametrize("job_status,outcome", [
    (None, "done"),             # left the spooler
    (0x80, "done"),             # JOB_STATUS_PRINTED (kept documents)
    (0x1000, "done"),           # JOB_STATUS_COMPLETE
    (0x0, "waiting"),
    (0x8, "waiting"),           # spooling
    (0x10, "waiting"),          # printing
    (0x2000, "waiting"),        # retained, nothing said yet
    (0x12, "failed"),           # error while printing
    (0x2012, "failed"),         # the same, retained (seen on the booth PC, 15.11.2026)
    (0x2010, "waiting"),        # retained while printing
    (0x2008, "waiting"),        # retained while spooling
    (0x2080, "done"),           # retained after printing
    (0x2, "failed"),
    (0x20, "failed"),           # offline
    (0x40, "failed"),           # paper out
    (0x200, "failed"),          # blocked
    (0x400, "failed"),          # user intervention
    (0x4, "failed"),            # deleting: somebody cancelled it
    (0x100, "failed"),          # deleted
])
def test_when_a_windows_job_counts_as_done(job_status, outcome):
    assert windows_job_outcome(job_status) == outcome


# ── a fake Windows spooler ───────────────────────────────────────────
class FakeSpooler:
    """The spooler as far as the booth sees it. ``online`` False = switched off / unplugged.

    ``visible_offline``: whether Windows marks the printer offline on its own
    (attribute WORK_OFFLINE) — with a USB printer that is exactly what has to
    be checked on the real PC. With False the printer looks fine until a job fails.
    """

    def __init__(self, online=True, visible_offline=False):
        self.online = online
        self.visible_offline = visible_offline
        self.jobs = []             # dicts: JobId, pDocument, Status, ticks
        self.came_out = []         # documents that really reached paper
        self.deleted = []
        self._next = 1

    def add(self, document):
        self.jobs.append({"JobId": self._next, "pDocument": document, "Status": 0x8, "ticks": 0})
        self._next += 1

    def tick(self):
        """One look at the queue: an online printer takes the first job, then the next."""
        if not self.jobs:
            return
        job = self.jobs[0]
        if not self.online:
            job["Status"] = 0x12          # "Fehler - Drucken"
            return
        job["Status"] = 0x10
        job["ticks"] += 1
        if job["ticks"] >= 2:
            self.jobs.pop(0)
            self.came_out.append(job["pDocument"])

    def module(self):
        sp = self
        m = types.ModuleType("win32print")
        m.PRINTER_ENUM_LOCAL, m.PRINTER_ENUM_CONNECTIONS = 2, 4
        m.JOB_CONTROL_DELETE = 5
        m.OpenPrinter = lambda name: object()
        m.ClosePrinter = lambda h: None
        m.GetDefaultPrinter = lambda: "Canon SELPHY CP1300"
        m.EnumPrinters = lambda flags: [(0, "", "Canon SELPHY CP1300", "")]

        def get_printer(h, level):
            attrs = 0x400 if (sp.visible_offline and not sp.online) else 0
            return {"Status": 0, "Attributes": attrs, "cJobs": len(sp.jobs), "pDevMode": None}

        def enum_jobs(h, first, count, level):
            sp.tick()
            return tuple({k: v for k, v in j.items() if k != "ticks"} for j in sp.jobs)

        def set_job(h, job_id, level, info, command):
            assert command == 5
            sp.deleted.append(job_id)
            sp.jobs = [j for j in sp.jobs if j["JobId"] != job_id]

        m.GetPrinter, m.EnumJobs, m.SetJob = get_printer, enum_jobs, set_job
        return m


@pytest.fixture()
def spooler(monkeypatch):
    sp = FakeSpooler()
    monkeypatch.setitem(sys.modules, "win32print", sp.module())
    return sp


def _windows_printer(spooler, monkeypatch):
    printer = backends.WindowsPrinter("Canon SELPHY CP1300")
    # GDI is Windows only: drawing the page is replaced by handing a document
    # to the fake spooler, which is what StartDoc/EndDoc amount to.
    monkeypatch.setattr(printer, "_spoolen", lambda path, document: spooler.add(document), raising=True)
    printer.JOB_POLL = 0.01
    printer.ERROR_PATIENCE = 0.0
    printer.JOB_DEADLINE = 5.0
    return printer


def _sheet(tmp_path, name="a.jpg"):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (60, 40), "white").save(p)
    return p


def test_a_windows_print_returns_only_after_the_job_left_the_spooler(spooler, monkeypatch, tmp_path):
    printer = _windows_printer(spooler, monkeypatch)
    printer.print_file(_sheet(tmp_path))
    assert len(spooler.came_out) == 1 and not spooler.jobs


def test_a_windows_job_that_fails_is_taken_back_and_raises(spooler, monkeypatch, tmp_path):
    spooler.online = False
    printer = _windows_printer(spooler, monkeypatch)
    with pytest.raises(RuntimeError):
        printer.print_file(_sheet(tmp_path))
    # Taken out of the Windows queue: otherwise it would come out when the
    # printer is back AND again when the booth retries it.
    assert spooler.deleted and not spooler.jobs and not spooler.came_out


def test_a_windows_job_stuck_without_any_error_is_taken_back_at_the_deadline(spooler, monkeypatch, tmp_path):
    printer = _windows_printer(spooler, monkeypatch)
    printer.JOB_DEADLINE = 0.2
    monkeypatch.setattr(spooler, "tick", lambda: None)     # sits there, says nothing
    with pytest.raises(RuntimeError):
        printer.print_file(_sheet(tmp_path))
    assert spooler.deleted and not spooler.jobs


def test_a_job_in_error_makes_the_windows_printer_unavailable(spooler, monkeypatch):
    printer = _windows_printer(spooler, monkeypatch)
    spooler.online = False
    spooler.add("someone's print")
    assert printer.status()["state"] == "offline"          # before: "printing", because cJobs > 0


def test_windows_use_printer_offline_is_offline(spooler, monkeypatch):
    printer = _windows_printer(spooler, monkeypatch)
    spooler.online, spooler.visible_offline = False, True
    assert printer.status()["state"] == "offline"


# ── the print service with a printer that is down ───────────────────
class DownPrinter(PrinterBackend):
    """Says it is offline; counts every sheet that would have reached it."""
    name = "fake"

    def __init__(self):
        self.up = False
        self.printed = []

    def status(self):
        return {"state": "ready" if self.up else "offline", "name": "Fake",
                **({} if self.up else {"reasons": ["offline-report"], "hinweis": "Drucker offline"})}

    def print_file(self, path, copies=1):
        self.printed.append(Path(path).name)


def _wait(cond, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_a_printer_known_to_be_offline_gets_no_job_and_nothing_is_counted(tmp_path):
    printer = DownPrinter()
    svc = PrintService([printer], tmp_path / "count.txt",
                       options=[{"tray_total": 18, "ribbon_total": 36}])
    _wait(lambda: svc.stations[0].state() == "offline")
    svc.submit(PrintJob(_sheet(tmp_path), 1, "run1"))
    assert _wait(lambda: svc.outcome("run1").get("state") == "stuck")
    assert printer.printed == []
    assert svc.printed == 0
    st = svc.stations[0]
    assert st.fach_gedruckt == 0 and st.band_gedruckt == 0 and st.printed == 0
    assert svc.spool.counts()["done"] == 0


def test_when_the_printer_is_back_the_waiting_print_comes_out_once(tmp_path):
    printer = DownPrinter()
    svc = PrintService([printer], tmp_path / "count.txt")
    _wait(lambda: svc.stations[0].state() == "offline")
    svc.submit(PrintJob(_sheet(tmp_path), 1, "run1"))
    assert _wait(lambda: svc.outcome("run1").get("state") == "stuck")
    printer.up = True
    assert _wait(lambda: svc.outcome("run1").get("state") == "printed")
    time.sleep(0.3)
    assert printer.printed == ["a.jpg"] and svc.printed == 1


def test_windows_printer_switched_off_mid_evening_prints_once_when_it_is_back(spooler, monkeypatch, tmp_path):
    """The spooler copy is taken back, the booth keeps its own: one sheet, not two."""
    printer = _windows_printer(spooler, monkeypatch)
    spooler.online, spooler.visible_offline = False, True
    svc = PrintService([printer], tmp_path / "count.txt")
    svc.submit(PrintJob(_sheet(tmp_path), 1, "run1"))
    assert _wait(lambda: svc.outcome("run1").get("state") == "stuck")
    assert svc.printed == 0 and spooler.came_out == []
    spooler.online = True
    assert _wait(lambda: svc.outcome("run1").get("state") == "printed")
    time.sleep(0.3)
    assert len(spooler.came_out) == 1 and svc.printed == 1


def test_an_unplugged_usb_printer_that_looks_fine_ends_held_not_printed(spooler, monkeypatch, tmp_path):
    """Worst case: Windows shows nothing until a job fails (status 0, not marked offline).

    Every attempt reaches the spooler, fails and is taken back; after the
    attempts the print is held for the operator. Nothing is counted, and when
    the printer is back and the operator releases it, one sheet comes out.
    """
    printer = _windows_printer(spooler, monkeypatch)
    spooler.online, spooler.visible_offline = False, False
    svc = PrintService([printer], tmp_path / "count.txt",
                       options=[{"tray_total": 18, "ribbon_total": 36}])
    svc.submit(PrintJob(_sheet(tmp_path), 1, "run1"))
    assert _wait(lambda: svc.outcome("run1").get("state") == "stuck")
    assert _wait(lambda: svc.spool.counts()["held"] == 1, 30)
    assert svc.printed == 0 and spooler.came_out == [] and not spooler.jobs
    st = svc.stations[0]
    assert st.fach_gedruckt == 0 and st.band_gedruckt == 0
    assert "eingeschaltet" in svc.status()["held_reason"]
    spooler.online = True
    assert svc.release_held() == 1
    assert _wait(lambda: svc.outcome("run1").get("state") == "printed")
    time.sleep(0.3)
    assert len(spooler.came_out) == 1 and svc.printed == 1 and st.fach_gedruckt == 1


# ── CUPS and IPP: "offline-report" is offline ───────────────────────
def test_cups_offline_report_makes_the_printer_unavailable(monkeypatch):
    import subprocess

    class R:
        stdout = "printer Canon_SELPHY_CP1300 is idle.  enabled since Sat Nov 14 09:46:11 2026"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(backends.IppPrinter, "status",
                        lambda self: {"state": "ready", "reasons": ["offline-report"]})
    st = backends.CupsPrinter("Canon_SELPHY_CP1300").status()
    assert st["state"] == "offline" and "offline" in st["hinweis"]


def test_ipp_offline_report_makes_the_printer_unavailable(monkeypatch):
    printer = backends.IppPrinter("ipp://192.0.2.1/ipp/print")
    answer = (b"\x02\x00\x00\x00\x00\x00\x00\x01\x04"
              + backends.IppPrinter._attr(0x23, "printer-state", 3)
              + backends.IppPrinter._attr(0x44, "printer-state-reasons", "offline-report") + b"\x03")
    monkeypatch.setattr(printer, "_request", lambda *a, **k: answer)
    assert printer.status()["state"] == "offline"


def test_an_unreadable_windows_queue_is_not_taken_for_done(spooler, monkeypatch, tmp_path):
    """EnumJobs failing for a moment must not count a sheet that is still in the queue."""
    printer = _windows_printer(spooler, monkeypatch)
    win32print = sys.modules["win32print"]
    real, fails = win32print.EnumJobs, {"n": 3}

    def flaky(*a):
        if fails["n"]:
            fails["n"] -= 1
            raise OSError("RPC server unavailable (fake)")
        return real(*a)
    monkeypatch.setattr(win32print, "EnumJobs", flaky)
    printer.print_file(_sheet(tmp_path))
    assert spooler.came_out and not spooler.jobs


# ── the operator's switch: follow the Windows job or not ────────────────
def test_follow_jobs_off_returns_after_spooling_even_with_a_failing_job(spooler, monkeypatch, tmp_path):
    spooler.online = False                      # the job would sit in the queue with an error
    printer = _windows_printer(spooler, monkeypatch)
    printer.follow_jobs = False
    printer.print_file(_sheet(tmp_path), copies=2)          # must not raise, must not wait
    assert len(spooler.jobs) == 2               # both copies handed over, still in the queue
    assert not spooler.deleted and not spooler.came_out


def test_follow_jobs_off_never_looks_at_the_queue(spooler, monkeypatch, tmp_path):
    printer = backends.WindowsPrinter("Canon SELPHY CP1300", follow_jobs=False)
    spooled = []
    monkeypatch.setattr(printer, "_spoolen", lambda path, document: spooled.append(document))
    monkeypatch.setattr(printer, "_warte_auf_auftrag", lambda document: pytest.fail("queue was followed"))
    printer.print_file(_sheet(tmp_path), copies=2)
    assert len(spooled) == 2


def test_follow_jobs_is_on_by_default(spooler, monkeypatch, tmp_path):
    assert backends.WindowsPrinter("x").follow_jobs is True
    spooler.online = False
    printer = _windows_printer(spooler, monkeypatch)
    with pytest.raises(RuntimeError):
        printer.print_file(_sheet(tmp_path))
    assert spooler.deleted


def test_follow_jobs_config_default_is_true():
    from boothcore.config import DEFAULT_LOCAL
    assert DEFAULT_LOCAL["printer"]["follow_jobs"] is True


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), ("true", True), ("on", True), ("1", True),
    ("false", False), ("False", False), ("0", False), ("", False), ("off", False), (" Off ", False),
    (None, True),
])
def test_follow_jobs_setting_forms(spooler, value, expected):
    from boothcore.printing.service import create_printer
    cfg = {"backend": "windows", "name": "Canon SELPHY CP1300"}
    if value is not None:
        cfg["follow_jobs"] = value
    assert create_printer(cfg, Path("/tmp")).follow_jobs is expected


def test_follow_jobs_missing_key_is_on(spooler):
    from boothcore.printing.service import create_printer
    assert create_printer({"backend": "windows", "name": "x"}, Path("/tmp")).follow_jobs is True


# ── GDI without MFC: _spoolen on win32gui + win32print ──────────────────
# On the booth PC (15.11.2026) `import win32ui` failed with "DLL load failed"
# until the Visual C++ runtime (mfc140u.dll) was installed. win32ui is
# pywin32's MFC wrapper; the same GDI calls exist in win32gui (CreateDC,
# DeleteDC) and win32print (GetDeviceCaps, StartDoc/StartPage/EndPage/EndDoc,
# AbortDoc), which need nothing beyond Windows. The fakes below record the
# calls; whether the real modules behave the same is checked on the booth PC.
class FakeGdi:
    CAPS = {110: 1800, 111: 1200, 112: 30, 113: 20}   # PHYSICALWIDTH/HEIGHT/OFFSETX/OFFSETY (landscape card)

    def __init__(self, spooler, fail_draw=False):
        self.spooler = spooler
        self.calls = []
        self.drawn = []
        self.fail_draw = fail_draw

    def install(self, monkeypatch):
        gdi = self
        win32print = sys.modules["win32print"]
        win32gui = types.ModuleType("win32gui")

        def create_dc(driver, device, devmode):
            gdi.calls.append(("CreateDC", driver, device, devmode))
            return 4242
        win32gui.CreateDC = create_dc
        win32gui.DeleteDC = lambda hdc: gdi.calls.append(("DeleteDC", hdc))
        monkeypatch.setitem(sys.modules, "win32gui", win32gui)
        # win32ui (MFC) must not be needed: importing it fails, as it did there.
        monkeypatch.setitem(sys.modules, "win32ui", None)

        monkeypatch.setattr(win32print, "GetDeviceCaps", lambda hdc, i: gdi.CAPS[i], raising=False)

        def start_doc(hdc, info):
            gdi.calls.append(("StartDoc", hdc, info))
            gdi.spooler.add(info[0])
            return gdi.spooler.jobs[-1]["JobId"]
        monkeypatch.setattr(win32print, "StartDoc", start_doc, raising=False)
        for name in ("StartPage", "EndPage", "EndDoc"):
            monkeypatch.setattr(win32print, name, (lambda n: lambda hdc: gdi.calls.append((n, hdc)))(name),
                                raising=False)

        def abort_doc(hdc):
            gdi.calls.append(("AbortDoc", hdc))
            gdi.spooler.jobs = gdi.spooler.jobs[:-1]
        monkeypatch.setattr(win32print, "AbortDoc", abort_doc, raising=False)

        class Dib:
            def __init__(self, image):
                self.image = image

            def draw(self, handle, dst):
                if gdi.fail_draw:
                    raise OSError("draw failed (fake)")
                assert isinstance(handle, int)          # Dib.draw takes a plain HDC integer
                gdi.drawn.append((self.image.size, dst))
                gdi.calls.append(("draw", handle))
        from PIL import ImageWin
        monkeypatch.setattr(ImageWin, "Dib", Dib)


def test_spooling_uses_plain_gdi_without_win32ui(spooler, monkeypatch, tmp_path):
    gdi = FakeGdi(spooler)
    gdi.install(monkeypatch)
    printer = backends.WindowsPrinter("Canon SELPHY CP1300", follow_jobs=False)
    job_id = printer._spoolen(_sheet(tmp_path), "a.jpg #1")
    names = [c[0] for c in gdi.calls]
    assert names == ["CreateDC", "StartDoc", "StartPage", "draw", "EndPage", "EndDoc", "DeleteDC"]
    assert gdi.calls[0] == ("CreateDC", "WINSPOOL", "Canon SELPHY CP1300", None)
    assert gdi.calls[1] == ("StartDoc", 4242, ("a.jpg #1", None, None, 0))
    assert job_id == spooler.jobs[-1]["JobId"]


def test_the_borderless_geometry_is_unchanged(spooler, monkeypatch, tmp_path):
    gdi = FakeGdi(spooler)
    gdi.install(monkeypatch)
    printer = backends.WindowsPrinter("Canon SELPHY CP1300", follow_jobs=False)
    printer._spoolen(_sheet(tmp_path), "a.jpg #2")         # 60x40 landscape onto 1800x1200
    (size, dst), = gdi.drawn
    assert size == (60, 40)
    # cover-scaled to the physical page (factor 30), centred, minus the printable offset
    assert dst == (0 - 30, 0 - 20, 1800 - 30, 1200 - 20)


def test_a_portrait_picture_is_turned_onto_a_landscape_page(spooler, monkeypatch, tmp_path):
    from PIL import Image
    gdi = FakeGdi(spooler)
    gdi.install(monkeypatch)
    p = tmp_path / "hoch.jpg"
    Image.new("RGB", (40, 60), "white").save(p)
    backends.WindowsPrinter("x", follow_jobs=False)._spoolen(p, "hoch #1")
    assert gdi.drawn[0][0] == (60, 40)


def test_a_failed_draw_aborts_the_document_so_it_cannot_come_out_later(spooler, monkeypatch, tmp_path):
    gdi = FakeGdi(spooler, fail_draw=True)
    gdi.install(monkeypatch)
    with pytest.raises(OSError):
        backends.WindowsPrinter("x", follow_jobs=False)._spoolen(_sheet(tmp_path), "a #3")
    names = [c[0] for c in gdi.calls]
    assert "AbortDoc" in names and names[-1] == "DeleteDC" and not spooler.jobs


def test_the_job_is_followed_by_the_id_startdoc_returned(spooler, monkeypatch, tmp_path):
    """A driver that shows the document under another name must not make it read as gone."""
    gdi = FakeGdi(spooler)
    gdi.install(monkeypatch)
    real_add = spooler.add

    def add_renamed(document):
        real_add(document)
        spooler.jobs[-1]["pDocument"] = "Remote Downlevel Document"
    monkeypatch.setattr(spooler, "add", add_renamed)
    printer = backends.WindowsPrinter("Canon SELPHY CP1300")
    printer.JOB_POLL, printer.ERROR_PATIENCE, printer.JOB_DEADLINE = 0.01, 0.0, 5.0
    printer.print_file(_sheet(tmp_path))
    # returned only once the job really left the queue, not at the first look
    assert len(spooler.came_out) == 1 and not spooler.jobs


def test_a_queue_that_cannot_be_read_counts_the_job_as_handed_over_and_never_spools_it_twice(
        spooler, monkeypatch, tmp_path, caplog):
    """Seen on the booth PC: EnumJobs raised every time (win32timezone missing).

    Before: 240 s of looking, then a failure without deleting the job, a retry
    and the same picture spooled again. Now: after a few seconds the job counts
    as handed over, the booth says once that it cannot follow, nothing is retried.
    """
    import logging
    printer = _windows_printer(spooler, monkeypatch)
    printer.QUEUE_UNREADABLE_SECONDS = 0.05
    win32print = sys.modules["win32print"]

    def kaputt(*a):
        raise ImportError("No module named 'win32timezone'")
    monkeypatch.setattr(win32print, "EnumJobs", kaputt)
    monkeypatch.setattr(spooler, "tick", lambda: None)
    svc = PrintService([printer], tmp_path / "count.txt")
    with caplog.at_level(logging.INFO, logger="booth.print"):
        svc.submit(PrintJob(_sheet(tmp_path), 1, "run1"))
        assert _wait(lambda: svc.outcome("run1").get("state") == "printed", 10)
        svc.submit(PrintJob(_sheet(tmp_path, "b.jpg"), 1, "run2"))
        assert _wait(lambda: svc.outcome("run2").get("state") == "printed", 10)
    time.sleep(0.3)
    assert len(spooler.jobs) == 2              # one spooled copy per print, none twice
    assert not spooler.deleted and svc.printed == 2
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and "nicht lesbar" in r.getMessage()]
    assert len(warnings) == 1


def test_a_job_that_cannot_be_deleted_is_not_retried(spooler, monkeypatch, tmp_path):
    printer = _windows_printer(spooler, monkeypatch)
    spooler.online = False
    win32print = sys.modules["win32print"]

    def verweigert(*a):
        raise OSError("Zugriff verweigert (fake)")
    monkeypatch.setattr(win32print, "SetJob", verweigert)
    printer.print_file(_sheet(tmp_path))       # does not raise: a retry would be a second sheet
    assert len(spooler.jobs) == 1


def test_a_status_that_cannot_read_the_queue_says_so(spooler, monkeypatch):
    printer = _windows_printer(spooler, monkeypatch)
    assert printer.status()["queue_readable"] is True

    def kaputt(*a):
        raise ImportError("No module named 'win32timezone'")
    monkeypatch.setattr(sys.modules["win32print"], "EnumJobs", kaputt)
    st = printer.status()
    assert st["queue_readable"] is False


# ── the sequences seen on the booth PC, 15.11.2026 (SELPHY CP1300) ──────
# Job status bits as EnumJobs gave them there. With the Canon driver a good
# print sat at 0x2010 (RETAINED|PRINTING) for about 55 s and then left the
# queue. 0x2012 (…|ERROR) was a printer that could not be reached. 0x2016
# (…|DELETING) stayed in the queue for good because the port was stuck.
def _scripted(spooler, monkeypatch, statuses):
    """Our job walks through ``statuses``, one per look; ``None`` = it left the queue."""
    seq = list(statuses)

    def tick():
        if not spooler.jobs:
            return
        status = seq.pop(0) if len(seq) > 1 else seq[0]
        if status is None:
            spooler.came_out.append(spooler.jobs.pop(0)["pDocument"])
        else:
            spooler.jobs[0]["Status"] = status
    monkeypatch.setattr(spooler, "tick", tick)


@pytest.mark.parametrize("status,state", [
    (0x2010, "printing"),       # the good case with the Canon driver
    (0x2000, "printing"),       # RETAINED alone
    (0x2012, "offline"),
    (0x2016, "offline"),        # deleting, stuck at the port
])
def test_the_bits_seen_on_the_booth_pc(status, state):
    assert windows_printer_state(0, 0, [status])["state"] == state


def test_retained_alone_is_neither_failed_nor_done():
    assert windows_job_outcome(0x2000) == "waiting"


def test_a_job_retained_while_printing_for_a_long_time_then_gone_is_printed_once(spooler, monkeypatch, tmp_path):
    # ~55 s at 0x2010 on the PC; here 40 looks of 0.01 s
    _scripted(spooler, monkeypatch, [0x2008] + [0x2010] * 40 + [None])
    printer = _windows_printer(spooler, monkeypatch)
    svc = PrintService([printer], tmp_path / "count.txt")
    svc.submit(PrintJob(_sheet(tmp_path), 1, "run1"))
    assert _wait(lambda: svc.outcome("run1").get("state") == "printed")
    time.sleep(0.3)
    assert len(spooler.came_out) == 1 and svc.printed == 1 and not spooler.deleted


def test_a_job_that_turns_to_error_is_taken_back_and_not_counted(spooler, monkeypatch, tmp_path):
    _scripted(spooler, monkeypatch, [0x2010, 0x2010, 0x2012])
    printer = _windows_printer(spooler, monkeypatch)
    with pytest.raises(RuntimeError):
        printer.print_file(_sheet(tmp_path))
    assert spooler.deleted and not spooler.came_out


def test_a_job_stuck_while_deleting_reads_offline_and_starts_no_retry_storm(spooler, monkeypatch, tmp_path):
    """0x2016 stays in the queue for good: the printer is offline, our job is
    gone for printing, and no second copy is spooled while it sits there."""
    _scripted(spooler, monkeypatch, [0x2010, 0x2016])
    printer = _windows_printer(spooler, monkeypatch)
    set_job_calls = []
    real_set_job = sys.modules["win32print"].SetJob

    def set_job(h, job_id, level, info, command):
        set_job_calls.append(job_id)
        # Windows takes the command and the job stays, still deleting
    monkeypatch.setattr(sys.modules["win32print"], "SetJob", set_job)
    svc = PrintService([printer], tmp_path / "count.txt")
    svc.submit(PrintJob(_sheet(tmp_path), 1, "run1"))
    assert _wait(lambda: svc.outcome("run1").get("state") == "stuck")
    time.sleep(1.5)
    assert spooler._next == 2, "spooled once, not again while the deleting job blocks the port"
    assert svc.printed == 0 and not spooler.came_out
    assert set_job_calls == [], "a job Windows is already deleting is not deleted again"
    assert printer.status()["state"] == "offline"
    assert real_set_job
