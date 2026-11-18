"""After a Windows build: no Canon SDK file in the build tree or in a program.

Only the free edition is ever built and signed in CI (SignPath signs open
source only; AGENTS.md rule 1). tools/check_hygiene.py --build-output and
--pyinstaller look at what a build produced: every name below a folder, and
the table of contents of each PyInstaller program. The archive reading itself
was tried against real PyInstaller programs (one with a fake EDSDK.dll packed,
one without); here the rules are held without PyInstaller.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_hygiene", ROOT / "tools" / "check_hygiene.py")
check_hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_hygiene)


@pytest.mark.parametrize("name", [
    "EDSDK.dll", "EdsImage.dll", "EDSDK/Dll/EDSDK.dll", "edsdk\\EDSDK_64\\Dll\\EdsImage.dll",
    "EDSDK.framework/Versions/A/EDSDK", "Header/EDSDK.h", "EDSDKTypes.h", "EDSDKErrors.h",
    "libEDSDK.so", "EDSDK.lib", "data/EDSDK/readme.txt",
])
def test_canon_sdk_files_and_the_edsdk_folder_are_found(name):
    assert check_hygiene.is_vendor_sdk_path(name)


@pytest.mark.parametrize("name", [
    "booth/boothcore/camera/edsdk.py", "booth/boothcore/edsdk_setup.py", "booth/tests/fake_edsdk.py",
    "PYZ.pyz/boothcore.camera.edsdk", "boothcore/web/static/legal/THIRD-PARTY-NOTICES.txt",
    "docs/CAMERA-MOVIE.md", "LICENSE-EXCEPTION",
])
def test_the_projects_own_wrapper_and_texts_pass(name):
    assert not check_hygiene.is_vendor_sdk_path(name)


def test_a_canon_file_anywhere_in_the_tree_fails(tmp_path):
    (tmp_path / "packaging" / "windows" / "dist").mkdir(parents=True)
    (tmp_path / "packaging" / "windows" / "dist" / "PhotoboxBooth.exe").write_bytes(b"MZ")
    assert check_hygiene.check_build_tree([tmp_path]) == []
    sdk = tmp_path / "booth" / "boothcore" / "web" / "EdsImage.dll"
    sdk.parent.mkdir(parents=True)
    sdk.write_bytes(b"MZ")
    problems = check_hygiene.check_build_tree([tmp_path])
    assert len(problems) == 1 and "EdsImage.dll" in problems[0]


def test_git_internals_are_not_searched(tmp_path):
    (tmp_path / ".git" / "EDSDK").mkdir(parents=True)
    assert check_hygiene.check_build_tree([tmp_path]) == []


def test_a_program_that_cannot_be_read_fails_instead_of_passing(tmp_path):
    not_a_program = tmp_path / "PhotoboxBooth.exe"
    not_a_program.write_bytes(b"MZ not a PyInstaller archive")
    problems = check_hygiene.check_pyinstaller([not_a_program])
    assert problems and "cannot read" in problems[0]


def test_the_build_mode_does_not_need_git(tmp_path):
    assert check_hygiene.main(["--build-output", str(tmp_path)]) == 0
