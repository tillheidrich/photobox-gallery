"""Failed uploads one by one: listed with their reason, retried or given up."""
from pathlib import Path

import pytest

from photobox_sync.agent import Agent, job_ids
from photobox_sync.outbox import Outbox


def _outbox(tmp_path, n=3):
    box = Outbox(tmp_path / "outbox.sqlite")
    f = tmp_path / "bild.jpg"
    f.write_bytes(b"x")
    for i in range(n):
        box.add(target="gallery", uid=f"u{i}", session_uid=f"run{i}", event_slug="fest" if i else "",
                kind="photo", variant="", path=f)
    with box._con() as con:
        con.execute("UPDATE outbox SET status='failed', last_error='HTTP 413: Datei zu groß', attempts=5")
    return box


def test_the_list_names_file_target_reason_and_event(tmp_path):
    rows = _outbox(tmp_path).failed_list()
    assert [r["run"] for r in rows] == ["run0", "run1", "run2"]
    assert rows[0]["file"] == "bild.jpg" and rows[0]["error"].startswith("HTTP 413")
    assert rows[0]["event_slug"] == "" and rows[1]["event_slug"] == "fest"


def test_retry_one_and_discard_the_rest(tmp_path):
    box = _outbox(tmp_path)
    first = box.failed_list()[0]["id"]
    assert box.retry_ids([first]) == 1
    assert box.counts()["pending"] == 1 and box.counts()["failed"] == 2
    assert box.discard_failed(None) == 2
    c = box.counts()
    assert c["failed"] == 0 and c.get("discarded") == 2
    assert box.retry_ids(None) == 0, "a discarded upload is never picked up again"


def test_the_heartbeat_lists_them_and_all_needs_the_confirmation(tmp_path):
    box = _outbox(tmp_path)
    agent = Agent(outbox=box, local_config=lambda: {"server": {"url": "", "api_key": ""}})
    st = agent.outbox_status()
    assert st["queue_failed"] == 3 and len(st["failed_uploads"]) == 3
    # Plain words since 17.11.2026 (test_failed_upload_reasons.py), no raw error.
    assert st["failed_uploads"][0]["reason"] == "Galerie lehnt ab: 413 (Datei zu groß)"
    with pytest.raises(ValueError):
        agent.job_handlers["discard_failed_uploads"]({})
    assert agent.job_handlers["discard_failed_uploads"]({"ids": [st["failed_uploads"][0]["id"]]}) == "1 Uploads verworfen"
    assert agent.job_handlers["discard_failed_uploads"]({"confirm": "all"}) == "2 Uploads verworfen"
    assert "failed_uploads" not in agent.outbox_status()


@pytest.mark.parametrize("bad", [{"ids": ["1", "x"]}, {"ids": [True]}, {"ids": "1,2"}])
def test_ids_that_are_not_whole_numbers_are_refused(bad):
    with pytest.raises(ValueError):
        job_ids(bad)
