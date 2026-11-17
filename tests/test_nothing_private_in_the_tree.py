"""The repository is published: nothing private of the owner in the tree.

Final review V1–V3: internal business strategy under ``docs/intern/``, the
owner's street address as an example in a comment, his private mail address
as contact, the old production hostname in example configs, links into the
internal wiki and a real tenant name in a pairing example. Examples are
invented now (``example.org``, "demo", "Musterstraße 1"); the GitHub handle
stays where it names the project.

The fragments are base64 here so that this test does not publish them itself.
"""
import base64
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FRAGMENTS = [base64.b64decode(x).decode() for x in (
    "Um90ZG9ybndlZw==",                          # the owner's street
    "MjEyNTUgVG9zdGVkdA==",                      # postcode and town
    "NzM5NTM4MTk=",                              # phone number
    "dGlsbGhlaWRyaWNoLmRl",                      # private mail domain (mail@…, fotobox.…)
    "cGhvdG9ib3guaGVpZHJpY2gtZGlnaXRhbC5kZQ==",  # old production hostname
    "d2lraS5oZWlkcmljaC1kaWdpdGFsLmRl",          # internal wiki
    "dGlsbGhlaWRyaWNoLUFCQ0Q=",                  # a real tenant name in a pairing example
)]


def _tracked_text_files():
    names = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    for name in names.decode("utf-8").split("\0"):
        if not name or name.endswith((".cube", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ttf", ".otf", ".woff",
                                      ".woff2", ".ico", ".mp3", ".wav", ".mp4", ".zip", ".pdf")):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def test_no_internal_business_folder():
    assert not (ROOT / "docs" / "intern").exists()


def test_no_private_address_hostname_or_wiki_link_in_any_tracked_file():
    found = [(name, frag) for name, text in _tracked_text_files() for frag in FRAGMENTS if frag in text]
    assert found == []
