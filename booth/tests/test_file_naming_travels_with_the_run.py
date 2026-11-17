"""The event's file-name rule reaches the storage targets with every file.

The gallery sends the rule and the event's name with the event data
(profile ``event.file_naming``, ``event.name``). The booth copies both into
each queued file's meta, so a USB stick or an FTP server gets
"<Event>_<date>_<time>.jpg" with no connection to the gallery. A run
without the rule queues exactly what it queued before.
"""
import json
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-file-naming-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8}})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _metas(booth, uid):
    return {e["uid"]: json.loads(e["meta"]) if isinstance(e["meta"], str) else e["meta"]
            for e in booth.outbox.due(limit=200) if e["session_uid"] == uid}


def _run(booth):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    return s.uid


def test_the_rule_and_the_event_name_travel_in_the_meta(booth):
    booth.store.profile["event"] = {**booth.store.profile["event"], "name": "Hochzeit Müller",
                                    "file_naming": "event_datetime"}
    uid = _run(booth)
    meta = _metas(booth, uid)[uid]
    assert meta["file_naming"] == "event_datetime"
    assert meta["event_name"] == "Hochzeit Müller"
    assert meta["taken_at"]


def test_without_the_rule_nothing_new_is_written(booth):
    booth.store.profile["event"] = {**booth.store.profile["event"], "name": "Sommerfest"}
    booth.store.profile["event"].pop("file_naming", None)
    uid = _run(booth)
    meta = _metas(booth, uid)[uid]
    assert "file_naming" not in meta and "event_name" not in meta
