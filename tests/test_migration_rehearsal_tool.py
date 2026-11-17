"""The migration rehearsal's own judgement: what counts as loss, what it prints.

The full rehearsal (old code from git, two interpreters, ~10 s) runs as
``python tools/migration_rehearsal.py``; these tests pin the parts that
decide its verdict, so a refactoring cannot quietly make it blind.
"""
from __future__ import annotations

import importlib.util
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("migration_rehearsal", ROOT / "tools" / "migration_rehearsal.py")
rehearsal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rehearsal)


def _db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE events (id INTEGER PRIMARY KEY, slug TEXT, name TEXT);
        INSERT INTO events VALUES (1, 'a', 'A'), (2, 'b', 'B');
        CREATE TABLE likes (id INTEGER PRIMARY KEY, media_id INTEGER);
        INSERT INTO likes VALUES (1, 1);
    """)
    con.commit()
    con.close()
    return path


def test_a_missing_row_and_a_rewritten_value_are_seen(tmp_path):
    before = _db(tmp_path / "before.sqlite3")
    after = tmp_path / "after.sqlite3"
    shutil.copy(before, after)
    con = sqlite3.connect(after)
    con.execute("ALTER TABLE events ADD COLUMN code TEXT")       # additive: fine
    con.execute("DELETE FROM events WHERE id=2")                  # loss
    con.execute("UPDATE likes SET media_id=7")                    # rewritten old value
    con.commit()
    con.close()
    rows = rehearsal.compare_rows(before, after)
    assert rows["events"]["missing"] == 1
    assert rows["events"]["changed"] == {}
    assert rows["likes"]["changed"] == {"media_id": 1}


def test_identical_databases_give_identical_snapshots(tmp_path):
    a = _db(tmp_path / "a.sqlite3")
    b = tmp_path / "b.sqlite3"
    shutil.copy(a, b)
    assert rehearsal.snapshot(a) == rehearsal.snapshot(b)
    con = sqlite3.connect(b)
    con.execute("UPDATE events SET name='changed' WHERE id=1")
    con.commit()
    con.close()
    assert rehearsal.snapshot(a)["tables"]["events"] != rehearsal.snapshot(b)["tables"]["events"]


def test_a_report_on_real_data_names_no_values(tmp_path):
    r = rehearsal.Rehearsal(tmp_path, tmp_path, scrub=True)
    text = r.clean("KeyError: 'hochzeit-mueller' for anna@example.org key 3f9c2a7b1d4e5f60a1b2c3d4")
    assert "hochzeit" not in text and "anna@" not in text and "3f9c2a7b" not in text


def test_the_child_programs_are_valid_python():
    for code in (rehearsal.POPULATE, rehearsal.MIGRATE, rehearsal.CHECK + rehearsal.CHECK_KNOWN):
        compile(code, "<child>", "exec")


def test_children_have_no_network():
    """A restored production database carries real SMTP and Telegram settings."""
    assert "socket.socket.connect = " in rehearsal._COMMON
    assert "getaddrinfo" in rehearsal._COMMON
