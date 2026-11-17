"""A single 2×6 strip prints twice on a 10×15 sheet; printers carry a profile.

The template is designed once as one strip (paper ``streifen-2x6``). The
guest's picture — gallery, download — is that one strip; the print file is
the 10×15 sheet with the strip twice, cut in the middle. Templates on every
other paper print exactly as before. The printer profiles say which papers a
model takes and whether it prints borderless; without a model the booth
behaves like the SELPHY it was built for.
"""
import json
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import compose, vorlagen_kern
from boothcore.printing import models


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-streifen-"))
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "collage"]
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)

    def capture(target):
        Image.new("RGB", (1500, 1000), (40, 90, 200)).save(target, "JPEG", quality=95)
        return Path(target)
    monkeypatch.setattr(b.camera, "capture", capture)
    yield b
    b.camera.stop()
    b.mail.stop()


def _vorlage(booth, paper, canvas, slots):
    v, _ = vorlagen_kern.pruefe({"name": f"Test {paper}", "paper": paper, "canvas": canvas, "slots": slots,
                                 "shapes": [{"id": "grund", "kind": "rect", "x": 0, "y": 0,
                                             "w": canvas[0], "h": canvas[1], "fill": "#f0e0d0"}],
                                 "order": ["grund", "overlay"]})
    booth.assets_dir.mkdir(parents=True, exist_ok=True)
    (booth.assets_dir / f"{v['id']}.json").write_text(json.dumps(v), encoding="utf-8")
    booth.store.profile["collage_templates"] = [f"{v['id']}.json"]
    return v


def _lauf(booth, vorlage):
    s = booth.start_session("collage", "3:2", vorlage["id"], None)
    for _ in range(max(1, s.shots_needed)):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def test_a_strip_template_prints_twice_side_by_side_and_the_guest_keeps_one_strip(booth):
    v = _vorlage(booth, "streifen-2x6", [600, 1800],
                 [{"x": 60, "y": 72 + i * 380, "w": 480, "h": 360, "shot": i} for i in range(3)])
    s = _lauf(booth, v)
    with Image.open(s.result) as ergebnis:
        assert ergebnis.size == (600, 1800)
    with Image.open(s.print_file) as bogen:
        bogen = bogen.convert("RGB")
        assert bogen.size == (1200, 1800)
        links, rechts = bogen.crop((0, 0, 600, 1800)), bogen.crop((600, 0, 1200, 1800))
        # JPEG: the two halves are the same picture, not the same bytes.
        unterschied = max(abs(a - b) for a, b in zip(links.resize((60, 180)).tobytes(),
                                                     rechts.resize((60, 180)).tobytes()))
        assert unterschied < 12


def test_a_postcard_template_prints_as_it_always_did(booth):
    v = _vorlage(booth, "10x15", [1200, 1800], [{"x": 96, "y": 96, "w": 1008, "h": 700}])
    s = _lauf(booth, v)
    with Image.open(s.print_file) as bogen, Image.open(s.result) as ergebnis:
        assert bogen.size == ergebnis.size == (1200, 1800)


def test_without_a_model_the_profile_is_the_selphy_of_today():
    ohne = models.druckprofil(None)
    assert ohne == models.druckprofil("selphy-cp") == models.druckprofil("unbekannt")
    assert ohne["randlos"] is True
    assert ohne["anschnitt_mm"] == 0                 # the sheet goes out at trim size, as before
    assert "10x15" in ohne["papiere"] and "10x15-quer" in ohne["papiere"]


def test_every_profile_names_only_papers_the_templates_know():
    for m in models.MODELLE:
        profil = models.druckprofil(m["id"])
        assert set(profil["papiere"]) <= set(vorlagen_kern.PAPIERE), m["id"]
        assert profil["quelle"]


def test_strips_are_cut_by_the_printer_on_a_dnp_and_by_hand_on_a_selphy():
    assert models.kann_papier("dnp-ds620", "streifen-2x6")
    assert models.druckprofil("dnp-ds620")["schnitt"] == "drucker"
    assert models.druckprofil("selphy-cp")["schnitt"] == "hand"
    assert not models.kann_papier("dnp-qw410", "streifen-2x6")
    assert models.kann_papier("dnp-qw410", "11x20")
    assert not models.kann_papier("selphy-cp", "15x20")


def test_the_admin_list_of_models_carries_the_profile():
    eintrag = next(m for m in models.auswahl() if m["id"] == "dnp-ds620")
    assert eintrag["profil"]["randlos"] is True
    assert "15x20" in eintrag["profil"]["papiere"]
    assert eintrag["profil"]["text"]                 # one sentence for the admin page
