# Third-party components

Photobox Gallery itself is AGPL-3.0-or-later ([`LICENSE`](LICENSE)), with the
section 7 permission for the Canon EDSDK in
[`LICENSE-EXCEPTION`](LICENSE-EXCEPTION). This file lists everything in the
repository or in a build that was written by someone else, with its licence.
It travels with the gallery image and with the Windows and macOS builds of
the booth and the sync program.

Licence texts that are not part of a package's own files live in
[`docs/lizenzen/`](docs/lizenzen/).

**Not included, ever:** the Canon EOS Digital SDK (EDSDK). It is Canon's
proprietary SDK under Canon's own agreement. Every operator obtains it from
Canon and installs it on their own machine (`--install-edsdk`).

## Fonts

All three are under the SIL Open Font License 1.1. The copyright line and the
full licence text of each are in `docs/lizenzen/`. No Reserved Font Name is
declared in any of them.

| Font | Copyright | Licence text | Files |
|---|---|---|---|
| Inter | 2016 The Inter Project Authors (github.com/rsms/inter) | `docs/lizenzen/OFL-Inter.txt` | `app/static/fonts/inter-variable.woff2`, `app/static/fonts/ttf/Inter-*.ttf`, `booth/boothcore/media/fonts/Inter-*.ttf`, `booth/boothcore/web/static/fonts/inter-variable.woff2` |
| Archivo | 2020 The Archivo Project Authors (github.com/Omnibus-Type/Archivo) | `docs/lizenzen/OFL-Archivo.txt` | `app/static/fonts/archivo-variable.woff2`, `app/static/fonts/ttf/Archivo-*.ttf`, `booth/boothcore/media/fonts/Archivo-*.ttf`, `booth/boothcore/web/static/fonts/archivo-variable.woff2` |
| JetBrains Mono | 2020 The JetBrains Mono Project Authors (github.com/JetBrains/JetBrainsMono) | `docs/lizenzen/OFL-JetBrains-Mono.txt` | `app/static/fonts/jetbrains-mono-variable.woff2` |

Each font folder carries a `LICENSES.txt` that points here.

The built-in template collection (`app/vorlagen_bibliothek/medien/`) ships
five more families as template media, also under the SIL Open Font License
1.1 and unmodified. The PNG pictures in the same folder are produced by this
project's own code in `tools/kollektion` (`python -m tools.kollektion.bauen`)
and are part of the project under its licence. DM Serif Display declares the Reserved Font Name
"Source" (inherited from Source Serif); the files are distributed unchanged
under their own name.

| Font | Copyright | Licence text | Files |
|---|---|---|---|
| Cormorant SC | 2015 The Cormorant Project Authors (github.com/CatharsisFonts/Cormorant) | `docs/lizenzen/OFL-Cormorant.txt` | `app/vorlagen_bibliothek/medien/CormorantSC-Medium.ttf`, `CormorantSC-SemiBold.ttf` |
| Pinyon Script | 2024 The Pinyon Project Authors (github.com/SorkinType/Pinyon) | `docs/lizenzen/OFL-PinyonScript.txt` | `app/vorlagen_bibliothek/medien/PinyonScript-Regular.ttf` |
| DM Serif Display | 2014-2018 Adobe, 2019 Google LLC | `docs/lizenzen/OFL-DMSerifDisplay.txt` | `app/vorlagen_bibliothek/medien/DMSerifDisplay-Regular.ttf`, `DMSerifDisplay-Italic.ttf` |
| Great Vibes | 2015 The Great Vibes Pro Project Authors (github.com/googlefonts/great-vibes) | `docs/lizenzen/OFL-GreatVibes.txt` | `app/vorlagen_bibliothek/medien/GreatVibes-Regular.ttf` |
| Courier Prime | 2015 The Courier Prime Project Authors (github.com/quoteunquoteapps/CourierPrime) | `docs/lizenzen/OFL-CourierPrime.txt` | `app/vorlagen_bibliothek/medien/CourierPrime-Regular.ttf` |

The files were taken unchanged from the Google Fonts repository
(github.com/google/fonts, `ofl/cormorantsc`, `ofl/pinyonscript`,
`ofl/dmserifdisplay`, `ofl/greatvibes`, `ofl/courierprime`) on 15.11.2026.
The collection's own licence note is `docs/vorlagen/LIZENZEN.md`.

## ffmpeg (inside the booth builds)

The booth uses ffmpeg for videos, boomerangs and GIFs. It is not in this
repository; the builds take it from the Python package `imageio-ffmpeg`
(BSD-2-Clause), whose wheels contain a prebuilt ffmpeg executable. The booth
runs it as a separate program, it does not link against it.

* **Licence:** the ffmpeg binary in `imageio-ffmpeg` 0.8.1 for macOS arm64
  (`ffmpeg-macos-aarch64-v9.0`, FFmpeg 9.0) is configured with
  `--enable-gpl` and libx264/libx265; `ffmpeg -L` reports **GPL version 2 or
  later**. The licence text is `docs/lizenzen/GPL-2.0.txt`. The Windows binary
  of the same package could not be checked here (no Windows machine); treat it
  as GPL as well and confirm with `ffmpeg -L` on the build machine before a
  release.
* **Source:** FFmpeg's source is at <https://ffmpeg.org/download.html>
  (releases: <https://ffmpeg.org/releases/>); the build configuration of each
  binary is printed by `ffmpeg -version`. The binaries are built and published
  by the imageio project (<https://github.com/imageio/imageio-binaries>).
* **Written offer:** for three years after a build of this project that
  contains ffmpeg was passed on, the maintainer (Till Heidrich) provides the
  complete corresponding source code of that ffmpeg binary on request, for no
  more than the cost of providing it (GPL-2.0, section 3(b)). To ask for it,
  open an issue at <https://github.com/tillheidrich/photobox-gallery/issues>
  titled "ffmpeg source request" and name the build (version from `/ueber` or
  the installer's file name). A build made by someone else names its own
  publisher and contact in its `THIRD-PARTY-NOTICES.txt`.

## Python packages

Versions are the ones the test suites last passed with, as pinned in
[`constraints.txt`](constraints.txt).
Licences are read from each installed package's own metadata
(`License-Expression`, `License` or the licence file in its `.dist-info`),
not from memory. The package's own licence files are copied into the builds.

### Installed directly (`requirements.txt`, `booth/requirements.txt`, `sync/requirements.txt`)

| Package | Version | Licence | Used by |
|---|---|---|---|
| Flask | 3.1.3 | BSD-3-Clause | gallery |
| Werkzeug | 3.1.9 | BSD-3-Clause | gallery |
| Markdown | 3.11.3 | BSD-3-Clause | gallery |
| gunicorn | 27.0.0 | MIT | gallery |
| requests | 2.34.2 | Apache-2.0 | gallery |
| pyftpdlib | 2.2.0 | MIT (licence file) | gallery |
| pyOpenSSL | 26.4.0 | Apache-2.0 | gallery |
| Pillow | 13.0.0 | MIT-CMU | gallery, booth |
| qrcode | 8.2 | BSD-3-Clause (licence file; includes the MIT-style notice of pyqrnative) | gallery, booth |
| paramiko | 5.1.0 | LGPL-2.1 | gallery, booth, sync |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | booth |
| fastapi | 0.162.4 | MIT | booth, sync |
| uvicorn | 0.54.0 | BSD-3-Clause | booth, sync |
| python-multipart | 0.0.33 | Apache-2.0 | booth |
| httpx | 0.28.1 | BSD-3-Clause | booth, sync |
| pluggy | 1.7.0 | MIT | booth |
| psutil | 7.2.2 | BSD-3-Clause | booth, sync |
| opencv-python-headless | 5.0.0.93 | Apache-2.0; the wheel bundles further libraries (among them FFmpeg under LGPL-2.1 and libvpx), listed with their licences in its `LICENSE-3RD-PARTY.txt` | booth |
| imageio-ffmpeg | 0.8.1 | BSD-2-Clause (the bundled ffmpeg binary: see above) | booth |
| pywin32 | not pinned | not read here — Windows only, not installed in the reference environment; check its metadata on the build machine | booth (Windows) |
| pywebview | 6.2.1 | BSD-3-Clause (licence file) | booth (Windows): the own window, `booth/boothcore/window.py` |
| photobox-sync | this project | AGPL-3.0-or-later | booth |

### Pulled in by the packages above

| Package | Version | Licence |
|---|---|---|
| annotated-doc | 0.0.6 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| bcrypt | 5.0.0 | Apache-2.0 |
| blinker | 1.9.0 | MIT (licence file) |
| certifi | 2026.10.23 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.1 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| invoke | 3.0.3 | BSD-2-Clause |
| itsdangerous | 2.2.0 | BSD-3-Clause (licence file) |
| Jinja2 | 3.1.6 | BSD-3-Clause (licence file) |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pyasynchat | 1.0.5 | PSF-2.0 |
| pyasyncore | 1.0.5 | PSF-2.0 |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| PyNaCl | 1.6.2 | Apache-2.0 |
| bottle | not pinned (0.13.4 seen with pywebview 6.2.1) | MIT (pulled in by pywebview, Windows build only) |
| proxy_tools | not pinned (0.1.0 seen) | BSD-2-Clause as in its repository's LICENSE.txt (PyPI metadata says MIT); the sdist ships no text, so `docs/lizenzen/BSD-2-Clause-proxy_tools.txt` holds it (`OVERRIDES` in `tools/third_party_notices.py`, with the source). Pulled in by pywebview, Windows build only |
| pythonnet | not pinned (3.2.1 seen) | MIT, LICENSE in the wheel (checked from PyPI on 15.11.2026); Windows only (pulled in by pywebview) |
| clr_loader | not pinned (0.3.1 seen) | MIT, LICENSE in the wheel (no licence field; recognised by the text); Windows only, as pythonnet |
| colorama | not pinned (0.4.6 seen) | BSD-3-Clause, LICENSE.txt in the wheel; pulled in by click on Windows only |
| starlette | 1.10.0 | BSD-3-Clause |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |

### Only for the tests (`requirements-dev.txt`), never shipped

| Package | Version | Licence |
|---|---|---|
| pytest | 9.2.0 | MIT |
| pytesseract | 0.3.13 | Apache-2.0 |
| iniconfig | 2.3.1 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |

## The own window on Windows (Edge WebView2)

The booth's window (`booth/boothcore/window.py`) is drawn by the **Microsoft
Edge WebView2 Runtime**, which is part of Windows 11 and is not shipped with
the booth. The build carries the two WebView2 SDK assemblies and the native
loader that come inside the pywebview wheel (`webview/lib/`:
`Microsoft.Web.WebView2.Core.dll`, `Microsoft.Web.WebView2.WinForms.dll`,
`runtimes/*/native/WebView2Loader.dll`), plus pywebview's own
`WebBrowserInterop.*.dll`. Microsoft publishes the WebView2 SDK for
redistribution with applications under a BSD-3-Clause-style licence; its text
(from the `Microsoft.Web.WebView2` NuGet package, which the pywebview wheel does
not carry) is in [`docs/lizenzen/WebView2-SDK.txt`](docs/lizenzen/WebView2-SDK.txt)
since 15.11.2026. Compare it with the NuGet page of the SDK version pywebview
bundles before a release. The WebView2 *Runtime* is not shipped: the Windows
installer only offers to download Microsoft's Evergreen Bootstrapper from
Microsoft when the runtime is missing (`docs/INSTALLER.md`).

Why pywebview: it is the smallest maintained library that puts a system web
view into a native window from Python (BSD-3-Clause, pure Python, uses the
WebView2 that Windows already has). CEF and Qt WebEngine would each add a
complete Chromium of their own (100 MB and more) to the .exe; `msedge --app
--kiosk` would still be a browser with its own profile, updates and dialogs.

## Generated with every Windows build

The tables above are written by hand. What a Windows build really contains is
listed by `tools/third_party_notices.py`, which reads the packages installed in
the build environment and writes `THIRD-PARTY-NOTICES.txt` (inside the .exe,
next to it and in the installer). It adds what is not a pip package: the
Python runtime (PSF licence, including the libraries python.org bundles), the
PyInstaller bootloader (GPL-2.0-or-later with the bootloader exception, which
allows the built program to carry its own licence), the fonts, ffmpeg and the
WebView2 SDK files. The build stops when a bundled package has no licence text
or a licence that does not go with AGPL-3.0-or-later.

The setup program (`Photobox-Setup-<version>.exe`) is made with **Inno Setup**
(jrsoftware.org, its own permissive licence; the setup and uninstaller carry
Inno Setup's code and its copyright notice unchanged).

## Notices: trademarks, export

Canon, EOS and SELPHY are trademarks of Canon Inc.; Windows, Edge and WebView2
of Microsoft Corporation; DNP of Dai Nippon Printing Co., Ltd. They are used
only to describe what the software works with; the project is not affiliated
with these companies. The software uses standard TLS from Python's ssl module
(OpenSSL) and the `cryptography`/`PyNaCl`/`bcrypt` packages for transport
security, as published open-source software; it implements no cryptographic
algorithm of its own. (Classification for export purposes was not assessed - an open point for
the publisher, not legal advice.)

## Built-in colour looks (LUTs)

The `.cube` files in `booth/boothcore/media/luts_builtin/` are this
project's own work under its licence (AGPL-3.0-or-later). Nine of them are
generated by `tools/make_luts.py`: each look is defined there by its own
documented maths (tone curve, saturation, channel matrix, split toning) with
parameters chosen for this project, nothing taken from a film-emulation pack
or any other file. The output is deterministic, and
`booth/tests/test_luts_are_generated.py` regenerates every file and compares
it byte for byte. `warm-film.cube` was generated for the project earlier and
says so in its header.

Until 15.11.2026 these nine files had arrived in one commit (`dec8dc2`,
18.09.2026) without a stated origin; they were replaced, not relicensed (final
review V4). Seven looks were named after Kodak and Fujifilm films until 0.3.
Those names are trademarks and the looks are not those films; the old keys
still work (`LEGACY_KEYS` in `booth/boothcore/media/luts.py`) and lead to the
new looks of the same name.

| File | Until 0.3 | Origin | Licence |
|---|---|---|---|
| `warm-film.cube` | — | generated for this project (file header) | AGPL-3.0-or-later, as part of this project |
| `bleach-bypass.cube` | — | `tools/make_luts.py` | AGPL-3.0-or-later, as part of this project |
| `earthy.cube` | — | `tools/make_luts.py` | AGPL-3.0-or-later, as part of this project |
| `vivid-colour.cube` | `film-ektar-100` | `tools/make_luts.py` | AGPL-3.0-or-later, as part of this project |
| `soft-matte.cube` | `film-fuji-800z` | `tools/make_luts.py` | AGPL-3.0-or-later, as part of this project |
| `faded-print.cube` | `film-gold-200` | `tools/make_luts.py` | AGPL-3.0-or-later, as part of this project |
| `natural-portrait.cube` | `film-portra-400` | `tools/make_luts.py` | AGPL-3.0-or-later, as part of this project |
| `soft-cinema.cube` | `fuji-eterna` | `tools/make_luts.py` | AGPL-3.0-or-later, as part of this project |
| `golden-warm.cube` | `kodak-gold` | `tools/make_luts.py` | AGPL-3.0-or-later, as part of this project |
| `classic-mono.cube` | `mono-trix-400` | `tools/make_luts.py` | AGPL-3.0-or-later, as part of this project |
