#!/usr/bin/env python3
"""Three checks that review keeps finding by hand. Run it before you push.

    python tools/check_hygiene.py

1. No vendor binaries in the tree. Our own ctypes wrapper for the Canon EDSDK is
   fine — the DLLs from Canon are not, they are under NDA. No package archives
   either (wheels, eggs, sdists): the builds install their tools from pip, so a
   wheel in the tree is a leftover from a build, never an input.
2. No credentials. A long value that looks like a token next to a key-ish name.
   Translation files and the legacy docs are exempt; they talk *about*
   passwords, they do not contain any.
3. One version for the whole ecosystem: VERSION and the booth agree.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Binaries that may never be committed, whatever the reason.
FORBIDDEN_BINARIES = re.compile(r"(?i)(edsdk|edsimage).*\.(dll|lib|dylib|so|framework)$|\.(dll|lib|dylib)$")
# Python package archives, wherever they sit. Build leftovers, not sources.
FORBIDDEN_ARCHIVES = re.compile(r"(?i)\.(whl|egg|tar\.gz|tgz)$")

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


def check_version() -> list[str]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
    booth = (ROOT / "booth" / "boothcore" / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in booth:
        return [f"booth/boothcore/__init__.py disagrees with VERSION ({version})"]
    return []


def main() -> int:
    files = tracked_files()
    problems = check_binaries(files) + check_secrets(files) + check_version()
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"ok — {len(files)} files, version {(ROOT / 'VERSION').read_text().strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
