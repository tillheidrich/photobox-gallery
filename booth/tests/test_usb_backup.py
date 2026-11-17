"""A whole event — or all of them — onto a USB stick, started at the booth.

Asked for on 14.11.2026: the stick goes into the socket at the bottom of the
booth and the rest has to explain itself. So the booth notices the stick,
offers the backup in the operator menu, says how far it is and when the stick
may be pulled.

What goes onto the stick is what the booth still holds of each run: the
result, the print file, the single shots, the moving version. Not the small
copies the booth made for its own screen. Never a run a guest has deleted,
and never a "print only" run — that promise was "it stays on the booth".
"""
import json
import time
from pathlib import Path

import pytest

from boothcore import usb_backup


def _run(root: Path, event: str, uid: str, shots: int = 2) -> Path:
    d = root / "sessions" / event / "2026-11-18" / uid
    d.mkdir(parents=True)
    for name in ["result.jpg", "print.jpg", "thumb.jpg", "result_web.jpg"] + [f"shot{i+1}.jpg" for i in range(shots)]:
        (d / name).write_bytes(name.encode() * 50)
    return d


@pytest.fixture()
def box(tmp_path):
    data = tmp_path / "box"
    _run(data, "mara-jonas", "aaa111")
    _run(data, "mara-jonas", "bbb222", shots=3)
    _run(data, "anderes-fest", "ccc333")
    stick = tmp_path / "STICK"
    stick.mkdir()
    return data, stick


def _wait(job):
    for _ in range(200):
        if job.status()["state"] in ("done", "error"):
            return job.status()
        time.sleep(0.02)
    raise AssertionError("backup never finished")


def test_one_event_lands_readable_on_the_stick(box):
    data, stick = box
    status = _wait(usb_backup.start(data / "sessions", stick, events=["mara-jonas"]))
    assert status["state"] == "done", status
    ziel = stick / "Fotobox-Sicherung" / "mara-jonas" / "2026-11-18"
    assert sorted(p.name for p in (ziel / "aaa111").iterdir()) == ["print.jpg", "result.jpg", "shot1.jpg", "shot2.jpg"]
    assert (ziel / "bbb222" / "shot3.jpg").read_bytes() == b"shot3.jpg" * 50
    assert not (stick / "Fotobox-Sicherung" / "anderes-fest").exists()
    assert status["runs"] == 2 and status["files_done"] == status["files_total"] == 9


def test_all_events_when_none_is_named(box):
    data, stick = box
    status = _wait(usb_backup.start(data / "sessions", stick))
    assert status["state"] == "done"
    assert (stick / "Fotobox-Sicherung" / "anderes-fest" / "2026-11-18" / "ccc333" / "result.jpg").is_file()
    assert status["runs"] == 3


def test_the_small_screen_copies_stay_at_the_booth(box):
    data, stick = box
    _wait(usb_backup.start(data / "sessions", stick))
    names = {p.name for p in (stick / "Fotobox-Sicherung").rglob("*") if p.is_file()}
    assert "thumb.jpg" not in names and "result_web.jpg" not in names


def test_a_deleted_or_print_only_run_never_reaches_the_stick(box):
    data, stick = box
    status = _wait(usb_backup.start(data / "sessions", stick, skip=lambda uid: uid in {"aaa111", "ccc333"}))
    assert status["state"] == "done" and status["runs"] == 1
    assert not list((stick / "Fotobox-Sicherung").rglob("aaa111"))
    assert not list((stick / "Fotobox-Sicherung").rglob("ccc333"))


def test_a_second_backup_only_copies_what_is_new(box):
    data, stick = box
    _wait(usb_backup.start(data / "sessions", stick))
    _run(data, "mara-jonas", "ddd444")
    status = _wait(usb_backup.start(data / "sessions", stick))
    assert status["state"] == "done"
    assert status["files_copied"] == 4, "only the new run is written again"
    assert status["files_done"] == status["files_total"]


def test_the_stick_says_what_is_on_it(box):
    data, stick = box
    _wait(usb_backup.start(data / "sessions", stick))
    text = (stick / "Fotobox-Sicherung" / "LIESMICH.txt").read_text(encoding="utf-8")
    assert "mara-jonas" in text and "2 Durchgänge" in text


def test_a_full_stick_is_an_error_in_plain_words(box, monkeypatch):
    data, stick = box
    monkeypatch.setattr(usb_backup, "_free_bytes", lambda _p: 10)
    status = _wait(usb_backup.start(data / "sessions", stick))
    assert status["state"] == "error"
    assert "Platz" in status["message"]


def test_a_stick_pulled_halfway_is_an_error_not_a_crash(box, monkeypatch):
    data, stick = box
    echt = usb_backup.shutil.copyfile
    calls = {"n": 0}

    def reisst(src, dst, *a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(5, "Input/output error")
        return echt(src, dst, *a, **k)

    monkeypatch.setattr(usb_backup.shutil, "copyfile", reisst)
    status = _wait(usb_backup.start(data / "sessions", stick))
    assert status["state"] == "error"
    assert "Stick" in status["message"]
    assert not list((stick / "Fotobox-Sicherung").rglob("*.teil")), "no half file left behind"


def test_drives_come_from_the_roots_the_system_names(tmp_path, monkeypatch):
    a = tmp_path / "vol" / "STICK"; a.mkdir(parents=True)
    monkeypatch.setenv("BOOTH_USB_ROOTS", str(tmp_path / "vol"))
    drives = usb_backup.drives()
    assert [d["label"] for d in drives] == ["STICK"]
    assert drives[0]["path"] == str(a) and drives[0]["free"] > 0


def test_no_stick_is_an_empty_list_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTH_USB_ROOTS", str(tmp_path / "nichts"))
    assert usb_backup.drives() == []


def test_single_shots_can_stay_behind(box):
    data, stick = box
    _wait(usb_backup.start(data / "sessions", stick, originals=False))
    names = {p.name for p in (stick / "Fotobox-Sicherung").rglob("*") if p.is_file()}
    assert "result.jpg" in names and "print.jpg" in names
    assert not any(n.startswith("shot") for n in names)


def test_on_a_mac_only_usb_volumes_count_as_sticks():
    """Seen on 14.11.2026: the admin offered "Stick: Printer · 3040 GB frei" —
    a network share under /Volumes. A backup must never land there by itself."""
    import plistlib
    usb = plistlib.dumps({"BusProtocol": "USB", "WritableVolume": True})
    sd = plistlib.dumps({"BusProtocol": "Secure Digital", "WritableVolume": True})
    sata = plistlib.dumps({"BusProtocol": "SATA", "WritableVolume": True})
    locked = plistlib.dumps({"BusProtocol": "USB", "WritableVolume": False})
    assert usb_backup._mac_is_stick(usb) and usb_backup._mac_is_stick(sd)
    assert not usb_backup._mac_is_stick(sata)
    assert not usb_backup._mac_is_stick(locked)
    assert not usb_backup._mac_is_stick(b""), "a network share has no such record"


# --- review of 14 Nov 2026: what a guest did not want kept must not travel ---

def test_a_data_minimal_run_never_reaches_the_stick(box):
    data, stick = box
    run = next((data / "sessions").rglob("aaa111"))
    (run / ".datensparsam").write_text("")
    _wait(usb_backup.start(data / "sessions", stick))
    assert not list((stick / "Fotobox-Sicherung").rglob("aaa111"))
    assert list((stick / "Fotobox-Sicherung").rglob("bbb222"))


def test_a_run_deleted_after_the_backup_leaves_the_stick_too(box):
    data, stick = box
    _wait(usb_backup.start(data / "sessions", stick))
    assert list((stick / "Fotobox-Sicherung").rglob("aaa111"))
    gone = {"aaa111"}
    _wait(usb_backup.start(data / "sessions", stick, skip=gone.__contains__, retract=gone.__contains__))
    assert not list((stick / "Fotobox-Sicherung").rglob("aaa111"))
    assert list((stick / "Fotobox-Sicherung").rglob("bbb222"))       # nothing else is touched


def test_a_run_the_booth_does_not_know_stays_on_a_stick_it_is_already_on(box):
    """Unknown is not the same as deleted: a stick from another evening keeps its pictures."""
    data, stick = box
    _wait(usb_backup.start(data / "sessions", stick))
    _wait(usb_backup.start(data / "sessions", stick, skip=lambda uid: True, retract=lambda uid: False))
    assert list((stick / "Fotobox-Sicherung").rglob("aaa111"))


def test_a_run_still_being_taken_is_not_copied_yet():
    from types import SimpleNamespace
    from boothcore.app import Booth
    lib = SimpleNamespace(withheld=lambda uid: False, get=lambda uid: {"uid": uid} if uid == "fertig" else None)
    fake = SimpleNamespace(library=lib, sessions={"laeuft-noch": object()})
    assert Booth.usb_skip(fake, "laeuft-noch") is True      # its consent is not on record yet
    assert Booth.usb_skip(fake, "fertig") is False
    assert Booth.usb_skip(fake, "von-gestern") is False     # not in the library, not running: back it up
