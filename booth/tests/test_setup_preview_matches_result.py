"""The live preview shows exactly what ends up in the picture — set-up, countdown, result.

Seen on 15.11.2026 at the real booth (Canon M50, live view 3:2): "Die Vorschau
beim Format zeigt auch bei quer nicht das, was auf dem finalen Bild drauf
wäre — man sieht erst alles, wenn der Countdown läuft." Measured in headless
Chrome at 1920x1080 with a 3:2 test chart: the set-up box for "quer" (3:2)
came out 917x920 — ``max-width`` squeezed the box, ``aspect-ratio`` lost, and
``object-fit: cover`` cut a square out of the middle. The countdown's
full-screen live picture cut 7.8 % off top and bottom of every format, a
collage slot the server letterboxes (portrait slot, landscape camera) was
shown cropped, and boomerang/film (layout "blur": the whole frame on a
blurred backdrop) were shown as a narrow 9:16 slice.

Now one pure function decides, for mode, format, template and shot, which
part of the live picture the server keeps and how it sits in the frame
(``previewRule`` / ``cropRect`` / ``previewLayout`` between the
``<preview-geometry>`` markers in app.js). Set-up and countdown both place
the live picture with it. These tests run that very code with Node and hold
it against the server's own crop functions on real images.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from boothcore.media import boomerang, compose
from boothcore.media.vorlagen_render import passt_ins_feld

STATIC = Path(__file__).resolve().parents[1] / "boothcore" / "web" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

SOURCES = {"3:2": (1200, 800), "4:3": (1200, 900), "16:9": (1280, 720), "2:3 hochkant": (800, 1200)}
FORMATS = list(compose.ASPECTS)


def _geometry_source() -> str:
    start = JS.index("// <preview-geometry>")
    end = JS.index("// </preview-geometry>")
    return JS[start:end]


def run_js(expr: str):
    if not NODE:
        pytest.skip("node is not installed")
    script = _geometry_source() + f"\nprocess.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


class _Crops:
    """Records the boxes the server's code cuts with ``Image.crop``."""

    def __init__(self, monkeypatch):
        self.boxes = []
        orig = Image.Image.crop

        def crop(im, box=None):
            if box is not None and im.size in [s for s in SOURCES.values()]:
                self.boxes.append(tuple(int(v) for v in box))
            return orig(im, box)
        monkeypatch.setattr(Image.Image, "crop", crop)

    def take(self):
        boxes, self.boxes = self.boxes, []
        return boxes


def _server(kind: str, src, fmt: str = "", slot=None, collage_fit: str = "auto", layout: str = "blur", crops=None):
    """What the server keeps of a ``src``-sized shot: ("cover", [x, y, w, h]) or ("contain", None)."""
    img = Image.new("RGB", src, "#808080")
    if kind in ("photo",):
        compose.crop_to_aspect(img, fmt)
    elif kind == "gif":
        boomerang._gif_frames([img], fmt, 240, "native", False, map)
    elif kind in ("boomerang", "video"):
        width = boomerang.resolve_width("auto", img.size, fmt, layout, 1080, "native")
        boomerang.layout_frame(img, fmt, width, layout)
    elif kind == "collage":
        passt_ins_feld(img, int(slot["w"]), int(slot["h"]), str(slot.get("fit") or collage_fit))
    boxes = crops.take()
    if not boxes:
        return "contain", None
    x0, y0, x1, y1 = boxes[0]
    return "cover", [x0, y0, x1 - x0, y1 - y0]


def _js_cases(cases):
    out = run_js("(" + json.dumps(cases) + ").map((c) => { const r = previewRule(c.mode, c.fmt, c.tpl, c.shot, c.profile,"
                 " c.src[0], c.src[1]); return [r.fit, r.fit === 'cover' ? cropRect(r, c.src[0], c.src[1]) : null]; })")
    return out


def _assert_same(cases, expected, js):
    bad = []
    for c, (efit, erect), (jfit, jrect) in zip(cases, expected, js):
        if efit != jfit or (erect and any(abs(a - b) > 1 for a, b in zip(erect, jrect))):
            bad.append((c["label"], "server", efit, erect, "kiosk", jfit, jrect))
    assert not bad, "\n".join(map(str, bad))


def _profile(**over):
    p = {"collage_fit": "auto", "boomerang": {"layout": "blur"}, "video": {"layout": "blur"}}
    p.update(over)
    return p


def test_photo_and_gif_preview_crop_the_same_rectangle_as_the_server(monkeypatch):
    crops = _Crops(monkeypatch)
    cases, expected = [], []
    for sname, src in SOURCES.items():
        for fmt in FORMATS:
            for mode in ("photo", "gif"):
                cases.append({"label": f"{mode} {fmt} from {sname}", "mode": mode, "fmt": fmt, "tpl": None,
                              "shot": 0, "profile": _profile(), "src": list(src)})
                expected.append(_server(mode, src, fmt, crops=crops))
    _assert_same(cases, expected, _js_cases(cases))


@pytest.mark.parametrize("layout", ["blur", "crop"])
def test_boomerang_and_film_preview_follow_the_clip_layout(monkeypatch, layout):
    crops = _Crops(monkeypatch)
    cases, expected = [], []
    for sname, src in SOURCES.items():
        for fmt in FORMATS:
            for mode in ("boomerang", "video"):
                prof = _profile(**{mode: {"layout": layout}})
                cases.append({"label": f"{mode} {layout} {fmt} from {sname}", "mode": mode, "fmt": fmt, "tpl": None,
                              "shot": 0, "profile": prof, "src": list(src)})
                expected.append(_server(mode, src, fmt, layout=layout, crops=crops))
    _assert_same(cases, expected, _js_cases(cases))


def _own_template():
    # An operator's template: a landscape slot that insists on being filled
    # (fit "beschneiden") and a portrait one that wants the whole picture.
    return {"id": "eigen", "name": "Eigen", "canvas": [1800, 1200],
            "slots": [{"x": 0, "y": 0, "w": 1200, "h": 800, "fit": "beschneiden"},
                      {"x": 1200, "y": 0, "w": 600, "h": 1200, "fit": "einpassen"},
                      {"x": 0, "y": 800, "w": 600, "h": 400, "shot": 0}]}


@pytest.mark.parametrize("collage_fit", list(compose.EINPASSEN))
def test_collage_preview_shows_what_each_slot_keeps(monkeypatch, collage_fit):
    crops = _Crops(monkeypatch)
    cases, expected = [], []
    templates = list(compose.BUILTIN_TEMPLATES) + [_own_template()]
    for t in templates:
        info = compose.template_info(t)
        for sname, src in SOURCES.items():
            for shot in range(info["shots"]):
                slot = next(s for s in info["slots"] if s["shot"] % info["shots"] == shot)
                cases.append({"label": f"{t['id']} shot {shot} {collage_fit} from {sname}", "mode": "collage",
                              "fmt": "4:5", "tpl": info, "shot": shot, "profile": _profile(collage_fit=collage_fit),
                              "src": list(src)})
                expected.append(_server("collage", src, slot=slot, collage_fit=collage_fit, crops=crops))
    _assert_same(cases, expected, _js_cases(cases))


def test_template_info_tells_the_kiosk_every_slot_and_its_shot():
    info = compose.template_info(_own_template())
    assert info["slots"] == [{"w": 1200, "h": 800, "shot": 0, "fit": "beschneiden"},
                             {"w": 600, "h": 1200, "shot": 1, "fit": "einpassen"},
                             {"w": 600, "h": 400, "shot": 0, "fit": ""}]


@pytest.mark.parametrize("area", [(917, 920), (1920, 1080), (1280, 800), (600, 1000)])
@pytest.mark.parametrize("fmt", FORMATS)
def test_the_box_keeps_the_aspect_of_the_result_in_any_area(area, fmt):
    # The 15.11. fault: the box came out 917x920 for 3:2. Whatever the space,
    # the box has the result's aspect and the kept part fills it exactly.
    got = run_js(f"(() => {{ const r = previewRule('photo', '{fmt}', null, 0, {{}}, 1200, 800);"
                 f" return [previewLayout(r, 1200, 800, {area[0]}, {area[1]}), cropRect(r, 1200, 800)]; }})()")
    lay, crop = got
    box, img = lay["box"], lay["img"]
    want = compose.ASPECTS[fmt]
    assert box[2] <= area[0] + 0.5 and box[3] <= area[1] + 0.5
    assert abs(box[2] / box[3] - want) < 0.01
    assert max(box[2] / area[0], box[3] / area[1]) > 0.99          # as large as the space allows
    scale = box[2] / crop[2]
    assert abs(img[0] + crop[0] * scale - box[0]) < 1 and abs(img[1] + crop[1] * scale - box[1]) < 1
    assert abs(img[2] - 1200 * scale) < 1 and abs(img[3] - 800 * scale) < 1


def test_a_letterboxed_result_shows_the_whole_live_picture_inside_the_box():
    lay = run_js("(() => { const r = previewRule('video', '9:16', null, 0, {video: {layout: 'blur'}}, 1200, 800);"
                 " return [r.fit, r.bars, previewLayout(r, 1200, 800, 900, 900)]; })()")
    fit, bars, lay = lay
    assert fit == "contain" and bars == "blur"
    box, img = lay["box"], lay["img"]
    assert abs(img[2] - box[2]) < 1 and img[1] > box[1]             # full width, bars above and below
    assert abs(img[2] / img[3] - 1.5) < 0.01


def _function(name: str) -> str:
    m = re.search(rf"\n  (?:async )?function {name}\(.*?\n  \}}\n", JS, re.S)
    assert m, name
    return m.group(0)


def test_set_up_and_countdown_place_the_live_picture_with_the_same_function():
    apply = _function("applyFrame")
    assert "aspectRatio" not in apply, "the box size comes from previewLayout, not from CSS aspect-ratio"
    place = _function("placeLive")
    assert "previewRule(" in place and "previewLayout(" in place
    assert "#setupFrame" in apply or "setupFrame" in apply
    assert apply.count("placeLive(") >= 2                           # set-up and countdown
    # every shot of a collage gets the countdown preview of its own slot
    assert "flow.shot" in _function("captureShot")


def test_no_css_aspect_ratio_or_object_fit_decides_the_live_crop_any_more():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    live_rule = re.search(r"\n\.live\{[^}]*\}", css).group(0)
    assert "object-fit:cover" not in live_rule.replace(" ", "")
