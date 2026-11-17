"""Set the sync program up on a Windows PC — autostart, shortcut, firewall.

The machine this runs on is somebody's event PC: dslrBooth in the foreground,
this program behind it. So it has to come back on its own after a reboot and
stay out of the way. ``PhotoboxSync.exe --install`` does the whole setup; the
same functions answer "is autostart on?" for the remote admin page.

Everything here is Windows-only and fails soft: on macOS or Linux the state is
reported as unsupported rather than raising, because the same code ships in the
cross-platform package.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

TASK_NAME = "Photobox Sync"
FIREWALL_RULE = "Photobox Sync (lokal)"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def is_windows() -> bool:
    return os.name == "nt"


def program_path() -> str:
    """The running program — the .exe when frozen, otherwise ``pythonw -m``.

    ``pythonw`` instead of ``python`` on purpose: a console window that pops up
    at every logon is exactly what "runs in the background" must not mean.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe") if is_windows() and exe.with_name("pythonw.exe").exists() else exe
    return f'"{quiet}" -m photobox_sync'


def _run(args: List[str]) -> Tuple[bool, str]:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
        return out.returncode == 0, (out.stdout or out.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def enable_autostart(target: str = "") -> Tuple[bool, str]:
    """Start at logon, also on battery — an event PC is often on a UPS or a laptop."""
    if not is_windows():
        return False, "nur unter Windows"
    return _run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "LIMITED",
                 "/TN", TASK_NAME, "/TR", target or program_path()])


def disable_autostart() -> Tuple[bool, str]:
    if not is_windows():
        return False, "nur unter Windows"
    return _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])


def autostart_state() -> Dict[str, object]:
    """What the remote admin shows: on, off, or not applicable here."""
    if not is_windows():
        return {"supported": False, "enabled": False, "detail": "kein Windows"}
    ok, out = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    if not ok:
        return {"supported": True, "enabled": False, "detail": "kein Autostart eingerichtet"}
    disabled = "Deaktiviert" in out or "Disabled" in out
    return {"supported": True, "enabled": not disabled,
            "detail": "läuft bei jeder Anmeldung" if not disabled else "eingerichtet, aber deaktiviert"}


def _firewall(port: int) -> Tuple[bool, str]:
    """Only this machine talks to the sync program — never the whole network."""
    return _run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}",
                 "dir=in", "action=allow", "protocol=TCP", f"localport={port}",
                 "remoteip=127.0.0.1", "profile=any"])


def _no_sleep() -> List[Tuple[str, bool, str]]:
    """A sleeping PC uploads nothing. Only the mains profile is touched."""
    steps = [
        ("Standby aus (Netzbetrieb)", ["powercfg", "/change", "standby-timeout-ac", "0"]),
        ("Festplatte bleibt an", ["powercfg", "/change", "disk-timeout-ac", "0"]),
    ]
    return [(label, *_run(cmd)) for label, cmd in steps]


def _desktop() -> Optional[Path]:
    r"""The desktop as Windows knows it.

    With OneDrive folder protection on — a default on many Windows 10/11
    machines — the desktop is ``%USERPROFILE%\OneDrive\Desktop`` and the
    plain guess no longer exists.
    """
    ok, out = _run(["powershell", "-NoProfile", "-Command",
                    "[Environment]::GetFolderPath('Desktop')"])
    if ok and out:
        p = Path(out.strip())
        if p.is_dir():
            return p
    for p in (Path(os.path.expandvars(r"%USERPROFILE%\OneDrive\Desktop")),
              Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))):
        if p.is_dir():
            return p
    return None


def _shortcut(target: str, name: str = "Photobox Sync") -> Tuple[bool, str]:
    desktop = _desktop()
    if desktop is None:
        return False, "Desktop nicht gefunden"
    link = desktop / f"{name}.lnk"
    exe = target.strip('"').split('" ')[0].strip('"')
    ps = (f"$s=(New-Object -COM WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{exe}';$s.WorkingDirectory='{Path(exe).parent}';$s.Save()")
    ok, msg = _run(["powershell", "-NoProfile", "-Command", ps])
    return ok, str(link) if ok else msg


def install(port: int = 8766, power: bool = True) -> List[Tuple[str, bool, str]]:
    """Everything the PC needs. One line per step, so nothing happens silently."""
    if not is_windows():
        return [("Windows", False, "Dieser Schritt läuft nur unter Windows.")]
    target = program_path()
    results: List[Tuple[str, bool, str]] = [("Autostart bei Anmeldung", *enable_autostart(target))]
    if power:
        results += _no_sleep()
    results.append(("Firewall (nur dieser Rechner)", *_firewall(port)))
    results.append(("Verknüpfung auf dem Desktop", *_shortcut(target)))
    return results


def uninstall() -> List[Tuple[str, bool, str]]:
    if not is_windows():
        return [("Windows", False, "Dieser Schritt läuft nur unter Windows.")]
    return [("Autostart entfernt", *disable_autostart()),
            ("Firewall-Regel entfernt",
             *_run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"]))]


def report(results: List[Tuple[str, bool, str]]) -> str:
    lines = []
    for label, ok, detail in results:
        lines.append(("OK  " if ok else "--  ") + label + (f" · {detail}" if detail and not ok else ""))
    return "\n".join(lines)
