"""Findings of the Windows read-through of 14 Nov 2026.

None of this has run on Windows when these tests were written; they pin the
parts that can be checked from here.
"""
import subprocess
from pathlib import Path

import pytest

from boothcore import dauerbetrieb, lifecycle, winsetup

WIN = Path(__file__).resolve().parents[2] / "packaging" / "windows"


@pytest.mark.parametrize("mod", [dauerbetrieb, winsetup])
def test_command_output_in_another_code_page_does_not_crash_set_up(mod, monkeypatch):
    seen = {}

    def fake(args, **kw):
        seen.update(kw)
        raise UnicodeDecodeError("cp1252", b"\x81", 0, 1, "undefined")
    monkeypatch.setattr(subprocess, "run", fake)
    ok, text = mod._run(["powercfg", "/query"])
    assert not ok or ok == 1                       # reported as a failure, not raised
    assert seen.get("errors") == "replace"


def test_a_restarted_frozen_booth_gets_a_console_and_a_fresh_bundle():
    kw = lifecycle.respawn_kwargs()
    assert kw["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert kw["creationflags"] == getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    assert kw["creationflags"] != getattr(subprocess, "DETACHED_PROCESS", -1)


def test_set_up_does_not_wait_for_a_key_when_told_not_to(monkeypatch):
    monkeypatch.setenv("PHOTOBOX_NO_PAUSE", "1")
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("asked")))
    lifecycle.pause()


def test_set_up_survives_a_closed_keyboard(monkeypatch):
    monkeypatch.delenv("PHOTOBOX_NO_PAUSE", raising=False)
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(EOFError()))
    lifecycle.pause()


def test_first_settings_are_written_before_the_canon_sdk_step():
    ps = (WIN / "alles-einrichten.ps1").read_text(encoding="utf-8-sig")
    assert ps.index("Grundeinstellungen anlegen") < ps.index("--install-edsdk")
    assert 'Test-Path "D:\\"' not in ps            # D: may be the stick the set-up runs from
    assert "PHOTOBOX_NO_PAUSE" in ps


def test_the_python_probe_cannot_stop_the_build():
    ps = (WIN / "build.ps1").read_text(encoding="utf-8-sig")
    assert '& py -3.12 -c "import sys" 2>$null' not in ps


def test_batch_files_are_plain_and_have_windows_line_ends():
    for bat in WIN.glob("*.bat"):
        raw = bat.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), bat.name
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), bat.name


def test_the_program_still_starts(tmp_path):
    """A set-up helper imported twice in ``main`` once made every start fail."""
    import os, subprocess as sp, sys
    env = dict(os.environ, BOOTH_DATA=str(tmp_path), BOOTH_CAMERA="mock")
    out = sp.run([sys.executable, "-m", "boothcore", "--help"], capture_output=True, text=True, env=env,
                 cwd=str(Path(__file__).resolve().parents[1]), timeout=60)
    assert out.returncode == 0, out.stderr[-400:]
    import ast
    src = (Path(__file__).resolve().parents[1] / "boothcore" / "__main__.py").read_text(encoding="utf-8")
    main = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "main")
    local = [n for n in ast.walk(main) if isinstance(n, ast.ImportFrom) and any(a.name == "lifecycle" for a in n.names)]
    assert not local, "a local import of lifecycle shadows the module-level one"


def test_the_kiosk_browser_does_not_ask_to_restore_pages_after_a_power_cut():
    src = (Path(__file__).resolve().parents[1] / "boothcore" / "__main__.py").read_text(encoding="utf-8")
    for flag in ("--disable-session-crashed-bubble", "--hide-crash-restore-bubble", "--noerrdialogs"):
        assert flag in src, flag
