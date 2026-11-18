#!/usr/bin/env python3
"""Files of the Windows edition with camera SDK, made and checked outside the repository.

There are two Windows editions, both free of charge (docs/INSTALLER.md):

* the free edition, built from this repository alone - the only one ever
  built in CI and the only one that is ever code-signed;
* the edition with camera SDK, offered only on the project website: the same
  program plus the Canon EOS Digital SDK runtime libraries, built by the
  maintainer on the build PC with ``build.ps1 -Installer -WithCameraSdk <Dll folder>``.

Canon's files never enter this repository, its public mirror, CI or a GitHub
release. This helper is what build.ps1 runs for the second edition:

    stage    --sdk <SDK Dll folder> --out <stage>     copy the libraries unmodified
    notices  --free <THIRD-PARTY-NOTICES.txt> --out <file>   notices + SDK section
    verify-portable --exe <exe> --stage <stage>       exactly the expected files inside
    refuse-ci                                          exit 1 on a CI runner

Every folder it writes to must lie outside the repository, and it refuses to
run on a CI runner at all. Nothing here needs Windows; the tests run it with
empty placeholder files.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import shutil
import struct
import sys
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parents[2]

#: The download names, chosen once (ASCII). The website data and the docs use
#: exactly these; tests/test_camera_sdk_edition.py holds them together.
SUFFIX = "-mit-Kamera-SDK"
SETUP_NAME = "Photobox-Setup-{version}" + SUFFIX + ".exe"
PORTABLE_NAME = "PhotoboxBooth-{version}" + SUFFIX + ".exe"
SUMS_NAME = "SHA256SUMS" + SUFFIX + ".txt"
NOTICES_NAME = "THIRD-PARTY-NOTICES" + SUFFIX + ".txt"
#: What goes on the website: the edition with camera SDK is offered as a
#: setup only (decision 18.11.2026). The checksum file lists the setup alone.
PUBLISHED = (SETUP_NAME, SUMS_NAME, NOTICES_NAME)
#: The portable program with camera SDK is still built, for verify-portable,
#: but lies in this sub-folder of the output and is never published.
INTERNAL_DIR = "nicht-veroeffentlichen"
#: Default output folder on the build PC, outside the repository.
DEFAULT_OUT = r"%USERPROFILE%\Photobox-Build\mit-Kamera-SDK"

CI_VARIABLES = ("GITHUB_ACTIONS", "CI", "TF_BUILD", "GITLAB_CI", "BUILDKITE", "APPVEYOR")
MACHINE_X64 = 0x8664


def _bundle_module():
    """boothcore/camera_sdk_bundle.py by file path: standard library only,
    usable before the booth's dependencies are installed."""
    path = ROOT / "booth" / "boothcore" / "camera_sdk_bundle.py"
    spec = importlib.util.spec_from_file_location("photobox_camera_sdk_bundle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUNDLE = _bundle_module()


class Refused(Exception):
    pass


def on_ci(environ=None) -> str:
    environ = os.environ if environ is None else environ
    for name in CI_VARIABLES:
        if str(environ.get(name, "")).strip().lower() not in ("", "0", "false"):
            return name
    return ""


def refuse_ci(environ=None) -> None:
    name = on_ci(environ)
    if name:
        raise Refused(f"The edition with camera SDK is never built on a CI runner ({name} is set). "
                      "CI builds the free edition only.")


def inside_repository(path: Path, root: Path = ROOT) -> bool:
    path = Path(os.path.realpath(path))
    root = Path(os.path.realpath(root))
    return path == root or root in path.parents


def refuse_inside(path: Path, what: str, root: Path = ROOT) -> None:
    if inside_repository(path, root):
        raise Refused(f"{what} lies inside the repository ({path}). Canon's files never enter "
                      "the repository: use a folder outside it.")


def pe_machine(path: Path):
    try:
        with Path(path).open("rb") as fh:
            if fh.read(2) != b"MZ":
                return None
            fh.seek(0x3C)
            fh.seek(struct.unpack("<I", fh.read(4))[0])
            if fh.read(4) != b"PE\0\0":
                return None
            return struct.unpack("<H", fh.read(2))[0]
    except (OSError, struct.error):
        return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stage(sdk: Path, out: Path, root: Path = ROOT, environ=None) -> List[str]:
    """Copy the SDK's 64-bit libraries, byte for byte, into ``out/EDSDK`` and
    write the licence file next to it. Returns the copied file names."""
    refuse_ci(environ)
    sdk, out = Path(sdk), Path(out)
    refuse_inside(sdk, "The SDK folder", root)
    refuse_inside(out, "The output folder", root)
    if not sdk.is_dir():
        raise Refused(f"SDK folder not found: {sdk} (expected the SDK's Dll folder with EDSDK.dll)")
    libraries = sorted(p for p in sdk.iterdir() if p.is_file() and p.suffix.lower() == ".dll")
    names = {p.name.lower() for p in libraries}
    for needed in BUNDLE.RUNTIME_LIBRARIES:
        if needed.lower() not in names:
            raise Refused(f"{needed} is not in {sdk}. Give the SDK's Dll folder (64 bit).")
    for p in libraries:
        machine = pe_machine(p)
        if machine != MACHINE_X64:
            raise Refused(f"{p.name} is not a 64-bit Windows library (machine {machine!r}); "
                          "the booth is 64 bit. Give the 64-bit Dll folder.")
    target = out / BUNDLE.SDK_FOLDER
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    copied = []
    for p in libraries:
        dest = target / p.name
        shutil.copyfile(p, dest)            # unmodified: bytes only, checked below
        if sha256(dest) != sha256(p):
            raise Refused(f"copy of {p.name} differs from the original")
        copied.append(p.name)
    BUNDLE.write_licence_file(out / BUNDLE.LICENCE_FILE)
    (out / "camera-sdk-files.txt").write_text("\n".join(copied) + "\n", encoding="ascii")
    return copied


NOTICES_SECTION = """

==============================================================================
Canon EOS Digital SDK (runtime libraries) - only in the edition with camera SDK
==============================================================================

Files: {files}
Licence: proprietary, Canon's terms. NOT under the GNU Affero General Public
License and not under any other open-source licence. Included unmodified, in
object code only, solely for use of Canon cameras with this software.

End-user terms (also in CAMERA-SDK-LICENCE.txt next to the program):

{terms}
"""


def notices(free: Path, out: Path, files: Sequence[str], root: Path = ROOT) -> Path:
    refuse_inside(out, "The notices file", root)
    text = Path(free).read_text(encoding="utf-8-sig").rstrip() + NOTICES_SECTION.format(
        files=", ".join(files), terms=BUNDLE.licence_text())
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text.replace("\r\n", "\n"), encoding="utf-8-sig", newline="\r\n")
    return out


def expected_entries(stage_dir: Path) -> List[str]:
    files = (Path(stage_dir) / "camera-sdk-files.txt").read_text(encoding="ascii").split()
    return sorted([f"{BUNDLE.SDK_FOLDER}/{n}" for n in files] + [BUNDLE.LICENCE_FILE])


def sdk_entries(names: Sequence[str]) -> List[str]:
    """The entries of a program that belong to the camera SDK bundle."""
    out = []
    for n in names:
        n = n.replace("\\", "/")
        if n.startswith(BUNDLE.SDK_FOLDER + "/") or n == BUNDLE.LICENCE_FILE:
            out.append(n)
    return sorted(out)


def verify_portable(names: Sequence[str], stage_dir: Path) -> List[str]:
    want = expected_entries(stage_dir)
    have = sdk_entries(names)
    problems = []
    if have != want:
        problems.append(f"camera SDK files in the portable program: {have}, expected {want}")
    return problems


def _pyinstaller_names(exe: Path) -> List[str]:
    spec = importlib.util.spec_from_file_location("photobox_check_hygiene", ROOT / "tools" / "check_hygiene.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.pyinstaller_entries(Path(exe))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--sdk", required=True)
    s.add_argument("--out", required=True)
    n = sub.add_parser("notices")
    n.add_argument("--free", required=True)
    n.add_argument("--stage", required=True)
    n.add_argument("--out", required=True)
    v = sub.add_parser("verify-portable")
    v.add_argument("--exe", required=True)
    v.add_argument("--stage", required=True)
    sub.add_parser("refuse-ci")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "refuse-ci":
            refuse_ci()
            print("ok - not a CI runner")
        elif args.cmd == "stage":
            for name in stage(Path(args.sdk), Path(args.out)):
                print(f"copied unmodified: {name}")
        elif args.cmd == "notices":
            files = (Path(args.stage) / "camera-sdk-files.txt").read_text(encoding="ascii").split()
            print(notices(Path(args.free), Path(args.out), files))
        elif args.cmd == "verify-portable":
            problems = verify_portable(_pyinstaller_names(Path(args.exe)), Path(args.stage))
            for p in problems:
                print(f"error: {p}", file=sys.stderr)
            if problems:
                return 1
            print(f"ok - {args.exe} carries exactly {', '.join(expected_entries(Path(args.stage)))}")
    except Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
