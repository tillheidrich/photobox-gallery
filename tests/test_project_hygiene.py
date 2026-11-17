"""What a stranger needs before they touch the repository.

A README that says what this is and how to start it, a way to report a
vulnerability, a code of conduct, a contribution process that explains the
CLA, a changelog, and a pipeline that checks dependencies and secrets — with
its actions pinned to commits, so a moved tag cannot change what runs.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["README.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md",
                                  "CHANGELOG.md", "CLA.md", "LICENSE", "THIRD_PARTY.md",
                                  ".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/bug_report.yml"])
def test_the_file_a_stranger_looks_for_exists(name):
    assert (ROOT / name).is_file(), name


def test_the_readme_lists_the_screenshots_and_they_exist():
    section = _read("README.md").split("## Screenshots", 1)[1].split("\n## ", 1)[0]
    assert "docs/screenshots/" in section
    assert "tools/marketing_shots.mjs" in section, "say how to take them again"
    named = re.findall(r"^\s*[-*] `([^`]+)`", section, re.M)
    assert len(named) >= 4, "name each picture"
    for name in named:
        assert (ROOT / "docs" / "screenshots" / name).is_file(), name


def test_the_quick_start_installs_the_booth_from_its_own_folder():
    """``-e ../sync`` in booth/requirements.txt is resolved against the working directory."""
    start = _read("README.md").split("## Quick start", 1)[1].split("\n## ", 1)[0]
    assert "pip install -r booth/requirements.txt" not in start
    assert "cd booth && pip install -r requirements.txt" in start


def test_agents_md_installs_the_booth_from_its_own_folder():
    text = _read("AGENTS.md")
    assert "pip install -r requirements.txt booth/requirements.txt" not in text


def test_security_names_a_reporting_path_and_the_supported_versions():
    text = _read("SECURITY.md")
    section = text.split("## Reporting a vulnerability", 1)[1].split("\n## ", 1)[0]
    # A private way to report: an address, or GitHub's private vulnerability
    # reporting (final review V2 took the owner's private address out).
    assert "@" in section or "Report a vulnerability" in section
    assert "## Supported versions" in text


def test_contributing_explains_the_tests_and_the_cla():
    text = _read("CONTRIBUTING.md")
    for needed in ("python -m pytest tests -q", "tools/rehearsal.py", "tools/check_hygiene.py",
                   "I have read CLA.md and I agree to it."):
        assert needed in text, needed


def test_the_changelog_has_an_unreleased_entry():
    assert "## Unreleased" in _read("CHANGELOG.md")


def test_first_issues_offers_no_work_that_is_already_done():
    text = _read("docs/FIRST_ISSUES.md")
    headings = [h.lower() for h in re.findall(r"^### (.+)$", text, re.M)]
    for done in ("mirror mode", "gallery texts editable per event", "photobox doctor"):
        assert not any(done in h for h in headings), done


# ── The pipeline ─────────────────────────────────────────────────────────

def test_ci_audits_the_dependencies_and_scans_for_secrets():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pip-audit" in text and "gitleaks/gitleaks-action@" in text
    assert (ROOT / ".gitleaks.toml").is_file()


def test_every_action_is_pinned_to_a_commit():
    uses = re.findall(r"uses:\s*(\S+)(.*)", WORKFLOW.read_text(encoding="utf-8"))
    assert uses
    for ref, comment in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert re.search(r"#\s*v\d", comment), f"{ref}: name the release in a comment"


def test_ci_installs_the_booth_from_its_own_folder():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pip install -r booth/requirements.txt" not in text


def test_the_ci_tools_are_not_project_dependencies():
    for name in ("requirements.txt", "requirements-dev.txt", "booth/requirements.txt", "sync/requirements.txt"):
        text = _read(name).lower()
        assert "pip-audit" not in text and "gitleaks" not in text, name
