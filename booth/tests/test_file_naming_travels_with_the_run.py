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


# ── The way the profile really arrives (found on the booth PC, 18.11.2026) ──
# The tests above set the dict directly. The gallery's profile goes through
# Store.apply_profile, which keeps only the keys DEFAULT_PROFILE knows: without
# "file_naming" in DEFAULT_PROFILE["event"] the rule never reached the meta and
# the second target got "<uid>_shot1.jpg".

#: Keys the gallery puts into "event" that the booth does not read (the gallery
#: uses them itself or folds them into other profile keys).
BOOTH_IGNORES = {"moderation", "utm", "qr_query", "silent"}


def test_the_rule_survives_the_gallery_profile_and_lands_in_the_meta(booth):
    gallery_profile = {**booth.store.profile,
                       "event": {**booth.store.profile["event"], "slug": "hochzeit", "name": "Hochzeit Müller",
                                 "file_naming": "event_datetime", "moderation": False, "utm": {},
                                 "qr_query": ""}}
    booth.store.apply_profile(gallery_profile)
    assert booth.store.profile["event"]["file_naming"] == "event_datetime"
    uid = _run(booth)
    meta = _metas(booth, uid)[uid]
    assert meta["file_naming"] == "event_datetime" and meta["event_name"] == "Hochzeit Müller"


def test_every_event_key_the_gallery_sends_is_known_or_explicitly_ignored():
    import sys
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    import ast
    from boothcore import config
    from app import ecosystem
    src = Path(ecosystem.__file__).read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "profile_for_box")
    sent = set()
    for node in ast.walk(fn):
        # prof["event"] = {...} and prof["event"]["x"] = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                    if target.slice.value == "event" and isinstance(node.value, ast.Dict):
                        sent |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
                    inner = target.value
                    if (isinstance(inner, ast.Subscript) and isinstance(inner.slice, ast.Constant)
                            and inner.slice.value == "event"):
                        sent.add(target.slice.value)
    assert {"slug", "file_naming"} <= sent                          # the scan found the dicts
    known = set(config.DEFAULT_PROFILE["event"])
    assert sent - known - BOOTH_IGNORES == set()
    # The gallery has no "event" in its DEFAULT_PROFILE: it builds the block per box.
    assert "event" not in ecosystem.DEFAULT_PROFILE
