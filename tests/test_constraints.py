"""The versions the tests passed with are written down, and every install uses them.

The requirements files say `>=`: a floor, not a version. A fresh install six
months later gets whatever is newest, and a build for a wedding next week
should not be the first run of a new Pillow. constraints.txt pins the exact
versions the suites last passed with; the requirements files keep their open
floors for people who want the newest on purpose.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ["requirements.txt", "requirements-dev.txt", "booth/requirements.txt", "sync/requirements.txt"]
WINDOWS_ONLY = {"pywin32"}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins() -> dict:
    pins = {}
    for line in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][0-9A-Za-z.+!-]*)", line)
        assert m, f"not an exact pin: {line!r}"
        pins[_norm(m.group(1))] = m.group(2)
    return pins


def _required():
    for name in REQUIREMENTS:
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                package = re.split(r"[\[<>=;! ]", line, maxsplit=1)[0]
                if _norm(package) not in WINDOWS_ONLY:     # left open on purpose, see below
                    yield name, package


@pytest.mark.parametrize("source,package", sorted(set(_required())))
def test_every_required_package_is_pinned(source, package):
    assert _norm(package) in _pins(), f"{package} ({source}) has no pin in constraints.txt"


def test_windows_only_packages_are_named_not_pinned():
    text = (ROOT / "constraints.txt").read_text(encoding="utf-8")
    for name in WINDOWS_ONLY:
        assert name not in _pins()
        assert re.search(rf"^#.*\b{name}\b", text, re.M), f"say why {name} is not pinned"


def test_the_requirements_keep_their_open_floors():
    for name in ("requirements.txt", "booth/requirements.txt", "sync/requirements.txt"):
        lines = [l.split(";", 1)[0].split("#", 1)[0] for l in (ROOT / name).read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.lstrip().startswith(("#", "-"))]
        assert lines and all("==" not in l for l in lines), name
        assert any(">=" in l for l in lines), name


def test_every_documented_install_uses_the_constraints():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").split("## Quick start", 1)[1].split("\n## ", 1)[0]
    assert "pip install -r requirements.txt -c constraints.txt" in readme
    assert "pip install -r requirements.txt -c ../constraints.txt" in readme
    build_ps1 = (ROOT / "packaging" / "windows" / "build.ps1").read_text(encoding="utf-8-sig")
    code = "\n".join(l for l in build_ps1.splitlines() if not l.lstrip().startswith("#"))
    for line in [l for l in code.splitlines() if "pip install -r" in l]:
        assert "-c " in line and "constraints.txt" in code, line
    build_sh = (ROOT / "packaging" / "macos" / "build.sh").read_text(encoding="utf-8")
    for line in [l for l in build_sh.splitlines() if "pip install" in l and "-r " in l]:
        assert "constraints.txt" in line, line
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    for line in [l for l in workflow.splitlines() if "pip install -r" in l and "requirements-dev" not in l]:
        assert "constraints.txt" in line, line


def test_the_mac_build_installs_the_booth_from_its_own_folder():
    """`-e ../sync` is resolved against the working directory, as on Windows."""
    build_sh = (ROOT / "packaging" / "macos" / "build.sh").read_text(encoding="utf-8")
    assert 'pip install --quiet -r "$ROOT/booth/requirements.txt"' not in build_sh
    assert 'cd "$ROOT/booth"' in build_sh
