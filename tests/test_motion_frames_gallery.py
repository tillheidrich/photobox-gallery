"""Frames for boomerang and video in the gallery (17.11.2026).

Own frames are media of their own kind ("motion"): they never show up as a
print template. The booth profile form and the event page choose them; the
event's choice wins and tells the booth (``event.frames_fixed``). The page
under Medien & Vorlagen builds one with a live picture and refuses what the
booth could not draw, in plain German.
"""
import io
import json
import secrets
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "booth"))

from app import ecosystem                              # noqa: E402


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    ecosystem.init_schema()
    return client


def _event(name="Sommerfest 2027"):
    from app import db
    eid = db.create_event(name=name, slug="rf-" + secrets.token_hex(3), upload_token=secrets.token_hex(6))
    return db.get_event_by_id(eid)


def _device(event=None):
    from app import db
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    ecosystem.save_device_profile(dev["id"], {"modes": ["photo", "boomerang", "video"]},
                                  event["id"] if event else None)
    return db.get_device_by_id(dev["id"])


def _background(name="rf-hintergrund.png"):
    from app.blueprints.admin_booth import _assets_dir
    if any(a["file"] == name for a in ecosystem.list_assets("background")):
        return name
    buf = io.BytesIO()
    Image.new("RGB", (1080, 1920), (30, 90, 60)).save(buf, "PNG")
    (_assets_dir() / name).write_bytes(buf.getvalue())
    ecosystem.add_asset("background", "Hintergrund", name, secrets.token_hex(32), len(buf.getvalue()))
    return name


def _frame(**over):
    raw = {"kind": "motion_frame", "name": "Grün", "format": "9:16", "background": _background(),
           "window": {"x": 90, "y": 160, "w": 900, "h": 1300, "radius": 24},
           "texts": [{"value": "{event_name}", "x": 540, "y": 1520, "size": 70, "align": "center",
                      "color": "#ffffff"}]}
    raw.update(over)
    return raw


def test_the_defaults_are_off_and_equal_on_both_sides():
    from boothcore import config
    for mode in ("boomerang", "video"):
        assert ecosystem.DEFAULT_PROFILE[mode]["frames"] is False
        assert config.DEFAULT_PROFILE[mode] == ecosystem.DEFAULT_PROFILE[mode]
    assert (ROOT / "app" / "profile_rules.py").read_bytes() == \
        (ROOT / "booth" / "boothcore" / "profile_rules.py").read_bytes()


def test_the_page_builds_a_frame_with_a_picture_and_refuses_in_german(admin):
    page = admin.get("/admin/rahmen").get_data(as_text=True)
    assert "Rahmen bauen" in page and "Titel unten" in page
    r = admin.post("/admin/rahmen/vorschau", json=_frame(), headers={"X-CSRF-Token": "test-token"})
    assert r.status_code == 200 and r.mimetype == "image/png"
    bad = admin.post("/admin/rahmen/speichern", json=_frame(background="gibtsnicht.png"),
                     headers={"X-CSRF-Token": "test-token"})
    assert bad.status_code == 400 and "fehlt in den Medien" in bad.get_json()["error"]
    ok = admin.post("/admin/rahmen/speichern", json=_frame(), headers={"X-CSRF-Token": "test-token"})
    assert ok.status_code == 200 and ok.get_json()["file"] == "rahmen-gruen.json"
    assert admin.get("/admin/rahmen/bild/rahmen-gruen.json").mimetype == "image/png"
    assert admin.get("/admin/rahmen/bild/titel-unten").status_code == 200


def test_an_own_frame_is_no_print_template(admin):
    from app import frame_store, vorlagen
    from app.blueprints.admin_booth import _assets_dir
    frame, _ = frame_store.check(_frame(name="Nur Rahmen"), _assets_dir())
    frame_store.save(frame, _assets_dir())
    assert all(e["asset"]["file"] != "rahmen-nur-rahmen.json" for e in vorlagen.eigene(_assets_dir()))
    assert any(e["asset"]["file"] == "rahmen-nur-rahmen.json" for e in frame_store.own(_assets_dir()))


def test_a_json_upload_under_medien_is_checked(admin):
    good = json.dumps(_frame(name="Hochgeladen")).encode()
    r = admin.post("/admin/booth-assets", data={"csrf_token": "test-token", "kind": "motion",
                                                 "files": (io.BytesIO(good), "rahmen.json")},
                   content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    assert any(a["file"] == "rahmen-hochgeladen.json" for a in ecosystem.list_assets("motion"))
    bad = json.dumps({"kind": "motion_frame", "format": "3:2"}).encode()
    r = admin.post("/admin/booth-assets", data={"csrf_token": "test-token", "kind": "motion",
                                                 "files": (io.BytesIO(bad), "kaputt.json")},
                   content_type="multipart/form-data", follow_redirects=True)
    assert "Unbekanntes Format" in r.get_data(as_text=True)


def test_the_booth_form_chooses_frames_and_their_media_travel(admin):
    from app import db, frame_store
    from app.blueprints.admin_booth import _assets_dir
    frame, _ = frame_store.check(_frame(name="Für die Box"), _assets_dir())
    frame_store.save(frame, _assets_dir())
    dev = _device()
    admin.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "mode_boomerang": "on", "mode_video": "on",
        "boomerang_frames": "on", "boomerang_frame_none": "on",
        "boomerang_frame_templates": ["", "rahmen-fuer-die-box.json", "unbekannt.json"],
        "boomerang_frame_builtins": ["titel-unten", "erfunden"], "boomerang_frame_default": "titel-unten"},
        follow_redirects=True)
    p = ecosystem.device_profile(db.get_device_by_id(dev["id"]))["boomerang"]
    assert p["frames"] is True and p["frame_templates"] == ["rahmen-fuer-die-box.json"]
    assert p["frame_builtins"] == ["titel-unten"] and p["frame_default"] == "titel-unten"
    assert p["frame_guest_choice"] is False                   # unticked box = fixed default
    files = {a["file"] for a in ecosystem.assets_for_profile(ecosystem.device_profile(db.get_device_by_id(dev["id"])))}
    assert {"rahmen-fuer-die-box.json", frame["background"]} <= files
    page = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert 'name="boomerang_frames" checked' in page


def test_the_event_choice_wins_and_says_so(admin):
    from app import db
    ev = _event()
    dev = _device(ev)
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"], "ev_layouts_form": "1",
        "ev_frames_form": "1", "ev_frames_video": ["titel-links", "erfunden"]})
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://x.example")
    assert prof["video"]["frames"] is True and prof["video"]["frame_builtins"] == ["titel-links"]
    assert prof["video"]["frame_none"] is False
    assert prof["event"]["frames_fixed"] is True and prof["boomerang"]["frames"] is False
    page = admin.get(f"/admin/events/{ev['id']}/edit").get_data(as_text=True)
    assert 'name="ev_frames_video" value="titel-links" checked' in page
    # Saving the page with nothing ticked gives the choice back to the box.
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"], "ev_layouts_form": "1",
        "ev_frames_form": "1"})
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://x.example")
    assert prof["video"]["frames"] is False and prof["event"]["frames_fixed"] is False


def test_with_frames_off_the_profile_for_the_box_is_as_before():
    from app import db
    dev = _device()
    prof = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://x.example")
    for mode in ("boomerang", "video"):
        assert prof[mode]["frames"] is False and prof[mode]["frame_templates"] == []
    assert prof["event"]["frames_fixed"] is False


def test_the_booth_form_sets_the_gifs_frames_and_format_switch_like_the_boomerangs(admin):
    """The GIF is set up like the boomerang (18.11.2026) and takes the boomerang's frames."""
    from app import db, frame_store
    from app.blueprints.admin_booth import _assets_dir
    frame, _ = frame_store.check(_frame(name="Fürs GIF"), _assets_dir())
    frame_store.save(frame, _assets_dir())
    dev = _device()
    admin.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "mode_gif": "on", "gif_guest_choice": "on",
        "gif_frames": "on", "gif_frame_none": "on", "gif_frame_guest_choice": "on",
        "gif_frame_templates": ["", "rahmen-fuers-gif.json"], "gif_frame_builtins": ["titel-unten"]},
        follow_redirects=True)
    profile = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    gif = profile["gif"]
    assert gif["guest_choice"] is True and gif["frames"] is True
    assert gif["frame_templates"] == ["rahmen-fuers-gif.json"] and gif["frame_builtins"] == ["titel-unten"]
    assert gif["shots"] == 6                                  # the GIF's own keys stay
    files = {a["file"] for a in ecosystem.assets_for_profile(profile)}
    assert "rahmen-fuers-gif.json" in files
    page = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert 'name="gif_frames" checked' in page and 'name="gif_guest_choice" checked' in page
