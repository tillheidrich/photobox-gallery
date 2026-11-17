"""Die Galerie an der Box: nachdrucken, und ansehen, was sich bewegt.

Zwei Dinge, die im Betrieb auseinanderfallen: der Druck direkt nach der
Aufnahme und das spätere Nachdrucken durch irgendwen, der am Gerät durch die
Bilder blättert. Auf einer Hochzeit ist beides erwünscht, auf einer Messe mit
teurem Papier nur das erste — deshalb ein eigener Schalter, und die Kontingente
gelten trotzdem für beides.

Dazu die Kleinigkeit mit großer Wirkung: Ein GIF sieht als Kachel aus wie ein
Standbild. Ohne Abspielzeichen tippt niemand darauf.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-kiosk-"))
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


def _item(booth, uid: str, mode: str = "photo", printable: bool = True):
    folder = Path(booth.data) / "sessions" / uid
    folder.mkdir(parents=True, exist_ok=True)
    result = folder / ("clip.gif" if mode == "gif" else "result.jpg")
    Image.new("RGB", (600, 900), (140, 90, 70)).save(result)
    thumb = folder / "thumb.jpg"
    Image.new("RGB", (120, 180), (140, 90, 70)).save(thumb)
    print_file = None
    if printable:
        print_file = folder / "print.jpg"
        Image.new("RGB", (1200, 1800), (140, 90, 70)).save(print_file)
    booth.library.upsert(uid=uid, event_slug=booth.profile["event"].get("slug", ""), mode=mode,
                         result=result, thumb=thumb, print_file=print_file,
                         share_url=f"https://example.de/p/{uid}", look="")
    return uid


def _client(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    return TestClient(create_app(booth))


# ── Nachdrucken ───────────────────────────────────────────
def test_reprinting_is_on_by_default(booth):
    uid = _item(booth, "gal2345aaa")
    booth.store.profile["print"].update({"enabled": True, "limit_session": 5})
    client = _client(booth)
    listed = client.get("/api/gallery").json()["items"]
    assert listed[0]["print"]["enabled"] is True
    assert client.post(f"/api/print/{uid}", json={"copies": 1, "from_gallery": True}).status_code == 200


def test_switching_reprinting_off_closes_the_door_not_just_the_button(booth):
    """Der ausgegraute Knopf allein genügt nicht — die Box muss auch nein sagen."""
    uid = _item(booth, "gal2345bbb")
    booth.store.profile["print"].update({"enabled": True, "limit_session": 5,
                                         "reprint_from_gallery": False})
    client = _client(booth)
    item = client.get(f"/api/gallery/{uid}").json()
    assert item["print"] == {"enabled": False, "reprint_off": True}
    r = client.post(f"/api/print/{uid}", json={"copies": 1, "from_gallery": True})
    assert r.status_code == 403


def test_the_print_right_after_the_shot_is_untouched_by_that_switch(booth):
    uid = _item(booth, "gal2345ccc")
    booth.store.profile["print"].update({"enabled": True, "limit_session": 5,
                                         "reprint_from_gallery": False})
    client = _client(booth)
    # derselbe Aufruf ohne „aus der Galerie" — das ist der Druck am Ergebnisbildschirm
    assert client.post(f"/api/print/{uid}", json={"copies": 1}).status_code == 200


def test_the_quota_counts_reprints_too(booth):
    uid = _item(booth, "gal2345ddd")
    booth.store.profile["print"].update({"enabled": True, "limit_session": 1})
    client = _client(booth)
    assert client.post(f"/api/print/{uid}", json={"copies": 1, "from_gallery": True}).status_code == 200
    again = client.post(f"/api/print/{uid}", json={"copies": 1, "from_gallery": True})
    assert again.status_code == 409 and again.json()["detail"] == "print.limit"


def test_printing_off_altogether_beats_everything(booth):
    uid = _item(booth, "gal2345eee")
    booth.store.profile["print"].update({"enabled": False, "reprint_from_gallery": True})
    client = _client(booth)
    assert client.get(f"/api/gallery/{uid}").json()["print"]["enabled"] is False
    assert client.post(f"/api/print/{uid}", json={"copies": 1, "from_gallery": True}).status_code == 403


# ── Was sich bewegt ───────────────────────────────────────
def test_a_gif_tile_is_marked_as_moving(booth):
    _item(booth, "gal2345fff", mode="gif")
    _item(booth, "gal2345ggg", mode="boomerang")
    _item(booth, "gal2345hhh", mode="photo")
    items = {i["uid"]: i for i in _client(booth).get("/api/gallery").json()["items"]}
    assert items["gal2345fff"]["is_animated"] is True        # GIF: bewegt, aber kein Video
    assert items["gal2345fff"]["is_video"] is False
    assert items["gal2345ggg"]["is_animated"] is True
    assert items["gal2345hhh"]["is_animated"] is False


def test_the_result_screen_keeps_its_own_answer(booth):
    """Nur die Galerie bekommt die Zusatzangaben — am Ergebnisbildschirm ändert sich nichts."""
    uid = _item(booth, "gal2345iii", mode="gif")
    payload = _client(booth).get(f"/api/session/{uid}").json()
    assert "is_animated" not in payload


def test_the_gallery_stays_shut_in_privacy_mode(booth):
    _item(booth, "gal2345jjj")
    booth.store.profile["privacy"].update({"mode": "print_only"})
    assert _client(booth).get("/api/gallery").status_code == 403


# ── GIF zur Collage ───────────────────────────────────────
def _collage_session(booth, shots: int = 4):
    """Einen Collage-Durchgang bis zum Ergebnis durchspielen."""
    from boothcore.media import compose
    tpl = next(t for t in compose.BUILTIN_TEMPLATES
               if compose.template_info(t)["shots"] >= shots)
    s = booth.start_session("collage", "4:5", tpl["id"], None)
    for _ in range(s.shots_needed):
        booth.take_shot(s)
    return booth.finalize(s, "original")


def test_a_collage_stays_a_collage_unless_asked(booth):
    booth.store.profile["gif"].update({"from_collage": False})
    s = _collage_session(booth)
    assert s.anim is None
    assert _client(booth).get(f"/api/gallery/{s.uid}").json()["is_animated"] is False


def test_a_collage_can_bring_a_gif_along(booth):
    booth.store.profile["gif"].update({"from_collage": True, "from_collage_min": 4})
    s = _collage_session(booth)
    assert s.anim is not None and s.anim.exists() and s.anim.suffix == ".gif"
    # gedruckt wird weiter die Collage, nicht das GIF
    assert s.print_file.suffix == ".jpg"

    item = _client(booth).get(f"/api/gallery/{s.uid}").json()
    assert item["is_animated"] is True and item["anim_url"].endswith("/anim")
    assert _client(booth).get(item["anim_url"]).status_code == 200


def test_too_few_shots_make_no_film(booth):
    booth.store.profile["gif"].update({"from_collage": True, "from_collage_min": 8})
    s = _collage_session(booth)
    assert s.anim is None


def test_the_gif_reaches_the_gallery_under_its_own_id(booth, tmp_path):
    """Sonst verdrängt es in der Galerie die Collage — dort zählt Kennung + Fassung."""
    booth.store.save_local({"storage": {"folder": str(tmp_path / "ziel")}})
    booth.store.profile["gif"].update({"from_collage": True, "from_collage_min": 4})
    s = _collage_session(booth)
    rows = [r for r in booth.outbox.due(limit=50)]
    kinds = {(r["uid"], r["kind"], r["variant"]) for r in rows}
    assert (f"{s.uid}.gif", "gif", "web") in kinds
    assert (s.uid, "collage", "web") in kinds


def test_a_broken_gif_never_costs_the_collage(booth, monkeypatch):
    from boothcore.media import boomerang

    def boom(*a, **kw):
        raise RuntimeError("ffmpeg weg")

    monkeypatch.setattr(boomerang, "make_gif", boom)
    booth.store.profile["gif"].update({"from_collage": True, "from_collage_min": 4})
    s = _collage_session(booth)
    assert s.anim is None and s.result and s.result.exists()
