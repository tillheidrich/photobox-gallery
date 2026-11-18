"""Product name and version in every Windows file of a build.

A code signing service checks them before it signs anything. SignPath's terms:
"Set all product name attributes to your project's name" and "Set all product
version attributes to the same value in each build", enforced through the
metadata restrictions of the artifact configuration (docs/CODE-SIGNING.md).
Until November 2026 only the booth carried a version resource; the sync
program had none, and the setup's version text was four numbers where the
programs said three. Read from the scripts, there is no Windows here.
"""
import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "packaging" / "windows"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
ISS = (WIN / "installer" / "photobox.iss").read_text(encoding="utf-8-sig")

_spec = importlib.util.spec_from_file_location("version_resource", WIN / "version_resource.py")
vr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vr)


def _strings(text: str) -> dict:
    """The StringStruct pairs of a rendered resource, read as Python syntax."""
    tree = ast.parse(text, mode="eval")
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "StringStruct":
            key, value = (ast.literal_eval(a) for a in node.args)
            found[key] = value
    return found


def _fixed(text: str) -> dict:
    tree = ast.parse(text, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "FixedFileInfo":
            return {k.arg: ast.literal_eval(k.value) for k in node.keywords if k.arg in ("filevers", "prodvers")}
    return {}


def _iss(key: str) -> str:
    m = re.search(rf"(?m)^{key}=(.*)$", ISS)
    assert m, key
    return m.group(1).strip()


@pytest.mark.parametrize("program, description", [("PhotoboxBooth", "Photobox Booth"),
                                                  ("PhotoboxSync", "Photobox Sync")])
def test_each_program_names_one_product_and_this_version(program, description):
    s = _strings(vr.render(program, vr.read_version(ROOT)))
    assert s["ProductName"] == "Photobox"
    assert s["FileDescription"] == description
    assert s["ProductVersion"] == VERSION and s["FileVersion"] == VERSION
    assert s["OriginalFilename"] == f"{program}.exe" and s["InternalName"] == program
    assert s["CompanyName"] == "Till Heidrich"
    assert "AGPL-3.0-or-later" in s["LegalCopyright"]
    four = tuple(int(n) for n in (VERSION.split(".") + ["0"])[:4])
    assert _fixed(vr.render(program, VERSION)) == {"filevers": four, "prodvers": four}


def test_the_version_comes_from_the_version_file():
    assert vr.read_version(ROOT) == VERSION
    assert vr.numeric("0.9.1") == (0, 9, 1, 0) and vr.numeric("1.2.3.4") == (1, 2, 3, 4)


def test_a_quote_in_a_value_cannot_break_the_resource():
    # repr() quotes the values; the text stays valid syntax whatever they hold.
    _strings(vr.render("PhotoboxBooth", "1.0.0'x"))


@pytest.mark.parametrize("spec_name, program", [("photobox-booth.spec", "PhotoboxBooth"),
                                                ("photobox-sync.spec", "PhotoboxSync")])
def test_both_specs_embed_the_shared_resource(spec_name, program):
    spec = (WIN / spec_name).read_text(encoding="utf-8")
    assert '"version_resource.py"' in spec
    assert f'VERSION_FILE = _version_resource().write(ROOT, "{program}")' in spec
    assert "version=VERSION_FILE" in spec


def test_the_publisher_is_the_same_in_programs_setup_and_publisher_card():
    example = json.loads((WIN / "publisher.example.json").read_text(encoding="utf-8"))
    assert vr.COMPANY_NAME == example["name"]
    assert _iss("VersionInfoCompany") == "{#PublisherName}"
    # The setup's copyright, with the publisher filled in, is the programs' copyright.
    assert _iss("VersionInfoCopyright").replace("{#PublisherName}", vr.COMPANY_NAME) == vr.LEGAL_COPYRIGHT


def test_the_setup_names_the_same_product_and_version_text():
    assert '#define AppName "Photobox"' in ISS and vr.PRODUCT_NAME == "Photobox"
    assert _iss("VersionInfoProductName") == "{#AppName}"
    assert _iss("VersionInfoProductTextVersion") == "{#AppVersion}"
    assert _iss("VersionInfoTextVersion") == "{#AppVersion}"
    assert _iss("VersionInfoVersion") == "{#AppVersionNumeric}"
    assert _iss("VersionInfoProductVersion") == "{#AppVersionNumeric}"
    # build.ps1 passes the text version from VERSION as AppVersion.
    assert '"/DAppVersion=$AppVersion"' in (WIN / "build.ps1").read_text(encoding="utf-8-sig")


def test_the_resource_text_mentions_no_vendor_sdk():
    text = (WIN / "version_resource.py").read_text(encoding="utf-8").lower()
    for word in ("edsdk", "edsimage", "canon"):
        assert word not in text, word
