"""Canon EDSDK einrichten: die richtigen Dateien finden, prüfen, an ihren Platz legen.

Das SDK kommt aus dem Canon Developer Programme und steht unter NDA. Es darf
weder ins Repo noch in die .exe — der Betreiber lädt es selbst herunter. Was
danach folgt, ist trotzdem unnötig fehleranfällig: Im entpackten Archiv liegen
mehrere ``EDSDK.dll`` (32 und 64 Bit, teils in mehreren Sprachordnern), und wer
die falsche erwischt, bekommt beim Start nur „%1 ist keine zulässige
Win32-Anwendung“ zu sehen.

Dieses Modul sucht im entpackten SDK die zur laufenden Python-Fassung passende
Architektur, prüft sie im Dateikopf (statt dem Ordnernamen zu glauben) und legt
sie neben das Programm. Danach trägt die Box den Pfad selbst ein.

    python -m boothcore --install-edsdk "C:\\Users\\Till\\Downloads\\EDSDK_13.18.40"
"""
from __future__ import annotations

import io
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# Diese Dateien braucht die Box zur Laufzeit. EdsImage.dll muss neben EDSDK.dll
# liegen, sonst lädt das SDK zwar, kann aber keine Aufnahme entgegennehmen.
NEEDED = ("EDSDK.dll", "EdsImage.dll")
# macOS: Canon liefert ein Framework-Bündel. Darin liegt die eigentliche
# Bibliothek ohne Endung — kein PE-Kopf, also auch keine Architekturprüfung
# über pe_machine(); dafür sagt „file“ bzw. der Ladeversuch, was Sache ist.
MAC_FRAMEWORK = "EDSDK.framework"

MACHINE_X64 = 0x8664
MACHINE_X86 = 0x014C


@dataclass
class Found:
    path: Path
    machine: int

    @property
    def bits(self) -> int:
        return 64 if self.machine == MACHINE_X64 else 32


def pe_machine(path: Path) -> Optional[int]:
    """Architektur aus dem PE-Kopf lesen — verlässlicher als der Ordnername."""
    try:
        with path.open("rb") as fh:
            if fh.read(2) != b"MZ":
                return None
            fh.seek(0x3C)
            offset = struct.unpack("<I", fh.read(4))[0]
            fh.seek(offset)
            if fh.read(4) != b"PE\0\0":
                return None
            return struct.unpack("<H", fh.read(2))[0]
    except OSError:
        return None


def _candidates(root: Path, name: str) -> List[Found]:
    out = []
    for path in root.rglob(name):
        if not path.is_file():
            continue
        machine = pe_machine(path)
        if machine is not None:
            out.append(Found(path, machine))
    return out


def _pick(found: List[Found], want_bits: int) -> Optional[Found]:
    exact = [f for f in found if f.bits == want_bits]
    if not exact:
        return None
    # Bei mehreren gleich passenden: die größte Datei, das ist die echte
    # Bibliothek und keine Stub- oder Sprachfassung.
    return max(exact, key=lambda f: f.path.stat().st_size)


def target_dir() -> Path:
    """Standardziel: im Datenordner der Box.

    Neben dem Programm wäre naheliegender, überlebt aber kein Update der .exe
    und scheitert dort, wo das Programmverzeichnis schreibgeschützt ist. Der
    Datenordner bleibt über Updates hinweg bestehen; ``edsdk.py`` sucht dort
    zuerst und danach weiterhin neben dem Programm.
    """
    from .config import data_dir
    return Path(data_dir()) / "EDSDK"


def install(source: Path, target: Optional[Path] = None, want_bits: Optional[int] = None) -> Tuple[bool, List[str]]:
    """Dateien aus dem entpackten SDK an ihren Platz kopieren.

    Gibt (Erfolg, Zeilen für den Menschen) zurück. Es wird nichts gelöscht und
    nichts überschrieben, was nicht zum SDK gehört.
    """
    lines: List[str] = []
    want_bits = want_bits or (64 if struct.calcsize("P") == 8 else 32)
    source = source.expanduser().resolve()
    target = (target or target_dir()).resolve()

    if not source.exists():
        return False, [f"Ordner nicht gefunden: {source}",
                       "Das Archiv aus dem Canon Developer Portal zuerst entpacken."]

    if sys.platform == "darwin":
        return _install_mac(source, target, lines)
    if not sys.platform.startswith("win"):
        return False, [
            "Für Linux gibt es kein offizielles Canon-SDK.",
            "Auf Linux läuft die Box mit gphoto2: „sudo apt install libgphoto2-dev“ und "
            "„pip install gphoto2“, dann in der Verwaltung das Backend gphoto2 wählen.",
            "Canon-Kameras werden davon gut unterstützt; Live-Bild und Auslösen laufen, "
            "einzelne neuere Modelle brauchen eine frische libgphoto2.",
        ]
    names = NEEDED
    picked: List[Found] = []
    for name in names:
        found = _candidates(source, name) if name.endswith(".dll") else [
            Found(p, MACHINE_X64) for p in source.rglob(name) if p.is_file()]
        if not found:
            return False, [f"{name} liegt nicht in {source}.",
                           "Ist das wirklich der entpackte SDK-Ordner? Erwartet wird der Ordner,"
                           " in dem u. a. „EDSDK“ und „Sample“ liegen."]
        hit = _pick(found, want_bits) if name.endswith(".dll") else found[0]
        if not hit:
            have = ", ".join(sorted({f"{f.bits} Bit" for f in found}))
            return False, [f"{name} liegt nur als {have} vor, gebraucht wird {want_bits} Bit.",
                           "Python und SDK müssen dieselbe Architektur haben — für die Fotobox"
                           " ist das 64 Bit."]
        picked.append(hit)

    target.mkdir(parents=True, exist_ok=True)
    for hit in picked:
        shutil.copy2(hit.path, target / hit.path.name)
        lines.append(f"kopiert: {hit.path.name} ({hit.bits} Bit) ← {hit.path.parent}")

    # Alles, was daneben liegt und mitgehört (Sprachdateien der EdsImage.dll).
    for extra in source.rglob("*.dll"):
        if extra.name in {h.path.name for h in picked} or not extra.is_file():
            continue
        if extra.parent in {h.path.parent for h in picked} and pe_machine(extra) == picked[0].machine:
            shutil.copy2(extra, target / extra.name)
            lines.append(f"kopiert: {extra.name} (gehört dazu)")

    lines.append(f"Ziel: {target}")
    return True, lines


def _install_mac(source: Path, target: Path, lines: List[str]) -> Tuple[bool, List[str]]:
    """Das Framework-Bündel als Ganzes übernehmen — einzelne Dateien daraus nützen nichts."""
    found = [p for p in source.rglob(MAC_FRAMEWORK) if p.is_dir()]
    if not found:
        return False, [f"{MAC_FRAMEWORK} liegt nicht in {source}.",
                       "Im Canon-Portal gibt es eine eigene Fassung für macOS — die Windows-Fassung "
                       "enthält kein Framework."]
    src = max(found, key=lambda p: sum(f.stat().st_size for f in p.rglob("*") if f.is_file()))
    dest = target / MAC_FRAMEWORK
    try:
        target.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, symlinks=True)     # Symlinks im Bündel müssen Symlinks bleiben
    except OSError as e:
        return False, [f"Kopieren fehlgeschlagen: {e}"]
    lines.append(f"kopiert: {MAC_FRAMEWORK} ← {src.parent}")
    lines.append(f"Ziel: {target}")
    return True, lines


def library_name() -> str:
    """Wie die Bibliothek auf diesem System heißt."""
    if sys.platform == "darwin":
        return f"{MAC_FRAMEWORK}/EDSDK"
    return "EDSDK.dll" if sys.platform.startswith("win") else "libEDSDK.so"


def configure(store, target: Optional[Path] = None) -> str:
    """Pfad in der lokalen Konfiguration eintragen und das Backend umstellen."""
    dll = (target or target_dir()) / library_name()
    store.save_local({"camera": {"backend": "edsdk", "edsdk_dll": str(dll)}})
    return str(dll)


def setup_hint() -> str:
    """Ein Satz, der auf diesem System auch stimmt."""
    if sys.platform.startswith("win"):
        return "PhotoboxBooth.exe --install-edsdk <entpackter SDK-Ordner>"
    if sys.platform == "darwin":
        return "photobox-booth --install-edsdk <entpackter SDK-Ordner> (macOS-Fassung des SDK)"
    return "Auf Linux gibt es kein Canon-SDK: gphoto2 installieren und das Backend umstellen."


def has_sdk_platform() -> bool:
    """Gibt es für dieses System überhaupt ein Canon-SDK?"""
    return sys.platform.startswith("win") or sys.platform == "darwin"


def status(target: Optional[Path] = None) -> Tuple[bool, str]:
    """Für die Selbstdiagnose: liegt ein brauchbares SDK bereit?"""
    folder = (target or target_dir())
    if sys.platform == "darwin":
        fw = folder / MAC_FRAMEWORK
        if not fw.is_dir():
            return False, f"{MAC_FRAMEWORK} fehlt in {folder}"
        return True, f"EDSDK liegt bereit ({fw})"
    if not sys.platform.startswith("win"):
        return False, "Für Linux gibt es kein Canon-SDK — dort läuft die Box mit gphoto2."
    dll = folder / "EDSDK.dll"
    if not dll.exists():
        return False, f"EDSDK.dll fehlt in {folder}"
    machine = pe_machine(dll)
    want = 64 if struct.calcsize("P") == 8 else 32
    if machine is not None and (64 if machine == MACHINE_X64 else 32) != want:
        return False, f"EDSDK.dll ist {64 if machine == MACHINE_X64 else 32} Bit, gebraucht wird {want} Bit"
    if not (folder / "EdsImage.dll").exists():
        return False, f"EdsImage.dll fehlt neben der EDSDK.dll in {folder}"
    return True, f"EDSDK liegt bereit ({folder})"


def aus_zip(daten: bytes, target: Optional[Path] = None,
            want_bits: Optional[int] = None) -> Tuple[bool, List[str]]:
    """Das SDK direkt aus dem ZIP einrichten, wie Canon es ausliefert.

    Der bisherige Weg verlangte einen **entpackten** Ordner und einen
    Kommandozeilenaufruf. Wer am Veranstaltungsabend vor einem Kiosk-Rechner
    ohne Tastatur steht, hat beides nicht. Canon liefert ein ZIP; also nimmt
    die Box ein ZIP.

    Entpackt wird in einen Ordner, der danach wieder verschwindet, und zwar
    mit Prüfung jedes Eintrags: Ein Archiv darf nicht bestimmen, wohin auf
    dieser Platte geschrieben wird.
    """
    import tempfile
    import zipfile

    zeilen: List[str] = []
    with tempfile.TemporaryDirectory(prefix="edsdk-") as tmp:
        ziel = Path(tmp)
        try:
            with zipfile.ZipFile(io.BytesIO(daten)) as z:
                for eintrag in z.infolist():
                    # Kein Eintrag darf aus dem Ordner ausbrechen — weder mit
                    # ".." noch mit einem absoluten Pfad noch über einen Link.
                    name = eintrag.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in Path(name).parts:
                        zeilen.append(f"Übersprungen (verdächtiger Pfad): {name}")
                        continue
                    z.extract(eintrag, ziel)
        except zipfile.BadZipFile:
            return False, ["Das ist kein lesbares ZIP-Archiv."]
        # Canon packt das SDK je nach Fassung eine oder zwei Ebenen tief.
        wurzel = ziel
        eintraege = [p for p in ziel.iterdir() if not p.name.startswith("__MACOSX")]
        while len(eintraege) == 1 and eintraege[0].is_dir():
            wurzel = eintraege[0]
            eintraege = [p for p in wurzel.iterdir() if not p.name.startswith("__MACOSX")]
        ok, weitere = install(wurzel, target=target, want_bits=want_bits)
        return ok, zeilen + weitere
