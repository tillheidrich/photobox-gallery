"""Built-in start backgrounds at the booth (owner's request of 16.11.2026)."""
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WEB = Path(__file__).resolve().parents[1] / "boothcore" / "web"


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-startbg-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore import app as app_mod
    b = app_mod.Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "4711"})
    yield b
    b.mail.stop()


@pytest.fixture()
def client(booth):
    from boothcore.app import create_app
    return TestClient(create_app(booth))


def _set_media(booth, media):
    profile = json.loads(json.dumps(booth.profile))
    profile["start"]["media"] = media
    booth._apply_profile(profile)


def test_a_built_in_background_reaches_the_kiosk_without_a_file(booth, client):
    _set_media(booth, ["builtin:abend.svg", "builtin:unbekannt.svg", "fehlt.jpg"])
    assert client.get("/api/state").json()["start_media"] == ["builtin:abend.svg"]
    r = client.get("/static/start/abend.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg")


def test_the_admin_offers_them_next_to_the_own_files(client):
    token = client.post("/api/admin/login", json={"pin": "4711"}).json()["token"]
    d = client.get("/api/admin/assets", headers={"X-Admin-Token": token}).json()
    names = [x["name"] for x in d["start_builtins"]]
    assert "builtin:abend.svg" in names and all(x["url"].startswith("/static/start/") for x in d["start_builtins"])


def test_the_kiosk_and_the_admin_page_know_the_builtin_names():
    app_js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "/static/start/" in app_js and "startMediaUrl" in app_js
    admin = (WEB / "admin.html").read_text(encoding="utf-8")
    for needle in ('id="startTiles"', 'id="startPreview"', 'id="start_fit"', 'id="start_dim"', 'id="startKeins"'):
        assert needle in admin, needle
    assert 'multiple size="5"' not in admin, "no Ctrl-click list on a touch screen"


def test_the_shipped_backgrounds_are_drawings_not_photos():
    files = sorted((WEB / "static" / "start").glob("*"))
    assert len(files) >= 5 and all(f.suffix == ".svg" and f.stat().st_size < 4000 for f in files)
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "<image" not in text and "href=\"http" not in text, f.name
