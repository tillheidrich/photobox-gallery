"""The edition with camera SDK: Canon's runtime libraries shipped with the program.

There are two Windows editions, both free of charge:

* the free edition, built from this repository alone (and in CI), which
  contains no file of Canon's SDK - the operator brings the SDK
  (``--install-edsdk``) or uses a webcam or a Wi-Fi camera;
* the edition with camera SDK, offered on the project website, built by the
  maintainer from a folder outside the repository
  (``packaging/windows/build.ps1 -WithCameraSdk <folder>``). It carries the
  Canon EOS Digital SDK runtime libraries, unmodified, in a folder ``EDSDK``
  next to the program, together with ``CAMERA-SDK-LICENCE.txt``. It is
  published as a setup only; the build still makes a portable .exe with the
  same files inside, for its own check, which is not offered.

This module knows how to recognise the second edition at run time and holds
the end-user terms that come with it. It is standard library only: the build
loads it by file path to write the licence file and the setup page, before
any of the booth's dependencies exist. Nothing here is Canon's text; the
terms are the project's own neutral summary of what an end user has to
accept for those files.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

#: The folder the booth already searches next to the program (camera/edsdk.py).
SDK_FOLDER = "EDSDK"
#: The libraries the booth needs at run time; the build copies these and
#: whatever else the SDK's Dll folder holds for them.
RUNTIME_LIBRARIES = ("EDSDK.dll", "EdsImage.dll")
#: Next to the program in the setup, inside the program in the portable .exe.
#: Its presence is what marks a bundle: an EDSDK folder an operator filled by
#: hand is not "bundled" and is not listed as such.
LICENCE_FILE = "CAMERA-SDK-LICENCE.txt"
#: Inno Setup leaves unins000.dat (unins001.dat, ...) in {app}. Next to the
#: program it tells the setup's installation apart from a folder where
#: someone happened to put an EDSDK folder and a copy of the terms.
SETUP_MARKER = "unins*.dat"

TERMS_DE = """\
Kamera-SDK in dieser Fassung von Photobox
=========================================

Diese Fassung von Photobox ("mit Kamera-SDK") enthält zusätzlich die
Laufzeitbibliotheken des Canon EOS Digital SDK (EDSDK.dll, EdsImage.dll und
die Dateien, die diese beiden brauchen). Für diese Dateien gilt:

1. Sie sind urheberrechtlich geschützte und vertrauliche Dateien von Canon
   und werden nach den Bedingungen von Canon lizenziert - nicht nach der
   Lizenz von Photobox.
2. Sie dürfen nur zusammen mit dieser Software verwendet werden.
3. Sie dürfen nicht disassembliert, zurückentwickelt (Reverse Engineering)
   oder verändert werden, außer soweit das Gesetz es ausdrücklich erlaubt.
4. Sie dürfen nicht so weitergegeben werden, dass die Eigentumsrechte von
   Canon an ihnen beeinträchtigt werden - insbesondere nicht unter einer
   Open-Source-Lizenz mit Copyleft.

Die Dateien liegen unverändert und nur als Objektcode bei. Sie dienen
ausschließlich dazu, Kameras von Canon mit Photobox zu verwenden.

Diese Dateien stehen NICHT unter der GNU Affero General Public License
(AGPL). Die AGPL gilt für Photobox selbst; die Zusatzerlaubnis in
LICENSE-EXCEPTION.txt regelt, dass Photobox mit ihnen zusammen genutzt und
weitergegeben werden darf.

Wer diese Bedingungen nicht annehmen möchte, verwendet die freie Fassung von
Photobox ohne Kamera-SDK.
"""

TERMS_EN = """\
Camera SDK in this edition of Photobox
======================================

This edition of Photobox ("with camera SDK") additionally contains the
runtime libraries of the Canon EOS Digital SDK (EDSDK.dll, EdsImage.dll and
the files these two need). For these files:

1. They are proprietary and confidential files of Canon and are licensed
   under Canon's terms - not under the licence of Photobox.
2. They may only be used together with this software.
3. They must not be disassembled, reverse engineered or modified, except
   where the law expressly permits it.
4. They must not be distributed in a way that affects Canon's ownership of
   them - in particular not under an open-source licence with copyleft.

The files are included unmodified and in object code only. They serve
solely to use Canon cameras with Photobox.

These files are NOT under the GNU Affero General Public License (AGPL). The
AGPL applies to Photobox itself; the additional permission in
LICENSE-EXCEPTION.txt allows Photobox to be used and conveyed together with
them.

If you do not want to accept these terms, use the free edition of Photobox
without the camera SDK.
"""


def terms(lang: str = "de") -> str:
    return TERMS_EN if str(lang).lower().startswith("en") else TERMS_DE


def licence_text() -> str:
    """The file next to the program: German first, then English."""
    return TERMS_DE + "\n" + "-" * 76 + "\n\n" + TERMS_EN


def write_licence_file(path: Path) -> Path:
    """UTF-8 with BOM and CRLF, so Notepad and Inno Setup show it as it is."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(licence_text().replace("\r\n", "\n"), encoding="utf-8-sig", newline="\r\n")
    return path


def _program_dir() -> Optional[Path]:
    return Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else None


def bundle_roots() -> List[Path]:
    """Where a bundle can sit, and only there: inside the frozen program
    (PyInstaller's unpack folder), or the folder the setup installed into
    ({app}, recognised by the setup's uninstaller data). Any other folder
    next to some .exe is not the edition with camera SDK, whatever lies in it."""
    roots: List[Path] = []
    inside = getattr(sys, "_MEIPASS", None)
    if inside:
        roots.append(Path(inside))
    here = _program_dir()
    if here is not None and any(here.glob(SETUP_MARKER)):
        roots.append(here)
    return roots


def find(roots: Optional[Iterable[Path]] = None) -> Optional[Path]:
    """The root holding a bundled SDK (``EDSDK/EDSDK.dll`` and the licence
    file side by side), or None for the free edition."""
    for root in (bundle_roots() if roots is None else roots):
        root = Path(root)
        if (root / SDK_FOLDER / RUNTIME_LIBRARIES[0]).is_file() and (root / LICENCE_FILE).is_file():
            return root
    return None


def sdk_dir(roots: Optional[Iterable[Path]] = None) -> Optional[Path]:
    root = find(roots)
    return root / SDK_FOLDER if root else None


def component(roots: Optional[Iterable[Path]] = None) -> Optional[Dict[str, object]]:
    """The entry for the admin's About card, or None in the free edition."""
    root = find(roots)
    if root is None:
        return None
    folder = root / SDK_FOLDER
    files = sorted(p.name for p in folder.iterdir() if p.is_file())
    return {"name": "Canon EOS Digital SDK (runtime libraries)",
            "files": files,
            "licence": "Proprietary (Canon) - not under the AGPL",
            "terms_url": "/static/legal/" + LICENCE_FILE}


def acknowledge_once(data_dir: Path, out=None) -> bool:
    """Show the terms once on the first start of a build that carries the
    SDK, and keep a copy where the operator finds it.

    A program without a setup page to accept them on prints them into the
    console it has anyway at its first start, writes CAMERA-SDK-LICENCE.txt
    into the booth's data folder and remembers there that it did. Never next
    to the program: a licence file there is the setup's, and a copy written by
    the program would make that folder look like an installation.
    Returns True when it showed them now."""
    root = find()
    if root is None:
        return False
    marker = Path(data_dir) / "camera-sdk-terms-shown"
    if marker.exists():
        return False
    out = out or sys.stdout
    try:
        print(licence_text(), file=out)
    except Exception:  # noqa: BLE001 - a console that cannot print must not stop the booth
        pass
    target = Path(data_dir) / LICENCE_FILE
    if not target.exists():
        try:
            write_licence_file(target)
        except OSError:
            pass
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("shown\n", encoding="ascii")
    except OSError:
        pass
    return True


if __name__ == "__main__":  # used by the Windows build: write the licence file
    if len(sys.argv) != 2:
        raise SystemExit("usage: camera_sdk_bundle.py <output file>")
    print(write_licence_file(Path(sys.argv[1])))
