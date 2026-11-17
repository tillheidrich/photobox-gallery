#!/usr/bin/env python3
"""Write THIRD-PARTY-NOTICES.txt from what a build really bundles - or stop the build.

    python tools/third_party_notices.py --out dist/THIRD-PARTY-NOTICES.txt
    python tools/third_party_notices.py --platform win32 --out ...   (what a Windows build bundles)

Run it with the interpreter of the build environment (build.ps1 does): the
list of packages is not typed in here, it is read from that environment. The
roots are the booth's and the sync's requirement files (with their platform
markers evaluated for the target), and every package they pull in is followed
through its installed metadata. The PyInstaller specs pack the same closure
(``copy_metadata(..., recursive=True)``), so the notices and the .exe agree.

On top of the Python packages come the parts that are not pip packages: the
Python runtime itself, the PyInstaller bootloader inside the .exe, the fonts,
the ffmpeg binary and the WebView2 SDK files that ride along with pywebview.

The build stops (exit code 1) when a bundled component has no licence text
it could reproduce, and (exit code 2) when a licence is unknown or does not
go together with the project's AGPL-3.0-or-later. A licence nobody looked at
must not end up in a download because the build happened to be green.

Exit codes: 0 written, 1 licence text missing, 2 licence unknown/incompatible,
3 a root package is not installed (use --allow-missing only for a dry run).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import sysconfig
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENT_FILES = ("booth/requirements.txt", "sync/requirements.txt")
PROJECT_LICENCE = "AGPL-3.0-or-later"
#: Our own packages: covered by LICENSE, not third party.
OWN_PACKAGES = {"photobox-sync", "photobox-booth", "boothcore"}

# ── licences ────────────────────────────────────────────────────────────────
# Lower-case SPDX ids that may be combined with an AGPL-3.0-or-later program
# and passed on with it (permissive, weak copyleft, GPL-3/AGPL-3 family and
# GPL-2.0-or-later, which may be taken under version 3).
COMPATIBLE = {
    "mit", "mit-0", "mit-cmu", "bsd-2-clause", "bsd-3-clause", "0bsd", "isc", "apache-2.0",
    "psf-2.0", "python-2.0", "zlib", "cc0-1.0", "unlicense", "bsl-1.0", "hpnd", "ofl-1.1",
    "mpl-2.0", "lgpl-2.1-only", "lgpl-2.1-or-later", "lgpl-3.0-only", "lgpl-3.0-or-later",
    "gpl-2.0-or-later", "gpl-3.0-only", "gpl-3.0-or-later", "agpl-3.0-only", "agpl-3.0-or-later",
    "ms-webview2-sdk",            # BSD-3-Clause-style text of Microsoft.Web.WebView2, see docs/lizenzen
    # Deprecated SPDX spellings still found in metadata (paramiko: "LGPL-2.1").
    "lgpl-2.1", "lgpl-3.0", "gpl-3.0", "agpl-3.0",
}
# Known and not combinable with this project. Named so the message says why.
INCOMPATIBLE = {
    "gpl-2.0-only": "GPL-2.0 ohne „or later“ lässt sich nicht mit AGPL-3.0 kombinieren",
    "gpl-2.0": "GPL-2.0 ohne „or later“ lässt sich nicht mit AGPL-3.0 kombinieren",
    "gpl-1.0-only": "GPL-1.0 ohne „or later“",
    "sspl-1.0": "SSPL ist keine freie Lizenz im Sinne der AGPL",
    "busl-1.1": "Business Source License ist nicht frei",
    "cc-by-nc-4.0": "nicht-kommerziell: verträgt sich nicht mit der AGPL",
    "proprietary": "proprietär - darf nicht in einem freien Build liegen",
}
# Free-text "License:" values and trove classifiers, mapped to SPDX ids.
# Only unambiguous phrases; anything else stays unknown and stops the build.
PHRASES: Tuple[Tuple[str, str], ...] = (
    ("other/proprietary license", "proprietary"),
    ("gnu affero general public license v3 or later", "agpl-3.0-or-later"),
    ("gnu lesser general public license v2 or later", "lgpl-2.1-or-later"),
    ("gnu lesser general public license v3 or later", "lgpl-3.0-or-later"),
    ("gnu lesser general public license v3", "lgpl-3.0-only"),
    ("gnu library or lesser general public license", "lgpl-2.1-or-later"),
    ("gnu lesser general public license v2", "lgpl-2.1-only"),
    ("gnu general public license v2 or later", "gpl-2.0-or-later"),
    ("gnu general public license v3 or later", "gpl-3.0-or-later"),
    ("gnu general public license v3", "gpl-3.0-only"),
    ("gnu general public license v2", "gpl-2.0-only"),
    ("historical permission notice and disclaimer", "hpnd"),
    ("python software foundation license", "psf-2.0"),
    ("mozilla public license 2.0", "mpl-2.0"),
    ("apache software license", "apache-2.0"),
    ("apache license 2.0", "apache-2.0"),
    ("apache license, version 2.0", "apache-2.0"),
    ("apache 2.0", "apache-2.0"),
    ("mit no attribution", "mit-0"),
    ("mit license", "mit"),
    ("bsd 3-clause", "bsd-3-clause"),
    ("3-clause bsd", "bsd-3-clause"),
    ("new bsd", "bsd-3-clause"),
    ("bsd 2-clause", "bsd-2-clause"),
    ("bsd license", "bsd-3-clause"),
    ("isc license", "isc"),
    ("zlib/libpng", "zlib"),
    ("the unlicense", "unlicense"),
    ("cc0 1.0", "cc0-1.0"),
)
# Short "License:" values that are SPDX-like but not exactly SPDX.
SHORT = {"mit": "mit", "bsd": "bsd-3-clause", "psf": "psf-2.0", "apache 2": "apache-2.0",
         "apache-2": "apache-2.0", "mpl 2.0": "mpl-2.0", "lgpl": "lgpl-2.1-or-later",
         "isc": "isc", "zlib": "zlib", "bsd-3": "bsd-3-clause"}
# Typical first sentences of licence texts, for packages that only ship the text.
TEXT_HINTS: Tuple[Tuple[str, str], ...] = (
    ("permission is hereby granted, free of charge", "mit"),
    ("redistribution and use in source and binary forms", "bsd-3-clause"),
    ("apache license\nversion 2.0", "apache-2.0"),
    ("apache license, version 2.0", "apache-2.0"),
    ("mozilla public license version 2.0", "mpl-2.0"),
)
#: Packages whose metadata does not say enough, with the reason. Every entry
#: here was checked by a person; the build trusts it. Key: normalised name.
OVERRIDES: Dict[str, Tuple[str, str]] = {
    # proxy_tools 0.1.0 comes in with pywebview on Windows only. PyPI has only
    # an sdist (uploaded 2014-05-05 21:02 UTC) without a licence file, so the
    # installed package carries no text and the first installer build stopped
    # (15.11.2026). PyPI metadata says "MIT"; the module header says "BSD, see
    # LICENSE" (code extracted from Werkzeug); the repository's LICENSE.txt,
    # committed before the upload (ccd35a5, 2014-05-05 17:11 UTC), is a
    # two-clause BSD text with the copyright of Armin Ronacher (2013) and
    # Jonathan Tushman (2014). We reproduce that text verbatim and declare
    # BSD-2-Clause; MIT, what PyPI says, would be compatible as well.
    "proxy-tools": ("BSD-2-Clause",
                    "Lizenztext aus dem Repository (PyPI-sdist ohne Datei): https://github.com/jtushman/proxy_tools/"
                    "blob/ccd35a569b95bec271a59890780c7821756c548a/LICENSE.txt; PyPI-Metadaten sagen MIT"),
}
#: Licence texts in this repository for packages that ship none (see OVERRIDES).
OVERRIDE_TEXTS: Dict[str, str] = {
    "proxy-tools": "docs/lizenzen/BSD-2-Clause-proxy_tools.txt",
}
# The other Windows-only packages, checked on 15.11.2026 from their PyPI wheels
# without a Windows machine: pywebview 6.2.1 (BSD-3-Clause, LICENSE in the
# wheel), pythonnet 3.2.1 (MIT, LICENSE), clr_loader 0.3.1 (MIT, LICENSE; no
# licence field, recognised by its text), bottle 0.13.4 (MIT, LICENSE),
# colorama 0.4.6 (BSD-3-Clause, LICENSE.txt; pulled in by click on Windows),
# pywin32 312 (PSF plus the texts of its parts, in the wheel). They need no
# override. pywin32-ctypes, pefile and altgraph are PyInstaller's own build
# tools: they are not in the closure of booth/ and sync/ requirements and do
# not end up in the .exe, so they are not listed (PyInstaller's bootloader is,
# see pyinstaller_bootloader()).

LICENCE_FILE = re.compile(r"(?i)^(licen[cs]e|copying|notice|copyright)([._-].*)?$")
_TOKEN = re.compile(r"\(|\)|[A-Za-z0-9.+\-]+")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _spdx_tokens(expression: str) -> List[str]:
    return _TOKEN.findall(expression)


def verdict(expression: str) -> Tuple[str, str]:
    """("ok"|"incompatible"|"unknown", reason) for an SPDX-like expression.

    ``A OR B`` is fine when one side is; ``A AND B`` only when both are.
    ``WITH <exception>`` only ever widens a licence and is ignored here.
    """
    tokens = _spdx_tokens(expression)
    pos = 0

    def atom() -> Tuple[str, str]:
        nonlocal pos
        if pos < len(tokens) and tokens[pos] == "(":
            pos += 1
            result = expr()
            pos += 1                                  # ")"
        else:
            ident = tokens[pos].lower() if pos < len(tokens) else ""
            pos += 1
            if ident.endswith("+"):
                ident = ident[:-1] + "-or-later"
            if ident in COMPATIBLE:
                result = ("ok", ident)
            elif ident in INCOMPATIBLE:
                result = ("incompatible", f"{ident}: {INCOMPATIBLE[ident]}")
            else:
                result = ("unknown", ident or "(leer)")
        if pos < len(tokens) and tokens[pos].upper() == "WITH":
            pos += 2
        return result

    def term() -> Tuple[str, str]:
        nonlocal pos
        left = atom()
        while pos < len(tokens) and tokens[pos].upper() == "AND":
            pos += 1
            right = atom()
            if left[0] == "ok" and right[0] != "ok":
                left = right
        return left

    def expr() -> Tuple[str, str]:
        nonlocal pos
        left = term()
        while pos < len(tokens) and tokens[pos].upper() == "OR":
            pos += 1
            right = term()
            if right[0] == "ok" or left[0] == "unknown":
                left = right if left[0] != "ok" else left
        return left

    if not tokens:
        return "unknown", "(keine Angabe)"
    return expr()


def licence_of(meta, texts: Sequence[str] = ()) -> str:
    """The licence a package declares, as an SPDX-like expression ("" = unknown).

    Order: ``License-Expression`` (PEP 639), a short ``License`` field, the
    trove classifiers, and last the wording of the licence file itself.
    """
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    field_value = (meta.get("License") or "").strip()
    if field_value and len(field_value) <= 80 and "\n" not in field_value:
        low = field_value.lower()
        if low in SHORT:
            return SHORT[low]
        if verdict(field_value)[0] != "unknown":
            return field_value
        for phrase, spdx in PHRASES:
            if phrase in low:
                return spdx
    found = []
    for classifier in meta.get_all("Classifier") or []:
        if not classifier.startswith("License ::"):
            continue
        low = classifier.lower()
        for phrase, spdx in PHRASES:
            if phrase in low:
                found.append(spdx)
                break
    if found:
        return " OR ".join(sorted(set(found)))
    for text in list(texts) + ([field_value] if len(field_value) > 80 else []):
        low = " ".join(text.lower().split())
        low_lines = text.lower()
        for hint, spdx in TEXT_HINTS:
            if hint in low or hint in low_lines:
                return spdx
    return ""


@dataclass
class Component:
    name: str
    version: str
    licence: str
    texts: List[Tuple[str, str]] = field(default_factory=list)   # (file name, text)
    note: str = ""
    kind: str = "python"

    @property
    def verdict(self) -> Tuple[str, str]:
        return verdict(self.licence)


def _licence_texts(dist) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    seen = set()
    for f in dist.files or []:
        name = Path(str(f)).name
        if not LICENCE_FILE.match(name):
            continue
        parts = Path(str(f)).parts
        # Inside the dist-info folder (incl. PEP 639 licenses/) or the
        # package's top level - not a LICENSE deep inside vendored tests.
        if not (any(p.endswith(".dist-info") for p in parts) or len(parts) <= 2):
            continue
        try:
            text = Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        digest = hashlib.sha256(text.encode()).hexdigest()
        if text.strip() and digest not in seen:
            seen.add(digest)
            out.append((str(f).replace("\\", "/"), text))
    return out


def component_from_dist(dist) -> Component:
    meta = dist.metadata
    name = meta["Name"]
    texts = _licence_texts(dist)
    licence = licence_of(meta, [t for _, t in texts])
    note = ""
    if normalise(name) in OVERRIDES:
        licence, note = OVERRIDES[normalise(name)]
    if not texts and normalise(name) in OVERRIDE_TEXTS:
        rel = OVERRIDE_TEXTS[normalise(name)]
        texts = [(rel, (ROOT / rel).read_text(encoding="utf-8"))]
    long_field = (meta.get("License") or "").strip()
    if not texts and len(long_field) > 200:
        texts = [("METADATA License", long_field)]       # the text sits in the field itself
    return Component(name=name, version=meta["Version"], licence=licence, texts=texts, note=note)


# ── which packages ──────────────────────────────────────────────────────────

def read_roots(files: Iterable[Path]) -> List[str]:
    """Requirement lines of the build, without editable installs and options."""
    lines: List[str] = []
    for path in files:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split(" #")[0].strip()
            if not line or line.startswith(("#", "-")):
                continue
            lines.append(line)
    return lines


def marker_env(platform: str) -> Dict[str, str]:
    from packaging.markers import default_environment
    env = default_environment()
    if platform == "win32":
        env.update({"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"})
    env["extra"] = ""
    return env


def closure(roots: Sequence[str], platform: str,
            get: Callable[[str], object] = metadata.distribution) -> Tuple[List[object], List[str]]:
    """Every installed distribution the roots need on ``platform``; plus the names not installed."""
    from packaging.requirements import Requirement
    env = marker_env(platform)
    todo = [Requirement(r) for r in roots]
    seen: Dict[str, object] = {}
    missing: List[str] = []
    while todo:
        req = todo.pop(0)
        if req.marker is not None and not req.marker.evaluate(env):
            continue
        key = normalise(req.name)
        if key in seen or key in OWN_PACKAGES or key in missing:
            continue
        try:
            dist = get(req.name)
        except metadata.PackageNotFoundError:
            missing.append(key)
            continue
        seen[key] = dist
        for sub in dist.requires or []:
            sub_req = Requirement(sub)
            # Optional extras are not installed by the build; skip them.
            if sub_req.marker is not None and "extra" in str(sub_req.marker):
                continue
            todo.append(sub_req)
    return list(seen.values()), missing


# ── what is not a pip package ───────────────────────────────────────────────

def _repo_text(rel: str) -> List[Tuple[str, str]]:
    path = ROOT / rel
    return [(rel, path.read_text(encoding="utf-8"))] if path.is_file() else []


def python_runtime() -> Component:
    """The interpreter inside the .exe. Its LICENSE.txt also lists the libraries
    the python.org build bundles (OpenSSL, libffi, SQLite, zlib, ...)."""
    version = ".".join(map(str, sys.version_info[:3]))
    candidates = [Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE",
                  Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt"]
    texts = []
    for c in candidates:
        if c.is_file():
            texts = [(c.name, c.read_text(encoding="utf-8", errors="replace"))]
            break
    return Component("Python", version, "PSF-2.0", texts, kind="runtime",
                     note="Interpreter und Standardbibliothek in der .exe")


def pyinstaller_bootloader(get: Callable[[str], object] = metadata.distribution) -> Optional[Component]:
    """PyInstaller's bootloader is the start code of every .exe it builds.

    GPL-2.0-or-later with the bootloader exception, which allows passing on
    the built program under its own licence; the text is in PyInstaller's
    COPYING.txt. ``None`` when PyInstaller is not installed (no .exe build).
    """
    try:
        dist = get("pyinstaller")
    except metadata.PackageNotFoundError:
        return None
    c = component_from_dist(dist)
    c.licence = "GPL-2.0-or-later WITH Bootloader-exception"
    c.kind, c.note = "build", "nur der Bootloader steckt in der .exe"
    return c


def static_components(platform: str, has_webview: bool) -> List[Component]:
    out = [
        Component("Inter (Schrift)", "variable/4.x", "OFL-1.1", _repo_text("docs/lizenzen/OFL-Inter.txt"),
                  kind="font", note="booth/boothcore/media/fonts, booth/boothcore/web/static/fonts"),
        Component("Archivo (Schrift)", "variable", "OFL-1.1", _repo_text("docs/lizenzen/OFL-Archivo.txt"),
                  kind="font", note="booth/boothcore/media/fonts, booth/boothcore/web/static/fonts"),
        Component("FFmpeg (ffmpeg.exe aus imageio-ffmpeg)", "siehe ffmpeg -version",
                  "GPL-2.0-or-later", _repo_text("docs/lizenzen/GPL-2.0.txt"), kind="program",
                  note="eigenständiges Programm, wird nur aufgerufen, nicht gelinkt; "
                       "Quelltext-Angebot siehe Kopf dieser Datei"),
    ]
    if platform == "win32" and has_webview:
        out.append(Component(
            "Microsoft Edge WebView2 SDK (Microsoft.Web.WebView2.Core/WinForms.dll, WebView2Loader.dll)",
            "aus dem pywebview-Paket", "MS-WebView2-SDK", _repo_text("docs/lizenzen/WebView2-SDK.txt"),
            kind="sdk", note="Die WebView2-Laufzeit selbst ist Teil von Windows und wird nicht mitgeliefert."))
    return out


# ── output ──────────────────────────────────────────────────────────────────

HEADER = """\
Photobox - Hinweise zu Komponenten Dritter / Third-party notices
=================================================================

Photobox (Fotobox-Programm, Galerie, Sync) ist freie Software unter der
GNU Affero General Public License, Version 3 oder neuer (AGPL-3.0-or-later),
mit der Zusatzerlaubnis nach Abschnitt 7 in LICENSE-EXCEPTION. Diese Datei
nennt alles, was in diesem Build von anderen stammt, mit Lizenz und
Lizenztext. Sie wird beim Bau aus der Bauumgebung erzeugt
(tools/third_party_notices.py), nicht von Hand gepflegt.

Photobox is free software under the AGPL-3.0-or-later with the section 7
permission in LICENSE-EXCEPTION. This file lists every component in this
build that was written by someone else, with its licence and licence text.
It is generated from the build environment at build time.

Nicht enthalten / not included: das Canon EOS Digital SDK (EDSDK). Es steht
unter Canons eigenen Bedingungen; wer eine Canon-Kamera über USB nutzt, holt
es selbst bei Canon und richtet es mit "PhotoboxBooth.exe --install-edsdk"
ein.

FFmpeg - schriftliches Angebot / written offer (GPL-2.0, Abschnitt 3(b)):
Drei Jahre lang nach Weitergabe eines Builds, der ffmpeg enthält, stellt der
Herausgeber ({publisher}) auf Anfrage den vollständigen Quelltext genau dieses
ffmpeg-Programms bereit, für nicht mehr als die Kosten der Bereitstellung.
Kontakt: {contact}. FFmpeg-Quelltext: https://ffmpeg.org/download.html

Build: Plattform {platform}, Python {python}, erzeugt {when}
"""


def render(components: Sequence[Component], publisher: Dict[str, str], platform: str, when: str) -> str:
    contact = publisher.get("email") or publisher.get("support_url") or publisher.get("project_url", "")
    head = HEADER.format(publisher=publisher.get("name", "Till Heidrich"), contact=contact,
                         platform=platform, python=".".join(map(str, sys.version_info[:3])), when=when)
    lines = [head, "", "Übersicht / Summary", "-------------------", ""]
    width = max(len(c.name) for c in components) if components else 10
    for c in components:
        lines.append(f"{c.name.ljust(width)}  {c.version:<16} {c.licence}")
    for c in components:
        lines += ["", "", "=" * 78, f"{c.name} {c.version}", f"Lizenz / licence: {c.licence}"]
        if c.note:
            lines.append(f"Hinweis / note: {c.note}")
        lines.append("=" * 78)
        for fname, text in c.texts:
            lines += ["", f"--- {fname} ---", "", text.rstrip()]
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class Report:
    components: List[Component]
    missing_text: List[str]
    bad_licence: List[str]
    not_installed: List[str]

    @property
    def code(self) -> int:
        if self.not_installed:
            return 3
        if self.missing_text:
            return 1
        if self.bad_licence:
            return 2
        return 0


def check(components: Sequence[Component], not_installed: Sequence[str] = ()) -> Report:
    missing = [f"{c.name} {c.version}" for c in components if not c.texts]
    bad = []
    for c in components:
        state, why = c.verdict
        if state != "ok":
            label = "unbekannte Lizenz" if state == "unknown" else "nicht vereinbar"
            bad.append(f"{c.name} {c.version}: {label} ({c.licence or 'keine Angabe'}; {why})")
    return Report(list(components), missing, bad, list(not_installed))


def collect(platform: str, allow_missing: bool = False,
            get: Callable[[str], object] = metadata.distribution) -> Report:
    roots = read_roots(ROOT / f for f in REQUIREMENT_FILES)
    dists, not_installed = closure(roots, platform, get)
    comps = sorted((component_from_dist(d) for d in dists), key=lambda c: c.name.lower())
    has_webview = any(normalise(c.name) == "pywebview" for c in comps)
    extra = [python_runtime()]
    boot = pyinstaller_bootloader(get)
    if boot:
        extra.append(boot)
    report = check(extra + comps + static_components(platform, has_webview),
                   [] if allow_missing else not_installed)
    return report


# ── publisher (provider identification) ─────────────────────────────────────

PUBLISHER_EXAMPLE = ROOT / "packaging" / "windows" / "publisher.example.json"
PUBLISHER_REAL = ROOT / "packaging" / "windows" / "publisher.json"
PUBLISHER_KEYS = ("name", "project_url", "support_url", "address", "email", "phone")


def load_publisher(real: Path = PUBLISHER_REAL, example: Path = PUBLISHER_EXAMPLE) -> Dict[str, str]:
    """Who publishes this build: the owner's git-ignored publisher.json over the example.

    A value that is still the example's placeholder is dropped, so a build
    without the real file names the publisher and the project, and no
    invented address ends up in a download.
    """
    def _read(p: Path) -> Dict[str, str]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: str(v).strip() for k, v in data.items() if k in PUBLISHER_KEYS and isinstance(v, str)}

    sample = _read(example)
    own = _read(real)
    placeholders = {v for k, v in sample.items() if k not in ("name", "project_url", "support_url")}
    merged = {**{k: sample.get(k, "") for k in ("name", "project_url", "support_url")}, **own}
    return {k: v for k, v in merged.items() if v and v not in placeholders}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, help="THIRD-PARTY-NOTICES.txt to write")
    ap.add_argument("--json", type=Path, help="also write the inventory as JSON")
    ap.add_argument("--platform", default=sys.platform, help="target platform for the markers (win32 for the .exe)")
    ap.add_argument("--allow-missing", action="store_true",
                    help="dry run outside the build env: packages that are not installed are only reported")
    ap.add_argument("--publisher-out", type=Path,
                    help="write the effective publisher card (publisher.json without placeholders)")
    args = ap.parse_args(argv)

    from datetime import datetime, timezone
    if args.publisher_out:
        args.publisher_out.parent.mkdir(parents=True, exist_ok=True)
        args.publisher_out.write_text(json.dumps(load_publisher(), indent=1, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    report = collect(args.platform, args.allow_missing)
    publisher = load_publisher()
    for c in report.components:
        state = c.verdict[0]
        print(f"{'OK ' if state == 'ok' and c.texts else '!! '}{c.name} {c.version}: {c.licence or '?'}"
              f"{'' if c.texts else '  (KEIN LIZENZTEXT)'}")
    if args.allow_missing:
        _, missing = closure(read_roots(ROOT / f for f in REQUIREMENT_FILES), args.platform)
        for name in missing:
            print(f"-- nicht installiert, nicht geprüft: {name}")
    for line in report.missing_text:
        print(f"FEHLER: kein Lizenztext für {line}", file=sys.stderr)
    for line in report.bad_licence:
        print(f"FEHLER: {line}", file=sys.stderr)
    for name in report.not_installed:
        print(f"FEHLER: {name} gehört zum Build, ist hier aber nicht installiert", file=sys.stderr)
    if report.code:
        print("Nichts geschrieben. Lizenz klären (oder in OVERRIDES mit Begründung eintragen).", file=sys.stderr)
        return report.code
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = render(report.components, publisher, args.platform, when)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # UTF-8 with BOM: the Windows installer reads the file as text page.
        args.out.write_text(text, encoding="utf-8-sig", newline="\r\n" if args.platform == "win32" else "\n")
        print(f"geschrieben: {args.out} ({len(report.components)} Komponenten)")
    if args.json:
        inventory = [{"name": c.name, "version": c.version, "licence": c.licence, "kind": c.kind,
                      "files": [f for f, _ in c.texts], "note": c.note} for c in report.components]
        args.json.write_text(json.dumps(inventory, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
