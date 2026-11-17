"""Self-check: a live view that stays dark at the end of the ladder names the likely cause.

Booth PC, 15.11.2026 (EOS M50, M, flash): ISO and shutter climbed to their
limits and the live frame stayed near black. Many Canon bodies show the
exposure for the flash in live view ("exposure simulation"); switched off in
the camera menu, the live view shows the room. The self-check says so — as a
possibility to look at, not as a promise.
"""
from __future__ import annotations

from types import SimpleNamespace

from boothcore.doctor import OK, WARN, Doctor


class _Kamera:
    backend = "edsdk"

    def __init__(self, mean, at_end):
        self._status = {"on": True, "mean": mean, "at_end": at_end}

    def frage(self, _was):
        return "M"

    def preview_line(self):
        return "Live-Bild: ISO 12800, 1/30 · Helligkeit 4/255"

    def preview_status(self):
        return dict(self._status)


def _booth(mean, at_end, **flash):
    profil = {"flash": {"enabled": True, "recycle_seconds": 2, **flash}, "modes": ["photo"], "gif": {}}
    return SimpleNamespace(profile=profil, camera=_Kamera(mean, at_end))


def test_dark_at_the_end_of_the_ladder_points_to_exposure_simulation():
    check = Doctor(_booth(4.0, True)).check_flash()
    assert check.state == WARN
    assert "Belichtungssimulation" in check.hint and "Canon" in check.hint
    assert "Kameramenü" in check.hint


def test_the_hint_promises_nothing():
    hint = Doctor(_booth(4.0, True)).check_flash().hint.lower()
    for versprechen in ("garantiert", "immer", "behebt", "sicher"):
        assert versprechen not in hint, versprechen


def test_a_bright_live_view_or_rungs_left_give_no_hint():
    for mean, at_end in ((120.0, False), (4.0, False), (None, True), (120.0, True)):
        check = Doctor(_booth(mean, at_end)).check_flash()
        assert check.state == OK, (mean, at_end)
        assert "Belichtungssimulation" not in (check.hint or ""), (mean, at_end)


def test_the_hint_joins_an_existing_warning():
    check = Doctor(_booth(4.0, True, expected_mode="Av")).check_flash()
    assert check.state == WARN
    assert "Moduswahlrad" in check.hint and "Belichtungssimulation" in check.hint
