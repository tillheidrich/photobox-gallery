"""CI installs every package the three test suites import.

The 0.9.1 snapshot went red on GitHub Actions although all three suites were
green on the reference machine: CI installed only each suite's own part, but
the gallery tests import httpx (the booth's pairing client), the booth tests
import the gallery app (Flask) and the sync tests start a local FTP server
(pyftpdlib, a gallery dependency). Locally everything was installed, so
nothing showed the gap. These tests read the test files and the workflow and
fail before a push would.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
SUITES = [ROOT / "tests", ROOT / "booth" / "tests", ROOT / "sync" / "tests"]
INSTALLED_BY_CI = [ROOT / "requirements.txt", ROOT / "booth" / "requirements.txt",
                   ROOT / "sync" / "requirements.txt", ROOT / "requirements-dev.txt"]

# Import name -> distribution name, where they differ.
DISTRIBUTION = {"PIL": "pillow", "cv2": "opencv-python-headless", "nacl": "pynacl",
                "OpenSSL": "pyopenssl", "multipart": "python-multipart",
                "imageio_ffmpeg": "imageio-ffmpeg", "webview": "pywebview"}

# Imported by tests but arriving with a package that is named. Say which.
COMES_WITH = {"cryptography": "paramiko and pyOpenSSL",
              "starlette": "fastapi",
              "jinja2": "Flask",
              "pip": "every Python installation"}

# Tests that need these skip with a reason (pytest.importorskip): a CI runner
# has no browser download for them.
OPTIONAL = {"playwright": "browser tests, need a downloaded browser"}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def _local_names():
    names = set()
    for path in ROOT.rglob("*"):
        if SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            continue
        if path.suffix == ".py":
            names.add(path.stem)
        elif path.is_dir():
            names.add(path.name)
    return names


def _imports():
    found = {}
    for suite in SUITES:
        for path in suite.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    tops = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    tops = [node.module.split(".")[0]]
                else:
                    continue
                for top in tops:
                    found.setdefault(top, path.relative_to(ROOT).as_posix())
    return found


def _requirement_names():
    names = set()
    for path in INSTALLED_BY_CI:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            name = re.split(r"[\s<>=!~;\[]", line, maxsplit=1)[0]
            names.add(name.lower().replace("_", "-"))
    return names


def test_every_package_the_tests_import_is_installed_by_ci():
    local = _local_names()
    required = _requirement_names()
    missing = []
    for top, where in sorted(_imports().items()):
        if top in sys.stdlib_module_names or top == "__future__" or top in local:
            continue
        if top in COMES_WITH or top in OPTIONAL:
            continue
        dist = DISTRIBUTION.get(top, top).lower().replace("_", "-")
        if dist not in required:
            missing.append(f"{top} (first imported in {where})")
    assert not missing, "not in any requirements file CI installs: " + ", ".join(missing)


def _suite_install_step() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("      - name: Install\n")
    end = text.index("      - name: Run tests", start)
    return text[start:end]


def test_every_suite_installs_all_three_parts_and_the_test_packages():
    step = _suite_install_step()
    assert "working-directory" not in step, "the install step must run from the repository root"
    assert "if " not in step.split("run: |", 1)[1], "no suite may skip part of the install"
    for line in ("pip install -r requirements.txt -c constraints.txt",
                 "(cd booth && pip install -r requirements.txt -c ../constraints.txt)",
                 "pip install -r requirements-dev.txt -c constraints.txt"):
        assert line in step, line


def test_the_audit_sees_the_test_packages_too():
    text = WORKFLOW.read_text(encoding="utf-8")
    audit = text[text.index("  audit:"):text.index("  secrets:")]
    assert "requirements-dev.txt" in audit
