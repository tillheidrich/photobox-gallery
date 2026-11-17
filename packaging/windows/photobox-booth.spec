# PyInstaller-Bauplan für PhotoboxBooth.exe
#
# Eine Datei, die alles mitbringt: Kiosk-Oberfläche, LUTs, ffmpeg, Sync.
# Bauen: siehe build.ps1 im selben Ordner (läuft auf dem Windows-Rechner).
#
# Nicht enthalten und nie enthalten: Canon EDSDK. Die DLLs kommen von Canon und
# landen im Datenordner der Box unter EDSDK\ — dorthin legt sie
# „PhotoboxBooth.exe --install-edsdk <entpackter SDK-Ordner>“, und dort sucht die
# Box zuerst. Ein Ordner EDSDK\ neben der .exe wird weiterhin gefunden.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(os.environ.get("PHOTOBOX_ROOT", Path.cwd())).resolve()
BOOTH = ROOT / "booth"
ICON = ROOT / "packaging" / "windows" / "photobox.ico"

datas = [
    (str(BOOTH / "boothcore" / "web"), "boothcore/web"),
    (str(BOOTH / "boothcore" / "media" / "luts_builtin"), "boothcore/media/luts_builtin"),
    # Die Schriften der Marke. Ohne sie fällt compose.py auf Segoe UI zurück,
    # und jeder Abzug aus der .exe käme in einer anderen Schrift heraus als
    # jeder Abzug aus dem Quellcode.
    (str(BOOTH / "boothcore" / "media" / "fonts"), "boothcore/media/fonts"),
    # The built-in frames for boomerang and video (tools/build_motion_frames.py).
    (str(BOOTH / "boothcore" / "media" / "motion_frames.json"), "boothcore/media"),
]
datas += collect_data_files("imageio_ffmpeg")        # bringt ffmpeg mit
# The commit this build is made from, as boothcore/BUILD_COMMIT. Whoever gets
# the booth gets the right to the source of exactly this build (AGPL); the
# admin card shows the commit, and the booth itself never runs git.
# PHOTOBOX_COMMIT wins (a CI build), otherwise git on the build machine;
# without either the card simply shows no commit.
def _build_commit_file():
    import subprocess
    import tempfile
    sha = os.environ.get("PHOTOBOX_COMMIT", "").strip()
    if not sha:
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=10)
            sha = out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            sha = ""
    if not sha:
        return None
    path = Path(tempfile.mkdtemp(prefix="photobox-build-")) / "BUILD_COMMIT"
    path.write_text(sha + "\n", encoding="ascii")
    return path


_commit_file = _build_commit_file()
if _commit_file:
    datas.append((str(_commit_file), "boothcore"))

# The licences travel with the program: the project's own, the section 7
# permission for the Canon EDSDK, the list of third-party components and the
# licence texts it points at (fonts: OFL, ffmpeg: GPL-2.0). Same layout as in
# the repository, so the paths named in THIRD_PARTY.md hold inside the build.
datas += [(str(ROOT / _name), ".") for _name in ("LICENSE", "LICENSE-EXCEPTION", "THIRD_PARTY.md")]
datas.append((str(ROOT / "docs" / "lizenzen"), "docs/lizenzen"))
# THIRD-PARTY-NOTICES.txt (generated from this build environment by
# tools/third_party_notices.py) and the publisher card (publisher.json without
# placeholders), served as /static/legal/... for the admin's "Über" card.
# build.ps1 writes them before PyInstaller runs and names the folder in
# PHOTOBOX_LEGAL_DIR; a build without it simply shows neither.
_legal = os.environ.get("PHOTOBOX_LEGAL_DIR", "")
if _legal and Path(_legal).is_dir():
    datas.append((_legal, "boothcore/web/static/legal"))
    datas.append((str(Path(_legal) / "THIRD-PARTY-NOTICES.txt"), "."))
else:
    print("note: PHOTOBOX_LEGAL_DIR is not set - the About card shows no third-party notices")
# Each package keeps its own licence files in its .dist-info folder;
# copy_metadata(recursive=True) brings them along with those of everything the
# package pulls in. A package that is not installed on this platform (pywin32
# outside Windows) is skipped with a note instead of breaking the build.
from importlib.metadata import PackageNotFoundError as _NotInstalled
from PyInstaller.utils.hooks import copy_metadata
for _dist in ('fastapi', 'uvicorn', 'python-multipart', 'pillow', 'qrcode', 'httpx', 'pluggy', 'psutil', 'numpy', 'opencv-python-headless', 'paramiko', 'imageio-ffmpeg', 'pywin32',
              # The own window (boothcore/window.py): pywebview with pythonnet on Windows.
              'pywebview', 'pythonnet', 'clr_loader', 'bottle', 'proxy_tools'):
    try:
        datas += copy_metadata(_dist, recursive=True)
    except _NotInstalled:
        print(f"note: {_dist} is not installed here, its licence files are not packed")

# Kamera- und Druckerbackends werden zur Laufzeit nach Namen geladen —
# PyInstaller sieht das nicht, also hier von Hand.
# `collect_submodules("boothcore")` lieferte hier immer eine leere Liste:
# boothcore ist nicht installiert, und der Kopf des Bauplans läuft, bevor
# `pathex` auf sys.path liegt. Das versprochene Sicherheitsnetz gab es also
# nie. Deshalb pathex vorziehen — dann stimmt der Aufruf auch.
import sys as _sys
for _p in (str(BOOTH), str(ROOT / "sync")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
hiddenimports = (collect_submodules("uvicorn") + collect_submodules("photobox_sync")
                 + collect_submodules("boothcore") + ["qrcode", "encodings.idna",
                    # boothcore/second_target.py imports these only when a sealed password arrives
                    "nacl.public", "nacl.exceptions", "nacl.bindings"])
# pywin32 imports these by name at run time, where PyInstaller does not see
# them: win32print.EnumJobs returns a time that needs win32timezone, and
# without it the booth could not read the Windows print queue (seen on the
# booth PC, 15.11.2026). Printing draws through win32gui + win32print, not
# win32ui: win32ui is MFC and needed mfc140u.dll from the Visual C++ runtime,
# which a fresh Windows does not have ("DLL load failed" on the booth PC).
if __import__("sys").platform == "win32":
    hiddenimports += ["win32timezone", "win32print", "win32gui", "win32con"]

# The own window: pywebview picks its GUI backend by name at run time
# (webview.guilib), so PyInstaller does not see winforms/edgechromium.
# pywebview's own hook (webview/__pyinstaller) brings the WebView2 SDK DLLs
# from webview/lib; pythonnet's runtime DLL and clr_loader's native helper
# are data files of those packages. A build machine without pywebview still
# builds - the booth then falls back to Chrome and says so in its log.
try:
    import webview as _webview  # noqa: F401
    from PyInstaller.utils.hooks import collect_data_files as _cdf, collect_dynamic_libs as _cdl
    hiddenimports += ["webview", "webview.guilib", "webview.platforms.winforms",
                      "webview.platforms.edgechromium", "clr", "clr_loader", "pythonnet"]
    for _pkg in ("webview", "pythonnet", "clr_loader"):
        datas += _cdf(_pkg)
    _webview_binaries = _cdl("webview") + _cdl("clr_loader")
except ImportError:
    print("note: pywebview is not installed here - the build will open Chrome instead of its own window")
    _webview_binaries = []


# Version resource: Explorer, the Task Manager and "Apps & Features" show a
# named program by a known publisher instead of a bare "PhotoboxBooth.exe".
def _version_file():
    import re
    import tempfile
    text = (BOOTH / "boothcore" / "__init__.py").read_text(encoding="utf-8")
    version = re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
    nums = [int(n) for n in re.findall(r"\d+", version)[:4]] + [0, 0, 0, 0]
    four = tuple(nums[:4])
    fields = {"CompanyName": "Till Heidrich", "FileDescription": "Photobox - Fotobox-Programm",
              "FileVersion": version, "InternalName": "PhotoboxBooth", "LegalCopyright": "AGPL-3.0-or-later",
              "OriginalFilename": "PhotoboxBooth.exe", "ProductName": "Photobox", "ProductVersion": version}
    strings = ",\n".join(f"          StringStruct('{k}', '{v}')" for k, v in fields.items())
    body = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={four}, prodvers={four}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040704B0', [
{strings}
    ])]),
    VarFileInfo([VarStruct('Translation', [0x0407, 1200])])
  ]
)
"""
    path = Path(tempfile.mkdtemp(prefix="photobox-version-")) / "version.txt"
    path.write_text(body, encoding="utf-8")
    return str(path)


VERSION_FILE = _version_file()

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launch_booth.py")],
    pathex=[str(BOOTH), str(ROOT / "sync")],
    binaries=_webview_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest", "numpy.f2py", "PySide6", "PyQt5"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="PhotoboxBooth",
    icon=str(ICON) if ICON.exists() else None,
    version=VERSION_FILE,
    # The console stays: --install, --doctor, --quit and --window-selftest
    # print into it. Started by the task, the .exe is the watcher
    # (launch_booth.py -> boothcore.waechter); the watcher owns the console
    # and hides it when nobody opened it from a prompt
    # (waechter.console_should_hide -> window._hide_own_console). The booth,
    # its child, shares that console and leaves it alone. A short flash of
    # the console at logon remains: a windowed (console=False) build would
    # lose every printed line of the one-off commands.
    console=True,            # zeigt Fehler im Klartext, wenn etwas klemmt
    upx=False,
)
# Eine Datei entsteht dadurch, dass a.binaries und a.datas direkt an EXE
# gehen und kein COLLECT folgt — NICHT durch ein Argument. `onefile=True`
# stand hier und wurde von PyInstaller kommentarlos verworfen; wer auf
# `onefile=False` stellt, um einen Ordner zu bauen, hätte lange gesucht.
