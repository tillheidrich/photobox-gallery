"""Wie viele Filter der Gast sieht — und ob überhaupt.

Zwölf Filter sind auf einer Feier mit Schlange keine Auswahl, sondern eine
Bremse. Deshalb drei Schalter: welche Filter es gibt, höchstens wie viele
gezeigt werden, und ob der Gast überhaupt wählen soll.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-looks-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["luts"] = {**b.profile["luts"], "enabled": False}
    return b


def test_by_default_the_guest_picks_from_the_list(booth):
    assert booth.profile["looks_mode"] == "ask"
    assert booth.look_names() == booth.profile["looks"]


def test_a_limit_cuts_the_list(booth):
    booth.store.profile["looks_max"] = 3
    namen = booth.look_names()
    assert len(namen) == 3
    assert "original" in namen         # ohne „Original" käme niemand zurück


def test_the_limit_keeps_original_even_if_it_is_not_first(booth):
    booth.store.profile["looks"] = ["warm", "cool", "mono", "original", "film"]
    booth.store.profile["looks_max"] = 2
    assert booth.look_names() == ["original", "warm"]


def test_one_fixed_filter_means_no_choice_at_all(booth):
    booth.store.profile.update({"looks_mode": "auto", "looks_auto": "film"})
    assert booth.look_names() == ["film"]


def test_off_means_the_picture_stays_as_it_came_out_of_the_camera(booth):
    booth.store.profile["looks_mode"] = "off"
    assert booth.look_names() == ["original"]


def test_the_kiosk_only_shows_the_strip_when_there_is_something_to_choose(booth):
    """Eine Liste mit einem Eintrag blendet die Oberfläche selbst aus."""
    booth.store.profile["looks_mode"] = "off"
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    zustand = TestClient(create_app(booth)).get("/api/state").json()
    assert len(zustand["looks"]) == 1


def test_a_finalised_session_still_accepts_the_fixed_filter(booth):
    """Sonst stünde die Box bei „immer derselbe" mit einem Fehler da."""
    import time
    booth.camera.start()
    for _ in range(50):
        if booth.camera.state == "ready":
            break
        time.sleep(0.1)
    booth.store.profile.update({"looks_mode": "auto", "looks_auto": "warm"})
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "warm")
    assert s.look == "warm"
    booth.camera.stop()
