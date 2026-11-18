#!/usr/bin/env python3
"""Three checks that review keeps finding by hand. Run it before you push.

    python tools/check_hygiene.py

1. No vendor binaries in the tree. Our own ctypes wrapper for the Canon EDSDK is
   fine — the DLLs from Canon are not, they are Canon's proprietary files. No package archives
   either (wheels, eggs, sdists): the builds install their tools from pip, so a
   wheel in the tree is a leftover from a build, never an input.
2. No credentials. A long value that looks like a token next to a key-ish name.
   Translation files and the legacy docs are exempt; they talk *about*
   passwords, they do not contain any.
3. One version for the whole ecosystem: VERSION and the booth agree.
4. No third-party service named as if it were part of the product: the
   booth's second upload target is "an online gallery via FTP/FTPS/SFTP", not
   a brand. The only allowed form is the quoted legacy identifier under which
   existing booths store that target; the CHANGELOG below 0.9.2 and
   docs/legacy/ are history and stay as written.

After a Windows build the same guard checks what the build produced, because
only the free edition may ever be built and signed in CI:

    python tools/check_hygiene.py --build-output . \\
        --pyinstaller packaging/windows/dist/PhotoboxBooth.exe packaging/windows/dist/PhotoboxSync.exe

fails when a file of Canon's SDK lies anywhere in the build tree or sits
inside one of the PyInstaller programs (their table of contents and the
embedded Python archive are read without running anything). It needs
PyInstaller importable for --pyinstaller, which the build environment has.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parent.parent

# Binaries that may never be committed, whatever the reason.
FORBIDDEN_BINARIES = re.compile(r"(?i)(edsdk|edsimage).*\.(dll|lib|dylib|so|framework)$|\.(dll|lib|dylib)$")
# Python package archives, wherever they sit. Build leftovers, not sources.
FORBIDDEN_ARCHIVES = re.compile(r"(?i)\.(whl|egg|tar\.gz|tgz)$")
# Canon's SDK in a build: the libraries, the framework and the headers, by the
# names Canon gives them, and the folder "--install-edsdk" fills. The project's
# own wrapper (boothcore/camera/edsdk.py, edsdk_setup.py) is Python source and
# never matches: no Canon file ends in .py.
VENDOR_SDK_PART = re.compile(r"(?i)^(lib)?(edsdk|edsimage)[^.]*\.(dll|lib|dylib|so|h|framework|bundle)$|^edsdk$")

# A key-ish name, then a long value without spaces that has both letters and digits.
SECRET = re.compile(
    r"""(?ix)
    (api[_-]?key|secret|password|passwd|token|credential)     # was es zu sein behauptet
    ["']?\s*[:=]\s*                                           # Zuweisung
    ["']([A-Za-z0-9_\-./+]{16,})["']                          # und ein Wert ohne Leerzeichen
    """
)
PLACEHOLDER = re.compile(r"(?i)your_|example|placeholder|change-me|paste_|dummy|xxx|\.\.\.|•")
SKIP_PATHS = ("docs/legacy/", "app/i18n/", "boothcore/web/static/i18n.js", "tests/", "/tests/")
SKIP_SUFFIX = (".po", ".pot", ".lock")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def check_binaries(files: list[str]) -> list[str]:
    return ([f"vendor binary committed: {f}" for f in files if FORBIDDEN_BINARIES.search(f)]
            + [f"package archive committed (install it from pip instead): {f}"
               for f in files if FORBIDDEN_ARCHIVES.search(f)])


def check_secrets(files: list[str]) -> list[str]:
    problems = []
    for name in files:
        if any(s in name for s in SKIP_PATHS) or name.endswith(SKIP_SUFFIX):
            continue
        path = ROOT / name
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, IsADirectoryError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            match = SECRET.search(line)
            if match and not PLACEHOLDER.search(match.group(2)):
                problems.append(f"looks like a credential: {name}:{number}")
    return problems


# Spelled in two parts so this file does not trip its own check.
SERVICE_NAME = re.compile("(?i)" + "pic" + "drop")
#: The identifier existing booths store their second target under (local
#: "targets"); renaming it would orphan their settings.
LEGACY_IDENTIFIER = re.compile("[\"'`]" + "pic" + "drop" + "[\"'`]")
NAME_EXEMPT_PREFIXES = ("docs/legacy/",)


def check_service_name(files: list[str]) -> list[str]:
    problems = []
    for name in files:
        if name.startswith(NAME_EXEMPT_PREFIXES):
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, IsADirectoryError):
            continue
        if name == "CHANGELOG.md":
            cut = text.find("\n## 0.9.1")
            text = text if cut < 0 else text[:cut]
        for number, line in enumerate(text.splitlines(), 1):
            if SERVICE_NAME.search(LEGACY_IDENTIFIER.sub("", line)):
                problems.append(f"service name in a public text (say \"online gallery via FTP\"): {name}:{number}")
    return problems


def check_version() -> list[str]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
    booth = (ROOT / "booth" / "boothcore" / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in booth:
        return [f"booth/boothcore/__init__.py disagrees with VERSION ({version})"]
    return []


def is_vendor_sdk_path(name: str) -> bool:
    """True when any part of the path is a Canon SDK file or the EDSDK folder.

    Separators of both kinds: PyInstaller writes the archive's table of
    contents with the separator of the machine that built it."""
    return any(VENDOR_SDK_PART.search(part) for part in re.split(r"[\\/]", name) if part)


def check_build_tree(roots: Iterable[Path]) -> List[str]:
    """Every file and folder below the roots, by name, .git left out."""
    problems = []
    for root in roots:
        root = Path(root)
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in list(dirs) + files:
                rel = os.path.relpath(os.path.join(base, name), root)
                if is_vendor_sdk_path(rel):
                    problems.append(f"Canon SDK file in the build tree: {root / rel}")
    return problems


def pyinstaller_entries(path: Path) -> List[str]:
    """Names inside a PyInstaller program: its table of contents and the
    modules of every embedded Python archive. Read only, nothing is run."""
    from PyInstaller.archive.readers import PKG_ITEM_PYZ, CArchiveReader

    archive = CArchiveReader(str(path))
    names = list(archive.toc)
    for name, entry in archive.toc.items():
        if entry[-1] == PKG_ITEM_PYZ:
            names += [f"{name}/{module}" for module in archive.open_embedded_archive(name).toc]
    return names


def check_pyinstaller(paths: Iterable[Path]) -> List[str]:
    """A program that cannot be read counts as a failure: a guard that skips
    what it cannot open would pass every broken build."""
    problems = []
    for path in paths:
        try:
            names = pyinstaller_entries(Path(path))
        except Exception as exc:  # noqa: BLE001 - any failure to read must stop the build
            problems.append(f"cannot read {path} as a PyInstaller program: {exc}")
            continue
        if not names:
            problems.append(f"{path}: empty table of contents")
        problems += [f"Canon SDK file inside {path}: {n}" for n in names if is_vendor_sdk_path(n)]
    return problems


def check_build(build_output: List[str], programs: List[str]) -> int:
    problems = check_build_tree(Path(p) for p in build_output) + check_pyinstaller(Path(p) for p in programs)
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"ok — no Canon SDK file in {', '.join(build_output) or '-'}; "
          f"{len(programs)} PyInstaller program(s) read")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build-output", nargs="+", default=[], metavar="DIR",
                    help="after a build: folders to search for Canon SDK files")
    ap.add_argument("--pyinstaller", nargs="+", default=[], metavar="EXE",
                    help="after a build: PyInstaller programs to open and search")
    args = ap.parse_args(argv)
    if args.build_output or args.pyinstaller:
        return check_build(args.build_output, args.pyinstaller)

    files = tracked_files()
    problems = check_binaries(files) + check_secrets(files) + check_version() + check_service_name(files)
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"ok — {len(files)} files, version {(ROOT / 'VERSION').read_text().strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
