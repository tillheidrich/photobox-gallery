"""The licences travel with every build, not only with the source.

A .exe or .app that contains the fonts, ffmpeg and a dozen Python packages has
to carry their licence texts and the project's own licence. Checked on the
build plans, because no build runs here.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPECS = ["packaging/windows/photobox-booth.spec", "packaging/windows/photobox-sync.spec",
         "packaging/macos/photobox-booth.spec"]


def _code(spec: str) -> str:
    """Only the lines that do something — the comments may quote what is missing."""
    text = (ROOT / spec).read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.parametrize("spec", SPECS)
def test_every_build_carries_the_licence_files(spec):
    code = _code(spec)
    for needed in ('"LICENSE"', '"LICENSE-EXCEPTION"', '"THIRD_PARTY.md"', '"lizenzen"'):
        assert needed in code, f"{spec} does not pack {needed}"


@pytest.mark.parametrize("spec", SPECS)
def test_every_build_carries_the_licence_files_of_its_packages(spec):
    assert "copy_metadata(" in _code(spec), f"{spec}: the packages' own licence files stay behind"


def test_the_files_the_builds_pack_exist():
    for name in ("LICENSE", "LICENSE-EXCEPTION", "THIRD_PARTY.md"):
        assert (ROOT / name).is_file(), name
    assert (ROOT / "docs" / "lizenzen" / "GPL-2.0.txt").is_file()
