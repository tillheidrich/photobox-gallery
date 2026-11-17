"""The booth's sealing key for the second target is made once and never overwritten (review, 17.11.2026).

Before, any OSError while reading ``keys/second_target.key`` (a lock, a virus
scanner) made a new key and wrote it over the good one: every password the
gallery had sealed to the old key was lost. The file was also chmod-ed to
0600 only after it was written.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from boothcore import second_target as st


def test_a_new_key_is_made_once_with_mode_0600(tmp_path):
    k = st.Keys(tmp_path)
    pub = k.public_b64()
    assert k.path.is_file() and st.Keys(tmp_path).public_b64() == pub
    if os.name == "posix":
        assert stat.S_IMODE(k.path.stat().st_mode) == 0o600
    assert not k.path.with_suffix(".tmp").exists()


def test_an_unreadable_key_file_is_retried_then_raised_and_never_replaced(tmp_path, monkeypatch):
    st.Keys(tmp_path).private()
    path = tmp_path / "keys" / "second_target.key"
    before = path.read_bytes()
    monkeypatch.setattr(st, "READ_PAUSE_S", 0)
    calls = []
    real = Path.read_text

    def locked(self, *a, **kw):
        if self == path:
            calls.append(1)
            raise PermissionError("held by a scanner")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(OSError):
        st.Keys(tmp_path).private()
    assert len(calls) == st.READ_ATTEMPTS
    monkeypatch.setattr(Path, "read_text", real)
    assert path.read_bytes() == before


def test_a_transient_lock_is_survived(tmp_path, monkeypatch):
    pub = st.Keys(tmp_path).public_b64()
    path = tmp_path / "keys" / "second_target.key"
    monkeypatch.setattr(st, "READ_PAUSE_S", 0)
    real = Path.read_text
    state = {"n": 0}

    def once(self, *a, **kw):
        if self == path and state["n"] == 0:
            state["n"] += 1
            raise PermissionError("locked for a moment")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", once)
    assert st.Keys(tmp_path).public_b64() == pub


def test_a_damaged_key_file_raises_and_stays(tmp_path):
    path = tmp_path / "keys" / "second_target.key"
    path.parent.mkdir(parents=True)
    path.write_text("kaputt!", encoding="ascii")
    with pytest.raises(ValueError):
        st.Keys(tmp_path).private()
    assert path.read_text(encoding="ascii") == "kaputt!"
