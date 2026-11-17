"""No full guest address and no secret in what the booth logs (final review A4).

The booth's warnings go into the log buffer, which the sync agent sends to
the gallery. ``mail to <address> failed`` put the guest's address there.
Call sites now log the shortened address (``a***@example.org``); the buffer
masks whatever slips through, before anything is stored or sent.
"""
import logging
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "boothcore"
GUEST = "anna.meier@example.org"


def test_the_buffer_masks_addresses_and_secrets():
    from boothcore.diagnostics import LogBuffer
    buf = LogBuffer()
    buf.add("warning", f"mail to {GUEST} failed", detail=f"SMTPRecipientsRefused {{'{GUEST}': (550, b'no')}}")
    buf.add("warning", "admin_pin=938271 gesetzt, Bearer pbk_secret")
    text = repr(buf.peek()) + repr(buf.take())
    assert GUEST not in text and "938271" not in text and "pbk_secret" not in text
    assert "a***@example.org" in text


def test_a_failed_mail_logs_the_shortened_address(tmp_path, caplog):
    from boothcore.delivery.mail import MailSender
    sender = MailSender(tmp_path / "mail.db", lambda: {}, start_worker=False)
    try:
        with caplog.at_level(logging.WARNING):
            sender._fail({"id": 1, "attempts": 0, "address": GUEST}, f"550 <{GUEST}> unknown")
    finally:
        sender.stop()
    assert GUEST not in caplog.text
    assert "a***@example.org" in caplog.text


def test_no_log_call_in_the_booth_passes_a_raw_address():
    """Source check: a log call naming ``address``/``email`` must shorten it."""
    pattern = re.compile(r"log\w*\.(debug|info|warning|error|exception|critical)\((?:[^()]|\([^()]*\))*\)", re.S)
    offenders = []
    for path in ROOT.rglob("*.py"):
        if "web" in path.parts:
            continue
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            call = m.group(0)
            if re.search(r"\b(address|email|to_email|recipient)\b|\[\"address\"\]", call) and "redact" not in call:
                offenders.append(f"{path.name}: {call[:80]}")
    assert offenders == []
