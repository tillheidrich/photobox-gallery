"""The template guide for AI assistants stays true to the code (17.11.2026).

``docs/VORLAGEN-FORMAT-FUER-KI.md`` (German) and its English twin
``docs/TEMPLATE-FORMAT-FOR-AI.md`` are meant to be handed to an assistant
with no access to this repository. Everything they promise is checked here:
every JSON example passes the real check (``vorlagen_kern.pruefe``) without
a single hint and unchanged, and renders on its paper; the paper table and
the limits match ``PAPIERE`` and the constants; the papers marked for the
SELPHY are the SELPHY print profile's. When the format changes, this fails
before the guide misleads anyone.
"""
import json
import re
from pathlib import Path

import pytest

from app import vorlagen_kern as kern
from app import vorlagen_render as render

DOCS = Path(__file__).resolve().parents[1] / "docs"
GUIDES = [DOCS / "VORLAGEN-FORMAT-FUER-KI.md", DOCS / "TEMPLATE-FORMAT-FOR-AI.md"]


def _blocks(path: Path):
    return [json.loads(b) for b in re.findall(r"```json\n(.*?)\n```", path.read_text(encoding="utf-8"), flags=re.S)]


def examples(path: Path):
    """The print templates' examples (the frames for boomerang/video have their own section)."""
    return [b for b in _blocks(path) if b.get("kind") != "motion_frame"]


def frame_examples(path: Path):
    return [b for b in _blocks(path) if b.get("kind") == "motion_frame"]


def test_each_guide_has_the_three_frame_examples_and_both_have_the_same():
    de, en = (frame_examples(g) for g in GUIDES)
    assert [e["id"] for e in de] == ["sommerfest-titel", "sommerfest-bild", "leinwand-titel-links"]
    assert de == en


@pytest.mark.parametrize("ex", [pytest.param(e, id=e["id"]) for e in frame_examples(GUIDES[0])])
def test_the_frame_example_passes_the_check_untouched_and_renders(ex, tmp_path):
    from PIL import Image
    from app import motion_frames
    # The pictures example 2 names, as the assistant would have uploaded them.
    Image.new("RGB", (1080, 1920), (40, 110, 80)).save(tmp_path / "hintergrund-sommerfest.png")
    over = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    over.paste((255, 255, 255, 255), (0, 0, 1080, 40))
    over.save(tmp_path / "overlay-sommerfest.png")
    checked, hints = motion_frames.check(json.loads(json.dumps(ex)), media=tmp_path)
    assert hints == [], hints
    for key, value in ex.items():
        if key in ("texts", "shapes"):
            for mine, theirs in zip(value, checked[key]):
                for k, v in mine.items():
                    assert theirs.get(k) == v, (key, k)
        else:
            assert checked[key] == value, key
    size = motion_frames.output_size(checked)
    bottom, top = motion_frames.render_layers(checked, size, tmp_path, {"event_name": "Sommerfest 2027",
                                                                         "date": "17.07.2027"})
    assert bottom.size == top.size == tuple(motion_frames.FORMATS[ex["format"]])
    # The text keeps out of the bottom 320 px the guide recommends for 9:16.
    if ex["format"] == "9:16":
        assert top.getchannel("A").crop((0, 1600, 1080, 1920)).getbbox() is None
    pic = motion_frames.preview(checked, tmp_path, max_px=400)
    assert max(pic.size) == 400


def all_examples():
    for g in GUIDES:
        for ex in examples(g):
            yield pytest.param(ex, id=f"{g.stem}:{ex['id']}")


def test_each_guide_has_the_four_examples_and_both_have_the_same():
    de, en = (examples(g) for g in GUIDES)
    assert [e["id"] for e in de] == ["ein-foto-quer", "zwei-fotos-quer", "drei-fotos-streifen", "vier-fotos-hoch"]
    assert de == en
    assert [len(e["slots"]) for e in de] == [1, 2, 3, 4]
    assert [e["paper"] for e in de] == ["10x15-quer", "10x15-quer", "streifen-2x6", "10x15"]


@pytest.mark.parametrize("ex", list(all_examples()))
def test_the_example_passes_the_check_untouched_and_renders(ex):
    checked, hints = kern.pruefe(json.loads(json.dumps(ex)))
    assert hints == [], hints
    for key in ("id", "name", "paper", "canvas"):
        assert checked[key] == ex[key]
    for liste in ("slots", "texts", "shapes"):
        assert len(checked.get(liste) or []) == len(ex.get(liste) or [])
        for mine, theirs in zip(ex.get(liste) or [], checked.get(liste) or []):
            for key, value in mine.items():
                assert theirs.get(key) == value, (liste, mine.get("id"), key)
    for key, value in (ex.get("qr") or {}).items():
        assert checked["qr"][key] == value
    assert checked["canvas"] == kern.PAPIERE[ex["paper"]]["canvas"]
    # Every text anchor and the QR code keep the safe margin the guide asks for.
    rand = render.sicherer_rand(checked)
    w, h = checked["canvas"]
    for t in checked["texts"]:
        assert rand <= t["x"] <= w - rand and t["y"] >= rand
    if checked.get("qr"):
        q = checked["qr"]
        assert q["x"] >= rand and q["y"] >= rand and q["x"] + q["size"] <= w - rand and q["y"] + q["size"] <= h - rand
    for style in ("clean", "hell", "hochzeit"):
        img = render.render_template(checked, render.beispielbilder(checked["shots"]), qr_url=render.BEISPIEL_QR,
                                     texts={"event_name": "Name des Events", "date": "18.11.2026"},
                                     style_cfg={"preset": style})
        assert img.size == tuple(checked["canvas"])
        sheet = render.druckbogen(img, checked["paper"])
        two_up = kern.PAPIERE[checked["paper"]].get("zwei_auf")
        assert sheet.size == tuple(two_up["canvas"] if two_up else checked["canvas"])


def _table(path: Path):
    rows = re.findall(r"^\| `([a-z0-9-]+)` \| (\d+) × (\d+) \| [^|]+\| [^|]+\| (ja|nein|yes|no)\b",
                      path.read_text(encoding="utf-8"), flags=re.M)
    return {r[0]: ([int(r[1]), int(r[2])], r[3] in ("ja", "yes")) for r in rows}


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.stem)
def test_the_paper_table_is_the_code_s(guide):
    from booth.boothcore.printing import models
    table = _table(guide)
    assert set(table) == set(kern.PAPIERE)
    selphy = set(models.DRUCKPROFILE["selphy-cp"]["papiere"])
    for pid, (canvas, on_selphy) in table.items():
        assert canvas == kern.PAPIERE[pid]["canvas"], pid
        assert on_selphy == (pid in selphy), pid


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.stem)
def test_the_limits_and_safe_margin_are_the_code_s(guide):
    text = guide.read_text(encoding="utf-8")
    line = re.search(r"<!-- schema: (.*?) -->", text).group(1)
    stated = dict(pair.split("=") for pair in line.split())
    assert {k: int(v) for k, v in stated.items()} == {
        "MAX_SLOTS": kern.MAX_SLOTS, "MAX_TEXTE": kern.MAX_TEXTE, "MIN_KANTE": kern.MIN_KANTE,
        "MAX_FORMEN": kern.MAX_FORMEN, "MAX_BILDER": kern.MAX_BILDER}
    margin = render.sicherer_rand({"paper": "10x15"})
    assert margin == 47 and "47 px" in text
    assert kern.PAPIERE["10x15"]["sicher_mm"] == 4.0
    for font in kern.SCHRIFTEN:
        assert f"`{font}`" in text
    for token in ("@hintergrund", "@schrift", "@gedaempft", "@akzent"):
        assert token in text
    for preset in render.STYLE_PRESETS:
        assert f"`{preset}`" in text


def test_the_guides_are_linked():
    assert "VORLAGEN-FORMAT-FUER-KI.md" in (DOCS / "README.md").read_text(encoding="utf-8")
    assert "VORLAGEN-FORMAT-FUER-KI.md" in (DOCS.parent / "README.md").read_text(encoding="utf-8")
