"""The Windows edition with camera SDK: made outside the repository, never in CI.

There is no Windows and no Canon file here. The "libraries" are placeholder
files made in tmp_path with just enough of a PE header for the 64-bit check;
none of them is or contains anything of Canon's.
"""
import importlib.util
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packaging" / "windows" / "camera_sdk_edition.py"
BUILD_PS1 = (ROOT / "packaging" / "windows" / "build.ps1").read_text(encoding="utf-8-sig")
SPEC = (ROOT / "packaging" / "windows" / "photobox-booth.spec").read_text(encoding="utf-8")
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _load():
    spec = importlib.util.spec_from_file_location("camera_sdk_edition_under_test", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ed = _load()


def _fake_dll(path: Path, machine: int = 0x8664) -> Path:
    head = bytearray(b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x40)) + b"PE\0\0" + struct.pack("<H", machine)
    path.write_bytes(bytes(head) + path.name.encode())
    return path


@pytest.fixture
def sdk(tmp_path):
    folder = tmp_path / "outside" / "Dll"
    folder.mkdir(parents=True)
    for name in ("EDSDK.dll", "EdsImage.dll", "Helper.dll"):
        _fake_dll(folder / name)
    return folder


def test_the_names_are_chosen_once_and_ascii():
    assert ed.SETUP_NAME.format(version="0.9.2") == "Photobox-Setup-0.9.2-mit-Kamera-SDK.exe"
    assert ed.PORTABLE_NAME.format(version="0.9.2") == "PhotoboxBooth-0.9.2-mit-Kamera-SDK.exe"
    assert ed.SUMS_NAME == "SHA256SUMS-mit-Kamera-SDK.txt"
    for n in (ed.SETUP_NAME, ed.PORTABLE_NAME, ed.SUMS_NAME):
        assert n.isascii()
    # build.ps1 spells the same names.
    assert '"PhotoboxBooth-$AppVersion-mit-Kamera-SDK.exe"' in BUILD_PS1
    assert '"Photobox-Setup-$AppVersion-mit-Kamera-SDK.exe"' in BUILD_PS1
    assert "SHA256SUMS-mit-Kamera-SDK.txt" in BUILD_PS1


def _sdk_part() -> str:
    start = BUILD_PS1.index("if ($WithCameraSdk) {\n    $Edition")
    return "\n".join(l for l in BUILD_PS1[start:].splitlines() if not l.lstrip().startswith("#"))


def test_the_edition_with_camera_sdk_is_published_as_setup_only():
    # Decision 18.11.2026: the portable .exe with camera SDK is not offered.
    # Published: the setup, its checksum file and the notices - nothing else.
    assert ed.PUBLISHED == (ed.SETUP_NAME, ed.SUMS_NAME, ed.NOTICES_NAME)
    assert ed.NOTICES_NAME == "THIRD-PARTY-NOTICES-mit-Kamera-SDK.txt"
    assert ed.PORTABLE_NAME not in ed.PUBLISHED
    sdk = _sdk_part()
    # The checksum file lists exactly the setup ...
    assert 'foreach ($name in @($SdkSetup)) {' in sdk
    assert "$Portable) | Sort-Object" not in sdk
    # ... and the portable program, still built for the check, lies apart in
    # a folder whose name says it is not for the website.
    assert ed.INTERNAL_DIR == "nicht-veroeffentlichen"
    assert '$Internal = Join-Path $CameraSdkOut "nicht-veroeffentlichen"' in sdk
    assert 'Copy-Item "$CameraSdkOut\\pyi\\PhotoboxBooth.exe" "$Internal\\$Portable" -Force' in sdk
    # Next to the setup it is only ever removed (left over from an older build).
    for line in sdk.splitlines():
        if '"$CameraSdkOut\\$Portable"' in line:
            assert line.lstrip().startswith("Remove-Item "), line
    # The notices go next to the setup under their published name.
    assert 'Copy-Item "$SdkLegal\\THIRD-PARTY-NOTICES.txt" "$CameraSdkOut\\THIRD-PARTY-NOTICES-mit-Kamera-SDK.txt" -Force' in sdk


def test_the_docs_offer_the_edition_with_camera_sdk_as_setup_only():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "PhotoboxBooth-<version>-mit-Kamera-SDK.exe" not in readme
    assert "only as a setup" in readme
    installer = (ROOT / "docs" / "INSTALLER.md").read_text(encoding="utf-8")
    row = next(l for l in installer.splitlines() if l.startswith("| Dateien |"))
    assert "PhotoboxBooth-<v>-mit-Kamera-SDK.exe" not in row
    assert "THIRD-PARTY-NOTICES-mit-Kamera-SDK.txt" in row and "SHA256SUMS-mit-Kamera-SDK.txt" in row
    assert "nur als Setup" in installer
    for name in ("CODE-SIGNING-POLICY.md", "CODE-SIGNING.md", "CODE-SIGNATUR.md"):
        text = " ".join((ROOT / "docs" / name).read_text(encoding="utf-8").split())
        assert "setup only" in text or "nur als Setup" in text, name
    policy = " ".join((ROOT / "docs" / "CODE-SIGNING-POLICY.md").read_text(encoding="utf-8").split())
    assert "The setup of the edition with camera SDK is itself never signed" in policy
    assert "packs the same booth program" in policy


def test_a_stale_stage_variable_never_reaches_the_free_build():
    # A PHOTOBOX_CAMERA_SDK_STAGE left in the shell from an earlier run would
    # make photobox-booth.spec pack Canon's files into the free program.
    clear = "Remove-Item Env:PHOTOBOX_CAMERA_SDK_STAGE -ErrorAction SilentlyContinue"
    assert BUILD_PS1.index(clear) < BUILD_PS1.index("# 1. Python besorgen")
    assert BUILD_PS1.index(clear) < BUILD_PS1.index('"$Here\\photobox-booth.spec"')


def test_the_free_programs_are_checked_before_anything_is_signed():
    code = "\n".join(l for l in BUILD_PS1.splitlines() if not l.lstrip().startswith("#"))
    first_run = code.index("\nTest-FreeEdition\n")
    assert code.index("function Test-FreeEdition") < first_run
    assert first_run < code.index('Sign-Datei "$Here\\dist\\$n"')
    assert first_run < code.index("/Sphotobox=")
    assert first_run > code.index('"$Here\\photobox-sync.spec"')


def test_stage_copies_every_library_unmodified_and_writes_the_terms(sdk, tmp_path):
    out = tmp_path / "stage"
    copied = ed.stage(sdk, out, root=ROOT, environ={})
    assert copied == ["EDSDK.dll", "EdsImage.dll", "Helper.dll"]
    for name in copied:
        assert (out / "EDSDK" / name).read_bytes() == (sdk / name).read_bytes()
    assert (out / "CAMERA-SDK-LICENCE.txt").is_file()
    assert ed.expected_entries(out) == ["CAMERA-SDK-LICENCE.txt", "EDSDK/EDSDK.dll",
                                         "EDSDK/EdsImage.dll", "EDSDK/Helper.dll"]


def test_a_folder_inside_the_repository_is_refused(sdk, tmp_path):
    with pytest.raises(ed.Refused, match="inside the repository"):
        ed.stage(ROOT / "packaging", tmp_path / "stage", root=ROOT, environ={})
    with pytest.raises(ed.Refused, match="inside the repository"):
        ed.stage(sdk, ROOT / "packaging" / "windows" / "stage", root=ROOT, environ={})


@pytest.mark.parametrize("var", ["GITHUB_ACTIONS", "CI"])
def test_it_is_refused_on_a_ci_runner(sdk, tmp_path, var):
    with pytest.raises(ed.Refused, match="CI"):
        ed.stage(sdk, tmp_path / "stage", root=ROOT, environ={var: "true"})
    assert ed.main(["refuse-ci"]) in (0, 1)


def test_32_bit_or_missing_libraries_are_refused(sdk, tmp_path):
    _fake_dll(sdk / "EdsImage.dll", machine=0x014C)
    with pytest.raises(ed.Refused, match="64-bit"):
        ed.stage(sdk, tmp_path / "a", root=ROOT, environ={})
    (sdk / "EdsImage.dll").unlink()
    with pytest.raises(ed.Refused, match="EdsImage.dll is not in"):
        ed.stage(sdk, tmp_path / "b", root=ROOT, environ={})


def test_the_notices_get_a_section_saying_not_agpl(sdk, tmp_path):
    out = tmp_path / "stage"
    files = ed.stage(sdk, out, root=ROOT, environ={})
    free = tmp_path / "free.txt"
    free.write_text("Photobox third-party notices\n", encoding="utf-8")
    text = ed.notices(free, tmp_path / "n.txt", files, root=ROOT).read_text(encoding="utf-8-sig")
    assert text.startswith("Photobox third-party notices")
    assert "Canon EOS Digital SDK (runtime libraries)" in text
    assert "NOT under the GNU Affero General Public" in text
    assert "unmodified, in\r\nobject code only" in text or "object code only" in " ".join(text.split())


def test_the_portable_check_wants_exactly_the_staged_files(sdk, tmp_path):
    out = tmp_path / "stage"
    ed.stage(sdk, out, root=ROOT, environ={})
    good = ["boothcore/web/index.html", "EDSDK\\EDSDK.dll", "EDSDK/EdsImage.dll", "EDSDK/Helper.dll",
            "CAMERA-SDK-LICENCE.txt", "PYZ.pyz"]
    assert ed.verify_portable(good, out) == []
    assert ed.verify_portable([n for n in good if "Helper" not in n], out)
    assert ed.verify_portable(good + ["EDSDK/Other.dll"], out)


def test_build_ps1_refuses_ci_and_folders_inside_the_repository():
    assert "[string]$WithCameraSdk" in BUILD_PS1
    assert re.search(r"if \(\$env:GITHUB_ACTIONS -or \$env:CI\) \{\s*throw", BUILD_PS1)
    assert '.StartsWith("$Root\\", [StringComparison]::OrdinalIgnoreCase)' in BUILD_PS1
    # The free edition is built and checked first, and once more afterwards.
    first_guard = BUILD_PS1.index("\nTest-FreeEdition\n")
    second_build = BUILD_PS1.index("if ($WithCameraSdk) {\n    $Edition")
    assert first_guard < second_build
    # definition + before signing + after the setup + after the second edition
    assert BUILD_PS1.count("Test-FreeEdition") == 4
    # Never signed: the SDK setup gets no SignTool define.
    sdk_part = "\n".join(l for l in BUILD_PS1[second_build:].splitlines() if not l.lstrip().startswith("#"))
    assert "/DSignToolName" not in sdk_part and "Sign-Datei" not in sdk_part and "/Sphotobox" not in sdk_part
    assert "$isccSdk = $isccBase + @(" in sdk_part


def test_the_spec_adds_the_sdk_only_from_a_stage_outside_the_repository():
    assert 'os.environ.get("PHOTOBOX_CAMERA_SDK_STAGE", "")' in SPEC
    block = SPEC[SPEC.index("_camera_sdk_stage = "):SPEC.index("# Kamera- und Druckerbackends")]
    assert "_edition.refuse_ci()" in block and "_edition.refuse_inside(" in block


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_ever_builds_the_edition_with_camera_sdk(workflow):
    code = "\n".join(l for l in workflow.read_text(encoding="utf-8").splitlines()
                     if not l.lstrip().startswith("#"))
    for token in ("WithCameraSdk", "CameraSdkDir", "PHOTOBOX_CAMERA_SDK_STAGE", "camera_sdk_edition",
                  "mit-Kamera-SDK", "CameraSdkOut"):
        assert token not in code, f"{workflow.name} uses {token}"
