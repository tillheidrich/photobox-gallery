"""The gallery says a run is gone: that is a delivery, not an error.

A run deleted in the gallery (guest, operator, retention) is refused there
with ``{"ok": true, "gone": true}``. The agent must take that as delivered —
no retry, no "failed" — drop the rest of that run from every queue, and tell
the booth so it can remove its own copy.
"""
from pathlib import Path

from photobox_sync.agent import Agent
from photobox_sync.outbox import Outbox


class GoneGallery:
    name = "gallery"

    def __init__(self, gone_sessions):
        self.gone = set(gone_sessions)
        self.sent = []

    def upload(self, item):
        self.sent.append(item["uid"])
        if item["session_uid"] in self.gone:
            return {"ok": True, "gone": True}
        return {"ok": True, "id": 1}


def _agent(tmp_path, gone_calls):
    ob = Outbox(tmp_path / "outbox.sqlite")
    return ob, Agent(outbox=ob, local_config=lambda: {}, session_gone=gone_calls.append)


def _add(ob, tmp_path, uid, session_uid, variant="web", target="gallery", priority=1):
    f = tmp_path / f"{uid}-{variant}.jpg"
    f.write_bytes(b"jpg")
    ob.add(target=target, uid=uid, session_uid=session_uid, event_slug="e", kind="photo",
           variant=variant, path=f, priority=priority)


def test_gone_counts_as_delivered_and_drops_the_rest_of_the_run(tmp_path):
    gone_calls = []
    ob, agent = _agent(tmp_path, gone_calls)
    _add(ob, tmp_path, "run1", "run1", "original", priority=6)
    _add(ob, tmp_path, "run1.1", "run1", "original", priority=8)
    _add(ob, tmp_path, "run1", "run1", "original", target="nas", priority=6)
    _add(ob, tmp_path, "keep1", "keep1", "web")
    target = GoneGallery({"run1"})

    agent._drain_target(target, [i for i in ob.due(limit=50) if i["target"] == "gallery"])

    assert gone_calls == ["run1"]
    # The raw shot was not even sent: the run was already known to be gone.
    assert target.sent.count("run1.1") == 0
    left = {(i["uid"], i["target"]) for i in ob.due(limit=50)}
    assert left == set()                       # keep1 delivered, run1 dropped everywhere
    assert ob.counts()["failed"] == 0
    assert ob.offen_fuer_session("run1")["offen"] == 0


def test_a_normal_answer_is_not_mistaken_for_gone(tmp_path):
    gone_calls = []
    ob, agent = _agent(tmp_path, gone_calls)
    _add(ob, tmp_path, "run2", "run2", "web")
    agent._drain_target(GoneGallery(set()), ob.due(limit=50))
    assert gone_calls == []
    assert ob.counts()["done"] == 1


# ── Package 1.2: the sync answer lists runs deleted in the gallery ──────────

class SyncingGallery:
    name = "gallery"

    def __init__(self, answers):
        self.answers = list(answers)
        self.payloads = []

    def sync(self, payload):
        self.payloads.append(payload)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _syncing_agent(tmp_path, gallery, gone_calls):
    ob = Outbox(tmp_path / "outbox.sqlite")
    agent = Agent(outbox=ob, local_config=lambda: {"server": {"url": "https://g", "api_key": "k"}},
                  session_gone=gone_calls.append)
    agent.gallery = lambda: gallery
    return ob, agent


def test_runs_deleted_in_the_gallery_are_removed_and_confirmed(tmp_path):
    from photobox_sync.targets import TemporaryError
    gone_calls = []
    gallery = SyncingGallery([{"ok": True, "deleted_sessions": ["del1", "bad/uid", "del2"]},
                              TemporaryError("offline"),
                              {"ok": True, "deleted_sessions": []},
                              {"ok": True}])
    ob, agent = _syncing_agent(tmp_path, gallery, gone_calls)
    _add(ob, tmp_path, "del1", "del1", "original", priority=6)

    assert agent.sync_once()
    assert gone_calls == ["del1", "del2"]                   # nonsense is not acted upon
    assert ob.offen_fuer_session("del1")["offen"] == 0
    assert not agent.sync_once()                            # lost: confirmation kept for later
    assert agent.sync_once()
    assert gallery.payloads[1]["deleted_seen"] == ["del1", "del2"]
    assert gallery.payloads[2]["deleted_seen"] == ["del1", "del2"]
    assert agent.sync_once()
    assert gallery.payloads[3].get("deleted_seen", []) == []   # confirmed once is enough


def test_an_old_gallery_without_the_list_changes_nothing(tmp_path):
    gone_calls = []
    gallery = SyncingGallery([{"ok": True}])
    ob, agent = _syncing_agent(tmp_path, gallery, gone_calls)
    assert agent.sync_once()
    assert gone_calls == []


# ── Deleted on the booth while a batch was in flight (booth PC, 15.11.2026) ──
class RecordingFolder:
    name = "folder"

    def __init__(self):
        self.tried = []

    def upload(self, item):
        self.tried.append(item["uid"])
        if not Path(item["path"]).exists():
            from photobox_sync.targets import PermanentError
            raise PermanentError(f"Datei fehlt: {item['path']}")
        return {"file": item["path"]}


def test_entries_cleared_after_the_batch_was_read_are_not_tried(tmp_path, caplog):
    """The agent reads up to 60 entries at once. A run deleted on the booth
    clears its entries (``cancel_session``) and its files; the batch in hand
    still held them, and every one was tried and failed with "Datei fehlt"."""
    import logging
    gone_calls = []
    ob, agent = _agent(tmp_path, gone_calls)
    _add(ob, tmp_path, "weg1", "weg1", "web", target="folder")
    _add(ob, tmp_path, "weg1", "weg1", "original", target="folder", priority=6)
    _add(ob, tmp_path, "bleibt", "bleibt", "web", target="folder")
    batch = ob.due(limit=60)
    ob.cancel_session("weg1")                              # Booth.delete
    for p in tmp_path.glob("weg1-*.jpg"):
        p.unlink()
    target = RecordingFolder()
    with caplog.at_level(logging.INFO):
        agent._drain_target(target, batch)
    assert target.tried == ["bleibt"]
    assert not [r for r in caplog.records if "Datei fehlt" in r.getMessage()]
    assert ob.counts() == {"pending": 0, "done": 1, "failed": 0}
