# PyInstaller-Bauplan für Photobox Booth.app (macOS)
#
# Bauen: ./build.sh im selben Ordner — der Bau läuft nur auf einem Mac.
#
# Anders als unter Windows entsteht kein Einzeldatei-Programm, sondern ein
# .app-Bündel. Der Grund ist nicht Geschmack: nur ein Bündel lässt sich
# signieren, notarisieren und mit einem Info.plist versehen, und nur mit einem
# Info.plist darf die Box nach der Kamera fragen. Eine einzelne Datei würde
# beim ersten Zugriff kommentarlos scheitern.
#
# Das Canon EDSDK ist auch hier nicht enthalten. Es liegt im Datenordner der
# Box unter EDSDK/ und wird mit „--install-edsdk“ dorthin gelegt.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(os.environ.get("PHOTOBOX_ROOT", Path.cwd())).resolve()
BOOTH = ROOT / "booth"
ICON = ROOT / "packaging" / "macos" / "photobox.icns"

datas = [
    (str(BOOTH / "boothcore" / "web"), "boothcore/web"),
    (str(BOOTH / "boothcore" / "media" / "luts_builtin"), "boothcore/media/luts_builtin"),
]
datas += collect_data_files("imageio_ffmpeg")
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
# Each package keeps its own licence files in its .dist-info folder;
# copy_metadata(recursive=True) brings them along with those of everything the
# package pulls in. A package that is not installed on this platform (pywin32
# outside Windows) is skipped with a note instead of breaking the build.
from importlib.metadata import PackageNotFoundError as _NotInstalled
from PyInstaller.utils.hooks import copy_metadata
for _dist in ('fastapi', 'uvicorn', 'python-multipart', 'pillow', 'qrcode', 'httpx', 'pluggy', 'psutil', 'numpy', 'opencv-python-headless', 'paramiko', 'imageio-ffmpeg', 'pywin32'):
    try:
        datas += copy_metadata(_dist, recursive=True)
    except _NotInstalled:
        print(f"note: {_dist} is not installed here, its licence files are not packed")

hiddenimports = (collect_submodules("uvicorn") + collect_submodules("photobox_sync")
                 + collect_submodules("boothcore") + ["qrcode", "encodings.idna"])

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launch_booth.py")],   # derselbe Einstieg wie unter Windows
    pathex=[str(BOOTH), str(ROOT / "sync")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest", "numpy.f2py", "PySide6", "PyQt5"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="photobox-booth",
    console=True,            # Fehler im Klartext, wenn jemand die Box vom Terminal startet
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="photobox-booth")
app = BUNDLE(
    coll,
    name="Photobox Booth.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="de.heidrich-digital.photobox-booth",
    info_plist={
        "CFBundleShortVersionString": os.environ.get("PHOTOBOX_VERSION", "0.0.0"),
        "CFBundleVersion": os.environ.get("PHOTOBOX_VERSION", "0.0.0"),
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        # Ohne diese beiden Sätze fragt macOS gar nicht erst — es beendet die App.
        # Sie erscheinen im Dialog, den der Betreiber beim ersten Start sieht.
        "NSCameraUsageDescription":
            "Die Fotobox löst die angeschlossene Kamera aus und zeigt ihr Live-Bild.",
        "NSPhotoLibraryUsageDescription":
            "Nur wenn eine Ablage in die Fotos-Mediathek zeigt.",
        # Die Box ist ein Kiosk: kein Dock-Symbol, kein Menü, nichts zum Wegklicken.
        "LSUIElement": False,
        "LSBackgroundOnly": False,
    },
)
