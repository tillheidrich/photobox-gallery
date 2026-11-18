"""The code signing policy says what SignPath requires, and only what is true.

SignPath's terms (signpath.org/terms) ask for a "Code signing policy" on the
home and download pages with one attribution sentence word for word, the team
roles with their members, a privacy statement, and uninstall instructions.
The repository's side: README section, docs/CODE-SIGNING-POLICY.md and
docs/CODE-SIGNING.md for the owner's settings. These tests keep them in step
with each other, with the workflow and with the code they describe.
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = (ROOT / "docs" / "CODE-SIGNING-POLICY.md").read_text(encoding="utf-8")
SETUP_DOC = (ROOT / "docs" / "CODE-SIGNING.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")

ATTRIBUTION = ("Free code signing provided by [SignPath.io](https://about.signpath.io), "
               "certificate by [SignPath Foundation](https://signpath.org)")
PRIVACY_SENTENCE = ("This program will not transfer any information to other networked systems "
                    "unless specifically requested by the user or the person installing or operating it.")
GITHUB = "[@tillheidrich](https://github.com/tillheidrich)"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = text.find("\n## ", start + len(heading))
    return text[start:nxt if nxt > 0 else len(text)]


def test_the_policy_carries_signpaths_attribution_word_for_word():
    assert POLICY.startswith("# Code signing policy\n")
    assert ATTRIBUTION in POLICY
    # The README names the same links in its own "Code signing policy" section.
    readme = _flat(_section(README, "## Code signing policy"))
    assert "[SignPath.io](https://about.signpath.io)" in readme
    assert "[SignPath Foundation](https://signpath.org)" in readme


def test_the_readme_section_links_the_policy_in_the_repository_and_on_the_site():
    readme = _section(README, "## Code signing policy")
    assert "(docs/CODE-SIGNING-POLICY.md)" in readme
    assert "https://photobox-gallery.com/en/code-signing-policy/" in readme
    assert "https://photobox-gallery.com/code-signing-policy/" in readme


def test_every_role_names_its_member():
    roles = _section(POLICY, "## Team roles")
    for role in ("**Authors (committers):**", "**Reviewers:**", "**Approvers:**"):
        line = roles[roles.index(role):].split("\n* ", 1)[0]
        assert "Till Heidrich" in line and GITHUB in line, role
    assert "multi-factor authentication" in _flat(roles)


def test_the_status_is_told_the_same_way_in_readme_and_policy():
    # Not active now; when signing starts, both places change together.
    policy, readme = _flat(POLICY).lower(), _flat(_section(README, "## Code signing policy")).lower()
    assert ("not active yet" in policy) == ("not active yet" in readme)
    if "not active yet" in policy:
        assert "unsigned" in policy and "unsigned" in readme
        status = _flat(_section(POLICY, "## Status")).lower()
        assert not re.search(r"\b(is|are) (now )?signed\b|signing is active", status + readme)


def test_the_privacy_part_uses_signpaths_sentence_and_points_at_the_details():
    privacy = _flat(_section(POLICY, "## Privacy"))
    assert PRIVACY_SENTENCE in privacy
    assert "(PRIVACY-BOOTH.md)" in privacy and "tools/outbound_connections.py" in privacy
    assert "https://photobox-gallery.com/en/privacy/" in privacy
    assert "https://photobox-gallery.com/datenschutz/" in privacy
    # What it says is what the connection list says.
    spec = importlib.util.spec_from_file_location("oc", ROOT / "tools" / "outbound_connections.py")
    oc = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = oc  # its dataclasses look themselves up there
    spec.loader.exec_module(oc)
    assert "no telemetry" in oc.NEVER_EN and "No telemetry" in privacy
    assert "no update check of its own without a paired gallery" in oc.NEVER_EN
    by_id = oc.by_id()
    for cid in ("mail_smtp", "webhooks", "targets", "remote_control", "guest_wifi", "printer_ipp", "camera_wifi"):
        assert by_id[cid].off_by_default, cid
    assert "photobox-gallery.com" in oc.TARGET_EN["pairing"] and "photobox-gallery.com" in privacy


def test_the_setup_shows_the_privacy_page_and_the_webview2_task_can_be_unticked():
    iss = (ROOT / "packaging" / "windows" / "installer" / "photobox.iss").read_text(encoding="utf-8-sig")
    assert 'InfoBeforeFile: "{#BuildDir}\\privacy-en.txt"' in iss
    task = next(l for l in iss.splitlines() if l.startswith('Name: "webview2"'))
    assert "Check: WebView2Missing" in task  # a task in the wizard, so it can be unticked
    assert "WebView2" in _section(POLICY, "## Privacy")


def test_the_uninstall_part_matches_the_programs():
    part = _flat(_section(POLICY, "## Uninstalling"))
    assert "%LOCALAPPDATA%\\PhotoboxBooth" in part and "%LOCALAPPDATA%\\PhotoboxSync" in part
    assert '"--uninstall"' in (ROOT / "booth" / "boothcore" / "__main__.py").read_text(encoding="utf-8")
    assert '"--uninstall"' in (ROOT / "sync" / "photobox_sync" / "__main__.py").read_text(encoding="utf-8")
    assert '/ "PhotoboxSync"' in (ROOT / "sync" / "photobox_sync" / "standalone.py").read_text(encoding="utf-8")
    assert '/ "PhotoboxBooth"' in (ROOT / "booth" / "boothcore" / "config.py").read_text(encoding="utf-8")
    iss = (ROOT / "packaging" / "windows" / "installer" / "photobox.iss").read_text(encoding="utf-8-sig")
    assert "{uninstallexe}" in iss and "Exec(Exe, '--uninstall'" in iss


def test_the_owner_doc_names_exactly_the_settings_the_workflow_reads():
    read = set(re.findall(r"(?:vars|secrets)\.(SIGNPATH_[A-Z_]+)", WORKFLOW))
    documented = set(re.findall(r"`(SIGNPATH_[A-Z_]+)`", _section(SETUP_DOC, "## Settings the workflow reads")))
    assert read == documented and "SIGNPATH_API_TOKEN" in read


def test_the_owner_doc_declares_the_parameter_the_workflow_sends():
    assert WORKFLOW.count("version: ${{ toJSON(needs.build.outputs.version) }}") == 2
    assert SETUP_DOC.count('<parameter name="version" required="true" />') == 2
    assert SETUP_DOC.count('product-name="Photobox"') == 2


def test_the_canon_edition_is_never_said_to_be_signed():
    text = _flat(_section(POLICY, "## What is signed, and what never is"))
    assert "Never signed through SignPath:" in text and "EDSDK" in text
