"""A saved template printed as a test sheet at the booth (job ``test_print``).

Before 12.11.2026 the job ignored its payload and always printed the
calibration page. Now it can carry one template: the booth renders it with
the sample photos through the renderer it prints guests' sheets with, marks
it as a test, does not book it on the event's allowance and answers with what
happened at the printer.
"""
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import compose, vorlagen_kern, vorlagen_render


@pytest.fixture
def booth(tmp_path):
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.store.profile["print"] = {**(b.profile.get("print") or {}), "enabled": True, "limit_event": 5,
                                "sharpen": "off"}
    b.TESTDRUCK_WARTEN = 10
    return b


def _ausdrucke(b):
    return sorted((b.data / "print-out").glob("*.jpg"))


def _eigene_vorlage():
    tpl, _ = vorlagen_kern.pruefe({
        "id": "probe-hochzeit", "name": "Probe Hochzeit", "paper": "10x15",
        "slots": [{"x": 60, "y": 60, "w": 1080, "h": 1300}],
        "texts": [{"value": "{event_name}", "x": 600, "y": 1500, "size": 80, "align": "center"}],
    })
    return tpl


def test_a_template_from_the_gallery_is_printed_with_sample_photos(booth):
    tpl = _eigene_vorlage()
    antwort = booth.test_print({"template": "probe-hochzeit.json", "content": tpl})
    assert "gedruckt" in antwort and "Probe Hochzeit" in antwort
    blatt = _ausdrucke(booth)
    assert len(blatt) == 1
    # Same renderer as in operation, same stand-ins as the editor's preview.
    erwartet = vorlagen_render.render_template(
        tpl, vorlagen_render.beispielbilder(tpl.get("shots") or 1), assets_dir=booth.assets_dir,
        qr_url=vorlagen_render.BEISPIEL_QR, texts=booth._testdruck_texte(),
        style_cfg=booth.profile.get("collage_style"), einpassen=booth.profile.get("collage_fit", "auto"))
    with Image.open(blatt[0]) as im:
        assert im.size == erwartet.size


def test_a_test_sheet_does_not_count_against_the_event(booth):
    slug = booth.profile["event"].get("slug", "")
    vorher = booth.print_allowance(None)["event_left"]
    booth.test_print({"template": compose.BUILTIN_TEMPLATES[0]["id"]})
    assert booth.library.event_prints(slug) == 0
    assert booth.print_allowance(None)["event_left"] == vorher


def test_a_built_in_template_is_known_by_its_id(booth):
    antwort = booth.test_print({"template": compose.BUILTIN_TEMPLATES[0]["id"]})
    assert "gedruckt" in antwort
    assert len(_ausdrucke(booth)) == 1


@pytest.mark.parametrize("name", ["../../etc/passwd", "/tmp/x.json", "gibt-es-nicht.json", "C:\\x.json"])
def test_only_templates_the_booth_knows_are_printed(booth, name):
    with pytest.raises(RuntimeError):
        booth.test_print({"template": name})
    assert _ausdrucke(booth) == []


def test_the_sheet_is_marked_as_a_test_unless_switched_off(booth):
    tpl = _eigene_vorlage()
    booth.test_print({"template": "probe-hochzeit.json", "content": tpl})
    booth.test_print({"template": "probe-hochzeit.json", "content": tpl, "mark": False})
    mit, ohne = _ausdrucke(booth)
    with Image.open(mit) as a, Image.open(ohne) as b:
        ecke = (a.width - 260, a.height - 120, a.width, a.height)
        assert a.crop(ecke).tobytes() != b.crop(ecke).tobytes()


def test_a_printer_that_is_down_is_reported_with_its_reason(booth):
    st = booth.printer.stations[0]
    st._poll = lambda: None
    st._status = {"state": "offline", "detail": "Papierfach offen"}
    with pytest.raises(RuntimeError) as fehler:
        booth.test_print({"template": compose.BUILTIN_TEMPLATES[0]["id"]})
    assert "nicht bereit" in str(fehler.value) and "Papierfach offen" in str(fehler.value)
    assert _ausdrucke(booth) == []


def test_without_a_template_the_calibration_page_comes_as_before(booth):
    antwort = booth.test_print({})
    assert "Kalibrierung" in antwort
    assert len(_ausdrucke(booth)) == 1


def test_the_job_hands_its_payload_to_the_test_print(booth):
    antwort = booth.agent.job_handlers["test_print"]({"template": compose.BUILTIN_TEMPLATES[0]["id"]})
    assert "gedruckt" in antwort
