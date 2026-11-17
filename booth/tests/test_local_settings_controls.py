"""The settings that were added to the gallery form can be set at the booth too.

The owner's rule: every feature is an optional switch, settable in the gallery
profile form AND at the booth's own admin page, because a booth has to be
configurable without internet. The booth's editor (static/settings.js) is built
from the profile, so the fields exist by construction; what it lacked were
readable names, the limits the gallery form applies, fixed choices for settings
with a few valid values, and tick boxes for the modes.

The limits and choices below are copied from the gallery form's parser
(app/blueprints/admin_booth.py, ``admin_booth_profile``): the booth does not
import the gallery, so the two lists are kept in step by this file.
"""
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _pages import page  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "boothcore" / "web"
JS = WEB / "static" / "settings.js"
NODE = shutil.which("node")

# path -> (min, max) as the gallery form's _num(...) calls have them.
GALLERY_LIMITS = {
    # 1–20 on both sides since 15.11.2026: the first shot always gets a countdown
    "countdown.first": (1, 20), "countdown.next": (1, 20), "countdown.review_seconds": (0, 10),
    "countdown.smile_ms": (200, 3000), "review_seconds": (3, 60),
    "gif.shots": (2, 10), "gif.interval": (0.5, 10), "gif.frame_ms": (80, 3000),
    "gif.video_seconds": (0, 60), "gif.from_collage_min": (2, 8),
    "gif.boomerang.fps": (5, 60), "gif.boomerang.loops": (1, 10),
    # 1-10 since 15.11.2026: a loop, not a film (the film mode keeps 1-60)
    "boomerang.seconds": (1, 10), "boomerang.fps": (5, 60), "boomerang.loops": (1, 10),
    "video.seconds": (1, 60), "video.fps": (5, 60),
    "attract.after_seconds": (10, 3600), "attract.seconds_per_item": (2, 120), "attract.dim": (0, 80),
    "sounds.volume": (0, 1), "looks_max": (0, 24), "flash.preview_gain": (0, 100),
}
_FORMATS = ["4:5", "9:16", "3:2", "1:1", "2:3"]
GALLERY_CHOICES = {
    "attract.source": ["recent", "media", "own", "text", "blank", "off"],
    "attract.hint_style": ["bar", "center", "none"],
    "attract.fit": ["cover", "contain"],
    "gif.format": _FORMATS,
    "gif.boomerang.format": _FORMATS + ["16:9"],
    "gif.boomerang.layout": ["blur", "crop"],
}
GALLERY_MODES = ["photo", "collage", "gif", "boomerang", "video"]

# One German name for every setting the task is about.
NEEDS_A_NAME = [
    "countdown.poses", "countdown.sound",
    "gif.shots", "gif.frame_ms", "gif.video_seconds", "gif.interval", "gif.format", "gif.boomerang",
    "gif.boomerang.enabled", "gif.boomerang.format", "gif.boomerang.layout", "gif.boomerang.loops",
    "gif.boomerang.guest_choice",
    "attract.enabled", "attract.source", "attract.after_seconds", "attract.show_hint",
    "sounds.enabled", "modes",
]


def _block(name):
    import re
    text = JS.read_text(encoding="utf-8")
    m = re.search(rf"const {name} = \{{(.*?)\n  \}};", text, re.S)
    assert m, f"no {name} block in settings.js"
    return m.group(1)


def _run_node(script, *args):
    if not NODE:
        pytest.skip("node is not installed")
    r = subprocess.run([NODE, "-e", script, str(JS), *args], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


_HEAD = "global.window = {}; require(process.argv[1]); const S = window.PhotoboxSettings;"


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-local-controls-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    monkeypatch.setattr(app_mod, "_WRONG_PIN_PAUSE", 0.0)
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})
    yield b
    b.mail.stop()


@pytest.fixture()
def admin(booth):
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    token = client.post("/api/admin/login", json={"pin": "468213"}).json()["token"]
    client.headers.update({"X-Admin-Token": token})
    return client


def test_every_setting_is_in_the_editor_the_booth_hands_out(admin):
    """The editor is built from the profile: whatever the gallery form sets exists here."""
    data = admin.get("/api/admin/profile/full").json()["profile"]
    for path in NEEDS_A_NAME + list(GALLERY_LIMITS) + list(GALLERY_CHOICES):
        node = data
        for part in path.split("."):
            assert isinstance(node, dict) and part in node, f"{path} is missing from the booth profile"
            node = node[part]


def test_the_settings_of_this_work_package_have_a_german_name():
    labels = _block("LABELS")
    for path in NEEDS_A_NAME:
        assert f'"{path}":' in labels, f"{path} shows its raw key at the booth"


def test_numbers_carry_the_limits_of_the_gallery_form():
    limits = _run_node(_HEAD + "console.log(JSON.stringify(S.limits));")
    for path, (low, high) in GALLERY_LIMITS.items():
        assert path in limits, f"{path} has no limit at the booth"
        assert (limits[path][0], limits[path][1]) == (low, high), path


def test_a_number_outside_the_limits_is_pulled_back():
    out = _run_node(_HEAD + "console.log(JSON.stringify(["
                    "S.limit('gif.shots', 99), S.limit('gif.shots', 0), S.limit('gif.shots', 4),"
                    "S.limit('gif.frame_ms', 5), S.limit('sounds.volume', 7), S.limit('no.limit.here', 123)]));")
    assert out == [10, 2, 4, 80, 1, 123]


def test_settings_with_a_few_valid_values_are_a_choice_not_free_text():
    html = _run_node(_HEAD + "console.log(JSON.stringify({"
                     "src: S.field('attract.source', 'recent', 'recent'),"
                     "fmt: S.field('gif.boomerang.format', '9:16', '9:16'),"
                     "lay: S.field('gif.boomerang.layout', 'blur', 'blur')}));")
    for key, path in (("src", "attract.source"), ("fmt", "gif.boomerang.format"), ("lay", "gif.boomerang.layout")):
        assert "<select" in html[key], path
        for option in GALLERY_CHOICES[path]:
            assert f"<option" in html[key] and f">{option}<" in html[key], (path, option)


def test_the_modes_are_tick_boxes_and_never_empty():
    out = _run_node(_HEAD + "console.log(JSON.stringify({"
                    "html: S.field('modes', ['photo','gif'], ['photo','collage','gif','boomerang','video']),"
                    "none: S.readMulti([])}));")
    assert out["html"].count('type="checkbox"') == len(GALLERY_MODES)
    for m in GALLERY_MODES:
        assert f'value="{m}"' in out["html"]
    assert out["html"].count(" checked") == 2
    # The gallery form falls back to a photo when nothing is ticked; so does the booth.
    assert out["none"] == ["photo"]


def test_the_numbers_of_a_field_show_their_limits():
    html = _run_node(_HEAD + "console.log(JSON.stringify(S.field('gif.shots', 6, 6)));")
    assert 'min="2"' in html and 'max="10"' in html


def test_the_boomerang_of_the_gif_is_a_group_of_its_own():
    gif = {"shots": 6, "boomerang": {"enabled": True, "format": "9:16", "layout": "blur", "fps": 15,
                                     "loops": 3, "guest_choice": False}}
    html = _run_node(_HEAD + "const g = JSON.parse(process.argv[2]);"
                     "console.log(JSON.stringify(S.fields('gif', g, g)));", json.dumps(gif))
    assert 'data-path="gif.boomerang.enabled"' in html
    assert 'data-path="gif.boomerang.guest_choice"' in html
    assert "<legend>" in html


def test_a_round_trip_through_the_save_route_is_stored_and_used(admin, booth):
    p = booth.profile
    patch = {
        "countdown": {**p["countdown"], "poses": True, "sound": False},
        "gif": {**p["gif"], "shots": 4, "frame_ms": 300, "video_seconds": 8,
                "boomerang": {**p["gif"]["boomerang"], "enabled": False, "format": "4:5", "layout": "crop",
                              "loops": 2, "guest_choice": True}},
        "attract": {**p["attract"], "enabled": False, "source": "own"},
        "sounds": {**p["sounds"], "enabled": False},
        "modes": ["photo", "gif"],
    }
    assert admin.post("/api/admin/profile", json=patch).status_code == 200
    # What the kiosk page reads.
    seen = admin.get("/api/state").json()["profile"]
    assert seen["countdown"]["poses"] is True and seen["countdown"]["sound"] is False
    assert (seen["gif"]["shots"], seen["gif"]["frame_ms"], seen["gif"]["video_seconds"]) == (4, 300, 8)
    assert seen["gif"]["boomerang"] == {**p["gif"]["boomerang"], "enabled": False, "format": "4:5",
                                        "layout": "crop", "loops": 2, "guest_choice": True}
    assert seen["attract"]["enabled"] is False and seen["attract"]["source"] == "own"
    assert seen["sounds"]["enabled"] is False
    assert seen["modes"] == ["photo", "gif"]
    # What the run uses: a GIF now asks the guest for four pictures.
    booth.camera.start()
    try:
        for _ in range(50):
            if booth.camera.state == "ready":
                break
            time.sleep(0.1)
        r = admin.post("/api/session", json={"mode": "gif"})
        assert r.status_code == 200, r.text
        assert r.json()["shots_needed"] == 4, r.json()
    finally:
        booth.camera.stop()
    # And it is on disk, so a restart keeps it.
    on_disk = json.loads((booth.data / "profile.json").read_text(encoding="utf-8"))
    assert on_disk["gif"]["shots"] == 4 and on_disk["attract"]["source"] == "own"


def test_a_change_made_at_the_booth_survives_the_gallery_while_held(admin, booth):
    admin.post("/api/admin/profile", json={"gif": {**booth.profile["gif"], "shots": 3}})
    admin.post("/api/admin/profile/hold", json={"hold": True})
    gallery = {**booth.profile, "gif": {**booth.profile["gif"], "shots": 8}}
    assert booth._apply_server_profile(gallery) is False
    assert booth.profile["gif"]["shots"] == 3


def test_the_help_page_names_the_newer_admin_cards_in_both_languages():
    """Admin cards are quoted by their German names, as the English help does for the rest."""
    import re
    html = page(WEB / "hilfe.html")
    admin = page(WEB / "admin.html")
    for name in ("Zweites Ziel im Netz", "Noch einmal drucken", "Papier nachgelegt", "Farbband gewechselt"):
        assert name in admin, f"{name} is not an admin card or button any more"
    wanted = {
        "de": ["Zweites Ziel im Netz", "Noch einmal drucken", "Nachdruck", "Papier nachgelegt", "Farbband gewechselt",
               "Druckauftrag verfolgen", "Jetzt alle winken!", "Bildschirm berühren, um Fotos zu machen"],
        "en": ["Zweites Ziel im Netz", "Noch einmal drucken", "reprint", "Papier nachgelegt", "Farbband gewechselt",
               "Druckauftrag verfolgen", "Now everybody wave!", "Touch the screen to take photos"],
    }
    for lang, needles in wanted.items():
        block = re.search(rf'<main lang="{lang}"[^>]*>(.*?)</main>', html, re.S).group(1)
        for needle in needles:
            assert needle in block, f"{lang}: {needle}"
    assert html.count("<section") % 2 == 0


def test_the_server_limits_are_the_browser_limits():
    """app.PROFILE_LIMITS (server) and LIMITS in settings.js (browser) are one table."""
    import re
    from boothcore.app import PROFILE_LIMITS
    js = {}
    for m in re.finditer(r'"([a-z_.]+)":\s*\[\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\]', _block("LIMITS")):
        js[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    assert js, "could not parse LIMITS"
    assert set(PROFILE_LIMITS) == set(js)
    for path, (low, high) in PROFILE_LIMITS.items():
        assert (float(low), float(high)) == js[path], path


def _saved(admin, booth, **groups):
    patch = {}
    for key, val in groups.items():
        patch[key] = {**booth.profile[key], **val} if isinstance(val, dict) else val
    assert admin.post("/api/admin/profile", json=patch).status_code == 200
    return booth.profile


def test_the_save_route_clamps_numbers_to_the_limits(admin, booth):
    p = _saved(admin, booth, gif={"shots": 99, "frame_ms": 5}, review_seconds=1,
               sounds={"volume": 7}, countdown={"first": -3})
    assert p["gif"]["shots"] == 10 and p["gif"]["frame_ms"] == 80
    assert p["review_seconds"] == 3 and p["sounds"]["volume"] == 1 and p["countdown"]["first"] == 1
    assert _saved(admin, booth, gif={"shots": 0})["gif"]["shots"] == 2


def test_the_save_route_clamps_nested_limits(admin, booth):
    p = booth.profile
    _saved(admin, booth, gif={"boomerang": {**p["gif"]["boomerang"], "fps": 500, "loops": 0}})
    assert booth.profile["gif"]["boomerang"]["fps"] == 60 and booth.profile["gif"]["boomerang"]["loops"] == 1


def test_a_value_that_is_no_number_keeps_the_current_one(admin, booth):
    before = booth.profile["gif"]["shots"]
    p = _saved(admin, booth, gif={"shots": "viele"}, looks_max=None)
    assert p["gif"]["shots"] == before
    assert p["looks_max"] == booth.profile["looks_max"]
    p = _saved(admin, booth, gif={"shots": True})
    assert p["gif"]["shots"] == before


def test_numbers_inside_the_limits_pass_unchanged(admin, booth):
    p = _saved(admin, booth, gif={"shots": 6, "interval": 1.5})
    assert p["gif"]["shots"] == 6 and p["gif"]["interval"] == 1.5


def test_the_two_boomerangs_are_named_apart():
    """The own capture mode and the extra film made from a GIF's stills must not read alike."""
    text = JS.read_text(encoding="utf-8")
    assert 'boomerang: ["Boomerang (eigener Aufnahme-Modus aus dem Livebild)"' in text
    assert '"gif.boomerang": "Zum GIF zusätzlich: Hin-und-zurück-Film"' in text
    assert "Boomerang-Fassung zum GIF" not in text


def test_the_live_view_ladder_limits_are_choices_with_a_german_name():
    """camera.preview_iso_max / preview_shutter_min: the same lists as the gallery form."""
    from boothcore.camera import exposure
    choices, labels = _block("CHOICES"), _block("LABELS")
    for path, values in (("camera.preview_iso_max", exposure.ISO_MAX_CHOICES),
                         ("camera.preview_shutter_min", exposure.SHUTTER_MIN_CHOICES)):
        assert f'"{path}": [' + ", ".join(f'"{v}"' for v in values) + "]" in choices, path
        assert f'"{path}":' in labels, path
