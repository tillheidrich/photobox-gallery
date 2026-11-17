"""What the project says about its licence has to be one story, told everywhere.

Until November 2026 the README, the CLA and CONTRIBUTING promised a sold build
"with the Canon EDSDK in place", while the Windows build plan said the SDK is
never included. The model is the second one: the source is AGPL and complete,
nobody ships the Canon SDK (every user gets it from Canon), and the maintainer
earns money only by running the software for others as a managed service.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PUBLIC_DOCS = [
    "README.md", "CLA.md", "CONTRIBUTING.md", "PRODUCT.md", "DESIGN.md", "AGENTS.md",
    "packaging/windows/README.md", "packaging/macos/README.md", "booth/README.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
]

# Phrases that describe an edition for sale. None of them may come back.
SOLD_EDITION = re.compile(
    r"(?i)for sale|paid build|paid,|prepared build|prepared application|ready-built|"
    r"sdk in place|edsdk in place|is sold|also sold|to be sold|dual licen|"
    r"verkaufte[nr]? anwendung|kaufversion|vor dem ersten verkauf"
)


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", PUBLIC_DOCS)
def test_no_document_promises_an_edition_for_sale(name):
    hits = [m.group(0) for m in SOLD_EDITION.finditer(_read(name))]
    assert not hits, f"{name} still describes a sold build: {hits}"


def test_the_readme_tells_the_model_in_its_licence_section():
    text = _read("README.md")
    section = text.split("## Licence", 1)[1]
    assert "AGPL-3.0-or-later" in section
    assert "managed service" in section.lower()
    assert "never" in section and "Canon" in section


def test_contributing_has_one_licence_section_not_two():
    headings = [line for line in _read("CONTRIBUTING.md").splitlines()
                if re.match(r"##\s+Licen[cs]", line)]
    assert len(headings) == 1, headings


def test_the_cla_still_grants_what_it_granted():
    """The wording around it changed; the rights a contributor grants did not."""
    text = _read("CLA.md")
    for phrase in (
        "perpetual, worldwide,\n   non-exclusive, irrevocable, royalty-free right to use, reproduce, modify,\n"
        "   publish and distribute your contribution, and to sublicense it",
        "**including\n   under licence terms other than the AGPL**",
        "**You keep your copyright.** This is a licence, not a transfer.",
        "**You grant patent rights.**",
        "I have read CLA.md and I agree to it.",
    ):
        assert phrase in text, phrase


def test_the_cla_names_the_reasons_it_exists():
    text = _read("CLA.md")
    assert "managed service" in text.lower()


# ── The section 7 permission for the Canon EDSDK ─────────────────────────

# sha256 of https://www.gnu.org/licenses/agpl-3.0.txt. The permission lives in
# its own file so that LICENSE stays the licence, byte for byte.
AGPL_3_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"

EDSDK_PERMISSION = (
    "Additional permission under GNU AGPL version 3 section 7: If you modify this Program, "
    "or any covered work, by linking or combining it with the Canon EOS Digital SDK (EDSDK) "
    "runtime libraries (EDSDK.dll, EdsImage.dll, libEDSDK.so, EDSDK.framework), containing "
    "parts covered by the terms of Canon's SDK agreement, the licensors of this Program grant "
    "you additional permission to convey the resulting work. Corresponding Source for a "
    "non-source form of such a combination need not include the EDSDK. This permission grants "
    "no rights under Canon's agreement; obtaining the SDK and complying with Canon's terms, "
    "including any redistribution limits, is solely your responsibility."
)


def _words(text: str) -> str:
    return " ".join(text.split())


def test_the_licence_file_is_the_agpl_unchanged():
    import hashlib
    assert hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest() == AGPL_3_SHA256


def test_the_edsdk_permission_is_granted_word_for_word():
    text = _words(_read("LICENSE-EXCEPTION"))
    heading, body = EDSDK_PERMISSION.split(": ", 1)
    assert heading in text
    assert body in text, "the permission text must not drift from what was decided"


def test_the_readme_licence_section_points_at_the_permission():
    section = _read("README.md").split("## Licence", 1)[1]
    assert "(LICENSE-EXCEPTION)" in section
