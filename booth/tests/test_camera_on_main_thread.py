"""macOS: the Canon SDK only works on the thread that owns the run loop.

Measured on a real EOS M50 on 2026-11-14 (macOS, EDSDK 13.21.10): opened from
a worker thread the SDK finds no camera, and a second EdsInitializeSDK there
never returns. On a Mac the camera loop therefore runs on the main thread and
the web server moves to a worker thread.
"""
import threading
import time

from boothcore.camera.mock import MockCamera
from boothcore.camera.service import CameraService


def test_by_default_the_camera_keeps_its_own_thread():
    service = CameraService(lambda: MockCamera())
    service.start()
    try:
        assert service._thread.is_alive()
    finally:
        service.stop()


def test_a_service_that_runs_in_the_caller_starts_no_thread():
    service = CameraService(lambda: MockCamera())
    service.run_in_caller = True
    service.start()
    assert not service._thread.is_alive()
    service.stop()                                    # must not raise without a thread


def test_run_forever_serves_frames_on_the_calling_thread_until_stopped():
    service = CameraService(lambda: MockCamera())
    service.run_in_caller = True
    service.start()
    seen = {}

    def kunde():
        frame, _ = service.latest_frame(after_seq=0, timeout=5.0)
        seen["frame"] = frame
        seen["thread"] = service.loop_thread
        service.stop()

    threading.Thread(target=kunde, daemon=True).start()
    start = time.monotonic()
    service.run_forever()                             # returns once stop() was called
    assert time.monotonic() - start < 10
    assert seen["frame"], "no live frame while the loop ran in the caller"
    assert seen["thread"] is threading.current_thread()


# ── A camera that never answers ────────────────────────────────────────

def test_a_camera_call_that_never_returns_is_reported_not_hidden():
    """Seen on the real M50 on 2026-11-14: after the booth had been killed in
    the middle of a session, EdsOpenSession of the next start never returned.
    The booth said "starting" for minutes — no message, no hint. A loop that
    has not come round for a while now reads as a camera that is not there,
    with the one sentence that helps: switch it off and on again.
    """
    import boothcore.camera.service as modul

    hangs = threading.Event()

    class Haengt(MockCamera):
        def open(self):
            hangs.wait(30)

    service = CameraService(lambda: Haengt())
    service.STALL_SECONDS = 0.3
    service.start()
    try:
        time.sleep(1.0)
        assert service.state == "reconnecting"
        assert "aus- und wieder ein" in (service.error or "")
    finally:
        hangs.set()
        service.stop()


def test_a_working_camera_is_never_called_stalled():
    service = CameraService(lambda: MockCamera())
    service.STALL_SECONDS = 0.3
    service.start()
    try:
        service.latest_frame(after_seq=0, timeout=5.0)
        time.sleep(0.8)
        assert service.state == "ready" and service.error is None
    finally:
        service.stop()


# ── Leaving without slamming the door ──────────────────────────────────

def test_stopping_hands_the_camera_back_before_the_program_ends():
    """Seen on the real M50 on 2026-11-14: a booth process that ended with the
    camera session open left the camera unusable for the next start — the SDK
    blocked or answered 0xC0 (bus error) until the camera was switched off and
    on. Every way out of the program now closes the session first.
    """
    closed = threading.Event()

    class Merkt(MockCamera):
        def close(self):
            closed.set()

    for in_caller in (False, True):
        closed.clear()
        service = CameraService(lambda: Merkt())
        service.run_in_caller = in_caller
        service.start()
        if in_caller:
            threading.Thread(target=service.run_forever, daemon=True).start()
        service.latest_frame(after_seq=0, timeout=5.0)
        service.stop()
        assert closed.is_set(), f"camera left open (run_in_caller={in_caller})"


def test_every_exit_of_the_program_goes_through_the_orderly_one():
    from pathlib import Path
    core = Path(__file__).resolve().parent.parent / "boothcore"
    for name in ("app.py", "updater.py", "__main__.py"):
        text = (core / name).read_text(encoding="utf-8")
        lines = [l for l in text.splitlines() if "os._exit(" in l and "def shutdown" not in l]
        allowed = [l for l in lines if "# orderly" in l]
        assert lines == allowed, f"{name}: os._exit outside Booth.shutdown: {lines}"
    main = (core / "__main__.py").read_text(encoding="utf-8")
    # Signals go through the watcher of lifecycle.py (see test_leaving_on_a_signal.py).
    life = (core / "lifecycle.py").read_text(encoding="utf-8")
    assert "lifecycle.watch_signals(" in main and '"SIGTERM"' in life
    # uvicorn takes the signals over while it runs; when it returns the
    # program still leaves through the orderly exit.
    assert main.rstrip().splitlines()[-4:] and "booth.shutdown(0)" in main.split("uvicorn.run(app, host=args.host")[-1]
