"""Guest e-mail delivery: validation, the offline queue, backoff and dry-run."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from boothcore.delivery.mail import (
    BACKOFF_SECONDS,
    HOURLY_BACKOFF,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTEMPTS,
    MailSender,
    backoff_for,
    valid_address,
)


class FakeClock:
    """A clock the test moves by hand, so no retry ever waits a real second."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


BASE_CONFIG = {
    "enabled": True,
    "host": "smtp.example.de",
    "port": 587,
    "user": "booth@example.de",
    "password": "secret",
    "security": "starttls",
    "from_addr": "booth@example.de",
    "from_name": "Fotobox",
    "subject": "Your photo",
    "body": "Have fun!",
    "attach": "web",
    "bcc": "",
}


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "mail.sqlite"


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def config() -> dict:
    return dict(BASE_CONFIG)


@pytest.fixture()
def photo(tmp_path: Path) -> Path:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff" + b"0" * 2048)
    return path


def make_sender(db_path: Path, config: dict, clock: FakeClock) -> MailSender:
    # No worker thread: the tests drive every attempt themselves.
    return MailSender(db_path, lambda: config, start_worker=False, clock=clock)


# -- address validation ---------------------------------------------------


@pytest.mark.parametrize("address", [
    "guest@example.de",
    "first.last+tag@sub.example.co.uk",
    "a_b-c@example-hosting.com",
])
def test_valid_addresses_are_accepted(db_path, config, clock, address):
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address=address, files=[], subject="s", body="b")
    assert sender.get(item_id)["address"] == address


@pytest.mark.parametrize("address", [
    "", "   ", "guest", "guest@", "@example.de", "guest@example",
    "guest example@example.de", "guest@@example.de", "guest..name@example.de",
    "guest@example..de", "guest@-example.de", "guest@example.de.",
    "a" * 250 + "@example.de",
])
def test_nonsense_addresses_are_rejected_immediately(db_path, config, clock, address):
    sender = make_sender(db_path, config, clock)
    with pytest.raises(ValueError):
        sender.enqueue(address=address, files=[], subject="s", body="b")
    assert sender.status()["pending"] == 0


def test_address_is_trimmed_and_domain_lowercased(db_path, config, clock):
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="  Guest@Example.DE ", files=[], subject="s", body="b")
    assert sender.get(item_id)["address"] == "Guest@example.de"


def test_valid_address_helper():
    assert valid_address("guest@example.de")
    assert not valid_address("guest@example")


# -- the queue survives a restart -----------------------------------------


def test_pending_mail_survives_a_new_sender_on_the_same_db(db_path, config, clock, photo):
    first = make_sender(db_path, config, clock)
    item_id = first.enqueue(address="guest@example.de", files=[photo], subject="Hi", body="Link",
                            session_uid="abc123")
    assert first.status() == {"pending": 1, "sent": 0, "failed": 0, "last_error": None}
    first.stop()

    second = make_sender(db_path, config, clock)
    assert second.status()["pending"] == 1
    item = second.get(item_id)
    assert item["status"] == "pending"
    assert item["address"] == "guest@example.de"
    assert item["session_uid"] == "abc123"
    assert json.loads(item["files"]) == [str(photo)]


# -- dry run ---------------------------------------------------------------


def test_dry_run_marks_sent_and_records_the_message(db_path, config, clock, photo):
    config["dry_run"] = True
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[photo],
                             subject="Your photo from the booth", body="Here it is")

    assert sender.send_now(item_id) is True

    record = sender.sent_messages[-1]
    assert record["to"] == "guest@example.de"
    assert record["subject"] == "Your photo from the booth"
    assert record["attachments"] == ["photo.jpg"]
    assert record["attachments_skipped"] is False
    assert "booth@example.de" in record["from"]

    item = sender.get(item_id)
    assert item["status"] == "sent"
    assert item["sent_at"] == clock.now
    assert sender.status() == {"pending": 0, "sent": 1, "failed": 0, "last_error": None}


def test_dry_run_opens_no_connection(db_path, config, clock, photo, monkeypatch):
    config["dry_run"] = True
    sender = make_sender(db_path, config, clock)

    def explode(*args, **kwargs):
        raise AssertionError("dry run must not talk to an SMTP server")

    monkeypatch.setattr("smtplib.SMTP", explode)
    monkeypatch.setattr("smtplib.SMTP_SSL", explode)

    item_id = sender.enqueue(address="guest@example.de", files=[photo], subject="s", body="b")
    assert sender.send_now(item_id) is True


def test_dry_run_falls_back_to_configured_subject_and_body(db_path, config, clock):
    config["dry_run"] = True
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[], subject="", body="")
    assert sender.send_now(item_id) is True
    record = sender.sent_messages[-1]
    assert record["subject"] == "Your photo"
    assert record["body"] == "Have fun!"


# -- attachments -----------------------------------------------------------


def test_oversized_attachments_are_skipped(db_path, config, clock, tmp_path):
    config["dry_run"] = True
    big = tmp_path / "clip.mp4"
    with big.open("wb") as handle:                    # sparse, so the test stays fast
        handle.truncate(MAX_ATTACHMENT_BYTES + 1)
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[big], subject="s",
                             body="https://gallery.example.de/p/abc")

    assert sender.send_now(item_id) is True
    record = sender.sent_messages[-1]
    assert record["attachments"] == []
    assert record["attachments_skipped"] is True
    assert "gallery.example.de" in record["body"]


def test_attach_link_mode_sends_without_files(db_path, config, clock, photo):
    config["dry_run"] = True
    config["attach"] = "link"
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[photo], subject="s", body="link")
    assert sender.send_now(item_id) is True
    assert sender.sent_messages[-1]["attachments"] == []


def test_missing_files_are_ignored(db_path, config, clock, tmp_path):
    config["dry_run"] = True
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[tmp_path / "gone.jpg"],
                             subject="s", body="b")
    assert sender.send_now(item_id) is True
    assert sender.sent_messages[-1]["attachments"] == []


# -- backoff and giving up -------------------------------------------------


def test_backoff_schedule():
    assert [backoff_for(n) for n in range(1, 7)] == BACKOFF_SECONDS + [HOURLY_BACKOFF]
    assert backoff_for(MAX_ATTEMPTS) == HOURLY_BACKOFF


def test_backoff_grows_and_a_failing_send_ends_as_failed(db_path, config, clock, photo, monkeypatch):
    sender = make_sender(db_path, config, clock)
    monkeypatch.setattr(sender, "_send_smtp",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("network is unreachable")))
    item_id = sender.enqueue(address="guest@example.de", files=[photo], subject="s", body="b")

    delays = []
    for attempt in range(1, MAX_ATTEMPTS):
        assert sender.send_now(item_id) is False
        item = sender.get(item_id)
        assert item["status"] == "pending"
        assert item["attempts"] == attempt
        delays.append(item["next_try"] - clock.now)
        clock.advance(delays[-1])

    assert delays == [float(s) for s in BACKOFF_SECONDS] + \
        [float(HOURLY_BACKOFF)] * (MAX_ATTEMPTS - 1 - len(BACKOFF_SECONDS))
    assert delays == sorted(delays)

    # The last attempt gives up for good.
    assert sender.send_now(item_id) is False
    item = sender.get(item_id)
    assert item["status"] == "failed"
    assert item["attempts"] == MAX_ATTEMPTS
    assert "network is unreachable" in item["last_error"]

    state = sender.status()
    assert state == {"pending": 0, "sent": 0, "failed": 1, "last_error": item["last_error"]}


def test_a_failing_mail_is_not_due_before_its_backoff_elapsed(db_path, config, clock, monkeypatch):
    sender = make_sender(db_path, config, clock)
    monkeypatch.setattr(sender, "_send_smtp",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    item_id = sender.enqueue(address="guest@example.de", files=[], subject="s", body="b")
    assert [i["id"] for i in sender.due()] == [item_id]

    sender.send_now(item_id)
    assert sender.due() == []
    clock.advance(BACKOFF_SECONDS[0])
    assert [i["id"] for i in sender.due()] == [item_id]


def test_last_error_survives_a_restart(db_path, config, clock, monkeypatch):
    sender = make_sender(db_path, config, clock)
    monkeypatch.setattr(sender, "_send_smtp",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("mailbox full")))
    item_id = sender.enqueue(address="guest@example.de", files=[], subject="s", body="b")
    sender.send_now(item_id)
    sender.stop()

    fresh = make_sender(db_path, config, clock)
    assert "mailbox full" in fresh.status()["last_error"]


def test_retry_failed_requeues_and_can_then_succeed(db_path, config, clock, monkeypatch):
    sender = make_sender(db_path, config, clock)
    monkeypatch.setattr(sender, "_send_smtp",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    item_id = sender.enqueue(address="guest@example.de", files=[], subject="s", body="b")
    for _ in range(MAX_ATTEMPTS):
        sender.send_now(item_id)
        clock.advance(HOURLY_BACKOFF)
    assert sender.status()["failed"] == 1

    assert sender.retry_failed() == 1
    assert sender.retry_failed() == 0
    item = sender.get(item_id)
    assert item["status"] == "pending"
    assert item["attempts"] == 0
    assert item["last_error"] is None
    assert sender.status() == {"pending": 1, "sent": 0, "failed": 0, "last_error": None}

    sent: list = []
    monkeypatch.setattr(sender, "_send_smtp", lambda cfg, message: sent.append(message["To"]))
    assert sender.send_now(item_id) is True
    assert sent == ["guest@example.de"]
    assert sender.status()["sent"] == 1


# -- configuration is read fresh -------------------------------------------


def test_configuration_is_read_on_every_attempt(db_path, config, clock, monkeypatch):
    sender = make_sender(db_path, config, clock)
    seen: list = []
    monkeypatch.setattr(sender, "_send_smtp", lambda cfg, message: seen.append(cfg["host"]))

    first = sender.enqueue(address="a@example.de", files=[], subject="s", body="b")
    assert sender.send_now(first) is True

    config["host"] = "smtp.other.de"                  # e.g. a new remote profile arrived
    second = sender.enqueue(address="b@example.de", files=[], subject="s", body="b")
    assert sender.send_now(second) is True

    assert seen == ["smtp.example.de", "smtp.other.de"]


def test_disabled_config_keeps_the_mail_pending_without_spending_attempts(db_path, config, clock):
    config["enabled"] = False
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[], subject="s", body="b")

    assert sender.send_now(item_id) is False
    item = sender.get(item_id)
    assert item["status"] == "pending"
    assert item["attempts"] == 0
    assert sender.status()["pending"] == 1


def test_status_counts_a_mix_of_states(db_path, config, clock, monkeypatch):
    config["dry_run"] = True
    sender = make_sender(db_path, config, clock)
    ok = sender.enqueue(address="ok@example.de", files=[], subject="s", body="b")
    sender.enqueue(address="wait@example.de", files=[], subject="s", body="b")
    bad = sender.enqueue(address="bad@example.de", files=[], subject="s", body="b")
    assert sender.send_now(ok) is True

    config["dry_run"] = False
    monkeypatch.setattr(sender, "_send_smtp",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    for _ in range(MAX_ATTEMPTS):
        sender.send_now(bad)
        clock.advance(HOURLY_BACKOFF)

    state = sender.status()
    assert (state["pending"], state["sent"], state["failed"]) == (1, 1, 1)
    assert "nope" in state["last_error"]


def test_send_now_on_an_unknown_row_is_false(db_path, config, clock):
    sender = make_sender(db_path, config, clock)
    assert sender.send_now(4711) is False


# -- worker thread ---------------------------------------------------------


def test_worker_thread_delivers_and_stops_cleanly(db_path, config, clock, photo):
    config["dry_run"] = True
    sender = MailSender(db_path, lambda: config, start_worker=True, clock=clock)
    try:
        sender.enqueue(address="guest@example.de", files=[photo], subject="s", body="b")
        for _ in range(100):
            if sender.status()["sent"] == 1:
                break
            import time as _time
            _time.sleep(0.02)
        assert sender.status()["sent"] == 1
        assert sender.sent_messages[-1]["attachments"] == ["photo.jpg"]
    finally:
        sender.stop()
    assert sender._thread is None


def test_smtp_security_modes_pick_the_right_client(db_path, config, clock, monkeypatch):
    sender = make_sender(db_path, config, clock)
    calls: list = []

    class FakeClient:
        def __init__(self, host, port, local_hostname=None, timeout=None, context=None):
            assert local_hostname, "smtplib would ask socket.getfqdn() for a name"
            calls.append(("ssl" if context is not None else "plain", host, port, timeout))

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, context=None):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, message):
            calls.append(("send", message["To"]))

        def quit(self):
            calls.append(("quit",))

    monkeypatch.setattr("smtplib.SMTP", FakeClient)
    monkeypatch.setattr("smtplib.SMTP_SSL", FakeClient)

    item_id = sender.enqueue(address="guest@example.de", files=[], subject="s", body="b")
    assert sender.send_now(item_id) is True
    assert ("starttls",) in calls
    assert ("login", "booth@example.de") in calls
    assert calls[0] == ("plain", "smtp.example.de", 587, 20)

    calls.clear()
    config["security"] = "ssl"
    config["port"] = 465
    other = sender.enqueue(address="guest2@example.de", files=[], subject="s", body="b")
    assert sender.send_now(other) is True
    assert ("starttls",) not in calls
    assert calls[0] == ("ssl", "smtp.example.de", 465, 20)


def test_missing_host_is_a_retry_not_a_crash(db_path, config, clock):
    config["host"] = ""
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[], subject="s", body="b")
    assert sender.send_now(item_id) is False
    item = sender.get(item_id)
    assert item["status"] == "pending"
    assert item["attempts"] == 1
    assert "SMTP host" in item["last_error"]


# -- no name lookup while composing -----------------------------------------


def test_composing_a_mail_never_asks_the_network_for_the_host_name(db_path, config, clock, photo, monkeypatch):
    """make_msgid() without a domain calls socket.getfqdn().

    That is a reverse name lookup. On a booth without internet — and on a Mac
    whose resolver was busy, 2026-11-14 — it blocked for minutes and took the
    whole test run with it. The Message-ID takes its domain from the sender
    address instead.
    """
    import socket

    def verboten(*_a, **_k):
        raise AssertionError("name lookup while composing a mail")

    monkeypatch.setattr(socket, "getfqdn", verboten)
    config["dry_run"] = True
    config["from_addr"] = "fotobox@example.de"
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[photo], subject="s", body="b")

    assert sender.send_now(item_id) is True
    assert sender.sent_messages


# -- the queue forgets guests' addresses ----------------------------------


def test_a_sent_mail_forgets_the_address_after_a_day_but_still_counts(db_path, config, clock, photo):
    from boothcore.delivery.mail import SENT_KEEP_SECONDS
    config["dry_run"] = True
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[photo], subject="s", body="Link")
    assert sender.send_now(item_id) is True

    clock.advance(SENT_KEEP_SECONDS - 60)
    sender.forget_old()
    assert sender.get(item_id)["address"] == "guest@example.de", "kept while it may still matter"

    clock.advance(120)
    sender.forget_old()
    item = sender.get(item_id)
    assert item["address"] == "" and item["body"] == "" and json.loads(item["files"]) == []
    assert sender.status()["sent"] == 1, "the admin page still counts it"


def test_a_pending_mail_keeps_its_address_however_old(db_path, config, clock):
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[], subject="s", body="b")
    clock.advance(30 * 24 * 3600)
    sender.forget_old()
    assert sender.get(item_id)["address"] == "guest@example.de"


def test_a_given_up_mail_goes_after_two_weeks(db_path, config, clock):
    from boothcore.delivery.mail import FAILED_KEEP_SECONDS
    sender = make_sender(db_path, config, clock)
    item_id = sender.enqueue(address="guest@example.de", files=[], subject="s", body="b")
    with sender._con() as con:
        con.execute("UPDATE mail_queue SET status='failed' WHERE id=?", (item_id,))
    clock.advance(FAILED_KEEP_SECONDS + 60)
    sender.forget_old()
    assert sender.get(item_id) is None
