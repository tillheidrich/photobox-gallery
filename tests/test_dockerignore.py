"""The gallery image carries the gallery, not the whole repository.

``Dockerfile`` copies the build context with ``COPY . .``. Without a
``.dockerignore`` that put the booth, the sync program, all three test suites,
the internal business documents, the website mock-up, the build scripts and
any local ``data/`` folder (a real SQLite file with guests' photos) into every
gallery image. What the gallery actually needs at runtime is ``app/``,
``run.py``, ``VERSION`` and ``requirements.txt`` — and the font licences,
because the image redistributes the fonts.

The matcher below covers the subset of the ``.dockerignore`` syntax the file
uses: one path per line, a trailing ``/**`` or nothing, and ``!`` exceptions,
evaluated top to bottom with the last match winning, as Docker does.
"""
import fnmatch
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
IGNORE = ROOT / ".dockerignore"


def _rules():
    rules = []
    for line in IGNORE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        rules.append((negate, line.lstrip("!").rstrip("/")))
    return rules


def _excluded(path: str) -> bool:
    excluded = False
    for negate, pattern in _rules():
        # A pattern matches the path itself or anything below it.
        hit = any(fnmatch.fnmatch(prefix, pattern)
                  for prefix in ("/".join(path.split("/")[:i + 1]) for i in range(path.count("/") + 1)))
        if hit:
            excluded = not negate
    return excluded


def test_there_is_a_dockerignore():
    assert IGNORE.is_file()


@pytest.mark.parametrize("path", [
    "booth/boothcore/app.py", "sync/photobox_sync/agent.py", "tests/test_hygiene_check.py",
    "docs/intern/anything.md", "website/mockup-2026.html", "packaging/windows/build.ps1",
    "tools/rehearsal.py", "windows_uploader/uploader.py", "data/photobox.sqlite3",
    ".env", ".venv/bin/python", "app/__pycache__/db.cpython-312.pyc", ".github/workflows/tests.yml",
])
def test_what_the_gallery_never_reads_stays_out(path):
    assert _excluded(path), path


@pytest.mark.parametrize("path", [
    "app/__init__.py", "app/static/data/collage_templates.json", "app/templates/admin/analytics.html",
    "run.py", "VERSION", "requirements.txt", "LICENSE", "docs/lizenzen/OFL-Inter.txt",
    # The image redistributes the fonts and the Python packages: the list of
    # third-party licences and the AGPL permission travel with it.
    "THIRD_PARTY.md", "LICENSE-EXCEPTION", "docs/lizenzen/GPL-2.0.txt",
    # The image installs with the tested versions.
    "constraints.txt",
    # The software page reads the commit from .git/HEAD when no build variable names it.
    ".git/HEAD",
])
def test_what_the_gallery_needs_at_runtime_stays_in(path):
    assert not _excluded(path), path


def test_the_gallery_code_does_not_import_the_booth_or_the_sync_program():
    """If this ever changes, the exclusions above have to change with it."""
    imports = re.compile(r"^\s*(from|import)\s+(boothcore|photobox_sync)\b", re.M)
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "app").rglob("*.py")
                 if imports.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
