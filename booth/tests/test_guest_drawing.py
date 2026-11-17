"""Guests draw or sign on their picture before it is printed and shared.

Off by default, and with it off nothing changes: no offer on the result
screen, the route refuses. Switched on, the kiosk sends the strokes as
vectors (colour, width relative to the picture's width, points 0..1) and the
booth paints them onto the finished picture at full resolution — the same way
a look is applied: a fresh render from the untouched shots, so the original
stays as it came from the camera, a second drawing replaces the first instead
of landing on top of it, and print, screen and upload all take the drawn
version.

The stroke helpers of the kiosk are pure functions between the
``<draw-helpers>`` markers in app.js; the tests below run that very code with
Node (skipped where Node is missing).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"
JS = (WEB / "static" / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
I18N = (WEB / "static" / "i18n.js").read_text(encoding="utf-8")
SETTINGS_JS = (WEB / "static" / "settings.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-draw-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    # A gallery to upload to, so the outbox has a target.
    b.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8}})
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def _on(booth, **more):
    booth.store.profile["draw"] = {"enabled": True, "modes": ["photo", "collage"],
                                   "colors": ["#ffffff", "#111111", "#e5484d"], **more}


def _photo(booth):
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _collage(booth):
    s = booth.start_session("collage", "4:5", "grid4", None)
    for _ in range(s.shots_needed):
        booth.take_shot(s)
    booth.finalize(s, "original")
    return s


def _line(y=0.5, color="#ffffff", width=0.03, x0=0.2, x1=0.8, n=7):
    return {"color": color, "width": width,
            "points": [[x0 + (x1 - x0) * i / (n - 1), y] for i in range(n)]}


def _px(path, x, y):
    with Image.open(path) as im:
        im = im.convert("RGB")
        return im.getpixel((int(x * (im.width - 1)), int(y * (im.height - 1))))


def _diff(a, b):
    return max(abs(p - q) for p, q in zip(a, b))


def _contrast(path, x, y):
    """White where the picture is dark, black where it is light: the stroke must show."""
    return "#111111" if sum(_px(path, x, y)) > 384 else "#ffffff"


# ── Off by default ────────────────────────────────────────────

def test_it_is_off_until_someone_wants_it():
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["draw"]["enabled"] is False
    assert DEFAULT_PROFILE["draw"]["modes"] == ["photo", "collage"], "No still to draw on in GIF and video"


def test_gallery_and_booth_ship_the_same_block():
    sys.path.insert(0, str(ROOT))
    from app import ecosystem
    from boothcore.config import DEFAULT_PROFILE
    assert DEFAULT_PROFILE["draw"] == ecosystem.DEFAULT_PROFILE["draw"]


def test_switched_off_the_result_offers_no_drawing(booth, client):
    s = _photo(booth)
    item = client.post(f"/api/session/{s.uid}/finish", json={"look": "original"}).json()
    assert "draw" not in item, "Off means the kiosk has nothing to show a button for"


def test_switched_off_the_route_refuses(booth, client):
    s = _photo(booth)
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line()]})
    assert r.status_code == 403 and r.json()["detail"] == "draw.disabled"
    assert s.drawing == []


def test_switched_on_the_result_offers_it_with_the_colours(booth, client):
    _on(booth)
    s = _photo(booth)
    item = client.post(f"/api/session/{s.uid}/finish", json={"look": "original"}).json()
    assert item["draw"]["colors"] == ["#ffffff", "#111111", "#e5484d"]


def test_not_offered_for_a_gif(booth, client):
    _on(booth)
    booth.store.profile["modes"] = ["photo", "collage", "gif"]
    s = booth.start_session("gif", "4:5", None, None)
    for _ in range(s.shots_needed):
        booth.take_shot(s)
    item = client.post(f"/api/session/{s.uid}/finish", json={"look": "original"}).json()
    assert "draw" not in item
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line()]})
    assert r.status_code == 409 and r.json()["detail"] == "draw.mode"


def test_a_mode_the_operator_left_out_is_refused(booth, client):
    _on(booth, modes=["photo"])
    s = _collage(booth)
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line()]})
    assert r.status_code == 409 and r.json()["detail"] == "draw.mode"


# ── Which run may be drawn on ─────────────────────────────────

def test_an_unknown_run_is_refused(booth, client):
    _on(booth)
    r = client.post("/api/session/gibtesnicht/draw", json={"strokes": [_line()]})
    assert r.status_code == 404


def test_a_run_without_a_picture_yet_is_refused(booth, client):
    _on(booth)
    s = booth.start_session("photo", "4:5", None, None)
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line()]})
    assert r.status_code == 409 and r.json()["detail"] == "draw.not_ready"


def test_the_run_of_the_guest_before_is_refused(booth, client):
    """Once the next guest has started, nobody draws on the previous picture."""
    _on(booth)
    first = _photo(booth)
    booth.start_session("photo", "4:5", None, None)
    r = client.post(f"/api/session/{first.uid}/draw", json={"strokes": [_line()]})
    assert r.status_code == 409 and r.json()["detail"] == "draw.not_current"


def test_a_deleted_run_is_refused(booth, client):
    _on(booth)
    s = _photo(booth)
    booth.delete(s.uid)
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line()]})
    assert r.status_code == 404


# ── What the route takes ──────────────────────────────────────

def test_too_many_strokes_are_refused(booth, client):
    from boothcore import drawing
    _on(booth)
    s = _photo(booth)
    strokes = [_line(n=2)] * (drawing.MAX_STROKES + 1)
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": strokes})
    assert r.status_code == 413 and r.json()["detail"] == "draw.strokes"
    assert s.drawing == []


def test_too_many_points_in_one_stroke_are_refused(booth, client):
    from boothcore import drawing
    _on(booth)
    s = _photo(booth)
    r = client.post(f"/api/session/{s.uid}/draw",
                    json={"strokes": [_line(n=drawing.MAX_POINTS_PER_STROKE + 1)]})
    assert r.status_code == 413 and r.json()["detail"] == "draw.points"


def test_too_many_points_altogether_are_refused(booth, client):
    from boothcore import drawing
    _on(booth)
    s = _photo(booth)
    per = drawing.MAX_POINTS_PER_STROKE
    count = drawing.MAX_POINTS_TOTAL // per + 1
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line(n=per)] * count})
    assert r.status_code == 413 and r.json()["detail"] == "draw.points"


def test_a_body_that_is_too_large_is_refused_before_it_is_read(booth, client):
    from boothcore import drawing
    _on(booth)
    s = _photo(booth)
    raw = b'{"strokes": [], "pad": "' + b"x" * (drawing.MAX_BYTES + 10) + b'"}'
    r = client.post(f"/api/session/{s.uid}/draw", content=raw,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"] == "draw.too_large"


@pytest.mark.parametrize("color", ["red", "#fff", "#12345g", "#1234567", "", None, 12, "#00ff00"])
def test_a_colour_that_is_not_offered_is_refused(booth, client, color):
    _on(booth)
    s = _photo(booth)
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [{**_line(), "color": color}]})
    assert r.status_code == 400 and r.json()["detail"] == "draw.color"


def test_colour_case_does_not_matter(booth, client):
    _on(booth)
    s = _photo(booth)
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [{**_line(), "color": "#E5484D"}]})
    assert r.status_code == 200
    assert s.drawing[0]["color"] == "#e5484d"


@pytest.mark.parametrize("raw", [
    b'{"strokes": [{"color": "#ffffff", "width": 0.01, "points": [[NaN, 0.5]]}]}',
    b'{"strokes": [{"color": "#ffffff", "width": 0.01, "points": [[0.5, Infinity]]}]}',
    b'{"strokes": [{"color": "#ffffff", "width": -Infinity, "points": [[0.5, 0.5]]}]}',
    b'{"strokes": [{"color": "#ffffff", "width": 0.01, "points": [["0.5", 0.5]]}]}',
    b'{"strokes": [{"color": "#ffffff", "width": 0.01, "points": [[0.5]]}]}',
    b'{"strokes": [{"color": "#ffffff", "width": 0.01, "points": [[true, 0.5]]}]}',
    b'{"strokes": [{"color": "#ffffff", "width": 0.01, "points": [[1e400, 0.5]]}]}',
])
def test_numbers_that_are_no_numbers_are_refused(booth, client, raw):
    _on(booth)
    s = _photo(booth)
    r = client.post(f"/api/session/{s.uid}/draw", content=raw,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] in ("draw.number", "draw.invalid")
    assert s.drawing == []


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"strokes": {}}', b'{"strokes": ["x"]}'])
def test_a_body_of_the_wrong_shape_is_refused(booth, client, raw):
    _on(booth)
    s = _photo(booth)
    r = client.post(f"/api/session/{s.uid}/draw", content=raw,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["detail"] == "draw.invalid"


def test_points_off_the_edge_and_widths_are_clamped(booth, client):
    """A finger that slides off the picture is ordinary: kept at the edge, not refused."""
    from boothcore import drawing
    _on(booth)
    s = _photo(booth)
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [
        {"color": "#ffffff", "width": 5, "points": [[-0.4, 0.5], [1.7, -2]]},
        {"color": "#ffffff", "width": 0, "points": [[0.5, 0.5]]},
    ]})
    assert r.status_code == 200
    a, b = s.drawing
    assert a["points"] == [(0.0, 0.5), (1.0, 0.0)]
    assert a["width"] == drawing.WIDTH_MAX and b["width"] == drawing.WIDTH_MIN


# ── What ends up on the picture ───────────────────────────────

def test_the_stroke_is_on_the_picture_and_nowhere_far_from_it(booth, client):
    _on(booth)
    s = _photo(booth)
    before = Image.open(s.result).convert("RGB")
    color = _contrast(s.result, 0.5, 0.5)
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line(0.5, color)]})
    assert r.status_code == 200
    after = Image.open(s.result).convert("RGB")
    assert after.size == before.size, "Drawn at full resolution, not on a copy from the screen"
    w, h = after.size
    want = (255, 255, 255) if color == "#ffffff" else (17, 17, 17)
    assert _diff(after.getpixel((w // 2, h // 2)), want) < 40, "The stroke is where it was drawn"
    for x, y in [(0.5, 0.1), (0.5, 0.9), (0.05, 0.5), (0.95, 0.5), (0.1, 0.1)]:
        p = (int(x * (w - 1)), int(y * (h - 1)))
        assert _diff(after.getpixel(p), before.getpixel(p)) <= 6, f"changed far from the stroke at {x},{y}"


def test_the_edge_of_a_stroke_is_smooth(booth):
    """Anti-aliased: between stroke and picture there are in-between values."""
    from boothcore import drawing
    img = Image.new("RGB", (800, 600), (0, 0, 0))
    out = drawing.render(img, [{"color": "#ffffff", "width": 0.05,
                                "points": [(0.1, 0.13), (0.9, 0.47)]}])
    values = {out.getpixel((x, y))[0] for x in range(800) for y in range(0, 600, 3)}
    assert any(20 < v < 235 for v in values)
    assert img.getpixel((400, 180)) == (0, 0, 0), "The picture handed in stays untouched"


def test_a_single_tap_leaves_a_dot(booth):
    from boothcore import drawing
    img = Image.new("RGB", (400, 400), (0, 0, 0))
    out = drawing.render(img, [{"color": "#ffffff", "width": 0.05, "points": [(0.5, 0.5)]}])
    assert out.getpixel((200, 200))[0] > 200
    assert out.getpixel((200, 240))[0] < 20


def test_drawing_again_replaces_the_first_drawing(booth, client):
    _on(booth)
    s = _photo(booth)
    before = Image.open(s.result).convert("RGB")
    w, h = before.size
    top = (w // 2, int(0.3 * (h - 1)))
    client.post(f"/api/session/{s.uid}/draw",
                json={"strokes": [_line(0.3, _contrast(s.result, 0.5, 0.3))]})
    assert _diff(Image.open(s.result).convert("RGB").getpixel(top), before.getpixel(top)) > 60
    r = client.post(f"/api/session/{s.uid}/draw",
                    json={"strokes": [_line(0.7, _contrast(s.result, 0.5, 0.7))]})
    assert r.status_code == 200
    again = Image.open(s.result).convert("RGB")
    assert _diff(again.getpixel(top), before.getpixel(top)) <= 6, "The first drawing is gone, not underneath"
    bottom = (w // 2, int(0.7 * (h - 1)))
    assert _diff(again.getpixel(bottom), before.getpixel(bottom)) > 60


def test_an_empty_drawing_takes_the_drawing_away(booth, client):
    _on(booth)
    s = _photo(booth)
    before = Image.open(s.result).convert("RGB")
    client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line(0.5, _contrast(s.result, 0.5, 0.5))]})
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": []})
    assert r.status_code == 200 and s.drawing == []
    w, h = before.size
    assert _diff(Image.open(s.result).convert("RGB").getpixel((w // 2, h // 2)),
                 before.getpixel((w // 2, h // 2))) <= 6


def test_the_original_shot_stays_byte_for_byte(booth, client):
    _on(booth)
    s = _photo(booth)
    digest = hashlib.sha256(s.shots[0].read_bytes()).hexdigest()
    client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line()]})
    client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line(0.2)]})
    assert hashlib.sha256(s.shots[0].read_bytes()).hexdigest() == digest


def test_the_drawing_survives_a_change_of_look(booth, client):
    """The guest draws, then taps another look: the drawing stays on the new render."""
    _on(booth)
    s = _photo(booth)
    color = _contrast(s.result, 0.5, 0.5)
    client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line(0.5, color)]})
    other = next(n for n in booth.look_names() if n != "original")
    client.post(f"/api/session/{s.uid}/finish", json={"look": other})
    w, h = Image.open(s.result).size
    want = (255, 255, 255) if color == "#ffffff" else (17, 17, 17)
    assert _diff(Image.open(s.result).convert("RGB").getpixel((w // 2, h // 2)), want) < 40


def test_a_retake_starts_without_the_drawing(booth, client):
    _on(booth)
    booth.store.profile["retake"] = True
    s = _photo(booth)
    client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line()]})
    booth.take_shot(s, retake=True)
    assert s.drawing == [], "The drawing belonged to the picture that was thrown away"


def test_the_screen_preview_shows_the_drawing_and_can_leave_it_out(booth, client):
    """The drawing screen needs the picture without the drawing underneath the canvas."""
    _on(booth)
    s = _photo(booth)
    color = _contrast(s.result, 0.5, 0.5)
    client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line(0.5, color)]})
    import io
    want = (255, 255, 255) if color == "#ffffff" else (17, 17, 17)
    drawn = Image.open(io.BytesIO(client.get(f"/api/session/{s.uid}/preview.jpg?size=800").content))
    clean = Image.open(io.BytesIO(client.get(f"/api/session/{s.uid}/preview.jpg?size=800&draw=0").content))
    c = (drawn.width // 2, drawn.height // 2)
    assert _diff(drawn.convert("RGB").getpixel(c), want) < 40
    assert _diff(clean.convert("RGB").getpixel(c), want) > 40


# ── Print and upload take the drawn version ──────────────────

def test_the_print_is_the_drawn_version(booth, client, monkeypatch):
    _on(booth)
    booth.store.profile["print"].update({"enabled": True, "auto": False})
    s = _collage(booth)                       # on a collage the sheet is the print
    color = _contrast(s.print_file, 0.5, 0.5)
    client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line(0.5, color)]})
    sent = []
    monkeypatch.setattr(booth, "sheet_for", lambda src, uid="": Path(src))
    monkeypatch.setattr(booth.printer, "submit", lambda job: sent.append(job))
    booth.print_uid(s.uid, 1)
    assert sent and Path(sent[0].path) == Path(booth.library.get(s.uid)["print_file"])
    want = (255, 255, 255) if color == "#ffffff" else (17, 17, 17)
    assert _diff(_px(sent[0].path, 0.5, 0.5), want) < 40


def test_the_print_card_of_a_photo_changes_too(booth, client):
    _on(booth)
    s = _photo(booth)
    card = s.print_file.read_bytes()
    client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line()]})
    assert s.print_file.read_bytes() != card


def test_the_upload_is_the_drawn_version_and_goes_again(booth, client):
    """Already uploaded? The drawn version goes up again under the same id, as after a change of look."""
    _on(booth)
    s = _photo(booth)
    for job in booth.outbox.due(200):
        booth.outbox.mark_done(job["id"])
    assert not booth.outbox.due(200)
    color = _contrast(s.result_web, 0.5, 0.5)
    client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line(0.5, color)]})
    jobs = {(j["uid"], j["variant"]): j for j in booth.outbox.due(200)}
    web = jobs[(s.uid, "web")]
    want = (255, 255, 255) if color == "#ffffff" else (17, 17, 17)
    assert _diff(_px(web["path"], 0.5, 0.5), want) < 40
    assert (s.uid, "original") in jobs


def test_a_print_only_run_stays_off_the_outbox(booth, client):
    _on(booth)
    booth.store.profile["print"].update({"enabled": True, "auto": False})
    booth.store.profile["consent"] = {**(booth.store.profile.get("consent") or {}), "enabled": True,
                                      "print_only": True}
    options = booth.consent_options()
    if "print" not in options:
        pytest.skip("this booth offers no 'Nur drucken' (no printer)")
    s = booth.start_session("photo", "4:5", None, None, consent={"option": "print", "lang": "de"})
    booth.take_shot(s)
    booth.finalize(s, "original")
    r = client.post(f"/api/session/{s.uid}/draw", json={"strokes": [_line()]})
    assert r.status_code == 200
    assert not [j for j in booth.outbox.due(200) if j["session_uid"] == s.uid]


# ── Texts, settings, kiosk wiring ─────────────────────────────

DRAW_KEYS = ["draw.open", "draw.title", "draw.color", "draw.width", "draw.undo",
             "draw.clear", "draw.done", "draw.failed"]


def test_every_text_exists_in_both_languages_and_both_files():
    sys.path.insert(0, str(ROOT))
    from app import booth_texts
    de, en = I18N[I18N.index("de: {"):I18N.index("en: {")], I18N[I18N.index("en: {"):]
    for key in DRAW_KEYS:
        assert f'"{key}":' in de and f'"{key}":' in en, key
        assert key in booth_texts.DEFAULTS and key in booth_texts.SHIPPED_EN, key


def test_the_booth_editor_names_the_settings_in_german():
    for path in ("draw.enabled", "draw.modes", "draw.colors"):
        assert f'"{path}":' in SETTINGS_JS, path
    assert "draw: [" in SETTINGS_JS


def test_the_kiosk_has_a_button_and_a_canvas():
    assert 'id="drawBtn"' in HTML and 'data-action="draw"' in HTML
    assert 'id="drawOverlay"' in HTML and 'id="drawCanvas"' in HTML
    for action in ("draw-undo", "draw-clear", "draw-cancel", "draw-done"):
        assert f'data-action="{action}"' in HTML, action


def test_the_overlay_counts_as_open_for_buzzer_and_keys():
    """Otherwise a mouse buzzer press while drawing would end the run."""
    start = JS.index("function overlayOpen()")
    assert "#drawOverlay" in JS[start:start + 600]


def test_going_home_closes_the_drawing_and_forgets_the_strokes():
    """The next guest must not find the previous guest's signature on the canvas."""
    start = JS.index("function home()")
    body = JS[start:JS.index("\n  }\n", start)]
    assert "closeDraw(" in body


def test_a_finger_on_the_canvas_holds_the_result_timer():
    assert "drawPointer === null" in JS, "the global tap handler must not restart the timer mid-stroke"


def test_the_look_thumbnails_are_reloaded_after_a_drawing():
    """Structural check only (no browser): drawDone refreshes #looks images with a new stamp."""
    assert "function refreshLookThumbs()" in JS
    helper = JS[JS.index("function refreshLookThumbs()"):]
    helper = helper[:helper.index("\n  }\n")]
    assert "#looks" in helper and "&t=" in helper and "Date.now()" in helper
    start = JS.index("async function drawDone()")
    assert "refreshLookThumbs()" in JS[start:JS.index("async function sendMail()", start)]


def test_the_canvas_takes_no_scroll_or_zoom():
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    start = css.index("#drawCanvas")
    assert "touch-action:none" in css[start:start + 200].replace(" ", "")


# ── The kiosk's pure stroke helpers (run with Node) ───────────

def _helpers() -> str:
    return JS[JS.index("// <draw-helpers>"):JS.index("// </draw-helpers>")]


def _node(expr: str):
    if not NODE:
        pytest.skip("node is not installed")
    script = _helpers() + f"\nprocess.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


def test_js_normalises_into_the_picture_and_clamps():
    rect = {"left": 100, "top": 50, "width": 400, "height": 200}
    assert _node(f"drawNorm(300, 150, {json.dumps(rect)})") == [0.5, 0.5]
    assert _node(f"drawNorm(0, 400, {json.dumps(rect)})") == [0, 1]
    assert _node(f"drawNorm(NaN, 100, {json.dumps(rect)})") is None


def test_js_skips_points_that_add_nothing():
    pts = _node("(() => { const p = [[0.5, 0.5]]; drawAddPoint(p, [0.5001, 0.5], 0.002);"
                " drawAddPoint(p, [0.6, 0.5], 0.002); return p; })()")
    assert pts == [[0.5, 0.5], [0.6, 0.5]]


def test_js_smoothing_uses_quadratic_midpoints_like_the_booth():
    from boothcore import drawing
    points = [[0.1, 0.1], [0.3, 0.2], [0.5, 0.1], [0.7, 0.3]]
    js = _node(f"drawSmooth({json.dumps(points)})")
    assert js[0] == ["M", 0.1, 0.1]
    assert js[1][0] == "Q" and js[1][1:3] == [0.3, 0.2]
    assert js[1][3:] == pytest.approx([0.4, 0.15])
    assert js[-1] == ["L", 0.7, 0.3]
    py = [list(seg) for seg in drawing.segments(points)]
    assert len(py) == len(js)
    for a, b in zip(js, py):
        assert a[0] == b[0] and a[1:] == pytest.approx(b[1:])


def test_js_payload_keeps_inside_the_limits_of_the_booth():
    from boothcore import drawing
    limits = {"strokes": drawing.MAX_STROKES, "points": drawing.MAX_POINTS_PER_STROKE,
              "total": drawing.MAX_POINTS_TOTAL}
    many = _node(f"drawPayload(Array.from({{length: {drawing.MAX_STROKES + 5}}}, () => "
                 f"({{color: '#ffffff', width: 0.01, points: [[0.1, 0.1], [0.2, 0.2]]}})), {json.dumps(limits)})")
    assert len(many) == drawing.MAX_STROKES
    long = _node(f"drawPayload([{{color: '#ffffff', width: 0.01, points: Array.from({{length: "
                 f"{drawing.MAX_POINTS_PER_STROKE + 50}}}, (_, i) => [i / 5000, 0.5])}}], {json.dumps(limits)})")
    assert len(long[0]["points"]) == drawing.MAX_POINTS_PER_STROKE
    empty = _node(f"drawPayload([{{color: '#ffffff', width: 0.01, points: []}}], {json.dumps(limits)})")
    assert empty == []
