"""Second review M2: the public snapshot builds on macOS and checks a deny-list.

``tools/make_public_snapshot.sh`` used ``cp --parents`` (GNU only) and
stopped on a Mac. It copies with Python now. The test runs it for real into
a temporary folder: one commit, the author address from SNAPSHOT_EMAIL, the
excluded paths absent, and a deny-list kept outside the repository
(SNAPSHOT_DENYLIST) stops it when one of its lines occurs. The script never
pushes: the snapshot has no remote.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "make_public_snapshot.sh"

pytestmark = pytest.mark.skipif(not shutil.which("bash") or not shutil.which("git"), reason="needs bash and git")


def _run(out: Path, **env):
    full = dict(os.environ, PYTHON=sys.executable, SNAPSHOT_EMAIL="snapshot@example.org", **env)
    return subprocess.run(["bash", str(SCRIPT), str(out)], cwd=ROOT, env=full,
                          capture_output=True, text=True, timeout=300)


def test_the_snapshot_builds_with_one_commit_and_without_the_excluded_files(tmp_path):
    deny = tmp_path / "deny.txt"
    deny.write_text("# nothing of ours\nzz-this-string-is-nowhere-" + "q" * 12 + "\n", encoding="utf-8")
    out = tmp_path / "public"
    r = _run(out, SNAPSHOT_DENYLIST=str(deny))
    assert r.returncode == 0, r.stdout + r.stderr
    git = lambda *a: subprocess.run(["git", "-C", str(out), *a], capture_output=True, text=True).stdout.strip()
    assert git("rev-list", "--count", "HEAD") == "1"
    assert git("log", "-1", "--format=%ae") == "snapshot@example.org"
    assert git("remote") == ""                                   # nothing to push to
    assert not (out / "docs" / "PUBLISHING.md").exists()
    assert not (out / "docs" / "FIRST_ISSUES.md").exists()
    assert not list((out / "docs").glob("release-*.txt"))
    assert not (out / "docs" / "intern").exists()
    assert (out / "AGENTS.md").exists() and (out / "tools" / "make_public_snapshot.sh").exists()
    assert (tmp_path / "public.bundle").exists()


def test_a_deny_list_hit_stops_the_snapshot_without_printing_the_word(tmp_path):
    word = (ROOT / "VERSION").read_text(encoding="utf-8").split()[0]     # certainly in the tree
    deny = tmp_path / "deny.txt"
    deny.write_text(f"# test\n{word}\n", encoding="utf-8")
    r = _run(tmp_path / "public", SNAPSHOT_DENYLIST=str(deny))
    assert r.returncode != 0
    assert "deny-list line 2" in r.stderr
    assert not (tmp_path / "public" / ".git").exists()
