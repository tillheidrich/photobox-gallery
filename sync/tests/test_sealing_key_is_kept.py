"""The sealing key survives a file it cannot read for a moment (review 17.11.2026).

``SealKeys.private`` generated a new key on ANY OSError and wrote it over the
good one: a lock or a virus scanner holding the file for a moment destroyed
the key, and every password the gallery had sealed to it could no longer be
opened. The file was also written first and made 0600 afterwards.
"""
import base64
import os
import stat
import sys
from pathlib import Path

import pytest

from photobox_sync import sealing


def _made(tmp_path):
    keys = sealing.SealKeys(tmp_path)
    first = keys.key_id()
    return keys.path, first


def test_a_new_key_is_made_only_when_there_is_no_file(tmp_path):
    path, first = _made(tmp_path)
    assert path.is_file()
    assert sealing.SealKeys(tmp_path).key_id() == first


def test_a_file_held_for_a_moment_is_read_again_not_replaced(tmp_path, monkeypatch):
    path, first = _made(tmp_path)
    before = path.read_bytes()
    real = Path.read_text
    calls = {"n": 0}

    def flaky(self, *a, **k):
        if self == path:
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(13, "held by another process")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", flaky)
    monkeypatch.setattr(sealing, "READ_PAUSE_S", 0.0)
    assert sealing.SealKeys(tmp_path).key_id() == first
    assert path.read_bytes() == before


def test_a_file_that_stays_unreadable_raises_and_is_never_overwritten(tmp_path, monkeypatch):
    path, _ = _made(tmp_path)
    before = path.read_bytes()
    real = Path.read_text

    def held(self, *a, **k):
        if self == path:
            raise PermissionError(13, "held by another process")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", held)
    monkeypatch.setattr(sealing, "READ_PAUSE_S", 0.0)
    with pytest.raises(OSError):
        sealing.SealKeys(tmp_path).private()
    assert path.read_bytes() == before


def test_a_damaged_file_raises_and_is_never_overwritten(tmp_path):
    path, _ = _made(tmp_path)
    path.write_text("not base64 !!!", encoding="ascii")
    with pytest.raises(ValueError):
        sealing.SealKeys(tmp_path).private()
    assert path.read_text(encoding="ascii") == "not base64 !!!"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_key_file_is_created_0600_from_the_start(tmp_path, monkeypatch):
    modes = []
    real = os.open

    def spy(file, flags, mode=0o777, *a, **k):
        if str(file).endswith(".tmp") or "sync_targets" in str(file):
            modes.append(mode)
        return real(file, flags, mode, *a, **k)
    monkeypatch.setattr(os, "open", spy)
    keys = sealing.SealKeys(tmp_path)
    keys.private()
    assert modes and all(m == 0o600 for m in modes)
    assert stat.S_IMODE(keys.path.stat().st_mode) == 0o600
    assert len(base64.b64decode(keys.path.read_text(encoding="ascii"))) == 32
