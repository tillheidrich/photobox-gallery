"""A guest's print allowance comes back exactly once, however a waiting print ends.

Closing list 43 / Paket A (c): with a print limit active, a print that waits
(held after failing, or accepted while the paper was out) can end three ways:
the operator throws it away, it gets too old (``queue_max_hours``), or it
prints late after all. The allowance must be given back once and booked again
once — never twice back (a free sheet) and never charged twice. The spool
marker ``quota_returned`` carries this; these tests walk the combinations the
older tests do not.
"""
import tempfile
import time
from pathlib import Path

import pytest

from boothcore.printing.service import PrintService


class Selphy:
    """``silent``: says "ready" without paper, so the job itself fails and is held."""

    name = "test-selphy"

    def __init__(self):
        self.paper, self.silent, self.out = True, False, []

    def status(self):
        if not self.paper and not self.silent:
            return {"state": "error", "reasons": ["media-empty"], "hinweis": "Papier leer"}
        return {"state": "ready"}

    def print_file(self, path, copies=1):
        if not self.paper:
            raise RuntimeError("Kein Papier")
        self.out.append(Path(path).name)


def _wait(cond, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-kontingent-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    monkeypatch.setattr(PrintService, "HELD_RETRY_SECONDS", 0.5)
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    _wait(lambda: b.camera.state == "ready", 5)
    b.selphy = Selphy()
    b.printer.stations[0].backend = b.selphy
    b.store.profile["print"].update({"enabled": True, "auto": False, "limit_session": 1,
                                     "limit_event": 0, "copies_max": 1})
    yield b
    b.camera.stop()
    b.mail.stop()


def _photo(booth):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _paper(booth, there: bool, silent: bool = False):
    booth.selphy.paper, booth.selphy.silent = there, silent
    booth.printer.stations[0]._auffrischen()


def _held(booth, s):
    assert _wait(lambda: booth.printer.spool.counts()["held"] == 1), "the print must end up held"
    assert _wait(lambda: booth.library.prints(s.uid) == 0), "held: the allowance goes back"


def test_a_held_print_thrown_away_gives_the_sheet_back_once_not_twice(booth):
    s = _photo(booth)
    _paper(booth, False, silent=True)
    booth.print_uid(s.uid, 1)
    _held(booth, s)
    assert booth.printer.discard_waiting() == 1
    assert booth.library.prints(s.uid) == 0                  # not -1: no free sheet
    assert booth.print_allowance(s.uid)["session_left"] == 1  # the guest may print once, as before


def test_a_deferred_print_that_was_held_and_then_expired_gives_back_once(booth):
    booth.store.profile["print"].update({"queue_while_empty": True, "queue_max": 12, "queue_max_hours": 6})
    s = _photo(booth)
    _paper(booth, False)                                     # paper out, reported: accepted and deferred
    assert booth.print_uid(s.uid, 1)["deferred"] is True
    assert booth.library.prints(s.uid) == 1
    _paper(booth, False, silent=True)                        # "ready" without paper: tries, fails, held
    _held(booth, s)
    con = booth.printer.spool._con()
    with con:
        con.execute("UPDATE spool SET created=? WHERE deferred=1", (time.time() - 6 * 3600 - 60,))
    assert booth.printer.drop_stale() == 1
    assert booth.library.prints(s.uid) == 0
    assert booth.printer.drop_stale() == 0                   # nothing left to drop, nothing given twice
    assert booth.library.prints(s.uid) == 0


def test_a_deferred_print_that_expires_without_being_tried_gives_back_once(booth):
    booth.store.profile["print"].update({"queue_while_empty": True, "queue_max": 12, "queue_max_hours": 1})
    s = _photo(booth)
    _paper(booth, False)
    booth.print_uid(s.uid, 1)
    con = booth.printer.spool._con()
    with con:
        con.execute("UPDATE spool SET created=? WHERE deferred=1", (time.time() - 3600 - 60,))
    assert booth.printer.drop_stale() == 1
    assert booth.library.prints(s.uid) == 0
    assert booth.printer.discard_waiting() == 0
    assert booth.library.prints(s.uid) == 0


def test_a_deferred_print_that_prints_late_is_charged_once(booth):
    booth.store.profile["print"].update({"queue_while_empty": True, "queue_max": 12, "queue_max_hours": 6})
    s = _photo(booth)
    _paper(booth, False)
    booth.print_uid(s.uid, 1)
    _paper(booth, False, silent=True)
    _held(booth, s)
    _paper(booth, True)
    booth.printer.release_held()
    assert _wait(lambda: len(booth.selphy.out) == 1)
    assert _wait(lambda: booth.library.prints(s.uid) == 1), "printed after all: booked again"
    assert booth.printer.discard_waiting() == 0              # nothing waits any more
    time.sleep(1.0)
    assert booth.library.prints(s.uid) == 1
    assert booth.print_allowance(s.uid)["session_left"] == 0


# ── Deleting a run while its print is "printing" (16.11.2026, from the race fix) ──

class GatedOut:
    """Says "ready" to everyone but the printer thread; that one is held at
    its fresh look and then finds the paper out — the job is "printing" in
    the spool while the printer is not ready."""

    name = "gated-out"

    def __init__(self):
        import threading
        self.paper, self.hold, self.out = False, True, []
        self.entered, self.go = threading.Event(), threading.Event()

    def status(self):
        import threading
        if threading.current_thread().name != "printer1":
            return {"state": "ready"}
        if self.hold:
            self.entered.set()
            self.go.wait(10)
        return {"state": "ready"} if self.paper else {"state": "error", "reasons": ["media-empty"]}

    def print_file(self, path, copies=1):
        if not self.paper:
            raise RuntimeError("Kein Papier")
        self.out.append(Path(path).name)


def test_a_run_deleted_while_its_print_is_in_hand_never_prints(tmp_path):
    from PIL import Image
    from boothcore.printing.service import PrintJob
    bild = tmp_path / "gast.jpg"
    Image.new("RGB", (60, 90)).save(bild)
    drucker = GatedOut()
    dienst = PrintService(drucker, tmp_path / "counter.txt")
    dropped = []
    dienst.on_dropped = dropped.append
    dienst.submit(PrintJob(path=bild, copies=1, session_uid="u1"))
    assert drucker.entered.wait(10)
    assert dienst.spool.counts()["printing"] == 1
    assert dienst.drop_session("u1") == 1                    # the "printing" job is taken too
    drucker.go.set()
    st = dienst.stations[0]
    assert _wait(lambda: st.q.empty() and not st.busy)
    drucker.hold, drucker.paper = False, True                 # refilled
    st._auffrischen()
    dienst._retry_failed(st)
    dienst.release_held()
    assert _wait(lambda: st.q.empty() and not st.busy)
    assert drucker.out == []
    assert len(dropped) == 1 and dropped[0]["session_uid"] == "u1"
    assert dienst.drop_session("u1") == 0


def test_deleting_a_run_gives_its_waiting_print_back_once(booth):
    drucker = GatedOut()
    booth.printer.stations[0].backend = drucker
    booth.store.profile["print"].update({"limit_event": 5})
    s = _photo(booth)
    booth.print_uid(s.uid, 1)
    assert drucker.entered.wait(10)
    assert booth.library.event_prints(booth.profile["event"].get("slug", "")) == 1
    booth.delete(s.uid)
    assert booth.library.prints(s.uid) == 0                   # the sheet that never came out is back
    drucker.go.set()
    st = booth.printer.stations[0]
    assert _wait(lambda: st.q.empty() and not st.busy)
    drucker.hold, drucker.paper = False, True
    st._auffrischen()
    booth.printer.release_held()
    time.sleep(1.0)
    assert drucker.out == []
    assert booth.library.prints(s.uid) == 0                   # not -1
    assert booth.print_allowance(None)["event_left"] == 5
