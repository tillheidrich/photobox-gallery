"""Einrichtung auf Linux und macOS — dieselbe Zusage wie unter Windows.

Die Befehle selbst werden nicht ausgeführt: geprüft wird, was geschrieben wird
und dass ein fehlgeschlagener Schritt die anderen nicht verhindert.
"""
import plistlib

import pytest

from boothcore import hostsetup


@pytest.fixture()
def quiet(monkeypatch):
    """Alle äußeren Befehle melden Erfolg, ohne zu laufen."""
    calls = []

    def fake(args):
        calls.append(args)
        return True, ""

    monkeypatch.setattr(hostsetup, "_run", fake)
    monkeypatch.setattr(hostsetup.shutil, "which", lambda name: "/usr/bin/" + name)
    return calls


def test_linux_writes_a_user_service_that_restarts(tmp_path, quiet):
    results = hostsetup.install_linux(8765, tmp_path)
    unit = tmp_path / ".config" / "systemd" / "user" / "photobox-booth.service"
    text = unit.read_text(encoding="utf-8")
    assert "Restart=always" in text and "--port 8765" in text
    assert "WantedBy=default.target" in text
    assert ["systemctl", "--user", "enable", "--now", "photobox-booth.service"] in quiet
    assert (tmp_path / ".local" / "share" / "applications" / "photobox-booth.desktop").exists()
    assert all(ok for _, ok, _ in results), results


def test_linux_says_plainly_when_linger_needs_rights(tmp_path, monkeypatch):
    def fake(args):
        return (False, "Interactive authentication required") if args[0] == "loginctl" else (True, "")

    monkeypatch.setattr(hostsetup, "_run", fake)
    monkeypatch.setattr(hostsetup.shutil, "which", lambda name: "/usr/bin/" + name)
    results = hostsetup.install_linux(8765, tmp_path)
    linger = next(r for r in results if r[0] == "Läuft auch ohne Anmeldung")
    assert linger[1] is False and "Administratorrechte" in linger[2]
    # Der Rest läuft trotzdem durch — die Box startet nach der Anmeldung.
    assert (tmp_path / ".config" / "systemd" / "user" / "photobox-booth.service").exists()


def test_linux_survives_a_desktop_without_gsettings(tmp_path, monkeypatch):
    monkeypatch.setattr(hostsetup, "_run", lambda args: (True, ""))
    monkeypatch.setattr(hostsetup.shutil, "which", lambda name: None)
    results = hostsetup.install_linux(8765, tmp_path)
    screen = next(r for r in results if r[0] == "Bildschirm bleibt an")
    assert screen[1] is False and "von Hand" in screen[2]


def test_mac_plist_is_valid_and_keeps_the_machine_awake(tmp_path, quiet):
    hostsetup.install_mac(8765, tmp_path)
    plist = tmp_path / "Library" / "LaunchAgents" / "de.heidrich-digital.photobox-booth.plist"
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == "de.heidrich-digital.photobox-booth"
    assert data["RunAtLoad"] is True and data["KeepAlive"] is True
    # caffeinate statt Systemeinstellungen: braucht kein Administratorkennwort
    assert data["ProgramArguments"][0] == "/usr/bin/caffeinate"
    assert "--port" in data["ProgramArguments"] and "8765" in data["ProgramArguments"]


def test_mac_desktop_starter_is_executable(tmp_path, quiet):
    hostsetup.install_mac(8765, tmp_path)
    starter = tmp_path / "Desktop" / "Fotobox.command"
    assert starter.read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert starter.stat().st_mode & 0o111


def test_mac_falls_back_to_the_older_launchctl(tmp_path, monkeypatch):
    seen = []

    def fake(args):
        seen.append(args)
        return (False, "Bootstrap failed") if "bootstrap" in args else (True, "")

    monkeypatch.setattr(hostsetup, "_run", fake)
    results = hostsetup.install_mac(8765, tmp_path)
    assert any("load" in args for args in seen), seen
    assert next(r for r in results if r[0] == "Dienst startet bei der Anmeldung")[1] is True


def test_uninstall_removes_what_install_created(tmp_path, quiet):
    hostsetup.install_linux(8765, tmp_path)
    hostsetup.uninstall_linux(tmp_path)
    assert not (tmp_path / ".config" / "systemd" / "user" / "photobox-booth.service").exists()

    hostsetup.install_mac(8765, tmp_path)
    hostsetup.uninstall_mac(tmp_path)
    assert not (tmp_path / "Library" / "LaunchAgents" / "de.heidrich-digital.photobox-booth.plist").exists()
    assert not (tmp_path / "Desktop" / "Fotobox.command").exists()


def test_the_switch_picks_the_right_system(monkeypatch):
    monkeypatch.setattr(hostsetup.os, "name", "posix")
    monkeypatch.setattr(hostsetup.sys, "platform", "darwin")
    monkeypatch.setattr(hostsetup, "install_mac", lambda port, home=None: [("mac", True, "")])
    assert hostsetup.install(8765)[0][0] == "mac"

    monkeypatch.setattr(hostsetup.sys, "platform", "linux")
    monkeypatch.setattr(hostsetup, "install_linux", lambda port, home=None: [("linux", True, "")])
    assert hostsetup.install(8765)[0][0] == "linux"
