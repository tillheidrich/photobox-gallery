"""What a Windows build hands out, checked from the scripts (there is no Windows here).

The owner's rule: the sync program is always in the setup, and booth and sync
are also downloadable on their own. So a build makes three files with the
version from VERSION in their names -- the setup (booth and sync), the booth
alone, the sync alone -- and SHA256SUMS.txt lists exactly those. The
un-versioned dist\\PhotoboxBooth.exe stays, because a box update copies that
path. None of it may contain a Canon SDK file.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "packaging" / "windows"
BUILD = (WIN / "build.ps1").read_text(encoding="utf-8-sig")
ISS = (WIN / "installer" / "photobox.iss").read_text(encoding="utf-8-sig")
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _code(text: str, comment: str) -> str:
    """Only the lines that do something; the comments quote old mistakes."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(comment))


def _section(text: str, name: str) -> str:
    m = re.search(rf"^\[{name}\]\s*$(.*?)(?=^\[[A-Za-z]+\]\s*$|\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def _expand(template: str) -> str:
    return template.replace("$AppVersion", VERSION).replace("{#AppVersion}", VERSION)


def test_the_version_for_every_file_name_comes_from_the_version_file():
    code = _code(BUILD, "#")
    assert '$AppVersion = (Get-Content "$Root\\VERSION" -Raw).Trim()' in code
    # Read once, before the build, not only inside the installer step.
    assert code.index("$AppVersion = (Get-Content") < code.index("photobox-booth.spec")
    assert code.count("$AppVersion = ") == 1


def test_the_build_names_all_three_downloads_with_the_version():
    code = _code(BUILD, "#")
    # Booth and sync: copied to "<name>-<version>.exe" for both names.
    assert '"$Here\\dist\\$n-$AppVersion.exe"' in code
    assert '$Release += "PhotoboxBooth"' in code and '$Release += "PhotoboxSync"' in code
    assert '"Photobox-Setup-$AppVersion.exe"' in code
    names = {_expand(f"{n}-$AppVersion.exe") for n in ("PhotoboxBooth", "PhotoboxSync")}
    names.add(_expand("Photobox-Setup-$AppVersion.exe"))
    assert names == {f"PhotoboxBooth-{VERSION}.exe", f"PhotoboxSync-{VERSION}.exe",
                     f"Photobox-Setup-{VERSION}.exe"}
    # Inno writes the setup under the same name build.ps1 hashes.
    assert _expand("Photobox-Setup-{#AppVersion}") + ".exe" == f"Photobox-Setup-{VERSION}.exe"
    assert "OutputBaseFilename=Photobox-Setup-{#AppVersion}" in ISS
    assert '"/DAppVersion=$AppVersion"' in code


def test_the_checksums_list_exactly_the_offered_files():
    code = _code(BUILD, "#")
    start = code.index("$Offered = @()")
    sums = code[start:code.index("SHA256SUMS.txt", start)]
    assert 'foreach ($n in $Release) { $Offered += "$n-$AppVersion.exe" }' in sums
    assert 'if ($Installer) { $Offered += "Photobox-Setup-$AppVersion.exe" }' in sums
    # Not "every .exe in dist": that picked up older setups and the
    # un-versioned copies.
    assert 'Get-ChildItem "$Here\\dist\\*.exe"' not in code
    assert "throw" in sums, "a missing download must stop the build, not shorten the list"


def test_the_unversioned_booth_exe_stays_for_the_box_update():
    spec = (WIN / "photobox-booth.spec").read_text(encoding="utf-8")
    assert 'name="PhotoboxBooth"' in spec
    code = _code(BUILD, "#")
    assert '--distpath "$Here\\dist"' in code
    # Copied, never moved or renamed away.
    assert "Copy-Item \"$Here\\dist\\$n.exe\"" in code
    assert "Move-Item" not in code and "Rename-Item" not in code


def test_the_setup_always_packs_the_sync_program():
    sync_spec = (WIN / "photobox-sync.spec").read_text(encoding="utf-8")
    assert 'name="PhotoboxSync"' in sync_spec
    assert '#define SyncExeName "PhotoboxSync.exe"' in ISS
    files = _section(ISS, "Files")
    assert 'Source: "{#DistDir}\\{#AppExeName}"' in files
    assert 'Source: "{#DistDir}\\{#SyncExeName}"' in files
    sync_line = next(l for l in files.splitlines() if "{#SyncExeName}" in l)
    assert "skipifsourcedoesntexist" not in sync_line, "the sync must never be optional"
    # Its own folder: an old C:\Fotobox\PhotoboxSync.exe is never overwritten.
    assert "{app}\\{#SyncDir}" in sync_line
    assert "{#SyncExeName}" in _section(ISS, "Icons")
    # Not started or put into autostart by the setup.
    assert "{#SyncExeName}" not in _section(ISS, "Run")
    code = _code(BUILD, "#")
    assert "if ($Installer -and ($BoothOnly -or $SyncOnly))" in code
    # -InstallerOnly packs what lies in dist: both files must be there.
    assert 'foreach ($n in "PhotoboxBooth.exe", "PhotoboxSync.exe") {\n            if (-not (Test-Path' in code


def test_no_canon_sdk_file_in_any_artefact():
    for spec_name in ("photobox-booth.spec", "photobox-sync.spec"):
        code = _code((WIN / spec_name).read_text(encoding="utf-8"), "#")
        for word in ("edsdk", "edsimage", "canon"):
            assert word not in code.lower(), f"{spec_name}: {word}"
    build = _code(BUILD, "#").lower()
    # The LIESMICH text tells the operator how to install Canon's SDK; no
    # command of the build copies or packs it.
    for line in build.splitlines():
        if "edsdk" in line or "edsimage" in line:
            assert "--install-edsdk" in line, line
    assert "EDSDK" not in _section(ISS, "Files").upper()
