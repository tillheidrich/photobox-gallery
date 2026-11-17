"""A box that failed and is being restarted by its watcher was not shut down on purpose.

The booth's exit codes (waechter.py): 0 quit, 3 restart, anything else an
error the watcher restarts after a pause. Since feat/box-zusammen the booth
reports the error as such ("error", expected back). The gallery's line used
to read "bewusst heruntergefahren … (Fehler, startet neu)" for it, which
contradicts itself; it now says what happened.
"""
import datetime as dt

from app import silence_alarm


def _row(reason, back):
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    return {"down_at": silence_alarm._fmt(now), "down_reason": reason, "down_expect_back": 1 if back else 0}


def test_an_error_restart_is_not_called_deliberate():
    de = silence_alarm.goodbye_text(_row("error", True))
    en = silence_alarm.goodbye_text(_row("error", True), lang="en")
    assert "bewusst" not in de and "Fehler" in de and "startet neu" in de
    assert "on purpose" not in en and "error" in en


def test_quit_and_restart_stay_deliberate():
    assert silence_alarm.goodbye_text(_row("quit", False)).startswith("bewusst heruntergefahren")
    assert "kommt wieder" in silence_alarm.goodbye_text(_row("restart", True))
    assert silence_alarm.goodbye_text(_row("restart", True), lang="en").startswith("shut down on purpose")
