"""The booth tells the gallery whether a picture carries a frame.

The gallery pairs a framed picture with the unframed capture of the same shot
(``<uid>`` / ``<uid>.f<i>`` with ``<uid>.<i>``) and offers "Mit Rahmen" and
"Ohne Rahmen". With no frame applied both are the same picture; the booth
says so in each upload's meta (``frame``), and the gallery then offers one
download (tests/test_pictures_without_frame.py).
"""
import json
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-frame-flag-"))
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


def _metas(booth, uid):
    return {e["uid"]: json.loads(e["meta"]) if isinstance(e["meta"], str) else e["meta"]
            for e in booth.outbox.due(limit=200) if e["session_uid"] == uid}


def _run(booth):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    return s.uid


def test_without_a_frame_the_uploads_say_so(booth):
    booth.store.profile["frame"] = {**booth.store.profile["frame"], "enabled": False}
    uid = _run(booth)
    metas = _metas(booth, uid)
    assert f"{uid}.1" in metas, "the unframed capture is queued (upload_originals)"
    assert metas[uid]["frame"] is False


def test_with_a_frame_text_the_uploads_say_framed(booth):
    booth.store.profile["frame"] = {**booth.store.profile["frame"], "enabled": True, "on_photo": True,
                                    "texts": [{"value": "Anna & Ben", "target": "both"}]}
    uid = _run(booth)
    assert _metas(booth, uid)[uid]["frame"] is True
