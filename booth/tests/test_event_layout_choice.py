"""At an event with its own layout choice the booth offers exactly the ticked layouts.

The gallery sends ``collage_exact`` with ``collage_templates`` (own files) and
``collage_builtins`` (ids). The booth then offers those, own first, built-ins
after, and nothing else — not the box's own templates, not a plugin's. One
layout: no choice on the screen (the kiosk shows the chooser only for more
than one). Without ``collage_exact`` everything is as before.
"""
import json
import tempfile
from pathlib import Path

import pytest

from boothcore.media import compose


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-event-layouts-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.assets_dir.mkdir(parents=True, exist_ok=True)
    for name in ("kunde.json", "haus.json"):
        (b.assets_dir / name).write_text(json.dumps({
            "id": name[:-5], "name": name, "shots": 1, "canvas": [1200, 1800],
            "slots": [{"x": 60, "y": 60, "w": 1080, "h": 1300}]}), encoding="utf-8")
    yield b
    b.mail.stop()


def test_own_and_built_in_layouts_together_in_the_ticked_order(booth):
    booth.store.profile.update({"collage_exact": True, "collage_templates": ["kunde.json"],
                                "collage_builtins": ["schlicht-1-weiss", "duo"]})
    assert [t["id"] for t in booth.templates()] == ["kunde", "schlicht-1-weiss", "duo"]


def test_only_built_ins_ticked_means_no_own_templates(booth):
    booth.store.profile.update({"collage_exact": True, "collage_templates": [], "collage_builtins": ["duo"]})
    assert [t["id"] for t in booth.templates()] == ["duo"]


def test_one_layout_means_no_choice(booth):
    booth.store.profile.update({"collage_exact": True, "collage_templates": ["kunde.json"],
                                "collage_builtins": []})
    assert len(booth.templates()) == 1


def test_without_the_switch_own_templates_replace_the_built_ins_as_before(booth):
    booth.store.profile.update({"collage_exact": False, "collage_templates": ["haus.json"],
                                "collage_builtins": ["duo"]})
    assert [t["id"] for t in booth.templates()] == ["haus"]


def test_nothing_usable_ticked_falls_back_to_the_catalogue(booth):
    booth.store.profile.update({"collage_exact": True, "collage_templates": ["fehlt.json"],
                                "collage_builtins": ["gibtsnicht"]})
    assert [t["id"] for t in booth.templates()] == [t["id"] for t in compose.BUILTIN_TEMPLATES
                                                    if not t.get("plain")]


def test_the_plain_qr_switch_works_in_the_exact_choice_too(booth):
    booth.store.profile.update({"collage_exact": True, "collage_templates": [],
                                "collage_builtins": ["schlicht-2-weiss"], "collage_plain_qr": True})
    [t] = booth.templates()
    assert not t["qr"].get("hidden")
