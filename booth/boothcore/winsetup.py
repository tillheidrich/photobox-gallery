"""Set the booth PC up from the program itself — no typing on a touch screen.

The booth machine has a touch screen and a small keyboard, so everything the
installation needs happens inside ``PhotoboxBooth.exe --install``: autostart at
logon, no sleep, no screen blanking, no USB power saving, a desktop shortcut
and a firewall rule for the local web interface.

Nothing here touches the Canon EDSDK: those DLLs come from Canon and are copied
into ``EDSDK\\`` next to the program by hand.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

TASK_NAME = "Photobox Booth"
FIREWALL_RULE = "Photobox Booth (lokal)"


def is_windows() -> bool:
    return os.name == "nt"


def program_path() -> str:
    r"""Path of the running program — the .exe when frozen, otherwise python -m.

    Quoted in both cases. ``schtasks /TR`` splits an unquoted value at the
    first space, so an .exe under ``C:\Program Files\…``, on the desktop of
    a user called "Max Mustermann", or in a OneDrive path silently created a
    task that starts the wrong thing. The sync side quoted it, this one did
    not — one of the two had to be wrong.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m boothcore'


# Windows' own tools (powercfg, reg, schtasks, netsh) answer in the OEM code
# page, not in the ANSI one Python assumes: a German "ü" then cannot be decoded
# and the whole set-up died on its first umlaut.
_OEM = "oem" if os.name == "nt" else None


def _run(args: List[str]) -> Tuple[bool, str]:
    try:
        out = subprocess.run(args, capture_output=True, text=True, encoding=_OEM,
                             errors="replace", timeout=60)
        return out.returncode == 0, (out.stdout or out.stderr or "").strip()
    except Exception as e:                      # noqa: BLE001 - a set-up step must never raise
        return False, str(e)


def _autostart(target: str) -> Tuple[bool, str]:
    """Start at logon and come back after a crash — the booth must not stay dark.

    The task starts the .exe without arguments: that is the watcher
    (waechter.py), which starts the booth as its child and brings it back
    after a crash. The task's own restart only covers a watcher that cannot
    start at all.
    """
    xml_cmd = ["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "LIMITED",
               "/TN", TASK_NAME, "/TR", target]
    ok, text = _run(xml_cmd)
    if ok:
        # schtasks' defaults suit an office PC, not a booth: no start on
        # battery, stop when the plug flickers, give up after 72 hours, no
        # second try. IgnoreNew: "schtasks /Run" while the booth runs starts
        # no second watcher. Soft: an older Windows without these cmdlets
        # keeps the task. Umstellen-auf-eigenes-Fenster.ps1 checks the same.
        _run(["powershell", "-NoProfile", "-Command",
              "$s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries "
              "-DontStopIfGoingOnBatteries "
              "-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); "
              f"Set-ScheduledTask -TaskName '{TASK_NAME}' -Settings $s | Out-Null"])
    return ok, text


def _dauerbetrieb():
    """Der Dauerbetrieb mit seinem Gedächtnis — gespeichert neben dem Profil."""
    from .config import data_dir
    from .dauerbetrieb import Dauerbetrieb
    return Dauerbetrieb(data_dir() / "dauerbetrieb.json")


def _power() -> List[Tuple[str, bool, str]]:
    """Die Energieeinstellungen — jetzt über ``dauerbetrieb``.

    Vorher standen hier fünf powercfg-Aufrufe, die den Istwert nicht gelesen
    haben. Das Setzen war damit nicht umkehrbar: ``uninstall()`` hat Aufgabe
    und Firewallregel entfernt und den Rechner mit abgeschaltetem Standby
    zurückgelassen. Wer die Box einmal auf seinem Laptop ausprobiert hatte,
    hatte den Laptop dauerhaft verstellt.
    """
    d = _dauerbetrieb()
    nach_key = {e["key"]: e for e in d.status()}
    return [(str(nach_key[r["key"]]["titel"]), bool(r["ok"]), str(r.get("meldung", "")))
            for r in d.empfohlen_anwenden()]


def _firewall(port: int) -> Tuple[bool, str]:
    """Only the machine itself talks to the booth — no rule for the whole network."""
    return _run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}",
                 "dir=in", "action=allow", "protocol=TCP", f"localport={port}",
                 "remoteip=127.0.0.1", "profile=any"])


def _desktop() -> Optional[Path]:
    r"""The desktop as Windows knows it, not as the path guess assumes.

    With OneDrive folder protection on — a default on many Windows 10/11
    machines — the desktop lives under ``%USERPROFILE%\OneDrive\Desktop``
    and ``%USERPROFILE%\Desktop`` no longer exists. The shortcut step then
    reported "Desktop nicht gefunden" on a perfectly normal machine.
    """
    ok, out = _run(["powershell", "-NoProfile", "-Command",
                    "[Environment]::GetFolderPath('Desktop')"])
    if ok and out:
        p = Path(out.strip())
        if p.is_dir():
            return p
    p = Path(os.path.expandvars(r"%USERPROFILE%\OneDrive\Desktop"))
    if p.is_dir():
        return p
    p = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))
    return p if p.is_dir() else None


def _shortcut(target: str, name: str = "Fotobox") -> Tuple[bool, str]:
    desktop = _desktop()
    if desktop is None:
        return False, "Desktop nicht gefunden"
    link = desktop / f"{name}.lnk"
    ps = (f"$s=(New-Object -COM WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{target.strip(chr(34))}';"
          f"$s.WorkingDirectory='{Path(target.strip(chr(34))).parent}';$s.Save()")
    ok, msg = _run(["powershell", "-NoProfile", "-Command", ps])
    return ok, str(link) if ok else msg


def install(port: int = 8765) -> List[Tuple[str, bool, str]]:
    """Everything the booth PC needs. Returns one line per step for the report."""
    if not is_windows():
        return [("Windows", False, "Dieser Schritt läuft nur unter Windows.")]
    target = program_path()
    results: List[Tuple[str, bool, str]] = [("Autostart bei Anmeldung", *_autostart(target))]
    results += _power()
    results.append(("Firewall (nur dieser Rechner)", *_firewall(port)))
    results.append(("Verknüpfung auf dem Desktop", *_shortcut(target)))
    return results


def set_autostart(on: bool) -> List[Tuple[str, bool, str]]:
    """Only the logon task, nothing else - for the Windows installer.

    ``install()`` also changes power settings, adds a firewall rule and a
    desktop shortcut. The installer offers those as separate choices (the
    hardening is off by default there), so it needs the task on its own. Run
    as the user who operates the booth: the task starts the program in that
    user's session, and the data folder is that user's.
    """
    if not is_windows():
        return [("Windows", False, "Dieser Schritt läuft nur unter Windows.")]
    if on:
        return [("Autostart bei Anmeldung", *_autostart(program_path()))]
    ok, text = _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
    if not ok and not _run(["schtasks", "/Query", "/TN", TASK_NAME])[0]:
        return [("Autostart entfernt", True, "war nicht eingerichtet")]
    return [("Autostart entfernt", ok, text)]


def uninstall() -> List[Tuple[str, bool, str]]:
    if not is_windows():
        return [("Windows", False, "Dieser Schritt läuft nur unter Windows.")]
    out = [("Autostart entfernt", *_run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME]))]
    out.append(("Firewall-Regel entfernt",
                *_run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"])))
    # Und die Windows-Einstellungen wieder so, wie sie vorgefunden wurden.
    # Nur die, die wir selbst verstellt haben: Was der Betreiber vorher
    # eingestellt hatte, bleibt seine Sache.
    d = _dauerbetrieb()
    nach_key = {e["key"]: e for e in d.status()}
    for r in d.alles_zuruecksetzen():
        titel = str(nach_key.get(str(r["key"]), {}).get("titel", r["key"]))
        out.append((f"Zurückgesetzt: {titel}", bool(r["ok"]), str(r.get("meldung", ""))))
    return out


def report(results: List[Tuple[str, bool, str]]) -> str:
    lines = []
    for label, ok, detail in results:
        mark = "OK  " if ok else "-- "
        lines.append(f"{mark}{label}" + (f" · {detail}" if detail and not ok else ""))
    return "\n".join(lines)
