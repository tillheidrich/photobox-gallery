"""Die Box läuft nur, wenn sie gebucht ist.

Aufgebaut wird nachmittags, gebucht ist ab abends. Dazwischen soll niemand das
Farbband leer drucken — und um zwei Uhr nachts, wenn der letzte Gast weg ist,
soll die Box nicht bis zum Morgen weiterlaufen. Ein Fenster über Mitternacht
ist deshalb der Normalfall, nicht der Sonderfall.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-zeit-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    yield b
    b.mail.stop()


def _uhr(stunde: int, minute: int = 0, wochentag: int = 4):
    """struct_time mit gesetzter Uhrzeit und Wochentag (4 = Freitag)."""
    return time.struct_time((2026, 9, 18 + wochentag, stunde, minute, 0, wochentag, 261, -1))


def _plan(booth, **werte):
    booth.store.profile["lock"]["schedule"] = {"enabled": True, "from": "", "until": "", "days": [], **werte}


def test_without_a_plan_the_box_runs(booth):
    assert booth.locked_by_schedule(_uhr(3)) is False
    assert booth.locked is False


def test_inside_the_booked_window_it_runs(booth):
    _plan(booth, **{"from": "19:00", "until": "23:00"})
    assert booth.locked_by_schedule(_uhr(20)) is False


def test_before_the_booking_it_pauses(booth):
    _plan(booth, **{"from": "19:00", "until": "23:00"})
    assert booth.locked_by_schedule(_uhr(15)) is True


def test_a_window_over_midnight_is_normal(booth):
    """19:00 bis 01:00 heißt abends bis nachts — nicht „nie"."""
    _plan(booth, **{"from": "19:00", "until": "01:00"})
    assert booth.locked_by_schedule(_uhr(20)) is False
    assert booth.locked_by_schedule(_uhr(0, 30)) is False
    assert booth.locked_by_schedule(_uhr(2)) is True
    assert booth.locked_by_schedule(_uhr(18, 59)) is True


def test_days_can_be_named(booth):
    _plan(booth, **{"from": "10:00", "until": "22:00", "days": [5, 6]})     # Sa, So
    assert booth.locked_by_schedule(_uhr(12, 0, wochentag=5)) is False
    assert booth.locked_by_schedule(_uhr(12, 0, wochentag=4)) is True


def test_the_night_belongs_to_the_evening_before(booth):
    """Samstag 19 Uhr bis Sonntag 1 Uhr gebucht: Die Nacht zählt zum Samstag."""
    _plan(booth, **{"from": "19:00", "until": "01:00", "days": [5]})
    assert booth.locked_by_schedule(_uhr(23, 0, wochentag=5)) is False
    assert booth.locked_by_schedule(_uhr(0, 30, wochentag=6)) is False, "die Nacht nach dem Samstag"
    assert booth.locked_by_schedule(_uhr(20, 0, wochentag=6)) is True, "der Sonntagabend ist nicht gebucht"


def test_a_switched_off_plan_changes_nothing(booth):
    booth.store.profile["lock"]["schedule"] = {"enabled": False, "from": "19:00", "until": "23:00", "days": []}
    assert booth.locked_by_schedule(_uhr(3)) is False


def test_a_manual_pause_still_wins(booth):
    _plan(booth, **{"from": "00:00", "until": "23:59"})
    booth.store.profile["lock"]["active"] = True
    assert booth.locked is True


def test_both_sides_know_the_same_block():
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parents[2]))
    from app import ecosystem
    from boothcore import config
    assert config.DEFAULT_PROFILE["lock"] == ecosystem.DEFAULT_PROFILE["lock"]
