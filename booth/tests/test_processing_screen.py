"""The processing screen shows the run's own picture in its shape, and something that moves.

Seen on 15.11.2026 at the real booth: "wird entwickelt – da braucht es ein
Icon oder eine Animation, weil trotz Querformat dann ein hochkant Bild in
dunkel kommt und darunter ‚wird entwickelt'". The screen held an empty 4:5
skeleton box for every mode and format. Now it shows the last shot (photo,
collage, GIF) or the last live picture (boomerang, film) in the shape the
result will have — the format, or the collage slot, cropped or whole like
the result — softly dimmed, with a ring turning around the mode's icon on
top. Without a picture, only the ring. The words name the mode.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "booth" / "boothcore" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
I18N = (WEB / "static" / "i18n.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
MODES = ("photo", "collage", "gif", "boomerang", "video")


def _section():
    return re.search(r'<section class="screen" id="s-processing">(.*?)</section>', HTML, re.S).group(1)


def _run(expr: str):
    if not NODE:
        pytest.skip("node is not installed")
    geo = JS[JS.index("// <preview-geometry>"):JS.index("// </preview-geometry>")]
    view = JS[JS.index("// <processing-view>"):JS.index("// </processing-view>")]
    out = subprocess.run([NODE, "-e", geo + view + f"\nprocess.stdout.write(JSON.stringify({expr}));"],
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def test_no_empty_placeholder_box_any_more():
    sec = _section()
    assert "skeleton" not in sec
    assert 'id="procStill" hidden' in sec           # the picture's box starts hidden
    assert 'class="proc-ring"' in sec and 'id="procIcon"' in sec


@pytest.mark.parametrize("mode,fmt,src,aspect,fit", [
    ("photo", "3:2", (1200, 800), 1.5, "cover"),
    ("photo", "16:9", (1200, 800), 16 / 9, "cover"),
    ("photo", "9:16", (1200, 800), 9 / 16, "cover"),
    ("photo", "2:3", (1200, 800), 2 / 3, "cover"),
    ("photo", "1:1", (1200, 800), 1.0, "cover"),
    ("gif", "4:5", (1200, 800), 0.8, "cover"),
    ("boomerang", "9:16", (1200, 800), 9 / 16, "cover"),       # crop, the default
    ("video", "9:16", (1200, 800), 9 / 16, "contain"),         # blur: the whole frame
    ("video", "16:9", (1280, 720), 16 / 9, "cover"),           # same aspect
])
def test_the_picture_has_the_shape_of_the_result(mode, fmt, src, aspect, fit):
    v = _run(f"processingView('{mode}', '{fmt}', null, 0, {{boomerang: {{}}, video: {{}}}}, {src[0]}, {src[1]})")
    assert abs(v["aspect"] - aspect) < 0.01 and v["fit"] == fit


def test_a_collage_shows_the_slot_the_last_shot_went_into():
    tpl = {"shots": 2, "slots": [{"shot": 0, "w": 600, "h": 400}, {"shot": 1, "w": 300, "h": 450}]}
    v = _run(f"processingView('collage', '', {json.dumps(tpl)}, 1, {{}}, 1200, 800)")
    assert abs(v["aspect"] - 300 / 450) < 0.01


def test_without_a_picture_there_is_no_box():
    assert _run("processingView('photo', '3:2', null, 0, {}, 0, 0)") is None


def test_the_box_takes_the_shape_and_stays_invisible_until_the_picture_is_there():
    rule = re.search(r"(?m)^\.proc-still\{([^}]*)\}", CSS).group(1)
    assert "aspect-ratio:var(--proc-ar)" in rule and "opacity:0" in rule
    assert ".proc-still.is-ready{opacity:1" in CSS
    assert ".proc-still[hidden]{display:none}" in CSS
    assert "aspect-ratio:4/5" not in re.search(r"/\* PROCESSING \*/(.*?)/\* RESULT \*/", CSS, re.S).group(1)


def test_the_animation_moves_only_transform_and_opacity_and_is_large_enough():
    for name in ("proc-spin", "proc-breathe"):
        body = re.search(r"@keyframes " + name + r"\{(.*?)\}\}", CSS).group(1)
        props = set(re.findall(r"([a-z-]+):", body))
        assert props <= {"transform", "opacity"}, (name, props)
    icon = re.search(r"(?m)^\.proc-icon svg\{([^}]*)\}", CSS).group(1)
    # 8.5vh is 102 px at 1920x1200; the floor keeps it readable on 1280x800.
    assert "width:clamp(80px,8.5vh,116px)" in icon
    text = re.search(r"(?m)^\.processing p\{([^}]*)\}", CSS).group(1)
    assert "font-size:clamp(28px,3vh,40px)" in text
    # Reduced motion: the ring stands still as a whole circle.
    assert re.search(r"prefers-reduced-motion:reduce\)\{[^@]*\.proc-arc\{stroke-dasharray:none", CSS)


def test_the_words_name_the_mode_in_both_languages_on_both_sides():
    sys_path = str(ROOT)
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from app import booth_texts
    for mode in MODES:
        key = f"processing.{mode}"
        assert I18N.count(f'"{key}":') == 2, key       # de and en
        assert key in booth_texts.KEYS, key
        assert booth_texts.shipped("de", key) and booth_texts.shipped("en", key), key
    assert '"processing.photo": "Euer Foto wird entwickelt …"' in I18N
    assert '"processing.boomerang": "Euer Boomerang wird fertig gemacht …"' in I18N
    assert 't("processing." + mode)' in JS
