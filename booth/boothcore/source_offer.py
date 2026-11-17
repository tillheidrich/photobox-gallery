"""Where the booth's source code lives and which build is running.

The booth is rented out with the program on it, so whoever has it in hand
must be able to find the source of exactly that build (AGPL-3.0, sections 6
and 13). Two facts answer that: an address and a commit.

* The address defaults to the project's repository. A fork or a modified
  build sets ``PHOTOBOX_SOURCE_URL`` instead of editing this file.
* The commit is written into the package by the build (the PyInstaller specs
  put a ``BUILD_COMMIT`` file next to this module) or passed in as
  ``PHOTOBOX_COMMIT``. The booth never runs git: a booth PC has no git, and an
  About card that guesses a commit is worse than one that shows none.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DEFAULT_SOURCE_URL = "https://github.com/tillheidrich/photobox-gallery"
SOURCE_URL_ENV = "PHOTOBOX_SOURCE_URL"
COMMIT_ENV = "PHOTOBOX_COMMIT"
BUILD_COMMIT_FILE = Path(__file__).resolve().parent / "BUILD_COMMIT"
LICENSE = "AGPL-3.0-or-later"


def source_url() -> str:
    """The address shown as "Quelltext". Anything that is not a web address is ignored."""
    value = os.environ.get(SOURCE_URL_ENV, "").strip()
    return value if value.startswith(("https://", "http://")) else DEFAULT_SOURCE_URL


def commit(path: Optional[Path] = None, short: bool = True) -> str:
    """The commit this build was made from, or "" when nobody wrote it down."""
    value = os.environ.get(COMMIT_ENV, "").strip()
    if not value:
        try:
            value = (path or BUILD_COMMIT_FILE).read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            value = ""
    if not all(c in "0123456789abcdef" for c in value.lower()):
        return ""                      # a damaged file must not end up on the card
    return value[:7] if short else value
