"""The second target (FTP/FTPS/SFTP) gets every file of the event (17.11.2026).

The owner: "everything from the folder must reach the second target". The queue
side of it: counts per target for the heartbeat, a one-time "send what this
event already has" that copies exactly the files the other targets were
given (never another event's, never twice), the target's own choices
``originals`` (default on) and ``print_sheets`` (default off), and a print
sheet that does not overwrite the result under the event's naming rule.
"""
import json

from photobox_sync import redact
from photobox_sync.outbox import Outbox
from photobox_sync.targets import FtpsTarget, SftpTarget, file_name, skips_variant


def _add(box, tmp_path, target, uid, event="fest", variant="web", session=None, name="result_web.jpg"):
    f = tmp_path / f"{uid}-{variant}-{name}"
    f.write_bytes(b"x")
    box.add(target=target, uid=uid, session_uid=session or uid.split(".")[0], event_slug=event, kind="photo",
            variant=variant, path=f, meta={"taken_at": "2026-11-17T20:01:02"})
    return f


def test_counts_per_target_say_sent_waiting_and_failed(tmp_path):
    box = Outbox(tmp_path / "q.db")
    _add(box, tmp_path, "gallery", "a")
    _add(box, tmp_path, "gallery", "b")
    _add(box, tmp_path, "picdrop", "a")
    first = box.due()[0]["id"]
    box.mark_done(first)
    counts = box.counts_by_target()
    assert counts["gallery"] == {"done": 1, "pending": 1, "failed": 0}
    assert counts["picdrop"] == {"done": 0, "pending": 1, "failed": 0}


def test_backfill_copies_the_event_once_and_no_other_event(tmp_path):
    box = Outbox(tmp_path / "q.db")
    _add(box, tmp_path, "gallery", "a", variant="web")
    _add(box, tmp_path, "gallery", "a", variant="original", name="result.jpg")
    _add(box, tmp_path, "gallery", "a.boom", variant="web", name="boomerang.mp4")
    _add(box, tmp_path, "usb", "a", variant="web")                     # the same file through another target
    _add(box, tmp_path, "gallery", "x", event="anderes-fest")
    assert box.copy_event_to("picdrop", "fest") == 3
    rows = [r for r in box.due(100) if r["target"] == "picdrop"]
    assert sorted((r["uid"], r["variant"]) for r in rows) == [("a", "original"), ("a", "web"), ("a.boom", "web")]
    assert json.loads(rows[0]["meta"])["taken_at"].startswith("2026-11-17")
    # A second tap queues nothing new, and an already sent file is not sent again.
    box.mark_done(rows[0]["id"])
    assert box.copy_event_to("picdrop", "fest") == 0
    assert box.status_of(rows[0]["id"]) == "done"


def test_backfill_leaves_out_withheld_runs_and_missing_files(tmp_path):
    box = Outbox(tmp_path / "q.db")
    _add(box, tmp_path, "gallery", "keep")
    _add(box, tmp_path, "gallery", "gone")
    f = _add(box, tmp_path, "gallery", "nofile")
    f.unlink()
    assert box.copy_event_to("picdrop", "fest", keep_session=lambda uid: uid != "gone") == 1
    assert box.copy_event_to("picdrop", "") == 0


def test_the_targets_own_choices_skip_without_connecting():
    original = {"variant": "original", "uid": "a", "path": "/x/result.jpg"}
    sheet = {"variant": "print", "uid": "a.print", "path": "/x/print.jpg"}
    web = {"variant": "web", "uid": "a", "path": "/x/result_web.jpg"}
    assert not skips_variant({}, original)                          # default: full size goes
    assert skips_variant({}, sheet)                                 # default: print sheets stay
    assert skips_variant({"originals": False}, original)
    assert not skips_variant({"print_sheets": True}, sheet)
    assert not skips_variant({"originals": False}, web)
    # No connection is opened for a skipped file (the host does not exist).
    cfg = {"host": "ftp.example.org", "user": "u", "originals": False}
    assert FtpsTarget(cfg).upload(original) == {"skipped": True}
    assert SftpTarget(cfg).upload(original) == {"skipped": True}


def test_a_print_sheet_keeps_its_own_name_under_the_event_rule():
    meta = {"file_naming": "event_datetime", "event_name": "Sommerfest", "taken_at": "2026-11-17T20:01:02"}
    result = file_name({"uid": "abc", "variant": "original", "path": "/x/result.jpg", "meta": meta}, flat=True)
    sheet = file_name({"uid": "abc.print", "variant": "print", "path": "/x/print.jpg", "meta": meta}, flat=True)
    assert result == "Sommerfest_2026-11-17_20-01-02.jpg"
    assert sheet == "Sommerfest_2026-11-17_20-01-02-druck.jpg"


def test_the_sealed_password_field_is_redacted():
    line = 'job payload {"password_sealed": "QUJDREVGR0hJSktMTU5PUA==", "host": "ftp.example.org"}'
    out = redact.text(line)
    assert "QUJDREVGR0hJSktMTU5PUA" not in out and "ftp.example.org" in out
    assert "sehrgeheim" not in redact.text("private_key=sehrgeheim")
