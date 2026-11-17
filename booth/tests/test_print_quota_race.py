"""The print allowance holds when two requests arrive at once.

A double tap, or the result screen and the kiosk gallery asking at the same
moment: both used to read "one print left", both submitted, and only then were
the prints booked — two sheets on a limit of one. Check, submit and booking
now happen in one step.
"""
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-quota-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    # Hand the job over slowly, as a real spooler can: that is the window in
    # which the second request used to slip through.
    real_submit = b.printer.submit

    def slow_submit(job):
        time.sleep(0.3)
        return real_submit(job)

    monkeypatch.setattr(b.printer, "submit", slow_submit)
    yield b
    b.camera.stop()
    b.mail.stop()


def _photo(booth):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _at_once(booth, uids):
    results, start = [], threading.Barrier(len(uids))

    def go(uid):
        start.wait()
        try:
            booth.print_uid(uid, 1)
            results.append("printed")
        except HTTPException as e:
            results.append(e.detail)

    threads = [threading.Thread(target=go, args=(u,)) for u in uids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return sorted(results)


def test_two_taps_on_the_last_print_of_a_run_print_once(booth):
    booth.store.profile["print"].update({"enabled": True, "auto": False, "limit_session": 1,
                                         "limit_event": 0, "copies_max": 1})
    s = _photo(booth)
    assert _at_once(booth, [s.uid, s.uid]) == ["print.limit", "printed"]
    assert booth.library.prints(s.uid) == 1


def test_two_runs_racing_for_the_last_print_of_the_event_print_once(booth):
    booth.store.profile["event"].update({"slug": "fest", "name": "Fest"})
    booth.store.profile["print"].update({"enabled": True, "auto": False, "limit_session": 0,
                                         "limit_event": 1, "copies_max": 1})
    a, b = _photo(booth), _photo(booth)
    assert _at_once(booth, [a.uid, b.uid]) == ["print.limit", "printed"]
    assert booth.library.event_prints("fest") == 1


def test_below_the_limit_both_go_through(booth):
    booth.store.profile["print"].update({"enabled": True, "auto": False, "limit_session": 2,
                                         "limit_event": 0, "copies_max": 1})
    s = _photo(booth)
    assert _at_once(booth, [s.uid, s.uid]) == ["printed", "printed"]
    assert booth.library.prints(s.uid) == 2
