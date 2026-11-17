"""The signing key when SECRET_KEY is missing or the docker-compose placeholder.

The old production code signed sessions with "change-me-in-production" when
nothing was set. This code generates a key once and keeps it in
DATA_DIR/.secret_key. Two things the upgrade notes rely on:

* Two Gunicorn workers start at the same moment on a fresh volume. Both
  must end up with the same key — otherwise a session or share link signed
  by one worker is refused by the other, at random.
* The fallback is loud: the log says where the key lives and that it has to
  survive (it is in every backup), or that SECRET_KEY should be set.
"""
from __future__ import annotations

import logging
import secrets

import pytest

import app as app_pkg


@pytest.fixture()
def fresh_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    return tmp_path


def test_a_worker_that_loses_the_race_uses_the_winners_key(fresh_dir, monkeypatch):
    real = secrets.token_urlsafe

    def other_worker_wins(n):
        # The other worker writes its key between our check and our write.
        (fresh_dir / ".secret_key").write_text("key-of-the-other-worker")
        return real(n)

    monkeypatch.setattr(secrets, "token_urlsafe", other_worker_wins)
    assert app_pkg._secret_key() == "key-of-the-other-worker"
    assert (fresh_dir / ".secret_key").read_text().strip() == "key-of-the-other-worker"


def test_the_generated_key_is_kept_and_private(fresh_dir):
    first = app_pkg._secret_key()
    assert first == app_pkg._secret_key()
    assert len(first) >= 40
    assert ((fresh_dir / ".secret_key").stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("value", [None, "change-me-in-production"])
def test_the_fallback_is_announced_in_the_log(fresh_dir, monkeypatch, caplog, value):
    if value:
        monkeypatch.setenv("SECRET_KEY", value)
    with caplog.at_level(logging.WARNING):
        app_pkg._secret_key()
    text = caplog.text
    assert "SECRET_KEY" in text and ".secret_key" in text


def test_a_short_secret_key_is_used_but_warned_about(fresh_dir, monkeypatch, caplog):
    monkeypatch.setenv("SECRET_KEY", "short")
    with caplog.at_level(logging.WARNING):
        assert app_pkg._secret_key() == "short"
    assert "SECRET_KEY" in caplog.text
