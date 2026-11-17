"""Den Rechner für den Dauerbetrieb einrichten — auf jedem der drei Systeme.

``--install`` soll überall dasselbe versprechen: Die Box startet von selbst,
der Bildschirm bleibt an, nichts geht in den Ruhezustand, und es gibt einen
Weg, sie von Hand zu starten. Wie das erreicht wird, ist je System verschieden:

* Windows: Aufgabenplanung, powercfg, Firewallregel — siehe ``winsetup.py``
* Linux:   systemd-Nutzerdienst, GNOME-Einstellungen, .desktop-Datei
* macOS:   LaunchAgent, ``caffeinate`` statt Energieeinstellungen, .command-Datei

Nichts davon braucht Administratorrechte. Das ist Absicht: Wer eine Fotobox
aufbaut, hat selten das Kennwort des Rechners dabei, und ein Dienst, der beim
Anmelden startet, reicht für ein Gerät, das ohnehin jemand einschaltet.

Rückgabe überall gleich: eine Liste aus (Schritt, geklappt, Anmerkung), die
``winsetup.report`` in Zeilen für Menschen verwandelt.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

Result = Tuple[str, bool, str]

SERVICE = "photobox-booth"
LABEL = "de.heidrich-digital.photobox-booth"


def program_command(port: Optional[int] = None) -> str:
    """Der Befehl, der die Box startet — als .exe/Binary oder als python -m."""
    if getattr(sys, "frozen", False):
        cmd = sys.executable
    else:
        cmd = f"{sys.executable} -m boothcore"
    return f"{cmd} --port {port}" if port else cmd


def _run(args: List[str]) -> Tuple[bool, str]:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=60)
        return out.returncode == 0, (out.stdout or out.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def _write(path: Path, text: str) -> Result:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return (str(path), True, "")
    except OSError as e:
        return (str(path), False, str(e))


# ── Linux ────────────────────────────────────────────────
def _linux_unit(port: int) -> str:
    return f"""[Unit]
Description=Photobox Booth
After=graphical-session.target

[Service]
Type=simple
ExecStart={program_command(port)}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_linux(port: int, home: Optional[Path] = None) -> List[Result]:
    home = home or Path.home()
    results: List[Result] = []

    unit = home / ".config" / "systemd" / "user" / f"{SERVICE}.service"
    path, ok, err = _write(unit, _linux_unit(port))
    results.append(("Dienst angelegt", ok, path if ok else err))
    if ok:
        _run(["systemctl", "--user", "daemon-reload"])
        results.append(("Dienst startet bei der Anmeldung",
                        *_run(["systemctl", "--user", "enable", "--now", f"{SERVICE}.service"])))
        # Ohne „linger“ endet der Nutzerdienst beim Abmelden. Klappt nur mit
        # Rechten; ohne sie läuft die Box trotzdem, solange jemand angemeldet ist.
        lingered, msg = _run(["loginctl", "enable-linger", os.environ.get("USER", "")])
        results.append(("Läuft auch ohne Anmeldung", lingered,
                        "" if lingered else "braucht Administratorrechte — ohne das startet die Box erst nach der Anmeldung"))

    # Bildschirm und Ruhezustand. Jede Einstellung einzeln, damit ein fehlendes
    # Werkzeug nicht die anderen verhindert.
    power = [
        ("Bildschirm bleibt an", ["gsettings", "set", "org.gnome.desktop.session", "idle-delay", "uint32 0"]),
        ("Kein Ruhezustand", ["gsettings", "set", "org.gnome.settings-daemon.plugins.power",
                              "sleep-inactive-ac-type", "nothing"]),
        ("Bildschirmsperre aus", ["gsettings", "set", "org.gnome.desktop.screensaver", "lock-enabled", "false"]),
    ]
    for label, cmd in power:
        if shutil.which(cmd[0]):
            results.append((label, *_run(cmd)))
        else:
            results.append((label, False, f"{cmd[0]} nicht vorhanden — bei anderer Oberfläche von Hand einstellen"))
    if shutil.which("xset"):                      # nur unter X11, unter Wayland ohne Wirkung
        _run(["xset", "s", "off", "-dpms"])

    desktop = f"""[Desktop Entry]
Type=Application
Name=Fotobox
Exec={program_command(port)}
Terminal=false
Categories=Graphics;
"""
    path, ok, err = _write(home / ".local" / "share" / "applications" / f"{SERVICE}.desktop", desktop)
    results.append(("Eintrag im Programmmenü", ok, path if ok else err))
    results.append(("Firewall", True, "nicht nötig — die Box lauscht nur auf diesem Rechner"))
    return results


def uninstall_linux(home: Optional[Path] = None) -> List[Result]:
    home = home or Path.home()
    out = [("Dienst gestoppt", *_run(["systemctl", "--user", "disable", "--now", f"{SERVICE}.service"]))]
    for path in (home / ".config" / "systemd" / "user" / f"{SERVICE}.service",
                 home / ".local" / "share" / "applications" / f"{SERVICE}.desktop"):
        try:
            path.unlink(missing_ok=True)
            out.append((f"Entfernt: {path.name}", True, ""))
        except OSError as e:
            out.append((f"Entfernt: {path.name}", False, str(e)))
    _run(["systemctl", "--user", "daemon-reload"])
    return out


# ── macOS ────────────────────────────────────────────────
def _mac_plist(port: int) -> str:
    # caffeinate hält Bildschirm und Rechner wach, ohne an den Systemeinstellungen
    # zu drehen — das bräuchte sonst das Administratorkennwort.
    parts = program_command(port).split()
    args = "".join(f"\n    <string>{a}</string>" for a in ["/usr/bin/caffeinate", "-dis", *parts])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
"""


def install_mac(port: int, home: Optional[Path] = None) -> List[Result]:
    home = home or Path.home()
    results: List[Result] = []
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    path, ok, err = _write(plist, _mac_plist(port))
    results.append(("Dienst angelegt", ok, path if ok else err))
    if ok:
        uid = os.getuid() if hasattr(os, "getuid") else 501
        started, msg = _run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)])
        if not started:                          # ältere Systeme kennen bootstrap nicht
            started, msg = _run(["launchctl", "load", "-w", str(plist)])
        results.append(("Dienst startet bei der Anmeldung", started, "" if started else msg))
    results.append(("Bildschirm bleibt an", True,
                    "über caffeinate im Dienst — die Systemeinstellungen bleiben unangetastet"))

    starter = home / "Desktop" / "Fotobox.command"
    path, ok, err = _write(starter, f"#!/bin/sh\nexec {program_command(port)}\n")
    if ok:
        try:
            starter.chmod(0o755)
        except OSError as e:
            ok, path = False, str(e)
    results.append(("Starter auf dem Schreibtisch", ok, path if ok else err))
    results.append(("Firewall", True, "nicht nötig — die Box lauscht nur auf diesem Rechner"))
    return results


def uninstall_mac(home: Optional[Path] = None) -> List[Result]:
    home = home or Path.home()
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    uid = os.getuid() if hasattr(os, "getuid") else 501
    ok, msg = _run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"])
    if not ok:
        ok, msg = _run(["launchctl", "unload", "-w", str(plist)])
    out = [("Dienst gestoppt", ok, "" if ok else msg)]
    for path in (plist, home / "Desktop" / "Fotobox.command"):
        try:
            path.unlink(missing_ok=True)
            out.append((f"Entfernt: {path.name}", True, ""))
        except OSError as e:
            out.append((f"Entfernt: {path.name}", False, str(e)))
    return out


# ── Weiche ───────────────────────────────────────────────
def install(port: int = 8765) -> List[Result]:
    if os.name == "nt":
        from .winsetup import install as win_install
        return win_install(port)
    if sys.platform == "darwin":
        return install_mac(port)
    return install_linux(port)


def uninstall() -> List[Result]:
    if os.name == "nt":
        from .winsetup import uninstall as win_uninstall
        return win_uninstall()
    if sys.platform == "darwin":
        return uninstall_mac()
    return uninstall_linux()
