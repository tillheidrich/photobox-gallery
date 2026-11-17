"""After a new pairing the booth offers its profile once, until the gallery answers."""
from pathlib import Path

from photobox_sync.agent import Agent
from photobox_sync.outbox import Outbox
from photobox_sync.targets import TemporaryError


class Gallery:
    name = "gallery"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.payloads = []

    def sync(self, payload):
        self.payloads.append(payload)
        answer = self.answers.pop(0) if self.answers else {"ok": True}
        if isinstance(answer, Exception):
            raise answer
        return answer


def _agent(data: Path, gallery: Gallery, state: dict) -> Agent:
    data.mkdir(parents=True, exist_ok=True)
    agent = Agent(outbox=Outbox(data / "outbox.sqlite"),
                  local_config=lambda: {"server": {"url": "https://g.example", "api_key": "k"}},
                  profile_seed=lambda: {"controls": {"mouse_start": "left"}} if state["pending"] else {},
                  profile_seed_answered=lambda a: state.update(pending=False, answer=a))
    agent.gallery = lambda: gallery
    return agent


def test_the_seed_goes_out_until_the_gallery_answers(tmp_path):
    state = {"pending": True}
    gallery = Gallery(TemporaryError("offline"), {"ok": True, "profile_seed": "taken"}, {"ok": True})
    agent = _agent(tmp_path, gallery, state)
    agent.sync_once()                                  # offline: still pending
    assert state["pending"] is True
    agent.sync_once()
    assert state == {"pending": False, "answer": "taken"}
    agent.sync_once()
    assert [("profile_seed" in p) for p in gallery.payloads] == [True, True, False]


def test_an_older_gallery_that_says_nothing_ends_the_offer(tmp_path):
    state = {"pending": True}
    agent = _agent(tmp_path, Gallery({"ok": True}), state)
    agent.sync_once()
    assert state == {"pending": False, "answer": "ignored"}
