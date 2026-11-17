"""The hygiene check keeps build leftovers out of the tree.

A PyInstaller run once left five wheels in the repository root, and the check
that runs on every pull request said "ok". The Windows build installs its tools
from pip; a wheel in the tree is never an input, only dead weight in every
clone, in the public snapshot and in the gallery image.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_hygiene", ROOT / "tools" / "check_hygiene.py")
check_hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_hygiene)


@pytest.mark.parametrize("name", [
    "pyinstaller-6.22.3-py3-none-manylinux2014_x86_64.whl",
    "packaging/windows/vendor/altgraph-0.17.5-py2.py3-none-any.whl",
    "booth/dist/boothcore-0.3.0.tar.gz",
    "sync/dist/photobox_sync-0.3.0.tgz",
    "old/thing-1.0-py3.11.egg",
])
def test_a_package_archive_anywhere_in_the_tree_fails_the_check(name):
    assert check_hygiene.check_binaries([name]), f"{name} should not pass"


@pytest.mark.parametrize("name", [
    "packaging/windows/build.ps1",
    "booth/requirements.txt",
    "app/static/img/logo.png",
    "docs/wheel-of-fortune.md",
])
def test_ordinary_files_still_pass(name):
    assert check_hygiene.check_binaries([name]) == []


def test_the_repository_itself_is_clean():
    assert check_hygiene.check_binaries(check_hygiene.tracked_files()) == []
