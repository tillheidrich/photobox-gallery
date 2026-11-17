"""A portrait boomerang fills its frame, from the live preview to the gallery.

Seen on 15.11.2026 at the real booth: "beim Boomerang in Story und
Hochformat ist das Bild nicht komplett gefüllt – ich hätte lieber ein
zugeschnittenes Bild, das füllt". ``boomerang.layout`` now defaults to
``crop`` (owner's decision 15.11.2026); a profile that says ``blur`` keeps
it. Every place that decides on its own when the key is missing follows the
same default, and the result screen gives the film a box of the film's own
shape, so a cropped 9:16 film is never shown inside wider bars.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

STATIC = Path(__file__).resolve().parents[1] / "boothcore" / "web" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")
BOOTH = Path(__file__).resolve().parents[1] / "boothcore"
NODE = shutil.which("node")


def _run_geometry(expr: str):
    if not NODE:
        pytest.skip("node is not installed")
    src = JS[JS.index("// <preview-geometry>"):JS.index("// </preview-geometry>")]
    out = subprocess.run([NODE, "-e", src + f"\nprocess.stdout.write(JSON.stringify({expr}));"],
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("fmt", ["9:16", "4:5", "2:3"])
def test_the_preview_of_a_boomerang_without_a_stored_layout_is_cropped(fmt):
    rule = _run_geometry(f"previewRule('boomerang', '{fmt}', null, 0, {{boomerang: {{}}}}, 1200, 800)")
    assert rule["fit"] == "cover" and rule["bars"] == ""
    # The film keeps its own default: the whole frame on a blurred backdrop.
    film = _run_geometry(f"previewRule('video', '{fmt}', null, 0, {{video: {{}}}}, 1200, 800)")
    assert film["fit"] == "contain" and film["bars"] == "blur"


def test_the_server_falls_back_to_crop_for_the_boomerang_only():
    app = (BOOTH / "app.py").read_text(encoding="utf-8")
    assert app.count('cfg.get("layout", "crop" if s.mode == "boomerang" else "blur")') == 2
    assert 'cfg.get("layout", "blur")' not in app
    layouts = (BOOTH / "layoutbilder.py").read_text(encoding="utf-8")
    assert '(p.get("boomerang") or {}).get("layout") or "crop"' in layouts


@pytest.fixture()
def booth(monkeypatch):
    import tempfile
    import time
    d = Path(tempfile.mkdtemp(prefix="booth-test-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()


def test_a_default_boomerang_is_cropped_to_the_format(booth):
    assert booth.store.profile["boomerang"]["layout"] == "crop"
    booth.store.profile["boomerang"].update({"seconds": 0.6, "fps": 10, "loops": 1, "format": "9:16"})
    s = booth.start_session("boomerang", "", None, None)
    booth.record_clip(s)
    booth.finalize(s, "original")
    from boothcore.media import boomerang
    from boothcore.camera.mock import _H, _W
    src = (_W, _H)                                  # the mock's live view
    assert src[0] / src[1] > 9 / 16 + 0.05          # a landscape live view: blur would add bars
    with Image.open(s.poster) as p:
        assert abs(p.width / p.height - 9 / 16) < 0.01
        # The crop's own width; "blur" would keep the whole frame's width.
        assert p.width == boomerang.native_width(src, "9:16", "crop")
        assert p.width != boomerang.native_width(src, "9:16", "blur")


def test_the_result_screen_gives_the_film_a_box_of_its_own_shape():
    rule = re.search(r"(?m)^\.result-media video\{([^}]*)\}", CSS).group(1)
    assert "aspect-ratio:var(--film-ar)" in rule
    assert "width:min(100%,calc(var(--film-h) * var(--film-ar)))" in rule and "height:auto" in rule
    assert "height:88vh" not in rule
    # The shape comes from the film itself once it is known.
    assert re.search(r'loadedmetadata[^\n]*\n?[^\n]*--film-ar', JS)
