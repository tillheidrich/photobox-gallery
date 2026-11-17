"""A boomerang in every format the profile offers: the file has that shape, unstretched.

For each format the guest can pick, the recorded boomerang (from a 3:2 live
view, like the M50's) must come out in exactly that aspect, cut from the
middle of the frames ("crop") or with the whole frame on the blurred
backdrop ("blur") — never squeezed. A square drawn in the middle of the
source must still be square in the film. The kiosk's preview rule for the
same mode, format and layout must keep the same part of the frame (the
rectangle the film keeps); test_setup_preview_matches_result.py holds the
rule against the server's crop for every source; here the whole run is.
"""
import io
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from boothcore.media import compose

STATIC = Path(__file__).resolve().parents[1] / "boothcore" / "web" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
SRC = (1200, 800)                      # the mock camera's and the M50's 3:2 live view
FORMATS = list(compose.ASPECTS)       # 4:5, 9:16, 3:2, 2:3, 1:1, 16:9


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-boom-fmt-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.profile["modes"] = ["photo", "boomerang"]
    b.store.profile["formats"] = list(FORMATS)
    b.store.profile["boomerang"].update({"seconds": 1.0, "fps": 8, "loops": 1})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


def _frames(n=6):
    """3:2 frames with a black square of 200 px in the middle on white."""
    out = []
    for _ in range(n):
        im = Image.new("RGB", SRC, "white")
        d = ImageDraw.Draw(im)
        cx, cy = SRC[0] // 2, SRC[1] // 2
        d.rectangle([cx - 100, cy - 100, cx + 99, cy + 99], fill="black")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=95)
        out.append(buf.getvalue())
    return out


def _square_extent(img: Image.Image):
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    xs = [x for x in range(w) if px[x, h // 2] < 60]
    ys = [y for y in range(h) if px[w // 2, y] < 60]
    return (max(xs) - min(xs) + 1) if xs else 0, (max(ys) - min(ys) + 1) if ys else 0


def _mp4_size(path: Path):
    from boothcore.media.boomerang import ffmpeg_exe
    r = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    import re
    m = re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})", r.stderr)
    assert m, r.stderr[-400:]
    return int(m.group(1)), int(m.group(2))


@pytest.mark.parametrize("layout", ["crop", "blur"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_the_boomerang_file_has_the_chosen_format_and_is_not_stretched(booth, monkeypatch, fmt, layout):
    booth.store.profile["boomerang"]["layout"] = layout
    monkeypatch.setattr(booth.camera, "record_timed", lambda seconds, fps: (_frames(), 1.0))
    s = booth.start_session("boomerang", fmt, None, None)
    assert s.fmt == fmt                                   # the guest's choice is taken
    booth.record_clip(s)
    booth.finalize(s, "original")
    ratio = compose.ASPECTS[fmt]
    w, h = _mp4_size(s.result)
    assert abs(w / h - ratio) < 0.01, (fmt, layout, w, h)
    with Image.open(s.poster) as poster:
        assert poster.size == (w, h)
        sw, sh = _square_extent(poster)
    assert sw and sh and abs(sw / sh - 1) < 0.04, (fmt, layout, sw, sh)   # square stays square


def _js(expr: str):
    if not NODE:
        pytest.skip("node is not installed")
    start = JS.index("// <preview-geometry>")
    end = JS.index("// </preview-geometry>")
    script = JS[start:end] + f"\nprocess.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("layout", ["crop", "blur"])
def test_the_preview_keeps_what_the_film_keeps_for_every_format(layout):
    from boothcore.media import boomerang
    cases = [{"fmt": f, "profile": {"boomerang": {"layout": layout}}} for f in FORMATS]
    js = _js("(" + json.dumps(cases) + ").map((c) => { const r = previewRule('boomerang', c.fmt, null, 0, c.profile, "
             f"{SRC[0]}, {SRC[1]}); return [r.aspect, r.fit, r.fit === 'cover' ? cropRect(r, {SRC[0]}, {SRC[1]}) : null]; }})")
    for fmt, (aspect, fit, rect) in zip(FORMATS, js):
        ratio = compose.ASPECTS[fmt]
        assert abs(aspect - ratio) < 1e-6
        width = boomerang.resolve_width("auto", SRC, fmt, layout, 1080, "native")
        whole_frame = layout == "blur" and abs(SRC[0] / SRC[1] - ratio) >= 0.05
        if whole_frame:
            assert fit == "contain", fmt
        else:
            assert fit == "cover", fmt
            kept = compose.crop_to_aspect(Image.new("RGB", SRC), fmt).size
            assert (rect[2], rect[3]) == kept, (fmt, rect, kept)
            assert rect[0] == (SRC[0] - kept[0]) // 2 or abs(rect[0] - (SRC[0] - kept[0]) / 2) <= 1
        assert boomerang.output_size(fmt, width)[0] > 0
