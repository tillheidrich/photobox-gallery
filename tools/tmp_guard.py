"""One temp folder per test run, and it goes away again — pass, fail or killed.

The three suites and the rehearsal make booths, spools, galleries and browser
profiles in the temp directory. Removing each folder by hand at the right
moment never held: on the Mac that runs the suites all day /tmp grew by about
20 GB a day ("drucker-*", "photobox-test-*", "booth-*", "spool-*", "held-*",
"photobox-probe-*" of up to 1 GB each) until the disk was full (15.11. and
17.11.2026).

So a run does not use the system temp directory at all. ``RunRoot`` makes one
folder ``photobox-run-<suite>-*`` in it, points ``tempfile`` and the
``TMPDIR``/``TEMP``/``TMP`` variables (inherited by every subprocess: booths,
galleries, headless browsers) at that folder, and removes it at the end. A run
that was killed (SIGKILL, a closed terminal) cannot clean up after itself; its
folder carries the pid that made it, and the next run removes folders whose pid
is gone. Folders without that mark are not ours and are never touched.

``leftovers()`` lists what appeared directly in the system temp directory
during the run, outside the run folder: something that builds its own
``/tmp/...`` path. The suites print that list loudly and fail with
``PHOTOBOX_TMP_GUARD=strict`` (set in CI). Locally it only warns, because other
programs write to /tmp at the same time and a run cannot tell their files from
its own.

Standard library only; loaded by file path from the conftest files and by
tools/rehearsal.py, so it needs no package and puts nothing on sys.path:

    spec = importlib.util.spec_from_file_location("photobox_tmp_guard", ROOT / "tools" / "tmp_guard.py")
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

PREFIX = "photobox-run-"
OWNER = ".owner-pid"
ENV_VARS = ("TMPDIR", "TEMP", "TMP")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) on Windows terminates the process instead of probing
        # it. Count every pid as alive there: no sweep, never a killed process.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:          # someone else's live process
        return True
    except OSError:
        return False
    return True


def sweep_stale(base: Path, prefix: str = PREFIX) -> List[str]:
    """Remove run folders under ``base`` whose owning process is gone.

    Only folders that carry our pid mark are considered; anything else with a
    similar name was made by someone else and stays.
    """
    removed: List[str] = []
    try:
        candidates = list(base.glob(prefix + "*"))
    except OSError:
        return removed
    for folder in candidates:
        try:
            pid = int((folder / OWNER).read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue
        if pid != os.getpid() and not _pid_alive(pid):
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
    return removed


def make_owned_dir(prefix: str, base: Optional[Path] = None) -> Path:
    """A fresh folder with the pid mark, so a later sweep can tell it is stale."""
    folder = Path(tempfile.mkdtemp(prefix=prefix, dir=str(base) if base else None))
    (folder / OWNER).write_text(str(os.getpid()), encoding="ascii")
    return folder


class RunRoot:
    """The run's own temp directory; see the module docstring."""

    def __init__(self, suite: str):
        # realpath: on macOS /tmp is a link to /private/tmp, and Leptonica
        # (behind the OCR tests) rewrites paths that start with /tmp.
        self.system = Path(os.path.realpath(tempfile.gettempdir()))
        self.before = set(_listdir(self.system))
        self.swept = sweep_stale(self.system)
        self.path = make_owned_dir(f"{PREFIX}{suite}-", self.system)
        self._previous = (tempfile.tempdir, {k: os.environ.get(k) for k in ENV_VARS})
        tempfile.tempdir = str(self.path)
        for k in ENV_VARS:
            os.environ[k] = str(self.path)
        atexit.register(self.remove)

    def leftovers(self) -> List[str]:
        """New entries directly in the system temp directory, not ours, not other runs'."""
        new = set(_listdir(self.system)) - self.before
        return sorted(n for n in new if n != self.path.name and not n.startswith(PREFIX))

    def remove(self) -> None:
        old_tempdir, old_env = self._previous
        if tempfile.tempdir == str(self.path):
            tempfile.tempdir = old_tempdir
        for k, v in old_env.items():
            if os.environ.get(k) == str(self.path):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        shutil.rmtree(self.path, ignore_errors=True)

    def report(self, strict: Optional[bool] = None) -> "tuple[str, bool]":
        """What escaped the run folder, as text, and whether the run should fail."""
        if strict is None:
            strict = os.environ.get("PHOTOBOX_TMP_GUARD", "").strip().lower() == "strict"
        left = self.leftovers()
        if not left:
            return "", False
        bar = "!" * 72
        lines = [bar, f"TEMP GUARD: {len(left)} new entr{'y' if len(left) == 1 else 'ies'} "
                      f"in {self.system} outside this run's own folder:"]
        lines += [f"  {n}" for n in left[:40]]
        if len(left) > 40:
            lines.append(f"  … and {len(left) - 40} more")
        lines.append("Something built its own temp path. Use tmp_path or tempfile instead.")
        if not strict:
            lines.append("(Other programs may have written these. PHOTOBOX_TMP_GUARD=strict fails the run.)")
        lines.append(bar)
        return "\n".join(lines), strict


def _listdir(path: Path) -> List[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []
