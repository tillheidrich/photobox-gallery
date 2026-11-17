"""The booth never loads a template file raw (17.11.2026).

A file in the media folder — synced from the gallery or put there by hand —
goes through ``vorlagen_kern.annehmen`` (``compose.load_template``). One that
does not pass is skipped with its reason in the log and is not offered to
guests; the booth does not crash and does not draw it. The built-in layouts
pass the same check and render byte for byte as before.
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

GUT = {"id": "box-gut", "name": "Gut", "paper": "10x15",
       "slots": [{"x": 60, "y": 60, "w": 1080, "h": 1300, "shot": 0, "fit": "einpassen"}]}


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-tor-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.profile["access"]["pin"] = "135790"
    yield b
    b.mail.stop()


def _lege(booth, name, inhalt):
    booth.assets_dir.mkdir(parents=True, exist_ok=True)
    (booth.assets_dir / name).write_text(json.dumps(inhalt), encoding="utf-8")


def test_a_broken_file_in_the_media_folder_is_skipped_with_its_reason(booth, caplog):
    _lege(booth, "gut.json", GUT)
    _lege(booth, "boese.json", dict(GUT, id="box-boese", overlay="../../geheim.png",
                                     slots=[{"x": -40, "y": 0, "w": 500, "h": 500}]))
    _lege(booth, "kaputt.json", {"slots": "alle"})
    booth.profile["collage_templates"] = ["gut.json", "boese.json", "kaputt.json"]
    booth.profile["collage_exact"] = True
    with caplog.at_level(logging.WARNING):
        angeboten = [t["id"] for t in booth.templates()]
    assert angeboten == ["box-gut"]
    text = caplog.text
    assert "boese.json" in text and "overlay" in text and "slots[0].x" in text
    assert "kaputt.json" in text and "slots" in text
    assert [n for n, _ in booth.own_template_files()] == ["gut.json"]


def test_a_loaded_file_is_the_checked_one_and_keeps_fit(booth):
    from boothcore.media import compose
    _lege(booth, "gut.json", dict(GUT, filter="sepia"))
    t = compose.load_template(booth.assets_dir / "gut.json")
    assert "filter" not in t and t["slots"][0]["fit"] == "einpassen"
    # The same content twice: the layout pictures are cached by it.
    assert compose.load_template(booth.assets_dir / "gut.json") == t


def test_the_booth_editor_refuses_with_the_fields(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    token = c.post("/api/admin/login", json={"pin": "135790"}).json()["token"]
    r = c.post("/api/admin/templates", json=dict(GUT, texts=[{"value": "x", "font": "C:/Windows/Fonts/a.ttf"}]),
               headers={"X-Admin-Token": token})
    assert r.status_code == 400 and "texts[0].font" in r.text


def test_a_test_print_with_refused_content_fails_with_the_reason(booth):
    with pytest.raises(RuntimeError, match="slots"):
        booth._testdruck_vorlage({"template": "x.json", "content": dict(GUT, slots=[{"x": 0}] * 20)})


@pytest.mark.parametrize("tid", [t["id"] for t in __import__("boothcore.media.compose", fromlist=["x"]).BUILTIN_TEMPLATES])
def test_every_built_in_layout_passes_and_renders_byte_for_byte(tid):
    from boothcore.media import compose, vorlagen_kern
    roh = next(t for t in compose.BUILTIN_TEMPLATES if t["id"] == tid)
    geprueft, hinweise = vorlagen_kern.annehmen(json.loads(json.dumps(roh)), messen=False)
    assert not any(h.startswith("Unbekannte") for h in hinweise), hinweise
    fotos = compose.beispielbilder(geprueft.get("shots") or 1)
    texte = {"event_name": "Mia & Tom", "date": "01.01.2027"}
    for stil in (None, {"preset": "hochzeit"}):
        a = compose.render_template(roh, fotos, qr_url="https://example.invalid/q", texts=texte, style_cfg=stil)
        b = compose.render_template(geprueft, fotos, qr_url="https://example.invalid/q", texts=texte, style_cfg=stil)
        assert a.tobytes() == b.tobytes(), tid
