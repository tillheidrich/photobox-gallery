"""The Windows version resource of PhotoboxBooth.exe and PhotoboxSync.exe.

Explorer, the Task Manager and "Apps & Features" show what is written here,
and a code signing service checks it: SignPath requires every signed file to
carry the project's name as product name and one product version per build,
and enforces both through the metadata restrictions of its artifact
configuration (docs/CODE-SIGNING.md). The setup (installer/photobox.iss) writes
the same product name, company, copyright and version.

Both PyInstaller specs load this file by path (it is not a package) and pass
the file it writes to EXE(version=...). It runs on any system, so the tests
check the text it produces on a machine without Windows.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Tuple

#: One product for every file of a build. SignPath: "Set all product name
#: attributes to your project's name." What differs per file is the
#: description.
PRODUCT_NAME = "Photobox"
#: The publisher, as packaging/windows/publisher.example.json names it and as
#: the setup shows it (AppPublisher). A test keeps the two equal.
COMPANY_NAME = "Till Heidrich"
LEGAL_COPYRIGHT = f"Copyright (C) {COMPANY_NAME}. AGPL-3.0-or-later"
#: US English, Unicode: the strings below are English.
LANGUAGE = "040904B0"

PROGRAMS = {
    "PhotoboxBooth": "Photobox Booth",
    "PhotoboxSync": "Photobox Sync",
}


def read_version(root: Path) -> str:
    """The version of this build, from VERSION (the same file build.ps1 and
    the setup read; tools/check_hygiene.py keeps the booth's copy equal)."""
    version = (Path(root) / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
    if not re.match(r"^\d+\.\d+\.\d+", version):
        raise ValueError(f"VERSION is empty or unreadable: {version!r}")
    return version


def numeric(version: str) -> Tuple[int, int, int, int]:
    """Four numbers for the fixed part of the resource: 0.9.1 -> (0, 9, 1, 0)."""
    nums = [int(n) for n in re.findall(r"\d+", version)[:4]]
    return tuple((nums + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def strings(program: str, version: str) -> dict:
    """The string table, in the order Explorer lists it."""
    return {
        "CompanyName": COMPANY_NAME,
        "FileDescription": PROGRAMS[program],
        "FileVersion": version,
        "InternalName": program,
        "LegalCopyright": LEGAL_COPYRIGHT,
        "OriginalFilename": f"{program}.exe",
        "ProductName": PRODUCT_NAME,
        "ProductVersion": version,
    }


def render(program: str, version: str) -> str:
    """The resource in PyInstaller's version-file syntax (VSVersionInfo)."""
    four = numeric(version)
    table = ",\n".join(f"          StringStruct({k!r}, {v!r})" for k, v in strings(program, version).items())
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={four}, prodvers={four}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable({LANGUAGE!r}, [
{table}
    ])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
"""


def write(root: Path, program: str) -> str:
    """Writes the resource for one program of this build and returns its path."""
    path = Path(tempfile.mkdtemp(prefix="photobox-version-")) / f"{program}-version.txt"
    path.write_text(render(program, read_version(root)), encoding="utf-8")
    return str(path)
