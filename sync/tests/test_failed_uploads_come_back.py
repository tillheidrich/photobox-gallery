"""Failed gallery uploads come back by themselves — at most hourly, capped per day.

A file on "failed" used to stay there until someone sent the remote job
``retry_failed``. A gallery that refused a file for a passing reason (a full
disk, a restart in the middle of an upload) then never got it, and nobody at
the event looks at the failed counter. Now, while the gallery answers, the
agent puts failed gallery files back into the queue once per hour; a file
that keeps failing gets a fixed number of attempts per day and then rests
until the next day, so a truly broken file cannot loop. Second targets
(FTP/SFTP, NAS, stick) keep their old behaviour.
"""
from __future__ import annotations

import logging
from pathlib import Path

from photobox_sync import outbox as outbox_mod
from photobox_sync.agent import AUTO_RETRY_EVERY, Agent
from photobox_sync.outbox import Outbox
from photobox_sync.targets import TemporaryError


class Gallery:
    name = "gallery"

    def __init__(self):
        self.down = False

    def sync(self, payload):
        if self.down:
            raise TemporaryError("kein Netz")
        return {"ok": True}


class Uhr:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def _agent(tmp: Path, uhr: Uhr, **local):
    ob = Outbox(tmp / "outbox.sqlite")
    ob.clock = uhr
    agent = Agent(outbox=ob, local_config=lambda: {"server": {"url": "https://g.example", "api_key": "k"}, **local})
    g = Gallery()
    agent.gallery = lambda: g
    agent.clock = uhr
    return agent, ob, g


def _failed(ob: Outbox, tmp: Path, target: str, uid: str) -> int:
    f = tmp / f"{uid}.jpg"
    f.write_bytes(b"x")
    ob.add(target=target, uid=uid, session_uid=uid, event_slug="e", kind="photo", variant="web", path=f)
    item = next(r for r in ob.due(100) if r["uid"] == uid)
    for _ in range(8):
        ob.mark_retry(item["id"], "abgelehnt", permanent=True)
    assert ob.status_of(item["id"]) == "failed"
    return item["id"]


def test_failed_gallery_files_return_when_the_gallery_answers(tmp_path, caplog):
    uhr = Uhr()
    agent, ob, g = _agent(tmp_path, uhr)
    item = _failed(ob, tmp_path, "gallery", "a1")
    with caplog.at_level(logging.INFO, logger="photobox.sync"):
        assert agent.sync_once()
    assert ob.status_of(item) == "pending"
    assert any("erneut" in r.getMessage() and "1" in r.getMessage() for r in caplog.records)


def test_at_most_once_per_hour(tmp_path):
    uhr = Uhr()
    agent, ob, g = _agent(tmp_path, uhr)
    agent.sync_once()                                   # the hour starts here
    item = _failed(ob, tmp_path, "gallery", "b1")
    uhr.t += AUTO_RETRY_EVERY - 60
    agent.sync_once()
    assert ob.status_of(item) == "failed", "within the hour: stays failed"
    uhr.t += 61
    agent.sync_once()
    assert ob.status_of(item) == "pending"


def test_nothing_happens_while_offline(tmp_path):
    uhr = Uhr()
    agent, ob, g = _agent(tmp_path, uhr)
    item = _failed(ob, tmp_path, "gallery", "c1")
    g.down = True
    assert not agent.sync_once()
    assert ob.status_of(item) == "failed"
    g.down = False
    assert agent.sync_once()
    assert ob.status_of(item) == "pending", "the connection is back: now"


def test_a_broken_file_gets_a_daily_cap_of_attempts(tmp_path):
    uhr = Uhr()
    agent, ob, g = _agent(tmp_path, uhr)
    item = _failed(ob, tmp_path, "gallery", "d1")       # 8 attempts today already
    versuche = 8
    for _ in range(10):                                 # ten hours of a file the gallery always refuses
        uhr.t += AUTO_RETRY_EVERY
        agent.sync_once()
        while ob.status_of(item) == "pending":
            ob.mark_retry(item, "abgelehnt", permanent=True)
            versuche += 1
    assert versuche == outbox_mod.MAX_ATTEMPTS_PER_DAY
    assert ob.status_of(item) == "failed"
    uhr.t += 86400                                      # next day: one more chance
    agent.sync_once()
    assert ob.status_of(item) == "pending"


def test_second_targets_are_left_alone(tmp_path):
    uhr = Uhr()
    agent, ob, g = _agent(tmp_path, uhr)
    ftp = _failed(ob, tmp_path, "nas-ftp", "e1")
    gal = _failed(ob, tmp_path, "gallery", "e2")
    agent.sync_once()
    assert ob.status_of(ftp) == "failed"
    assert ob.status_of(gal) == "pending"


def test_the_remote_job_still_retries_everything(tmp_path):
    uhr = Uhr()
    agent, ob, g = _agent(tmp_path, uhr)
    ftp = _failed(ob, tmp_path, "nas-ftp", "f1")
    assert agent.job_handlers["retry_failed"]({}) == 1
    assert ob.status_of(ftp) == "pending"


def test_it_can_be_switched_off(tmp_path):
    uhr = Uhr()
    agent, ob, g = _agent(tmp_path, uhr, retry_failed_auto=False)
    item = _failed(ob, tmp_path, "gallery", "g1")
    agent.sync_once()
    assert ob.status_of(item) == "failed"


def test_an_old_queue_gets_the_day_columns_and_keeps_its_rows(tmp_path):
    import sqlite3
    pfad = tmp_path / "alt.sqlite"
    con = sqlite3.connect(pfad)
    con.executescript("""CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL,
        uid TEXT NOT NULL, session_uid TEXT NOT NULL, event_slug TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
        variant TEXT NOT NULL, path TEXT NOT NULL, poster TEXT, meta TEXT NOT NULL DEFAULT '{}',
        bytes INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 5,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        next_try REAL NOT NULL DEFAULT 0, last_error TEXT, created REAL NOT NULL, done REAL,
        UNIQUE(target, uid, variant));
        INSERT INTO outbox(target, uid, session_uid, kind, variant, path, status, attempts, created)
        VALUES ('gallery', 'x', 'x', 'photo', 'web', '/x.jpg', 'failed', 8, 0);""")
    con.commit()
    con.close()
    ob = Outbox(pfad)
    assert ob.counts()["failed"] == 1
    assert ob.retry_failed_capped("gallery") == 1
    assert ob.counts()["pending"] == 1
