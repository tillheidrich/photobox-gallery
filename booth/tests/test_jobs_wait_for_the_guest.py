"""Second review R2-U2: restart, lock and test picture never land on a guest's run.

``send_booth_job`` could send ``restart_app``, ``lock`` and ``test_capture``
with the right ``manage``; the booth ran them at once, also in the middle of a
run. Live control already refused that ("nothing running"). Now the ordinary
jobs follow the same rule, through one helper.
"""
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-jobs-idle-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def _guest_is_at_it(b):
    b.sessions["run-1"] = SimpleNamespace(finalized=False, deleted=False, created=time.time())


@pytest.mark.parametrize("job", ["restart_app", "lock", "test_capture"])
def test_the_job_is_refused_while_a_guest_run_is_open(booth, job, monkeypatch):
    ran = []
    monkeypatch.setattr(booth, "restart_on_purpose", lambda: ran.append("restart") or "x")
    monkeypatch.setattr(booth, "test_capture", lambda: ran.append("capture") or "x")
    _guest_is_at_it(booth)
    booth.agent._run_job({"id": 77, "type": job, "payload": {}})
    ack = [a for a in booth.agent._acks if a["id"] == 77][0]
    assert ack["ok"] is False and "Durchgang" in ack["result"]
    assert ran == [] and booth.locked is False


def test_the_job_is_refused_while_a_print_is_on_its_way(booth, monkeypatch):
    monkeypatch.setattr(type(booth.printer), "pending", property(lambda self: 1), raising=False)
    booth.agent._run_job({"id": 78, "type": "lock", "payload": {}})
    ack = [a for a in booth.agent._acks if a["id"] == 78][0]
    assert ack["ok"] is False and "gedruckt" in ack["result"]


def test_with_nobody_at_the_booth_the_job_runs(booth):
    booth.agent._run_job({"id": 79, "type": "lock", "payload": {}})
    ack = [a for a in booth.agent._acks if a["id"] == 79][0]
    assert ack["ok"] is True
    assert booth.locked is True


def test_live_control_and_the_jobs_share_one_rule():
    from boothcore import fernsteuerung
    src = Path(fernsteuerung.__file__).read_text(encoding="utf-8")
    assert "nothing_running(self.b)" in src
    app_src = (Path(fernsteuerung.__file__).parent / "app.py").read_text(encoding="utf-8")
    for job in ("test_capture", "restart_app", "lock"):
        line = next(ln for ln in app_src.splitlines() if ln.strip().startswith(f'"{job}":'))
        assert "_when_idle" in line, job
