"""A test run leaves nothing behind in the temp directory.

On 15.11.2026 the Mac that ran the suites had 67,000 test folders and 37 GB
in /tmp ("booth-*", "spool-*", "drucker-*", "photobox-test-*") and a full
disk; on 17.11.2026 it was 98 % full again ("held-*", "photobox-probe-*",
pytest's own folders, runs that were killed). Each run now lives in one temp
folder of its own (tools/tmp_guard.py), removed at the end, swept by the next
run if the process was killed. These tests run part of the suite with an
empty temp directory of its own and count what is left.
"""
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def _guard():
    spec = importlib.util.spec_from_file_location("photobox_tmp_guard", REPO / "tools" / "tmp_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_partial_run_leaves_no_folders_in_tmp():
    own = Path(tempfile.mkdtemp(prefix="tmp-check-"))      # removed by conftest at the end
    env = dict(os.environ, TMPDIR=str(own), PHOTOBOX_TMP_GUARD="strict")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "tests/test_print_spool.py", "tests/test_printer_models.py",
                        "tests/test_paper_and_ribbon_counters.py", "tests/test_boomerang_format_choice.py",
                        "tests/test_prints_resume_without_the_pin.py"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "TEMP GUARD" not in r.stdout
    left = sorted(p.name for p in own.iterdir())
    assert left == [], left


def test_everything_tempfile_makes_lands_in_the_runs_own_folder():
    run_root = Path(tempfile.gettempdir())
    assert run_root.name.startswith("photobox-run-booth-"), run_root
    assert os.environ["TMPDIR"] == str(run_root)
    fd, name = tempfile.mkstemp(prefix="stray-")
    os.close(fd)
    with tempfile.NamedTemporaryFile(prefix="stray-", delete=False) as f:
        other = f.name
    assert Path(name).parent == run_root and Path(other).parent == run_root


def test_a_killed_run_is_swept_by_the_next_and_foreign_folders_stay(tmp_path):
    system = tmp_path / "system"
    system.mkdir()
    env = dict(os.environ, TMPDIR=str(system))
    script = textwrap.dedent(f"""
        import importlib.util, os, signal, tempfile
        spec = importlib.util.spec_from_file_location("g", {str(REPO / "tools" / "tmp_guard.py")!r})
        g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
        root = g.RunRoot("killed")
        (root.path / "booth").mkdir()
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    if os.name == "nt":
        pytest.skip("SIGKILL and the pid probe are POSIX; Windows never sweeps")
    r = subprocess.run([sys.executable, "-c", script], env=env, timeout=60)
    assert r.returncode == -signal.SIGKILL
    killed = [p for p in system.iterdir() if p.name.startswith("photobox-run-killed-")]
    assert len(killed) == 1, "the killed run could not clean up"
    foreign = system / "photobox-run-someone-else"          # no pid mark: not ours
    foreign.mkdir()

    g = _guard()
    swept = g.sweep_stale(system)
    assert swept == [killed[0].name]
    assert not killed[0].exists() and foreign.exists()


def test_the_guard_names_what_escaped_and_fails_only_when_strict(tmp_path, monkeypatch):
    system = tmp_path / "system"
    system.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system))
    for k in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(k, str(system))
    g = _guard()
    root = g.RunRoot("probe")
    (root.path / "inside").mkdir()                            # fine: inside the run folder
    (system / "drucker-handmade").mkdir()                     # escaped
    root.remove()
    assert not root.path.exists()
    text, fail = root.report(strict=False)
    assert "drucker-handmade" in text and "TEMP GUARD" in text and fail is False
    assert root.report(strict=True)[1] is True
    assert tempfile.tempdir == str(system), "the previous temp dir is back"
