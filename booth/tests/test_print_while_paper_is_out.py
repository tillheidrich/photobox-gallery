"""Prints asked for while the paper is out: queued (switch on) or refused (off).

``print.queue_while_empty`` (default off). On: a guest who presses "Drucken"
while the printer reports paper or ribbon out gets "Euer Druck ist
vorgemerkt …"; the job waits in the spool and prints once the printer is
ready again. At most ``queue_max`` waiting prints; one older than
``queue_max_hours`` is dropped (allowance back, log line). The operator can
throw away what waits (PIN). Off: the old 503 ``printer.error``.

The allowance: booked when the request is accepted. A held print gives it
back once; if it prints later after all it is booked again exactly once
(spool marker ``quota_returned``) — no free and no double-charged sheets.
"""
import logging
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from boothcore.printing.service import PrintService


class Selphy:
    """Paper out is reported (state "error") unless ``silent``; then only the job fails."""

    name = "test-selphy"

    def __init__(self):
        self.paper = True
        self.silent = False
        self.out = []

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
    d = Path(tempfile.mkdtemp(prefix="booth-vormerken-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    monkeypatch.setattr(PrintService, "HELD_RETRY_SECONDS", 0.5)
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    _wait(lambda: b.camera.state == "ready", 5)
    b.selphy = Selphy()
    b.printer.stations[0].backend = b.selphy
    b.store.profile["print"].update({"enabled": True, "auto": False, "limit_session": 2,
                                     "limit_event": 0, "copies_max": 1})
    yield b
    b.camera.stop()
    b.mail.stop()


def _photo(booth):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _paper(booth, there: bool):
    booth.selphy.paper = there
    booth.printer.stations[0]._auffrischen()      # what the 5-s poll would see


def _drive_retries(booth):
    """What the 5-s poll does after a refill, now and on this thread: look at
    the printer, put back what failed, release what is held. Then wait until
    the printer thread has worked off its queue — a condition, not a guess."""
    st = booth.printer.stations[0]
    st._auffrischen()
    booth.printer._retry_failed(st)
    booth.printer.release_held()
    assert _wait(lambda: st.q.empty() and not st.busy and not booth.printer.failed)


def _queue_on(booth, **extra):
    booth.store.profile["print"].update({"queue_while_empty": True, "queue_max": 12, "queue_max_hours": 6, **extra})


def test_off_by_default_the_guest_is_refused_as_before(booth):
    assert booth.profile["print"]["queue_while_empty"] is False
    s = _photo(booth)
    _paper(booth, False)
    with pytest.raises(HTTPException) as e:
        booth.print_uid(s.uid, 1)
    assert (e.value.status_code, e.value.detail) == (503, "printer.error")
    assert booth.library.prints(s.uid) == 0
    assert booth.print_allowance(s.uid)["can_queue"] is False


def test_on_the_print_is_accepted_booked_and_comes_out_after_the_refill(booth):
    _queue_on(booth)
    s = _photo(booth)
    _paper(booth, False)
    assert booth.print_allowance(s.uid)["can_queue"] is True
    r = booth.print_uid(s.uid, 1)
    assert r["deferred"] is True
    assert booth.library.prints(s.uid) == 1                  # booked on acceptance
    assert booth.printer.spool.deferred_open() == 1
    time.sleep(1.0)
    assert booth.selphy.out == []
    _paper(booth, True)                                      # a helper refills, presses nothing
    assert _wait(lambda: len(booth.selphy.out) == 1), "the queued print must come out by itself"
    time.sleep(1.5)
    assert len(booth.selphy.out) == 1                        # once, not twice
    assert booth.library.prints(s.uid) == 1
    assert booth.printer.spool.deferred_open() == 0


def test_beyond_the_maximum_the_old_refusal_says_why(booth):
    _queue_on(booth, queue_max=2)
    runs = [_photo(booth) for _ in range(3)]
    _paper(booth, False)
    booth.print_uid(runs[0].uid, 1)
    booth.print_uid(runs[1].uid, 1)
    with pytest.raises(HTTPException) as e:
        booth.print_uid(runs[2].uid, 1)
    assert (e.value.status_code, e.value.detail) == (503, "print.queue_full")
    assert booth.library.prints(runs[2].uid) == 0


def test_a_print_only_run_is_never_queued(booth, monkeypatch):
    _queue_on(booth)
    s = _photo(booth)
    monkeypatch.setattr(booth, "run_print_only", lambda x: True)
    _paper(booth, False)
    with pytest.raises(HTTPException) as e:
        booth.print_uid(s.uid, 1)
    assert e.value.detail == "printer.error"


def test_a_queued_print_older_than_the_limit_is_dropped_with_a_log_line(booth, caplog):
    _queue_on(booth, queue_max_hours=6)
    s = _photo(booth)
    _paper(booth, False)
    booth.print_uid(s.uid, 1)
    con = booth.printer.spool._con()
    with con:
        con.execute("UPDATE spool SET created=? WHERE deferred=1", (time.time() - 6 * 3600 - 60,))
    with caplog.at_level(logging.WARNING):
        assert booth.printer.drop_stale() == 1
    assert "ohne Papier verworfen" in caplog.text
    assert booth.library.prints(s.uid) == 0                  # the guest gets the sheet back
    _paper(booth, True)
    _drive_retries(booth)
    assert booth.selphy.out == []
    assert booth.library.prints(s.uid) == 0


def test_the_operator_can_throw_away_what_waits(booth):
    _queue_on(booth)
    a, b = _photo(booth), _photo(booth)
    _paper(booth, False)
    booth.print_uid(a.uid, 1)
    booth.print_uid(b.uid, 1)
    assert booth.printer.status()["deferred"] == 2
    # Whatever the printer thread is doing with them right now (one may sit
    # in "printing" for the moment it takes to find the tray still empty),
    # both are thrown away.
    assert booth.printer.discard_waiting() == 2
    assert booth.printer.discard_waiting() == 0              # a second press changes nothing
    assert booth.printer.status()["deferred"] == 0
    assert booth.library.prints(a.uid) == booth.library.prints(b.uid) == 0
    _paper(booth, True)
    _drive_retries(booth)
    assert booth.selphy.out == []
    assert booth.library.prints(a.uid) == booth.library.prints(b.uid) == 0   # given back once


def test_a_held_print_that_prints_later_is_booked_again_exactly_once(booth):
    """The printer says "ready" without paper: the job fails three times and is held."""
    s = _photo(booth)
    booth.selphy.silent = True
    _paper(booth, False)
    booth.print_uid(s.uid, 1)
    assert _wait(lambda: booth.printer.spool.counts()["held"] == 1)
    assert _wait(lambda: booth.library.prints(s.uid) == 0), "held: the allowance goes back"
    # Released and held again (still no paper): given back once, not twice.
    booth.printer.release_held()
    assert _wait(lambda: booth.printer.spool.counts()["held"] == 1 and booth.printer.spool.counts()["queued"] == 0)
    assert booth.library.prints(s.uid) == 0
    _paper(booth, True)
    assert _wait(lambda: len(booth.selphy.out) == 1)
    assert _wait(lambda: booth.library.prints(s.uid) == 1), "printed after all: booked again"
    time.sleep(1.5)
    assert booth.library.prints(s.uid) == 1


def test_the_spool_marker_counts_each_direction_once(tmp_path):
    from boothcore.printing.spool import Spool
    sp = Spool(tmp_path / "s.sqlite")
    i = sp.add(path="x.jpg", copies=1, session_uid="u")
    assert sp.take_quota_back(i) is False                     # nothing given back: nothing to book
    assert sp.mark_quota_returned(i) is True
    assert sp.mark_quota_returned(i) is False
    assert sp.take_quota_back(i) is True
    assert sp.take_quota_back(i) is False


def test_an_old_spool_file_gets_the_new_columns(tmp_path):
    import sqlite3
    from boothcore.printing import spool as spool_mod
    path = tmp_path / "alt.sqlite"
    con = sqlite3.connect(path)
    con.executescript(spool_mod.SCHEMA)
    con.execute("INSERT INTO spool(path, copies, session_uid, orientation, state, created, changed) "
                "VALUES ('a.jpg', 1, 'u', '', 'held', 1, 1)")
    con.commit()
    con.close()
    sp = spool_mod.Spool(path)
    assert sp.counts()["held"] == 1
    assert sp.deferred_open() == 0
    assert sp.mark_quota_returned(1) is True


def test_queued_prints_after_a_counted_empty_tray_restart_the_count(booth):
    st = booth.printer.stations[0]
    st.tray_total, st.ribbon_total = 3, 6
    st.fach_gedruckt = 3                                     # counted empty, nobody pressed a button
    _queue_on(booth)
    runs = [_photo(booth) for _ in range(2)]
    _paper(booth, False)
    for s in runs:
        booth.print_uid(s.uid, 1)
    _paper(booth, True)
    assert _wait(lambda: len(booth.selphy.out) == 2)
    assert st.sheets()["fach"]["rest"] == 1                  # new tray of 3, two through
    assert st.sheets()["farbband"]["rest"] == 4


def test_the_limits_hold_on_the_booth_and_in_the_defaults():
    from boothcore.app import PROFILE_LIMITS, clamp_profile_patch
    from boothcore.config import DEFAULT_PROFILE
    assert PROFILE_LIMITS["print.queue_max"] == (1, 50)
    assert PROFILE_LIMITS["print.queue_max_hours"] == (1, 24)
    patch = {"print": {"queue_max": 99, "queue_max_hours": 0}}
    clamp_profile_patch(patch, DEFAULT_PROFILE)
    assert patch["print"] == {"queue_max": 50, "queue_max_hours": 1}
    assert (DEFAULT_PROFILE["print"]["queue_while_empty"], DEFAULT_PROFILE["print"]["queue_max"],
            DEFAULT_PROFILE["print"]["queue_max_hours"]) == (False, 12, 6)


def test_the_guest_words_exist_in_both_languages():
    root = Path(__file__).resolve().parents[2]
    js = (root / "booth/boothcore/web/static/i18n.js").read_text(encoding="utf-8")
    py = (root / "app/booth_texts.py").read_text(encoding="utf-8")
    for key in ("print.deferred", "print.queue_full"):
        assert js.count(f'"{key}"') == 2, key
        assert py.count(f'"{key}"') == 2, key


# ── The race behind "discard missed one" (found 16.11.2026 under load) ─────

class Gated:
    """No paper; the printer thread's fresh look blocks until the test says so.

    That is the moment a job is "printing" in the spool while the printer
    turns out not to be ready — the window in which a discard used to miss it.
    """

    name = "gated"

    def __init__(self):
        import threading
        self.paper = False
        self.hold = True
        self.entered = threading.Event()
        self.go = threading.Event()
        self.out = []

    def status(self):
        import threading
        if self.hold and threading.current_thread().name == "printer1":
            self.entered.set()
            self.go.wait(10)
        return {"state": "ready"} if self.paper else {"state": "error", "reasons": ["media-empty"]}

    def print_file(self, path, copies=1):
        if not self.paper:
            raise RuntimeError("Kein Papier")
        self.out.append(Path(path).name)


def test_a_discard_while_the_printer_thread_holds_the_job_wins(tmp_path):
    from PIL import Image
    from boothcore.printing.service import PrintJob
    bild = tmp_path / "gast.jpg"
    Image.new("RGB", (60, 90)).save(bild)
    drucker = Gated()
    dienst = PrintService(drucker, tmp_path / "counter.txt")
    dropped = []
    dienst.on_dropped = dropped.append
    dienst.submit(PrintJob(path=bild, copies=1, session_uid="u1", deferred=True))
    assert drucker.entered.wait(10)
    assert dienst.spool.counts()["printing"] == 1           # the window is open
    assert dienst.discard_waiting() == 1
    drucker.go.set()                                         # the thread finds no paper, wants to requeue
    st = dienst.stations[0]
    assert _wait(lambda: st.q.empty() and not st.busy)
    assert dienst.spool.counts() == {"queued": 0, "printing": 0, "held": 0, "done": 0, "lost": 1}
    assert dienst.failed == []
    drucker.hold, drucker.paper = False, True                # refilled
    st._auffrischen()
    dienst._retry_failed(st)
    dienst.release_held()
    assert _wait(lambda: st.q.empty() and not st.busy)
    assert drucker.out == []
    assert len(dropped) == 1
    assert dienst.discard_waiting() == 0


def test_a_thrown_away_job_cannot_be_taken_requeued_or_held(tmp_path):
    from boothcore.printing.spool import Spool
    sp = Spool(tmp_path / "s.sqlite")
    i = sp.add(path="x.jpg", copies=1, session_uid="u")
    assert sp.started(i, 0) is True
    assert [r["id"] for r in sp.drop_waiting()] == [i]
    assert sp.requeue(i, "Kein Papier") is False
    assert sp.hold(i, "Kein Papier") is False
    assert sp.started(i, 0) is False
    assert sp.zustand(i) == "lost"
    j = sp.add(path="y.jpg", copies=1, session_uid="v")
    sp.drop_waiting()
    assert sp.started(j, 0) is False                         # thrown away before a printer took it
