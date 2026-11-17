"""A job runs once, and its result survives a restart.

The gallery delivers a job again when no result came back within a minute
(the answer may have been lost, the booth may have restarted). That is only
safe if the agent remembers what it has executed: a known job id is answered
with the stored result, the handler does not run a second time. Results that
were not sent yet are kept on disk, so a restart right after ``install_update``
or ``restart_app`` still reports them.
"""
import ast
from pathlib import Path

from photobox_sync.agent import ALLOWED_JOBS, Agent
from photobox_sync.outbox import Outbox
from photobox_sync.targets import TemporaryError

ROOT = Path(__file__).resolve().parents[2]


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


def _agent(data: Path, gallery: Gallery, calls: list) -> Agent:
    data.mkdir(parents=True, exist_ok=True)
    agent = Agent(outbox=Outbox(data / "outbox.sqlite"),
                  local_config=lambda: {"server": {"url": "https://g.example", "api_key": "k"}})
    agent.gallery = lambda: gallery
    agent.job_handlers["test_print"] = lambda p: calls.append("print") or "printed"
    agent.job_handlers["install_update"] = lambda p: calls.append("install") or "installing"
    return agent


JOB = {"id": 7, "type": "test_print", "payload": {}}


def test_a_job_delivered_twice_runs_once(tmp_path):
    calls = []
    gallery = Gallery({"ok": True, "jobs": [JOB]}, {"ok": True, "jobs": [JOB]}, {"ok": True})
    agent = _agent(tmp_path, gallery, calls)
    agent.sync_once()
    agent.sync_once()
    agent.sync_once()
    assert calls == ["print"]
    assert gallery.payloads[1]["job_results"] == [{"id": 7, "ok": True, "result": "printed"}]
    assert gallery.payloads[2]["job_results"] == [{"id": 7, "ok": True, "result": "printed"}]


def test_a_restart_after_executing_still_reports_and_runs_nothing_twice(tmp_path):
    calls = []
    job = {"id": 9, "type": "install_update", "payload": {}}
    first = _agent(tmp_path, Gallery({"ok": True, "jobs": [job]}), calls)
    first.sync_once()                      # executed — and then the booth goes down
    assert calls == ["install"]

    gallery = Gallery({"ok": True, "jobs": [job]}, {"ok": True})
    second = _agent(tmp_path, gallery, calls)
    second.sync_once()                     # the result reaches the gallery after all
    assert gallery.payloads[0]["job_results"] == [{"id": 9, "ok": True, "result": "installing"}]
    second.sync_once()                     # and the re-delivered job is answered, not run
    assert calls == ["install"]
    assert gallery.payloads[1]["job_results"] == [{"id": 9, "ok": True, "result": "installing"}]


def test_unsent_results_survive_an_offline_restart(tmp_path):
    calls = []
    first = _agent(tmp_path, Gallery({"ok": True, "jobs": [JOB]}, TemporaryError("offline")), calls)
    first.sync_once()
    first.sync_once()                      # could not report
    gallery = Gallery({"ok": True})
    _agent(tmp_path, gallery, calls).sync_once()
    assert gallery.payloads[0]["job_results"] == [{"id": 7, "ok": True, "result": "printed"}]
    third = Gallery({"ok": True})
    _agent(tmp_path, third, calls).sync_once()
    assert third.payloads[0]["job_results"] == []          # reported once is enough


def test_a_job_interrupted_by_a_crash_is_not_repeated(tmp_path):
    calls = []
    agent = _agent(tmp_path, Gallery(), calls)

    def dies(p):
        calls.append("dies")
        raise SystemExit("power cut")      # the process would be gone here

    agent.job_handlers["test_print"] = dies
    try:
        agent._run_job(JOB)
    except SystemExit:
        pass
    gallery = Gallery({"ok": True, "jobs": [JOB]}, {"ok": True})
    again = _agent(tmp_path, gallery, calls)
    again.sync_once()
    again.sync_once()
    assert calls == ["dies"]
    result = gallery.payloads[1]["job_results"][0]
    assert result["id"] == 7 and result["ok"] is False


def test_the_memory_is_bounded(tmp_path):
    agent = _agent(tmp_path, Gallery(), [])
    agent.job_handlers["doctor"] = lambda p: "ok"
    for i in range(260):
        agent._run_job({"id": 1000 + i, "type": "doctor", "payload": {}})
    agent._acks = []
    agent.ledger.mark_sent(list(range(1000, 1260)))
    assert len(agent.ledger.entries) <= 200


def test_another_gallery_does_not_share_the_memory(tmp_path):
    """Job ids are numbers of one gallery; a booth moved to another one starts fresh."""
    calls = []
    agent = _agent(tmp_path, Gallery({"ok": True, "jobs": [JOB]}), calls)
    agent.sync_once()
    other = Agent(outbox=Outbox(tmp_path / "outbox.sqlite"),
                  local_config=lambda: {"server": {"url": "https://other.example", "api_key": "k"}})
    other.gallery = lambda: Gallery({"ok": True, "jobs": [JOB]})
    other.job_handlers["test_print"] = lambda p: calls.append("print") or "printed"
    other.sync_once()
    assert calls == ["print", "print"]


def _job_types_of_the_gallery():
    """Read the gallery's list as text: the suites do not share an import path in CI."""
    tree = ast.parse((ROOT / "app" / "ecosystem.py").read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (node.targets[0] if isinstance(node, ast.Assign) else None)
        if getattr(target, "id", "") == "JOB_TYPES":
            return set(ast.literal_eval(node.value).keys())
    raise AssertionError("JOB_TYPES not found in app/ecosystem.py")


def test_the_agent_and_the_gallery_know_the_same_jobs():
    assert ALLOWED_JOBS == _job_types_of_the_gallery()


def test_no_job_without_a_handler_in_the_list():
    """reload_profile and send_logs were no-ops nobody could issue; they are gone."""
    assert "reload_profile" not in ALLOWED_JOBS and "send_logs" not in ALLOWED_JOBS
