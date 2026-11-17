"""No "Telegram/Discord notification failed" in the log when nothing is configured.

Final review T3: every booth problem, published event and new upload wrote
two warnings although no messenger was set up — noise that hides the real
failures. A channel that is not configured is skipped quietly; a configured
one that fails still warns.
"""
import logging

import pytest

from app import db, notifier


@pytest.fixture()
def nothing_configured(client, monkeypatch):
    client.get("/healthz")
    for env in ("TG_TOKEN", "TG_CHAT_ID", "DISCORD_WEBHOOK"):
        monkeypatch.delenv(env, raising=False)
    for key in ("notify_tg_token", "notify_tg_chat_id", "notify_discord_webhook", "notify_webhook_url"):
        db.set_setting(key, "")
    monkeypatch.setattr(notifier, "_in_quiet_hours", lambda: False)


def test_a_booth_problem_without_messengers_logs_no_failure(nothing_configured, caplog):
    with caplog.at_level(logging.DEBUG, logger="app.notifier"):
        notifier.notify_booth_problem("Box 1", ["Kamera fehlt"])
    assert "fehlgeschlagen" not in " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def test_a_published_event_without_messengers_logs_no_failure(nothing_configured, caplog):
    with caplog.at_level(logging.WARNING, logger="app.notifier"):
        notifier.notify_event_published(event_name="Fest", gallery_url="https://g.example.org/gallery/fest")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_configured_channel_that_fails_still_warns(nothing_configured, caplog, monkeypatch):
    db.set_setting("notify_tg_token", "123:abc")
    db.set_setting("notify_tg_chat_id", "42")
    monkeypatch.setattr(notifier, "_post_json", lambda url, payload: False)
    with caplog.at_level(logging.WARNING, logger="app.notifier"):
        notifier.notify_booth_problem("Box 1", [])
    assert any("Telegram" in r.getMessage() for r in caplog.records)
    assert not any("Discord" in r.getMessage() for r in caplog.records)
