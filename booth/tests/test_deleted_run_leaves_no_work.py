"""A deleted run leaves nothing for the uploads to do.

Seen on the booth PC (15.11.2026): after runs were deleted at the booth the
sync agent kept trying to copy their files to the second storage target
("folder") and gave up with "Datei fehlt". Two ways led there:

* the agent reads up to 60 queue entries at a time; entries cleared by the
  deletion were still tried from that batch;
* a run deleted while it was being finished (a look chosen, then "Löschen")
  had its queue entries added after the deletion had cleared the queue.
"""
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import HTTPException


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    d = Path(tempfile.mkdtemp(prefix="booth-delwork-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"storage": {"folder": str(tmp_path / "zweit"), "layout": "flat", "originals": True}})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _finished(b, mode="collage"):
    s = b.start_session(mode, "4:5", None, None)
    for _ in range(s.shots_needed):
        b.take_shot(s)
    b.finalize(s, "original")
    return s


# The batch in flight is the sync agent's part: sync/tests/test_gone_at_the_gallery.py
# (test_entries_cleared_after_the_batch_was_read_are_not_tried).


def test_a_deletion_clears_every_target_of_the_run(booth):
    s = _finished(booth)
    assert booth.outbox.offen_fuer_session(s.uid)["offen"] > 0
    booth.delete(s.uid)
    assert booth.outbox.offen_fuer_session(s.uid) == {"offen": 0, "erledigt": 0}


def test_a_run_deleted_while_it_was_being_finished_gets_no_queue_entries(booth, monkeypatch):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    real_add = booth.outbox.add
    calls = {"n": 0}

    def add(**kw):
        real_add(**kw)
        calls["n"] += 1
        if calls["n"] == 1:
            booth.delete(s.uid)          # "Löschen" tapped while the entries are being added
    monkeypatch.setattr(booth.outbox, "add", add)
    booth.finalize(s, "original")
    assert calls["n"] >= 2
    assert booth.outbox.offen_fuer_session(s.uid) == {"offen": 0, "erledigt": 0}


def test_a_run_deleted_before_its_finish_ends_gets_no_tile_and_no_entries(booth, monkeypatch):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    real_render = booth.render

    def render(*a, **k):
        out = real_render(*a, **k)
        s.deleted = True                 # the deletion arrives during the render
        return out
    monkeypatch.setattr(booth, "render", render)
    with pytest.raises(HTTPException) as e:
        booth.finalize(s, "original")
    assert e.value.status_code == 410
    assert booth.outbox.offen_fuer_session(s.uid) == {"offen": 0, "erledigt": 0}
    assert booth.library.get(s.uid) is None
