"""Copy whole events from the booth onto a USB stick.

The operator plugs a stick into the socket at the bottom of the booth and
taps one button. This module finds the stick, copies what the booth still
holds of each run, says how far it is and when the stick may be pulled.

A second backup onto the same stick only writes what is new, so the stick can
stay in for the evening and be topped up at the end.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

log = logging.getLogger("booth.usb")

FOLDER = "Fotobox-Sicherung"
# Small copies the booth makes for its own screen. They are of no use on a
# stick and only double the file count.
NOT_COPIED = {"thumb.jpg", "result_web.jpg", "print.druck.jpg"}
PARTIAL = ".teil"
# Room that stays free on the stick: file systems round up, and a stick
# filled to the last byte is a stick with a broken last file.
RESERVE = 8 * 1024 * 1024


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _total_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).total
    except OSError:
        return 0


def _windows_drives() -> List[Dict]:
    import ctypes
    kernel = ctypes.windll.kernel32                      # type: ignore[attr-defined]
    found = []
    # A card reader without a card must not pop up "no disk in drive".
    kernel.SetErrorMode(0x0001)                          # SEM_FAILCRITICALERRORS
    mask = kernel.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        if kernel.GetDriveTypeW(root) != 2:              # DRIVE_REMOVABLE
            continue
        label = ctypes.create_unicode_buffer(261)
        if not kernel.GetVolumeInformationW(root, label, 261, None, None, None, None, 0):
            continue                                     # card reader without a card
        found.append({"path": root, "label": label.value or f"USB-Stick ({root[:2]})"})
    return found


def _mac_is_stick(plist: bytes) -> bool:
    """Is this what ``diskutil info -plist`` says about a writable USB or SD volume?"""
    import plistlib
    try:
        info = plistlib.loads(plist)
    except Exception:                                   # noqa: BLE001 — no record: a network share
        return False
    return info.get("BusProtocol") in ("USB", "Secure Digital") and info.get("WritableVolume") is not False


def _mac_sticks() -> List[Dict]:
    """/Volumes also holds network shares and disk images. Only USB counts."""
    import subprocess
    found = []
    for d in _mounted_below([Path("/Volumes")], True):
        try:
            out = subprocess.run(["diskutil", "info", "-plist", d["path"]], capture_output=True, timeout=5).stdout
        except Exception:                               # noqa: BLE001
            continue
        if _mac_is_stick(out):
            found.append(d)
    return found


def _mounted_below(roots: Iterable[Path], only_other_devices: bool) -> List[Dict]:
    found = []
    try:
        system = os.stat("/").st_dev
    except OSError:
        system = None
    for root in roots:
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for p in entries:
            try:
                if only_other_devices and (os.stat(p).st_dev == system or not os.path.ismount(p)):
                    continue
            except OSError:
                continue
            found.append({"path": str(p), "label": p.name})
    return found


#: A random id written onto a stick when the admin confirms it at the booth
#: (S-7). The label is shown to the operator but anyone can name a stick
#: "BACKUP"; the id is known only to this stick and this booth.
MARKER = ".photobox-usb-id"


def stick_id(path: Path) -> str:
    """The id this booth once wrote onto the stick, or ""."""
    try:
        raw = (Path(path) / MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return raw if len(raw) == 32 and all(c in "0123456789abcdef" for c in raw) else ""


def mark_stick(path: Path) -> str:
    """Write a fresh id onto the stick and return it."""
    import secrets
    new_id = secrets.token_hex(16)
    (Path(path) / MARKER).write_text(new_id + "\n", encoding="utf-8")
    _flush(Path(path))
    return new_id


def drives() -> List[Dict]:
    """The sticks plugged in right now: ``[{path, label, free, total}]``.

    ``BOOTH_USB_ROOTS`` names folders whose sub-folders count as sticks —
    for tests and for a booth whose stick shows up somewhere unusual.
    """
    override = os.environ.get("BOOTH_USB_ROOTS", "").strip()
    try:
        if override:
            found = _mounted_below([Path(x) for x in override.split(os.pathsep) if x], False)
        elif sys.platform == "win32":
            found = _windows_drives()
        elif sys.platform == "darwin":
            found = _mac_sticks()
        else:
            user_dirs: List[Path] = []
            for base in (Path("/media"), Path("/run/media")):
                try:
                    user_dirs += [p for p in base.iterdir() if p.is_dir()]
                except OSError:
                    pass
            found = _mounted_below([Path("/media"), Path("/mnt")] + user_dirs, True)
    except Exception:                                    # noqa: BLE001
        log.exception("USB-Sticks ließen sich nicht ermitteln")
        return []
    out = []
    for d in found:
        try:
            d["free"] = _free_bytes(Path(d["path"]))
            d["total"] = _total_bytes(Path(d["path"]))
        except OSError:
            continue                                     # pulled while we looked
        out.append(d)
    return out


def events(sessions: Path, skip: Optional[Callable[[str], bool]] = None) -> List[Dict]:
    """What there is to back up: ``[{slug, runs}]``, biggest first."""
    out = []
    try:
        folders = sorted(p for p in sessions.iterdir() if p.is_dir())
    except OSError:
        return []
    for ev in folders:
        n = sum(1 for _ in _runs(ev, skip))
        if n:
            out.append({"slug": ev.name, "runs": n})
    return sorted(out, key=lambda e: -e["runs"])


def _runs(event_dir: Path, skip: Optional[Callable[[str], bool]]):
    try:
        days = sorted(p for p in event_dir.iterdir() if p.is_dir())
    except OSError:
        return
    for day in days:
        try:
            runs = sorted(p for p in day.iterdir() if p.is_dir())
        except OSError:
            continue
        for run in runs:
            if skip is not None and skip(run.name):
                continue
            if (run / ".datensparsam").exists():         # a data-minimal run is wiped shortly; it travels nowhere
                continue
            yield run


def _files(run: Path, originals: bool = True) -> List[Path]:
    try:
        return sorted(p for p in run.iterdir()
                      if p.is_file() and p.name not in NOT_COPIED and not p.name.startswith(".")
                      and (originals or not p.name.startswith("shot")))
    except OSError:
        return []


def _slugs(sessions: Path, wanted: Optional[List[str]], skip: Optional[Callable[[str], bool]]) -> List[str]:
    return list(wanted) if wanted else [e["slug"] for e in events(sessions, skip)]


def _plan(sessions: Path, root: Path, slugs: List[str], skip: Optional[Callable[[str], bool]],
          originals: bool):
    """``([(source, target, size, needs_copy)], {slug: runs})`` — what a pass would copy.

    A file that vanishes while the plan is made (its run was just deleted)
    is left out instead of ending the pass.
    """
    plan = []
    per_event: Dict[str, int] = {}
    for slug in slugs:
        ev = sessions / slug
        if not ev.is_dir() or ev.parent != sessions:
            continue
        for run in _runs(ev, skip):
            files = _files(run, originals)
            if not files:
                continue
            per_event[slug] = per_event.get(slug, 0) + 1
            for f in files:
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                target = root / slug / run.parent.name / run.name / f.name
                try:
                    same = target.stat().st_size == size
                except OSError:
                    same = False
                plan.append((f, target, size, not same))
    return plan, per_event


def has_work(sessions: Path, stick: Path, wanted: Optional[List[str]] = None,
             skip: Optional[Callable[[str], bool]] = None, originals: bool = True,
             retract: Optional[Callable[[str], bool]] = None) -> bool:
    """Would a pass onto this stick change anything? A file to copy or a run to take back.

    The automatic backup asks this before it starts. Until 15.11.2026 every
    reason to look (a run saved, a stick that seemed new, ten quiet minutes)
    started a full pass and logged "USB-Sicherung gestartet", also when the
    stick was already complete.
    """
    sessions, root = Path(sessions), Path(stick) / FOLDER
    slugs = _slugs(sessions, wanted, skip)
    plan, _ = _plan(sessions, root, slugs, skip, originals)
    if any(todo for *_x, todo in plan):
        return True
    if retract is None:
        return False
    for slug in slugs:
        try:
            for day in (root / slug).iterdir():
                if day.is_dir() and any(run.is_dir() and retract(run.name) for run in day.iterdir()):
                    return True
        except OSError:
            continue
    return False


class Job:
    """One backup run. Poll ``status()``; it never raises."""

    def __init__(self, sessions: Path, stick: Path, wanted: Optional[List[str]],
                 skip: Optional[Callable[[str], bool]], originals: bool = True,
                 retract: Optional[Callable[[str], bool]] = None):
        self.sessions, self.stick, self.wanted, self.skip = Path(sessions), Path(stick), wanted, skip
        self.retract = retract
        self.originals = originals
        self._lock = threading.Lock()
        self._s = {"state": "running", "message": "", "runs": 0, "events": [],
                   "files_total": 0, "files_done": 0, "files_copied": 0,
                   "bytes_total": 0, "bytes_done": 0, "stick": str(stick), "started": time.time()}
        self._thread = threading.Thread(target=self._run, name="usb-backup", daemon=True)

    def status(self) -> Dict:
        with self._lock:
            return dict(self._s)

    def _set(self, **kw) -> None:
        with self._lock:
            self._s.update(kw)

    def _run(self) -> None:
        try:
            self._copy()
        except OSError as exc:
            log.warning("USB-Sicherung abgebrochen: %s", exc)
            self._set(state="error", message="Der Stick ist nicht mehr erreichbar oder lässt sich nicht "
                                             "beschreiben. Bitte wieder einstecken und noch einmal sichern.")
        except Exception:                                # noqa: BLE001
            log.exception("USB-Sicherung fehlgeschlagen")
            self._set(state="error", message="Die Sicherung ist fehlgeschlagen. Einzelheiten stehen im Protokoll.")

    def _copy(self) -> None:
        slugs = _slugs(self.sessions, self.wanted, self.skip)
        root = self.stick / FOLDER
        plan, per_event = _plan(self.sessions, root, slugs, self.skip, self.originals)
        self._take_back(root, slugs)
        need = sum(size for _f, _t, size, todo in plan if todo)
        self._set(runs=sum(per_event.values()), events=sorted(per_event), files_total=len(plan),
                  bytes_total=sum(p[2] for p in plan))
        if not plan:
            self._set(state="error", message="Es gibt noch keine Aufnahmen zum Sichern.")
            return
        free = _free_bytes(self.stick)
        if need and need + RESERVE > free:
            missing = (need + RESERVE - free) / 1e6
            self._set(state="error", message=f"Auf dem Stick ist zu wenig Platz. Es fehlen {missing:,.0f} MB."
                      .replace(",", "."))
            return
        done = copied = size_done = 0
        for source, target, size, todo in plan:
            if todo:
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_name(target.name + PARTIAL)
                try:
                    shutil.copyfile(source, part)
                    os.replace(part, target)
                except OSError as exc:
                    try:
                        part.unlink()
                    except OSError:
                        pass
                    if not source.exists():
                        # The run was deleted while this pass ran (seen on the
                        # booth PC: "No such file … result.mp4" ended the whole
                        # backup). Its file is meant to be gone; the next pass
                        # takes the run off the stick if it was withheld.
                        log.info("USB-Sicherung: %s ist inzwischen gelöscht, übersprungen (%s)", source.name, exc)
                        done += 1
                        self._set(files_done=done)
                        continue
                    raise
                copied += 1
            done += 1
            size_done += size
            self._set(files_done=done, files_copied=copied, bytes_done=size_done)
        self._readme(root, per_event)
        _flush(self.stick)
        self._set(state="done", message="")

    def _take_back(self, root: Path, slugs: List[str]) -> None:
        """Remove from the stick what a guest has since deleted or kept to the print.

        Only runs ``retract`` names: a run this booth merely does not know (a
        stick from another evening) is somebody's backup and stays.
        """
        if self.retract is None:
            return
        for slug in slugs:
            try:
                days = [p for p in (root / slug).iterdir() if p.is_dir()]
            except OSError:
                continue
            for day in days:
                try:
                    runs = [p for p in day.iterdir() if p.is_dir()]
                except OSError:
                    continue
                for run in runs:
                    if self.retract(run.name):
                        shutil.rmtree(run, ignore_errors=True)

    def _readme(self, root: Path, per_event: Dict[str, int]) -> None:
        lines = ["Sicherung der Fotobox", time.strftime("Stand: %d.%m.%Y %H:%M"), "",
                 "Je Veranstaltung ein Ordner, darin je Tag und je Durchgang ein Ordner.",
                 "result = fertiges Bild, print = Druckdatei, shot1, shot2 … = Einzelaufnahmen.", ""]
        known: Dict[str, int] = {}
        try:
            for ev in sorted(p for p in root.iterdir() if p.is_dir()):
                known[ev.name] = sum(1 for day in ev.iterdir() if day.is_dir()
                                     for run in day.iterdir() if run.is_dir())
        except OSError:
            known = dict(per_event)
        for slug, n in sorted(known.items()):
            lines.append(f"{slug}: {n} {'Durchgang' if n == 1 else 'Durchgänge'}")
        (root / "LIESMICH.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _flush(stick: Path) -> None:
    """Have the system write everything out before we say "pull it"."""
    try:
        if hasattr(os, "sync"):
            os.sync()
    except OSError:
        pass


def start(sessions: Path, stick: Path, events: Optional[List[str]] = None,
          skip: Optional[Callable[[str], bool]] = None, originals: bool = True,
          retract: Optional[Callable[[str], bool]] = None) -> Job:
    job = Job(sessions, stick, list(events) if events else None, skip, originals, retract)
    job._thread.start()
    return job
