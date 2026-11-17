"""Wer wir sind, welche Fassung läuft — eine Stelle für beide Programme.

Die Versionsnummer steht in der Datei ``VERSION`` im Projektstamm und gilt für
das ganze Ökosystem: Galerie, Box und Sync tragen dieselbe Nummer. So kann
niemand raten, welche Fassung auf welchem Rechner läuft.

Der Commit kommt beim Deploy aus der Umgebung (Coolify setzt ihn), sonst aus
dem Git-Ordner, sonst gar nicht — eine Anzeige ohne Commit ist besser als eine
Anzeige, die einen falschen behauptet.

The source address is the AGPL section 13 offer: whoever runs a modified
gallery must let its users get the source. A fork sets ``PHOTOBOX_SOURCE_URL``
instead of editing this file. The commit can also come from a ``BUILD_COMMIT``
file written at build time, so a deployment without ``.git`` and without a
build variable still names its build; git is only the last resort, never a
requirement.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

PROJECT_NAME = "Photobox Gallery"
PROJECT_URL = "https://photobox-gallery.com"
DEFAULT_REPO_URL = "https://github.com/tillheidrich/photobox-gallery"
REPO_URL = DEFAULT_REPO_URL          # older imports; the live value is repo_url()
SOURCE_URL_ENV = "PHOTOBOX_SOURCE_URL"
LICENSE = "AGPL-3.0-or-later"
MAINTAINER = "Till Heidrich"

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
BUILD_COMMIT_FILE = ROOT / "BUILD_COMMIT"
FALLBACK_VERSION = "0.0.0"

# Coolify und die meisten Build-Systeme legen den Commit in eine dieser Variablen.
COMMIT_ENV = ("PHOTOBOX_COMMIT", "SOURCE_COMMIT", "COMMIT_SHA", "GIT_COMMIT", "GITHUB_SHA")


@lru_cache(maxsize=1)
def version() -> str:
    """Fassung des Ökosystems, z. B. ``0.9.1``."""
    env = os.environ.get("PHOTOBOX_VERSION", "").strip()
    if env:
        return env
    try:
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return FALLBACK_VERSION
    return text.splitlines()[0].strip() if text else FALLBACK_VERSION


def repo_url() -> str:
    """Where the source of this deployment is. Anything but a web address is ignored."""
    value = os.environ.get(SOURCE_URL_ENV, "").strip()
    return value if value.startswith(("https://", "http://")) else DEFAULT_REPO_URL


@lru_cache(maxsize=1)
def commit(short: bool = True) -> str:
    """Commit dieser Fassung — leer, wenn er sich nicht sicher ermitteln lässt."""
    for key in COMMIT_ENV:
        value = os.environ.get(key, "").strip()
        if value:
            return value[:7] if short else value
    try:
        value = BUILD_COMMIT_FILE.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        value = ""
    if value and all(c in "0123456789abcdef" for c in value.lower()):
        return value[:7] if short else value
    try:
        args = ["git", "-C", str(ROOT), "rev-parse"] + (["--short"] if short else []) + ["HEAD"]
        out = subprocess.run(args, capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def info(include_environment: bool = False) -> Dict[str, Any]:
    """Alles, was eine „Über"-Seite zeigen darf. Keine Geheimnisse, nie."""
    data: Dict[str, Any] = {
        "name": PROJECT_NAME,
        "version": version(),
        "commit": commit(),
        "project_url": PROJECT_URL,
        "repo_url": repo_url(),
        "license": LICENSE,
        "maintainer": MAINTAINER,
    }
    if include_environment:                      # nur für die Verwaltung
        import platform
        import sys
        data["python"] = sys.version.split()[0]
        data["platform"] = platform.platform(terse=True)
        data["data_dir"] = os.environ.get("DATA_DIR", "./data")
    return data
