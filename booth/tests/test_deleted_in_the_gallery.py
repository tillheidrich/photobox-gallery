"""Deleted in the gallery means deleted at the booth — without a slip going back.

Two ways the booth learns about it: an upload of the run is answered with
``gone`` (package 1.1), or the sync response lists the run (package 1.2).
Either way the booth removes its own copy — files, kiosk tile, queued
uploads, queued prints — and does NOT write a deletion slip of its own,
otherwise the deletion would travel back and forth forever. Print counters
stay, as with every deletion at the booth.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-gone-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8}})
    b.store.profile["upload_originals"] = True
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _run(booth):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    return s


class GoneGallery:
    name = "gallery"

    def upload(self, item):
        return {"ok": True, "gone": True}


def test_an_upload_answered_with_gone_removes_the_run_at_the_booth(booth):
    s = _run(booth)
    booth.library.add_prints(s.uid, 1)
    prints_before = booth.library.event_prints(s.event_slug)
    items = [i for i in booth.outbox.due(200) if i["session_uid"] == s.uid and i["target"] == "gallery"]
    assert len(items) >= 2, "web version and original should be queued"
    folder = Path(booth.library.get(s.uid)["result"]).parent
    assert folder.exists()

    booth.agent._drain_target(GoneGallery(), items)

    assert booth.library.get(s.uid) is None                      # kiosk gallery
    assert all(i["session_uid"] != s.uid for i in booth.outbox.due(200))   # queue, every target
    assert booth.outbox.offen_fuer_session(s.uid)["offen"] == 0
    assert not folder.exists()                                   # files
    assert booth.library.pending_deletions() == []               # no slip back to the gallery
    assert booth.library.event_prints(s.event_slug) == prints_before


def test_queued_prints_of_a_run_deleted_in_the_gallery_do_not_go_out(booth, monkeypatch):
    s = _run(booth)
    dropped = []
    monkeypatch.setattr(booth.printer, "drop_session", lambda uid: dropped.append(uid) or 1)
    booth.deleted_in_gallery(s.uid)
    assert dropped == [s.uid]


def test_a_run_the_booth_never_had_is_ignored(booth):
    booth.deleted_in_gallery("unbekannt123")
    assert booth.library.pending_deletions() == []


def test_deleting_at_the_booth_still_writes_the_slip(booth):
    s = _run(booth)
    booth.delete(s.uid)
    assert booth.library.pending_deletions() == [s.uid]
