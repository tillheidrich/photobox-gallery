"""Nothing offers to print a run that has no print sheet (18.11.2026, wedding day).

The owner, testing: "Ich weiß nicht, warum bei Videos angezeigt wird, dass man
sie drucken kann. Das macht keinen Sinn, das muss sofort weg." A video never
has a print sheet, a boomerang only with ``boomerang.print``; photo, collage
and GIF always do (a GIF prints its stills). Two places offered it anyway:

* the consent screen ("Nur drucken") before a video or boomerang run — the
  guest who took it got neither a print nor a gallery link;
* the booth admin's reprint list, a "Drucken" tile for every run, which for
  a film only ever answered 409.

The booth itself refuses as well: "Nur drucken" for such a mode is a 400, and
print and reprint of a run without sheet stay refused.
"""
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "boothcore" / "web" / "static"


@pytest.fixture()
def booth(monkeypatch, tmp_path):
    d = Path(tempfile.mkdtemp(prefix="booth-filmprint-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"server": {"url": "https://galerie.example", "api_key": "k" * 8},
                        "storage": {"folder": str(tmp_path / "ablage")}})
    b.store.profile["event"].update({"slug": "hochzeit", "name": "Hochzeit", "share_key": "k",
                                     "base_url": "https://galerie.example"})
    b.store.profile["print"].update({"enabled": True, "auto": False})
    b.store.profile["modes"] = ["photo", "collage", "gif", "boomerang", "video"]
    b.store.profile["consent"].update({"enabled": True})
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


def test_only_modes_with_a_print_sheet_offer_print_only(booth, client):
    c = client.get("/api/state").json()["consent"]
    assert c["options"] == ["gallery", "print"]
    assert c["print_modes"] == ["photo", "collage", "gif"]
    for mode in ("photo", "collage", "gif"):
        assert booth.consent_options(mode) == ["gallery", "print"], mode
    for mode in ("video", "boomerang"):
        assert booth.consent_options(mode) == [], mode
        assert booth.consent_notice_only(mode), mode


def test_a_boomerang_with_its_sheet_may_be_print_only(booth, client):
    booth.store.profile["boomerang"]["print"] = True
    assert booth.consent_options("boomerang") == ["gallery", "print"]
    assert "boomerang" in client.get("/api/state").json()["consent"]["print_modes"]
    assert booth.consent_options("video") == []


@pytest.mark.parametrize("mode", ["video", "boomerang"])
def test_print_only_for_a_film_is_refused_not_turned_into_a_gallery_run(booth, client, mode):
    r = client.post("/api/session", json={"mode": mode, "consent": {"option": "print", "lang": "de"}})
    assert r.status_code == 400 and r.json()["detail"] == "consent.option"
    # Without a choice the run starts; it is a gallery run with the note recorded.
    r = client.post("/api/session", json={"mode": mode, "consent": {"option": "notice", "lang": "de"}})
    assert r.status_code == 200, r.text
    s = booth.sessions[r.json()["uid"]]
    assert not s.print_only and s.consent["option"] == "notice"


def _film_row(booth, folder, uid="film1"):
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / "video.mp4"
    clip.write_bytes(b"\x00" * 64)
    booth.library.upsert(uid=uid, event_slug="hochzeit", mode="video", result=clip, thumb=None,
                         print_file=None, share_url="https://galerie.example/p/x", look="original")
    return uid


def test_a_film_in_the_gallery_has_no_print_button_and_no_reprint(booth, client, tmp_path):
    uid = _film_row(booth, tmp_path / "film1")
    item = client.get(f"/api/gallery/{uid}").json()
    assert item["has_print"] is False and item["print"] == {"enabled": False}
    listed = {i["uid"]: i for i in client.get("/api/gallery").json()["items"]}
    assert listed[uid]["has_print"] is False
    assert client.post(f"/api/print/{uid}", json={"copies": 1}).status_code == 409


def test_a_print_file_that_is_gone_is_no_print_offer(booth, client, tmp_path):
    uid = _film_row(booth, tmp_path / "gone1", "gone1")
    booth.library.upsert(uid=uid, event_slug="hochzeit", mode="photo",
                         result=Path(booth.library.get(uid)["result"]), thumb=None,
                         print_file=tmp_path / "gone1" / "print.jpg", share_url="", look="original")
    item = client.get(f"/api/gallery/{uid}").json()
    assert item["has_print"] is False and item["print"]["enabled"] is False


def test_the_kiosk_drops_print_only_for_a_mode_without_sheet():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    fn = js[js.index("function consentOptions(mode)"):js.index("function askConsent(mode)")]
    assert "S.consent.print_modes" in fn and 'o !== "print"' in fn
    assert "const options = consentOptions(mode);" in js
    assert "if (consentOptions(mode).length >= 2 && !chosen) return askConsent(mode);" in js
    # No screen for the mode, but the step is on: its note stands on the set-up screen.
    assert "consentOptions(mode).length < 2" in js


def test_the_admin_reprint_list_shows_only_runs_with_a_sheet():
    js = (STATIC / "admin-page.js").read_text(encoding="utf-8")
    block = js[js.index('$("nachdruckLaden").onclick'):js.index("function ftpWarnung")]
    assert ".filter((it) => it.has_print)" in block
    assert "(d.items || []).forEach" not in block
