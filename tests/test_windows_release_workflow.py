"""The Windows release workflow, read as text (there is no Windows runner here).

.github/workflows/windows-release.yml builds the Windows files of a release
on GitHub-hosted runners and, once the project is accepted by the SignPath
Foundation, has them signed. What must never go wrong there: a Canon SDK file
in a build (SignPath signs open source only, AGENTS.md rule 1), a signed
release falling back to unsigned files, a release published without the
owner, checksums of files that changed after they were computed, a secret in
a log. Each test below holds one of those. The workflow also has to run in
the public mirror, which is one commit with tags pushed by hand.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "windows-release.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
CHECK = (ROOT / "packaging" / "windows" / "check_release_files.ps1").read_text(encoding="utf-8")

SIGNPATH_SETTINGS = ("SIGNPATH_ORGANIZATION_ID", "SIGNPATH_PROJECT_SLUG", "SIGNPATH_SIGNING_POLICY_SLUG",
                     "SIGNPATH_ARTIFACT_CONFIGURATION_PROGRAMS", "SIGNPATH_ARTIFACT_CONFIGURATION_SETUP")
# Inputs of signpath/github-action-submit-signing-request v3.0 (its action.yml).
SIGNPATH_INPUTS = {"connector-url", "api-token", "organization-id", "project-slug", "signing-policy-slug",
                   "artifact-configuration-slug", "github-artifact-id", "github-token", "parameters",
                   "wait-for-completion-timeout-in-seconds", "service-unavailable-timeout-in-seconds",
                   "download-signed-artifact-timeout-in-seconds", "wait-for-completion",
                   "output-artifact-directory", "skip-decompress"}


def _jobs() -> dict:
    """Job name -> its text, split at the two-space keys under "jobs:"."""
    body = TEXT[TEXT.index("\njobs:\n") + len("\njobs:\n"):]
    parts = re.split(r"(?m)^  ([\w-]+):\s*$", body)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


JOBS = _jobs()


def _steps(job: str) -> list:
    """The steps of a job, in order, each as its own text."""
    text = JOBS[job]
    return re.split(r"(?m)^      - ", text[text.index("    steps:\n"):])[1:]


def _index(job: str, needle: str) -> int:
    hits = [i for i, s in enumerate(_steps(job)) if needle in s]
    assert hits, f"{job}: no step with {needle!r}"
    return hits[0]


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def test_it_runs_for_a_version_tag_and_by_hand_only():
    on = TEXT[TEXT.index("\non:\n"):TEXT.index("\npermissions:")]
    assert 'tags: ["v*"]' in on and "workflow_dispatch:" in on
    assert "pull_request" not in on and "branches" not in on
    # A tag is cut by hand in the mirror; it has to name the version in VERSION.
    assert '$env:GITHUB_REF_NAME -ne "v$v"' in JOBS["build"]


def test_every_action_is_pinned_to_a_commit_with_its_release_named():
    uses = re.findall(r"uses:\s*(\S+)(.*)", TEXT)
    assert len(uses) >= 8
    for ref, comment in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert re.search(r"#\s*v\d", comment), f"{ref}: name the release in a comment"


def test_the_same_action_is_pinned_to_the_same_commit_as_in_the_test_workflow():
    pinned = dict(re.findall(r"uses:\s*([\w.-]+/[\w.-]+)@([0-9a-f]{40})",
                             (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")))
    for action, sha in re.findall(r"uses:\s*([\w.-]+/[\w.-]+)@([0-9a-f]{40})", TEXT):
        if action in pinned:
            assert sha == pinned[action], f"{action} pinned differently from tests.yml"


def test_least_privilege():
    assert re.search(r"(?m)^permissions: \{\}$", TEXT)
    for name, job in JOBS.items():
        assert "    permissions:" in job, f"{name} names its own permissions"
        writes = re.findall(r"(\w[\w-]*): write", job)
        assert writes == (["contents"] if name == "release" else []), f"{name}: {writes}"
    for name in ("sign-programs", "sign-setup"):
        assert "actions: read" in JOBS[name] and "contents: read" in JOBS[name]
    assert TEXT.count("persist-credentials: false") == TEXT.count("actions/checkout@")


def test_the_build_runs_on_windows_with_python_312_through_build_ps1():
    build = JOBS["build"]
    assert "runs-on: windows-latest" in build and 'python-version: "3.12"' in build
    # build.ps1 is the build: no PyInstaller or ISCC call of the workflow's own.
    assert "./packaging/windows/build.ps1 -Installer -Python python" in build
    assert "./packaging/windows/build.ps1 -InstallerOnly -Python python" in JOBS["sign-setup"]
    code = _code(TEXT).lower()
    assert "-m pyinstaller" not in code and "iscc" not in code and "pip install" not in code
    # Unsigned on the runner, always: no certificate is ever handed to build.ps1.
    for job in ("build", "sign-setup"):
        assert 'PHOTOBOX_SIGN_THUMBPRINT: ""' in JOBS[job] and 'PHOTOBOX_SIGN_COMMAND: ""' in JOBS[job]
        assert "-Sign" not in _code(JOBS[job]).replace("-Signed", "")
    assert "PHOTOBOX_COMMIT: ${{ github.sha }}" in build


@pytest.mark.parametrize("job", ["build", "sign-setup"])
def test_the_canon_guard_runs_before_anything_is_uploaded(job):
    guard = _index(job, "tools/check_hygiene.py --build-output .")
    step = _steps(job)[guard]
    assert "--pyinstaller packaging/windows/dist/PhotoboxBooth.exe packaging/windows/dist/PhotoboxSync.exe" in step
    # The build environment's Python: it has PyInstaller to open the programs.
    assert "packaging/windows/.buildenv/Scripts/python.exe" in step
    assert guard > _index(job, "build.ps1")
    for i, s in enumerate(_steps(job)):
        if "actions/upload-artifact@" in s or "signpath/github-action-submit-signing-request@" in s:
            assert i > guard, f"{job}: step {i} uploads before the guard"


def test_the_guard_knows_canon_file_names_and_ignores_the_own_wrapper():
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_hygiene", ROOT / "tools" / "check_hygiene.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.is_vendor_sdk_path("EDSDK/Dll/EDSDK.dll")
    assert mod.is_vendor_sdk_path("boothcore\\EdsImage.dll")
    assert not mod.is_vendor_sdk_path("PYZ.pyz/boothcore.camera.edsdk")


def test_signing_is_skipped_unless_secret_and_variables_are_there():
    config = JOBS["config"]
    assert "secrets.SIGNPATH_API_TOKEN != ''" in config
    for name in SIGNPATH_SETTINGS:
        assert f"vars.{name}" in config, name
    # Only "true"/"false" leaves the job; the token itself is never in its environment.
    assert "${{ secrets.SIGNPATH_API_TOKEN }}" not in config
    for job in ("sign-programs", "sign-setup"):
        assert "if: needs.config.outputs.signing == 'true'" in JOBS[job]


def test_the_api_token_goes_only_into_the_signpath_action_and_is_never_printed():
    for name, job in JOBS.items():
        for step in _steps(name) if "    steps:" in job else []:
            if "secrets.SIGNPATH_API_TOKEN" in step and "signpath/github-action-submit-signing-request@" not in step:
                assert name == "config" and "!= ''" in step, f"{name}: token outside the SignPath step"
    assert not re.search(r"(?i)(echo|write-host|write-output|print).*secrets\.", TEXT)


def test_the_signpath_steps_use_only_inputs_the_action_has():
    steps = [s for job in JOBS for s in (_steps(job) if "    steps:" in JOBS[job] else [])
             if "signpath/github-action-submit-signing-request@" in s]
    assert len(steps) == 2
    for step in steps:
        with_block = step[step.index("with:"):]
        keys = set(re.findall(r"(?m)^          ([\w-]+):", with_block))
        assert keys <= SIGNPATH_INPUTS, keys - SIGNPATH_INPUTS
        for needed in ("api-token", "organization-id", "project-slug", "signing-policy-slug",
                       "artifact-configuration-slug", "github-artifact-id", "output-artifact-directory"):
            assert needed in keys, needed
        assert "wait-for-completion: true" in step
        # The id of an artifact this run uploaded with actions/upload-artifact.
        assert re.search(r"github-artifact-id: \$\{\{ (needs\.build\.outputs\.programs-artifact-id"
                         r"|steps\.upload-setup\.outputs\.artifact-id) \}\}", step)
    assert "programs-artifact-id: ${{ steps.upload-programs.outputs.artifact-id }}" in JOBS["build"]


def test_booth_and_sync_are_signed_before_the_setup_is_built_from_them():
    assert "needs: [config, build, sign-programs]" in JOBS["sign-setup"]
    assert "name: programs-signed" in JOBS["sign-programs"] and "name: programs-signed" in JOBS["sign-setup"]
    setup = JOBS["sign-setup"]
    assert setup.index("name: programs-signed") < setup.index("build.ps1 -InstallerOnly")
    assert setup.index("build.ps1 -InstallerOnly") < setup.index("signpath/github-action-submit-signing-request@")


def test_every_signed_file_is_checked_before_it_moves_on():
    assert _index("sign-programs", "-Signed out/programs-signed/PhotoboxBooth.exe out/programs-signed/PhotoboxSync.exe") \
        > _index("sign-programs", "signpath/github-action-submit-signing-request@")
    check = _index("sign-setup", "Check every signature")
    assert check > _index("sign-setup", "signpath/github-action-submit-signing-request@")
    assert check < _index("sign-setup", "name: windows-signed")
    # What "checked" means: valid, signed by the SignPath Foundation, product name and version.
    assert 'Get-AuthenticodeSignature' in CHECK and '$sig.Status -ne "Valid"' in CHECK
    assert '$Signer = "SignPath Foundation"' in CHECK and "SignerCertificate.Subject" in CHECK
    assert '$ProductName = "Photobox"' in CHECK and "$info.ProductVersion -ne $Version" in CHECK


def test_the_checksums_are_written_after_signing_over_the_signed_files():
    sums = _index("sign-setup", "SHA256SUMS.txt")
    step = _steps("sign-setup")[sums]
    assert sums > _index("sign-setup", "Check every signature")
    assert sums < _index("sign-setup", "name: windows-signed")
    assert 'Get-FileHash "out/release/$n"' in step
    # LF and no BOM, like build.ps1: sha256sum -c elsewhere reads it.
    assert "(New-Object Text.UTF8Encoding $false)" in step and '-join "`n"' in step
    for name in ("Photobox-Setup-$env:VERSION.exe", "PhotoboxBooth-$env:VERSION.exe", "PhotoboxSync-$env:VERSION.exe"):
        assert name in step
    assert "sha256sum -c SHA256SUMS.txt" in JOBS["release"]


def test_the_release_is_only_ever_a_draft():
    release = JOBS["release"]
    creates = re.findall(r"gh release create[^\n]*", release)
    assert len(creates) == 2
    for line in creates:
        assert "--draft" in line and "--verify-tag" in line
    code = _code(TEXT)
    for forbidden in ("gh release edit", "--draft=false", "gh release upload", "make_latest", "--latest"):
        assert forbidden not in code, forbidden
    assert "github.ref_type == 'tag'" in release


def test_without_signpath_the_draft_is_an_unsigned_prerelease():
    release = JOBS["release"]
    unsigned = release[release.index("else"):]
    assert "--prerelease" in unsigned and '(unsigned)"' in unsigned and "name: windows-unsigned" in release


def test_a_signed_release_never_falls_back_to_unsigned_files():
    release = JOBS["release"]
    condition = release[release.index("if: >-"):release.index("runs-on:")]
    assert "needs.config.outputs.signing == 'true' && needs.sign-setup.result == 'success'" in condition
    assert "needs.config.outputs.signing != 'true' && needs.sign-setup.result == 'skipped'" in condition
    assert "needs.build.result == 'success'" in condition


def test_the_mirror_keeps_the_workflow():
    snapshot = (ROOT / "tools" / "make_public_snapshot.sh").read_text(encoding="utf-8")
    excludes = re.search(r"EXCLUDE = \[(.*?)\]", snapshot).group(1)
    for pattern in re.findall(r'r"([^"]+)"', excludes):
        assert not re.search(pattern, ".github/workflows/windows-release.yml"), pattern
        assert not re.search(pattern, "packaging/windows/check_release_files.ps1"), pattern
