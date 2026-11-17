"""Mail attachments follow the event's file-name rule (direct SMTP of the box).

Rule ``original`` (the default): the attachment keeps the file's own name,
as it always did. Rule ``event_datetime``: "<Event>_<date>_<time>.<ext>",
built by the same function the storage targets use
(``photobox_sync.targets.event_datetime_name``); several attachments of the
same second get ``_2``, ``_3``. The name sits in the MIME header the RFC 2231
way when it is not plain ASCII. No mail leaves the machine: dry run only.
"""
from __future__ import annotations

import datetime as dt
import email
import email.policy
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boothcore.delivery.mail import MailSender, attachment_names

CFG = {"enabled": True, "host": "smtp.example.invalid", "port": 587, "user": "booth@example.de",
       "password": "x", "security": "starttls", "from_addr": "booth@example.de", "from_name": "Fotobox",
       "subject": "Deine Fotos", "body": "Viel Spaß!", "attach": "web", "bcc": "", "dry_run": True,
       "transport": "smtp"}
WHEN = dt.datetime(2026, 7, 20, 21, 14, 3)


def _files(tmp_path: Path, *names: str):
    out = []
    for n in names:
        f = tmp_path / n
        f.write_bytes(b"\xff\xd8\xff" + b"0" * 512)
        out.append(f)
    return out


def test_original_keeps_todays_names():
    assert attachment_names([Path("/x/abc123_result_web.jpg")], file_naming="original",
                            event_name="Hochzeit", when=WHEN) is None
    assert attachment_names([Path("/x/a.jpg")], file_naming="", event_name="Hochzeit", when=WHEN) is None


def test_event_datetime_numbers_attachments_of_the_same_second():
    names = attachment_names([Path("/x/a.jpg"), Path("/x/b.jpg"), Path("/x/c.PNG")],
                             file_naming="event_datetime", event_name="Hochzeit Müller", when=WHEN)
    assert names == ["Hochzeit-Mueller_2026-07-20_21-14-03.jpg",
                     "Hochzeit-Mueller_2026-07-20_21-14-03_2.jpg",
                     "Hochzeit-Mueller_2026-07-20_21-14-03_3.png"]


def test_the_queue_attaches_under_the_given_names(tmp_path):
    sender = MailSender(tmp_path / "mail.sqlite", lambda: dict(CFG), start_worker=False)
    files = _files(tmp_path, "abc_result_web.jpg", "abc_single.jpg")
    names = attachment_names(files, file_naming="event_datetime", event_name="Sommerfest", when=WHEN)
    item = sender.enqueue(address="gast@example.de", files=files, subject="s", body="b", names=names)
    message, attached, skipped = sender._build(sender.get(item), dict(CFG))
    assert attached == names and not skipped
    assert [p.get_filename() for p in message.iter_attachments()] == names
    assert sender.send_now(item) is True
    assert sender.sent_messages[-1]["attachments"] == names


def test_without_names_the_queue_attaches_as_before(tmp_path):
    sender = MailSender(tmp_path / "mail.sqlite", lambda: dict(CFG), start_worker=False)
    files = _files(tmp_path, "abc_result_web.jpg")
    item = sender.enqueue(address="gast@example.de", files=files, subject="s", body="b")
    _, attached, _ = sender._build(sender.get(item), dict(CFG))
    assert attached == ["abc_result_web.jpg"]


def test_a_non_ascii_name_is_encoded_the_rfc_2231_way(tmp_path):
    sender = MailSender(tmp_path / "mail.sqlite", lambda: dict(CFG), start_worker=False)
    files = _files(tmp_path, "a.jpg")
    item = sender.enqueue(address="gast@example.de", files=files, subject="s", body="b",
                          names=["Grüße_2026.jpg"])
    message, _, _ = sender._build(sender.get(item), dict(CFG))
    raw = message.as_bytes()
    assert b"filename*=utf-8''Gr%C3%BC%C3%9Fe_2026.jpg" in raw
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    assert [p.get_filename() for p in parsed.iter_attachments()] == ["Grüße_2026.jpg"]


def test_an_old_queue_without_the_names_column_still_works(tmp_path):
    import sqlite3
    db = tmp_path / "mail.sqlite"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE mail_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT NOT NULL,
                   files TEXT NOT NULL DEFAULT '[]', subject TEXT NOT NULL DEFAULT '',
                   body TEXT NOT NULL DEFAULT '', session_uid TEXT NOT NULL DEFAULT '',
                   status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                   next_try REAL NOT NULL DEFAULT 0, last_error TEXT, created_at REAL NOT NULL, sent_at REAL)""")
    con.execute("INSERT INTO mail_queue(address, files, created_at) VALUES ('gast@example.de', '[]', 1)")
    con.commit()
    con.close()
    sender = MailSender(db, lambda: dict(CFG), start_worker=False)
    assert sender.send_now(1) is True                     # the old row, no names
    item = sender.enqueue(address="gast@example.de", files=_files(tmp_path, "a.jpg"), subject="s",
                          body="b", names=["x.jpg"])
    assert sender.get(item)["names"] == '["x.jpg"]'
    with pytest.raises(ValueError):
        sender.enqueue(address="gast@example.de", files=[], subject="s", body="b", names=["x.jpg"])


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-mail-names-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _mail_a_run(booth, event):
    from boothcore.app import create_app
    booth.store.profile["event"] = {**booth.store.profile["event"], **event}
    booth.store.profile["mail"] = {**(booth.store.profile.get("mail") or {}), **CFG}
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    client = TestClient(create_app(booth))
    r = client.post(f"/api/session/{s.uid}/mail", json={"address": "gast@example.de"})
    assert r.status_code == 200, r.text
    assert booth.mail.send_now(int(r.json()["id"])) is True
    return s, booth.mail.sent_messages[-1]["attachments"]


def test_the_booth_names_the_attachment_by_the_events_rule(booth):
    s, attached = _mail_a_run(booth, {"name": "Hochzeit Müller", "file_naming": "event_datetime"})
    when = dt.datetime.fromtimestamp(s.created).strftime("%Y-%m-%d_%H-%M-%S")
    assert len(attached) == 1
    assert attached[0] == f"Hochzeit-Mueller_{when}.jpg"


def test_the_booth_keeps_the_files_own_name_by_default(booth):
    booth.store.profile["event"].pop("file_naming", None)
    s, attached = _mail_a_run(booth, {"name": "Sommerfest"})
    assert len(attached) == 1
    assert attached[0] == Path(str(s.result_web or s.result)).name
