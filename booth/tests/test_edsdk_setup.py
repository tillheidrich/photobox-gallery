"""Einrichtung des Canon-SDK: die richtige Datei finden, die falsche abweisen.

Das SDK selbst liegt nicht im Repo — hier werden nur Dateien mit echtem
PE-Kopf nachgebaut, damit die Auswahl nach Architektur belegt ist.
"""
import struct

import pytest

from boothcore import edsdk_setup


def _pe(path, machine: int, payload: int = 4096) -> None:
    """Minimale, aber echte PE-Datei: MZ-Kopf, Zeiger, PE-Signatur, Maschinentyp."""
    head = bytearray(b"\0" * 0x40)
    head[0:2] = b"MZ"
    head[0x3C:0x40] = struct.pack("<I", 0x40)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(head) + b"PE\0\0" + struct.pack("<H", machine) + b"\0" * payload)


def _sdk(root, with_x64: bool = True):
    """Ein Baum, wie Canon ihn ausliefert: 32 und 64 Bit nebeneinander."""
    _pe(root / "EDSDK" / "Dll" / "EDSDK.dll", edsdk_setup.MACHINE_X86, 2048)
    _pe(root / "EDSDK" / "Dll" / "EdsImage.dll", edsdk_setup.MACHINE_X86, 2048)
    if with_x64:
        _pe(root / "EDSDK_64" / "Dll" / "EDSDK.dll", edsdk_setup.MACHINE_X64, 8192)
        _pe(root / "EDSDK_64" / "Dll" / "EdsImage.dll", edsdk_setup.MACHINE_X64, 8192)
    return root


def test_reads_the_architecture_from_the_file_not_the_folder(tmp_path):
    _pe(tmp_path / "irgendwo" / "EDSDK.dll", edsdk_setup.MACHINE_X64)
    assert edsdk_setup.pe_machine(tmp_path / "irgendwo" / "EDSDK.dll") == edsdk_setup.MACHINE_X64
    (tmp_path / "kaputt.dll").write_bytes(b"kein PE")
    assert edsdk_setup.pe_machine(tmp_path / "kaputt.dll") is None


def test_installs_the_64_bit_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(edsdk_setup.sys, "platform", "win32")
    target = tmp_path / "ziel"
    ok, lines = edsdk_setup.install(_sdk(tmp_path / "sdk"), target, want_bits=64)
    assert ok, lines
    assert (target / "EDSDK.dll").exists() and (target / "EdsImage.dll").exists()
    # Die 64-Bit-Fassung, nicht die daneben liegende 32-Bit-Datei.
    assert edsdk_setup.pe_machine(target / "EDSDK.dll") == edsdk_setup.MACHINE_X64


def test_refuses_the_wrong_architecture(tmp_path, monkeypatch):
    monkeypatch.setattr(edsdk_setup.sys, "platform", "win32")
    ok, lines = edsdk_setup.install(_sdk(tmp_path / "sdk", with_x64=False), tmp_path / "ziel", want_bits=64)
    assert not ok
    assert "32 Bit" in lines[0] and "64 Bit" in lines[0]


def test_says_plainly_when_the_folder_is_wrong(tmp_path, monkeypatch):
    monkeypatch.setattr(edsdk_setup.sys, "platform", "win32")
    ok, lines = edsdk_setup.install(tmp_path / "gibtsnicht", tmp_path / "ziel")
    assert not ok and "nicht gefunden" in lines[0]

    (tmp_path / "leer").mkdir()
    ok, lines = edsdk_setup.install(tmp_path / "leer", tmp_path / "ziel", want_bits=64)
    assert not ok and "EDSDK.dll" in lines[0]


def test_status_checks_both_files_and_the_architecture(tmp_path, monkeypatch):
    monkeypatch.setattr(edsdk_setup.sys, "platform", "win32")
    monkeypatch.setattr(edsdk_setup.struct, "calcsize", lambda fmt: 8)
    folder = tmp_path / "EDSDK"
    ok, msg = edsdk_setup.status(folder)
    assert not ok and "fehlt" in msg

    _pe(folder / "EDSDK.dll", edsdk_setup.MACHINE_X64)
    ok, msg = edsdk_setup.status(folder)
    assert not ok and "EdsImage.dll" in msg

    _pe(folder / "EdsImage.dll", edsdk_setup.MACHINE_X64)
    ok, msg = edsdk_setup.status(folder)
    assert ok and "bereit" in msg

    _pe(folder / "EDSDK.dll", edsdk_setup.MACHINE_X86)
    ok, msg = edsdk_setup.status(folder)
    assert not ok and "32 Bit" in msg


def test_configure_writes_the_path_into_the_local_config(tmp_path, monkeypatch):
    monkeypatch.setattr(edsdk_setup.sys, "platform", "win32")
    from boothcore.config import Store
    store = Store(tmp_path)
    dll = edsdk_setup.configure(store, tmp_path / "EDSDK")
    assert store.local["camera"]["backend"] == "edsdk"
    assert store.local["camera"]["edsdk_dll"] == dll == str(tmp_path / "EDSDK" / "EDSDK.dll")


def test_macos_takes_the_whole_framework(tmp_path, monkeypatch):
    """Auf einem Mac liefert Canon ein Bündel — einzelne Dateien daraus nützen nichts."""
    monkeypatch.setattr(edsdk_setup.sys, "platform", "darwin")
    fw = tmp_path / "sdk" / "EDSDK" / "Framework" / "EDSDK.framework"
    (fw / "Versions" / "A").mkdir(parents=True)
    (fw / "Versions" / "A" / "EDSDK").write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 4096)
    (fw / "EDSDK").symlink_to("Versions/A/EDSDK")

    ok, lines = edsdk_setup.install(tmp_path / "sdk", tmp_path / "ziel")
    assert ok, lines
    dest = tmp_path / "ziel" / "EDSDK.framework"
    assert (dest / "Versions" / "A" / "EDSDK").exists()
    assert (dest / "EDSDK").is_symlink()          # das Bündel muss seine Verweise behalten
    assert edsdk_setup.status(tmp_path / "ziel")[0]
    assert edsdk_setup.library_name() == "EDSDK.framework/EDSDK"


def test_macos_says_so_when_the_windows_archive_was_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(edsdk_setup.sys, "platform", "darwin")
    _sdk(tmp_path / "sdk")                        # nur DLLs, kein Framework
    ok, lines = edsdk_setup.install(tmp_path / "sdk", tmp_path / "ziel")
    assert not ok and "macOS" in lines[1]


def test_linux_points_at_gphoto2_instead(tmp_path, monkeypatch):
    """Für Linux gibt es kein Canon-SDK — das muss dastehen, statt ins Leere zu suchen."""
    monkeypatch.setattr(edsdk_setup.sys, "platform", "linux")
    (tmp_path / "sdk").mkdir()
    ok, lines = edsdk_setup.install(tmp_path / "sdk", tmp_path / "ziel")
    assert not ok
    assert "kein offizielles Canon-SDK" in lines[0] and "gphoto2" in lines[1]
