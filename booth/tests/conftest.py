"""Guards for the whole booth suite.

No real printer. A booth with default settings chooses its printer by itself
("auto": the Windows default printer, or the CUPS default wherever `lp`
exists). Most tests build a booth with default settings and print, so on any
machine with a printer set up the suite sent real jobs to it. During tests
"auto" therefore means the folder backend, and the CUPS backend refuses to
call `lp` even when a test names it. A test that needs a specific backend
still asks for it by name (windows with the fakes, ipp, hotfolder, folder).

The sync package of this checkout, not whatever an editable install points
at: a virtualenv shared by several worktrees had ``photobox_sync`` installed
from another one, so the booth suite tested an older sync package than the
booth runs with (the rehearsal sets PYTHONPATH the same way).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sync"))

import pytest  # noqa: E402

from boothcore.printing import backends, service


# ── Temporary folders are removed again ───────────────────────────────────
# The tests make their booths in tempfile.mkdtemp folders ("booth-*",
# "spool-*", "drucker-*") and nearly none removed them: on the Mac that ran
# the suites all day 67,000 folders and 37 GB piled up in /tmp (15.11.2026)
# until the disk was full. Every folder mkdtemp makes in this process is
# noted and removed when the session ends, pass or fail. Not after each
# test: a booth's printer thread still polls its spool for a moment after
# the test and would log "unable to open database file" into the run.
# booth/tests/test_tmp_is_cleaned.py counts that a partial run leaves
# nothing behind.
#
# That still left folders whenever a run was killed, and everything not made
# by mkdtemp (mkstemp, NamedTemporaryFile, browser profiles, booths started as
# subprocesses). So the whole run now lives in one folder of its own, removed
# at the end and swept by the next run if this one was killed; see
# tools/tmp_guard.py. The summary names anything that still escaped it.
import importlib.util as _ilu  # noqa: E402
import shutil as _shutil  # noqa: E402
import tempfile as _tempfile  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "photobox_tmp_guard", Path(__file__).resolve().parents[2] / "tools" / "tmp_guard.py")
_tmp_guard = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_tmp_guard)
RUN_TMP = _tmp_guard.RunRoot("booth")
_tmp_report: list = ["", False]

_every: list = []
_real_mkdtemp = _tempfile.mkdtemp


def _noted_mkdtemp(*args, **kwargs):
    path = _real_mkdtemp(*args, **kwargs)
    _every.append(path)
    return path


_tempfile.mkdtemp = _noted_mkdtemp


def pytest_sessionfinish(session, exitstatus):
    for p in _every:
        _shutil.rmtree(p, ignore_errors=True)
    RUN_TMP.remove()
    _tmp_report[:] = RUN_TMP.report()
    if _tmp_report[1] and session.exitstatus == 0:
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter):
    if _tmp_report[0]:
        terminalreporter.write_line(_tmp_report[0], red=True, bold=True)


@pytest.fixture(autouse=True)
def no_real_printer(monkeypatch):
    real_create = service.create_printer

    def create_printer(cfg, data):
        kind = (cfg.get("backend") or "auto").lower()
        if kind == "auto":
            cfg = dict(cfg, backend="folder")
        return real_create(cfg, data)

    def refuse(self, path, copies=1):
        raise AssertionError(f"a test tried to print {path} on a real printer (CUPS)")

    # The booth imports the function by name, so every name it goes by is
    # replaced - patching only the module that defines it would miss app.py.
    import boothcore.app
    import boothcore.printing
    for module in (service, boothcore.printing, boothcore.app):
        monkeypatch.setattr(module, "create_printer", create_printer)
    monkeypatch.setattr(backends.CupsPrinter, "print_file", refuse)


@pytest.fixture(autouse=True)
def no_real_power_switch(monkeypatch):
    """Remote control may restart or shut down the booth PC. Never from a test:
    the one function that reaches the operating system fails the test instead."""
    from boothcore import fernsteuerung

    def refuse(restart):
        raise AssertionError("a test reached the real power switch (restart/shutdown)")

    monkeypatch.setattr(fernsteuerung.Fernsteuerung, "pc_power", staticmethod(refuse))
