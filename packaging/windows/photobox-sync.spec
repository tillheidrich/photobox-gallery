# PyInstaller-Bauplan für PhotoboxSync.exe — der Sync allein, für Rechner, auf
# denen dslrBooth oder LumaBooth läuft und nur die Bilder abfließen sollen.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(os.environ.get("PHOTOBOX_ROOT", Path.cwd())).resolve()
SYNC = ROOT / "sync"

datas = [(str(SYNC / "photobox_sync" / "web"), "photobox_sync/web")] \
    if (SYNC / "photobox_sync" / "web").is_dir() else []

# The CA bundle for outgoing TLS (photobox_sync.trust): FTPS and SMTP trust
# the system roots plus certifi's cacert.pem, because the Windows store lacks
# roots the PC never fetched. PyInstaller's certifi hook packs it too when
# httpx pulls certifi in; said here so a build can never ship without it.
datas += collect_data_files("certifi")

ICON = ROOT / "packaging" / "windows" / "sync.ico"

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
for _dist in ('httpx', 'paramiko', 'psutil', 'fastapi', 'uvicorn'):
    try:
        datas += copy_metadata(_dist, recursive=True)
    except _NotInstalled:
        print(f"note: {_dist} is not installed here, its licence files are not packed")

# Version resource: product name, version, publisher and licence in the file,
# the same as the booth's (version_resource.py). Without it Explorer shows a
# bare "PhotoboxSync.exe", and a code signing service that checks product name
# and version refuses the file.
def _version_resource():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "photobox_version_resource", ROOT / "packaging" / "windows" / "version_resource.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERSION_FILE = _version_resource().write(ROOT, "PhotoboxSync")

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "launch_sync.py")],
    pathex=[str(SYNC)],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("uvicorn") + ["paramiko", "nacl.public", "nacl.exceptions", "nacl.bindings"],
    excludes=["tkinter", "matplotlib", "pytest", "cv2", "PySide6", "PyQt5"],
    noarchive=False,
)
pyz = PYZ(a.pure)
# console=False: Das Programm läuft den ganzen Abend im Hintergrund. Ein
# schwarzes Fenster in der Taskleiste wird versehentlich zugeklickt — und dann
# steht der Upload. Bedient wird es über das Symbol neben der Uhr und die
# Status-Seite; --install richtet den Autostart ein.
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PhotoboxSync",
          console=False, upx=False,
          icon=str(ICON) if ICON.exists() else None,
          version=VERSION_FILE)

# Eine Datei entsteht ohne COLLECT, nicht durch ein Argument: `onefile`
# kennt EXE() nicht und verwirft es stillschweigend.
