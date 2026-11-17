"""Which layouts the guests see, chosen at the box (owner, 16.11.2026).

"I cannot quickly mark which templates should be visible — that would be the
most important thing." The templates page lists every layout the box knows
(own templates and every built-in, the plain "schlicht-…" ones too), each
with a switch. The choice is written into the existing profile keys:
``collage_exact`` + ``collage_templates`` + ``collage_builtins`` (Booth.templates
reads them). Nothing new is invented for the gallery's side of it:

* an event that sets the layouts (``event.layouts_fixed``, sent by the
  gallery) wins over the box: the switches are locked unless the box holds
  its settings ("festhalten", ``profile_hold``);
* with a gallery but without an event choice, a change at the box stands
  until the gallery sends this box a new profile; the answer says so;
* a template the gallery sends with the profile comes back on the next sync
  when it is deleted here, so it is hidden here and deleted in the gallery;
* never zero layouts.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

EINFACH = {"name": "Abendvorlage", "paper": "10x15",
           "slots": [{"x": 60, "y": 60, "w": 1080, "h": 700, "shot": 0},
                     {"x": 60, "y": 800, "w": 1080, "h": 700, "shot": 1}]}


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-sichtbar-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.profile["access"]["pin"] = "135790"
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    c = TestClient(create_app(booth))
    token = c.post("/api/admin/login", json={"pin": "135790"}).json()["token"]
    c.headers.update({"X-Admin-Token": token})
    return c


def _layouts(client):
    return client.get("/api/admin/templates").json()


def _offered(booth):
    return [t["id"] for t in booth.templates()]


def _with_gallery(booth):
    booth.store.save_local({"server": {"url": "https://galerie.example.org", "api_key": "k"}})


def test_every_layout_the_box_knows_is_listed_with_what_the_tile_shows(client, booth):
    from boothcore.media import compose
    datei = client.post("/api/admin/templates", json=EINFACH).json()["asset"]["file"]
    d = _layouts(client)
    by_key = {x["key"]: x for x in d["layouts"]}
    for t in compose.BUILTIN_TEMPLATES:
        assert "b:" + t["id"] in by_key, t["id"]
    assert any(t.get("plain") for t in compose.BUILTIN_TEMPLATES)
    own = by_key["t:" + datei]
    assert own["kind"] == "own" and own["name"] == "Abendvorlage"
    assert own["shots"] == 2 and own["orientation"] == "portrait" and own["paper"]
    for x in d["layouts"]:
        assert set(x) >= {"key", "kind", "id", "name", "shots", "orientation", "paper", "visible", "origin", "deletable"}


def test_the_switches_show_what_the_guests_really_get(client, booth):
    """No choice yet: the box decides — the catalogue without the plain layouts."""
    d = _layouts(client)
    sichtbar = {x["id"] for x in d["layouts"] if x["visible"]}
    assert sichtbar == set(_offered(booth))
    assert d["visibility"]["mode"] == "auto"
    assert not any(x["visible"] for x in d["layouts"] if x["id"].startswith("schlicht"))


def test_a_choice_at_the_box_is_exactly_what_the_guests_get(client, booth):
    datei = client.post("/api/admin/templates", json=EINFACH).json()["asset"]["file"]
    r = client.post("/api/admin/templates/visibility",
                    json={"templates": [datei], "builtins": ["schlicht-1-weiss", "grid4"]})
    assert r.status_code == 200, r.text
    assert booth.profile["collage_exact"] is True
    assert booth.profile["collage_templates"] == [datei]
    assert booth.profile["collage_builtins"] == ["schlicht-1-weiss", "grid4"]
    assert set(_offered(booth)) == {"abendvorlage", "schlicht-1-weiss", "grid4"}
    d = r.json()
    assert {x["key"] for x in d["layouts"] if x["visible"]} == {"t:" + datei, "b:schlicht-1-weiss", "b:grid4"}
    assert d["visibility"]["mode"] == "exact"


def test_never_zero_layouts(client, booth):
    vorher = _offered(booth)
    r = client.post("/api/admin/templates/visibility", json={"templates": [], "builtins": []})
    assert r.status_code == 400 and "Mindestens ein Layout" in r.json()["detail"]
    r = client.post("/api/admin/templates/visibility", json={"templates": ["../profile.json"], "builtins": ["gibtsnicht"]})
    assert r.status_code == 400
    assert _offered(booth) == vorher


def test_without_a_gallery_the_choice_simply_stands(client, booth):
    r = client.post("/api/admin/templates/visibility", json={"builtins": ["grid4"]}).json()
    assert r["visibility"]["source"] == "box" and not r.get("warning")


def test_with_a_gallery_the_answer_says_when_it_is_replaced(client, booth):
    _with_gallery(booth)
    r = client.post("/api/admin/templates/visibility", json={"builtins": ["grid4"]}).json()
    assert r["visibility"]["source"] == "gallery"
    assert "festhalten" in r["warning"]
    booth.store.save_local({"profile_hold": True})
    r = client.post("/api/admin/templates/visibility", json={"builtins": ["grid4", "streifen4"]}).json()
    assert not r.get("warning") and r["visibility"]["hold"] is True


def test_the_next_gallery_profile_replaces_the_choice_unless_held(client, booth):
    """What the sync really does: the profile from the gallery replaces the box's keys whole."""
    _with_gallery(booth)
    client.post("/api/admin/templates/visibility", json={"builtins": ["grid4"]})
    vom_server = {**booth.profile, "version": booth.profile.get("version", 0) + 1,
                  "collage_exact": False, "collage_builtins": [], "collage_templates": []}
    booth.store.save_local({"profile_hold": True})
    assert booth._apply_server_profile(vom_server) is False
    assert _offered(booth) == ["grid4"]
    booth.store.save_local({"profile_hold": False})
    assert booth._apply_server_profile(vom_server) is True
    assert _offered(booth) != ["grid4"]


def test_an_event_that_sets_the_layouts_locks_the_switches(client, booth):
    _with_gallery(booth)
    prof = {**booth.profile, "version": 7, "collage_exact": True, "collage_builtins": ["streifen4"],
            "collage_templates": [], "event": {**booth.profile["event"], "name": "Sommerfest", "layouts_fixed": True}}
    booth._apply_server_profile(prof)
    d = _layouts(client)
    assert d["visibility"]["source"] == "event" and d["visibility"]["locked"] is True
    assert d["visibility"]["event"] == "Sommerfest"
    r = client.post("/api/admin/templates/visibility", json={"builtins": ["grid4"]})
    assert r.status_code == 409 and "Event" in r.json()["detail"]
    assert _offered(booth) == ["streifen4"]
    # Held at the box, the operator may decide here; the answer says what that costs.
    booth.store.save_local({"profile_hold": True})
    assert _layouts(client)["visibility"]["locked"] is False
    assert client.post("/api/admin/templates/visibility", json={"builtins": ["grid4"]}).status_code == 200
    assert _offered(booth) == ["grid4"]


def test_the_event_flag_survives_the_profile_rules(booth):
    from boothcore.config import safe_profile
    p = safe_profile({**booth.profile, "event": {**booth.profile["event"], "layouts_fixed": True}})
    assert p["event"]["layouts_fixed"] is True
    assert safe_profile({})["event"]["layouts_fixed"] is False


def test_a_template_from_the_gallery_is_hidden_here_not_deleted(client, booth):
    _with_gallery(booth)
    (booth.assets_dir).mkdir(parents=True, exist_ok=True)
    datei = client.post("/api/admin/templates", json=EINFACH).json()["asset"]["file"]
    galerie = "galerie-vorlage.json"
    (booth.assets_dir / galerie).write_bytes((booth.assets_dir / datei).read_bytes().replace(b"abendvorlage", b"galerievorlage"))
    booth._apply_server_profile({**booth.profile, "version": 3, "collage_templates": [galerie]})
    by_key = {x["key"]: x for x in _layouts(client)["layouts"]}
    assert by_key["t:" + galerie]["origin"] == "gallery" and by_key["t:" + galerie]["deletable"] is False
    assert by_key["t:" + datei]["origin"] == "box" and by_key["t:" + datei]["deletable"] is True
    r = client.post("/api/admin/templates/delete", json={"file": galerie})
    assert r.status_code == 409 and "Galerie" in r.json()["detail"]
    assert (booth.assets_dir / galerie).is_file()


def test_built_ins_are_hidden_never_deleted(client, booth):
    assert all(x["deletable"] is False for x in _layouts(client)["layouts"] if x["kind"] == "builtin")
    assert client.post("/api/admin/templates/delete", json={"file": "grid4"}).status_code == 404


def test_an_own_template_is_deleted_and_leaves_the_choice(client, booth):
    datei = client.post("/api/admin/templates", json=EINFACH).json()["asset"]["file"]
    client.post("/api/admin/templates/visibility", json={"templates": [datei], "builtins": ["grid4"]})
    assert client.post("/api/admin/templates/delete", json={"file": datei}).status_code == 200
    assert not (booth.assets_dir / datei).exists()
    assert booth.profile["collage_templates"] == [] and _offered(booth) == ["grid4"]


def test_deleting_the_last_visible_layout_is_refused(client, booth):
    datei = client.post("/api/admin/templates", json=EINFACH).json()["asset"]["file"]
    client.post("/api/admin/templates/visibility", json={"templates": [datei]})
    r = client.post("/api/admin/templates/delete", json={"file": datei})
    assert r.status_code == 409 and "sichtbar" in r.json()["detail"]
    assert (booth.assets_dir / datei).is_file()


def test_a_hidden_layout_still_has_a_picture_for_its_tile(client, booth):
    from boothcore.app import create_app
    assert "schlicht-2-schwarz" not in _offered(booth)
    r = client.get("/api/admin/templates/picture", params={"kind": "builtin", "id": "schlicht-2-schwarz"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    roh = TestClient(create_app(booth))
    assert roh.get("/api/admin/templates/picture", params={"kind": "builtin", "id": "grid4"}).status_code == 401
    assert client.get("/api/admin/templates/picture", params={"kind": "own", "id": "../local.json"}).status_code == 404
