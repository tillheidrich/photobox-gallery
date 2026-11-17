"""Data-minimal mode cleans up after a guest who walks away.

A run that never reaches the result screen (two of four shots, then the guest
leaves) used to stay on disk for at least an hour, and was only cleared when
the next guest started a run. After the last guest of the evening: never. In
the mode that promises nothing stays on the device, that is the one case that
must not happen.

Now every run in data-minimal mode carries its own timer, measured from its
last activity, and a booth that starts up clears what a previous run in this
mode left behind.
"""
import tempfile
import time
from pathlib import Path

import pytest


def _wait_camera(b):
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)


@pytest.fixture()
def data_dir(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-unfinished-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    # The product never waits less than ten seconds; the test does not want to.
    monkeypatch.setattr(app_mod, "_IDLE_FLOOR_SECONDS", 0)
    return d


def _booth(data_dir, mode="print_only", seconds=2, start_threads=False):
    from boothcore.app import Booth
    b = Booth(base=data_dir, start_threads=start_threads)
    b.store.profile["privacy"].update({"mode": mode, "delete_seconds": seconds})
    b.store.profile["print"].update({"enabled": True, "auto": False})
    if start_threads:                     # the booth started its camera itself
        for _ in range(50):
            if b.camera.state == "ready":
                break
            time.sleep(0.1)
    else:
        _wait_camera(b)
    return b


def _stop(b):
    b.camera.stop()
    b.mail.stop()


def _two_of_four(b):
    s = b.start_session("gif", "4:5", None, None)
    assert s.shots_needed >= 3
    b.take_shot(s)
    b.take_shot(s)
    return s


def test_a_guest_who_walks_away_leaves_nothing_behind(data_dir):
    b = _booth(data_dir)
    b.logbuf.take(500)
    try:
        s = _two_of_four(b)
        assert s.folder.exists() and len(s.shots) == 2
        deadline = time.time() + 6
        while s.folder.exists() and time.time() < deadline:
            time.sleep(0.1)
        assert not s.folder.exists(), "nobody came back, so the shots must go"
        proof = [e for e in b.logbuf.peek(50) if s.uid in e["message"]]
        assert proof and proof[0]["code"] == "privacy", "logged like every other deletion"
        with pytest.raises(Exception):
            b.get_session(s.uid)
    finally:
        _stop(b)


def test_the_period_counts_from_the_last_activity(data_dir):
    b = _booth(data_dir, seconds=2)
    try:
        s = b.start_session("gif", "4:5", None, None)
        b.take_shot(s)
        time.sleep(1.2)
        b.take_shot(s)                    # still here: the clock starts again
        time.sleep(1.3)                   # 2.5 s after the start, 1.3 s after the last shot
        assert s.folder.exists()
        deadline = time.time() + 4
        while s.folder.exists() and time.time() < deadline:
            time.sleep(0.1)
        assert not s.folder.exists()
    finally:
        _stop(b)


def test_a_finished_run_keeps_the_rule_it_already_had(data_dir):
    """Finished runs go after printing (or the wipe timer) as before — not twice, not earlier."""
    b = _booth(data_dir, seconds=2)
    try:
        s = b.start_session("photo", "4:5", None, None)
        b.take_shot(s)
        b.finalize(s, "original")
        assert s.uid not in b._idle_timers
        assert s.uid in b._wipe_timers
    finally:
        _stop(b)


def test_outside_data_minimal_mode_nothing_changes(data_dir):
    b = _booth(data_dir, mode="normal", seconds=1)
    try:
        s = _two_of_four(b)
        time.sleep(2.0)
        assert s.folder.exists(), "a normal booth keeps an unfinished run as before"
        assert not b._idle_timers
    finally:
        _stop(b)


def test_leftovers_of_a_previous_run_are_gone_after_start(data_dir):
    first = _booth(data_dir, seconds=600)
    s = _two_of_four(first)
    # Power cut: the process is gone, its timers with it.
    for timer in list(first._idle_timers.values()):
        timer.cancel()
    _stop(first)
    assert s.folder.exists()

    again = _booth(data_dir, seconds=600, start_threads=True)
    try:
        assert not s.folder.exists(), "the start clears what the last run left"
    finally:
        again.agent.stop()
        _stop(again)


def test_the_start_leaves_a_normal_booths_runs_alone(data_dir):
    first = _booth(data_dir, mode="normal")
    s = _two_of_four(first)
    _stop(first)
    again = _booth(data_dir, mode="print_only", start_threads=True)
    try:
        assert s.folder.exists(), "only what was shot under the data-minimal promise goes"
    finally:
        again.agent.stop()
        _stop(again)


def test_a_diagnosis_run_beside_the_booth_deletes_nothing(data_dir):
    """``--doctor`` opens the same data folder while the booth may be running."""
    first = _booth(data_dir, seconds=600)
    s = _two_of_four(first)
    probe = _booth(data_dir, seconds=600, start_threads=False)
    try:
        assert s.folder.exists()
    finally:
        for timer in list(first._idle_timers.values()):
            timer.cancel()
        _stop(probe)
        _stop(first)
