"""Guards for the whole sync suite.

The run lives in one temp folder of its own, removed at the end and swept by
the next run if this one was killed (tools/tmp_guard.py). The summary names
anything that still landed directly in the system temp directory.
"""
import importlib.util as _ilu
from pathlib import Path

_spec = _ilu.spec_from_file_location(
    "photobox_tmp_guard", Path(__file__).resolve().parents[2] / "tools" / "tmp_guard.py")
_tmp_guard = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_tmp_guard)
RUN_TMP = _tmp_guard.RunRoot("sync")
_tmp_report: list = ["", False]


def pytest_sessionfinish(session, exitstatus):
    RUN_TMP.remove()
    _tmp_report[:] = RUN_TMP.report()
    if _tmp_report[1] and session.exitstatus == 0:
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter):
    if _tmp_report[0]:
        terminalreporter.write_line(_tmp_report[0], red=True, bold=True)
