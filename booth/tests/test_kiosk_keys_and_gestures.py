"""Windows key and three/four-finger touch gestures: locked on request, and back again.

From the dslrBooth comparison (list item 47 / closing list 31): a guest who
hits the Windows key or swipes with three fingers lands on the desktop or the
task view, in front of everybody. Two more measures of the "Dauerbetrieb"
set, next to the existing edge-swipe lock, each reversible to the value that
stood there before. None of them is applied by "empfohlen": each one takes
the key or the gesture from the operator too.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boothcore import dauerbetrieb as db                      # noqa: E402
from tests.fake_windows import FakeWindows                     # noqa: E402

KEYS = r"HKLM\SYSTEM\CurrentControlSet\Control\Keyboard Layout"
DESKTOP = r"HKCU\Control Panel\Desktop"


def _booth(tmp_path, admin=True):
    w = FakeWindows(admin=admin)
    return db.Dauerbetrieb(tmp_path / "dauerbetrieb.json", run=w.run, windows=True), w


def test_both_new_measures_exist_and_are_never_applied_by_default():
    for key in ("windows_taste", "touchgesten", "randgesten"):
        m = db.NACH_KEY[key]
        assert m.empfohlen is False, key
    assert db.NACH_KEY["windows_taste"].admin and db.NACH_KEY["windows_taste"].neustart
    assert not db.NACH_KEY["touchgesten"].admin and db.NACH_KEY["touchgesten"].neustart


def test_the_windows_key_is_mapped_to_nothing_and_given_back(tmp_path):
    d, w = _booth(tmp_path)
    assert d.anwenden("windows_taste")["ok"] is True
    typ, wert = w.registry[(KEYS.lower(), "scancode map")]
    assert typ == "REG_BINARY"
    # header (8 zero bytes), 3 entries incl. terminator, LWin 0xE05B and RWin 0xE05C -> 0
    assert wert == "00000000000000000300000000005BE000005CE000000000"
    assert [e for e in d.status() if e["key"] == "windows_taste"][0]["zustand"] == "optimiert"
    assert d.zuruecksetzen("windows_taste")["ok"] is True
    assert (KEYS.lower(), "scancode map") not in w.registry    # there was none before: deleted, not guessed


def test_an_existing_key_map_of_the_operator_comes_back_unchanged(tmp_path):
    d, w = _booth(tmp_path)
    own = "0000000000000000020000001D003A0000000000"         # Caps Lock -> Ctrl, the operator's own
    w.setze_registry(KEYS, "Scancode Map", own, "REG_BINARY")
    assert d.lesen(db.NACH_KEY["windows_taste"]) == own        # a value name with a space is read
    d.anwenden("windows_taste")
    d.zuruecksetzen("windows_taste")
    assert w.registry[(KEYS.lower(), "scancode map")] == ("REG_BINARY", own)


def test_without_administrator_rights_the_key_lock_says_so_and_remembers_nothing(tmp_path):
    d, w = _booth(tmp_path, admin=False)
    r = d.anwenden("windows_taste")
    assert r["ok"] is False and "Administratorrechte" in r["meldung"]
    assert d.zuruecksetzen("windows_taste")["geaendert"] is False


def test_three_and_four_finger_gestures_off_and_back(tmp_path):
    d, w = _booth(tmp_path)
    w.setze_registry(DESKTOP, "TouchGestureSetting", "1")
    r = d.anwenden("touchgesten")
    assert r["ok"] is True and "Neustart" in r["meldung"]
    assert w.registry[(DESKTOP.lower(), "touchgesturesetting")] == ("REG_DWORD", "0")
    d.zuruecksetzen("touchgesten")
    assert w.registry[(DESKTOP.lower(), "touchgesturesetting")] == ("REG_DWORD", "1")


def test_outside_windows_nothing_is_touched(tmp_path):
    calls = []
    d = db.Dauerbetrieb(tmp_path / "z.json", run=lambda a: calls.append(a) or (0, ""), windows=False)
    for key in ("windows_taste", "touchgesten"):
        assert d.anwenden(key)["ok"] is False
    assert calls == []
