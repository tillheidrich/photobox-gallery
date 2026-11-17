"""Everything someone else wrote is named, with its licence, and travels along.

The fonts are under the OFL, which wants its text next to the fonts; the
booth builds contain ffmpeg, which is GPL; every Python package has its own
licence. THIRD_PARTY.md lists them, the font folders point at it, and the
gallery image keeps it.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENT_FILES = ["requirements.txt", "requirements-dev.txt", "booth/requirements.txt",
                     "sync/requirements.txt"]
FONT_DIRS = ["app/static/fonts", "booth/boothcore/media/fonts", "booth/boothcore/web/static/fonts"]


def _third_party() -> str:
    return (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")


def _requirement_names():
    for name in REQUIREMENT_FILES:
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            yield re.split(r"[\[<>=;! ]", line, maxsplit=1)[0]


@pytest.mark.parametrize("package", sorted(set(_requirement_names()), key=str.lower))
def test_every_required_package_is_listed_with_its_licence(package):
    rows = [line for line in _third_party().splitlines()
            if line.lower().startswith(f"| {package.lower()} |")]
    assert rows, f"{package} is missing from THIRD_PARTY.md"
    assert rows[0].count("|") >= 4, f"{package}: version and licence belong in the row"


@pytest.mark.parametrize("folder", FONT_DIRS)
def test_every_font_folder_points_at_its_licence(folder):
    pointer = ROOT / folder / "LICENSES.txt"
    assert pointer.is_file(), f"{folder} has no LICENSES.txt"
    text = pointer.read_text(encoding="utf-8")
    assert "SIL Open Font License" in text and "THIRD_PARTY.md" in text
    for font in (ROOT / folder).rglob("*"):
        if font.suffix.lower() in (".ttf", ".otf", ".woff", ".woff2"):
            family = font.stem.split("-")[0].lower()
            assert family in text.lower(), f"{font.name} is not named in {pointer}"
            licence = {"inter": "OFL-Inter.txt", "archivo": "OFL-Archivo.txt",
                       "jetbrains": "OFL-JetBrains-Mono.txt"}[family]
            assert (ROOT / "docs" / "lizenzen" / licence).is_file()


def test_ffmpeg_is_named_with_its_licence_and_its_source():
    text = _third_party()
    section = text.split("## ffmpeg", 1)[1].split("\n## ", 1)[0]
    assert "GPL" in section and "docs/lizenzen/GPL-2.0.txt" in section
    assert "ffmpeg.org" in section, "where the source is"
    licence = (ROOT / "docs" / "lizenzen" / "GPL-2.0.txt").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in licence and "Version 2, June 1991" in licence


def test_the_canon_sdk_is_named_as_never_included():
    assert "EDSDK" in _third_party() and "Not included, ever" in _third_party()


def test_design_md_no_longer_lists_the_missing_font_licences_as_open():
    text = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    assert "there is no `OFL.txt` beside them" not in text
