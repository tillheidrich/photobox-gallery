"""Live remote control: while the gallery says ``fast_seconds``, the agent polls
the small job door, runs what comes, delivers the answer at once, and stops
when the gallery answers 0. No long poll, one thread, results not lost.
"""
import time
from pathlib import Path

from photobox_sync.agent import Agent
from photobox_sync.outbox import Outbox
from photobox_sync.targets import TemporaryError


class Gallery:
    name = "gallery"

    def __init__(self, sync_answers, job_answers):
        self.sync_answers, self.job_answers = list(sync_answers), list(job_answers)
        self.job_payloads = []

    def sync(self, payload):
        return self.sync_answers.pop(0) if self.sync_answers else {"ok": True}

    def jobs(self, payload):
        self.job_payloads.append(payload)
        answer = self.job_answers.pop(0) if self.job_answers else {"ok": True, "jobs": [], "fast_seconds": 0}
        if isinstance(answer, Exception):
            raise answer
        return answer


def _agent(tmp: Path, gallery: Gallery, calls: list) -> Agent:
    agent = Agent(outbox=Outbox(tmp / "outbox.sqlite"),
                  local_config=lambda: {"server": {"url": "https://g.example", "api_key": "k"}})
    agent.gallery = lambda: gallery
    agent.job_handlers["remote_control"] = lambda p: calls.append(p["action"]) or '{"ok": 1}'
    agent.FAST_MIN = 0.01
    return agent


def _wait(cond, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_fast_polling_runs_the_job_reports_at_once_and_stops(tmp_path):
    calls = []
    job = {"id": 5, "type": "remote_control", "payload": {"action": "status"}}
    gallery = Gallery([{"ok": True, "fast_seconds": 0.05}],
                      [{"ok": True, "jobs": [job], "fast_seconds": 0.05},
                       TemporaryError("Wackelkontakt"),
                       {"ok": True, "jobs": [], "fast_seconds": 0}])
    agent = _agent(tmp_path, gallery, calls)
    agent.sync_once()
    assert _wait(lambda: agent._fast_thread is not None and not agent._fast_thread.is_alive(), 15)
    assert calls == ["status"]
    # The answer went out on the next round; the failed round did not lose it.
    sent = [p["job_results"] for p in gallery.job_payloads if p["job_results"]]
    assert sent[-1] == [{"id": 5, "ok": True, "result": '{"ok": 1}'}]
    assert agent.fast_seconds == 0


def test_no_fast_polling_without_a_session(tmp_path):
    gallery = Gallery([{"ok": True, "fast_seconds": 0}], [])
    agent = _agent(tmp_path, gallery, [])
    agent.sync_once()
    assert agent._fast_thread is None and gallery.job_payloads == []


def test_a_large_picture_answer_is_not_cut_to_4000_characters(tmp_path):
    big = "x" * 50_000
    gallery = Gallery([{"ok": True, "jobs": [{"id": 9, "type": "remote_control", "payload": {"action": "live_image"}}]}], [])
    agent = _agent(tmp_path, gallery, [])
    agent.job_handlers["remote_control"] = lambda p: big
    agent.sync_once()
    assert [a for a in agent._acks if a["id"] == 9][0]["result"] == big
