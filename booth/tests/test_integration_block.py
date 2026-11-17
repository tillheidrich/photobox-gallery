"""LUT-Filter, Greenscreen, E-Mail, freies QR-Ziel, dreifache Ablage, Fehlerprotokoll.

Alles ohne Hardware: die Mock-Kamera liefert Bilder, SMTP läuft im Trockenlauf.
"""
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture()
def booth(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="booth-block-"))
    monkeypatch.setenv("BOOTH_DATA", str(d))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=d, start_threads=False)
    b.store.save_local({"admin_pin": "468213"})  # the operator has replaced the factory PIN (see test_factory_pin.py)
    b.camera.start()
    for _ in range(50):
        if b.camera.state == "ready":
            break
        time.sleep(0.1)
    yield b
    b.camera.stop()
    b.mail.stop()


# ── LUTs ──────────────────────────────────────────────────
def test_builtin_luts_show_up_as_looks(booth):
    catalog = booth.lut_catalog()
    ids = {c["id"] for c in catalog}
    assert "lut-golden-warm" in ids and "lut-natural-portrait" in ids
    assert {"Golden Warm", "Natural Portrait"} <= {c["label"] for c in catalog}
    names = booth.look_names()
    assert "original" in names and "lut-golden-warm" in names
    assert booth.look_label("lut-natural-portrait") == "Natural Portrait"


def test_lut_look_changes_the_picture(booth):
    img = Image.new("RGB", (32, 32), (180, 140, 120))
    out = booth.apply_look("lut-golden-warm", img)
    assert out.size == img.size and out.getpixel((4, 4)) != img.getpixel((4, 4))


def test_only_selected_luts_are_offered(booth):
    booth.store.profile["luts"].update({"show": ["golden-warm"]})
    offered = [n for n in booth.look_names() if n.startswith("lut-")]
    assert offered == ["lut-golden-warm"]


def test_baked_lut_applies_before_the_filter(booth):
    plain = booth.prepare_shot(Image.new("RGB", (16, 16), (180, 140, 120)), None)
    booth.store.profile["luts"].update({"baked": "mono-trix-400"})
    baked = booth.prepare_shot(Image.new("RGB", (16, 16), (180, 140, 120)), None)
    r, g, b = baked.getpixel((2, 2))
    assert abs(r - g) < 6 and abs(g - b) < 6          # mono: grau
    assert plain.getpixel((2, 2)) != baked.getpixel((2, 2))


def test_unknown_baked_lut_does_not_break_the_shoot(booth):
    booth.store.profile["luts"].update({"baked": "gibt-es-nicht"})
    out = booth.prepare_shot(Image.new("RGB", (8, 8), (10, 200, 10)), None)
    assert out.size == (8, 8)


# ── Greenscreen ───────────────────────────────────────────
def test_chroma_replaces_the_green_wall(booth):
    booth.store.profile["chroma"].update({"enabled": True, "backgrounds": ["#0000ff"], "color": "green"})
    assert booth.chroma_active() and booth.chroma_backgrounds() == ["#0000ff"]
    s = booth.start_session("photo", "4:5", None, None, "#0000ff")
    green = Image.new("RGB", (64, 64), (16, 200, 40))
    green.paste(Image.new("RGB", (24, 24), (200, 40, 40)), (20, 20))   # „Gast" vor der Wand
    out = booth._apply_chroma(green, s)
    assert out.getpixel((2, 2))[2] > 200                # Wand ist jetzt blau
    assert out.getpixel((32, 32))[0] > 150              # der Gast bleibt stehen


def test_chroma_ignores_a_background_nobody_configured(booth):
    booth.store.profile["chroma"].update({"enabled": True, "backgrounds": ["#0000ff"]})
    s = booth.start_session("photo", "4:5", None, None, "#ff0000")
    assert s.chroma_background == ""                   # nicht angeboten -> nicht übernommen


def test_chroma_runs_through_a_full_session(booth):
    booth.store.profile["chroma"].update({"enabled": True, "backgrounds": ["#123456"]})
    s = booth.start_session("photo", "4:5", None, None, "#123456")
    booth.take_shot(s)
    booth.finalize(s, "original")
    assert s.result.exists() and s.thumb.exists()


# ── Freies QR-Ziel und UTM ────────────────────────────────
def test_qr_can_point_anywhere_and_carries_utm(booth):
    booth.store.profile["qr"].update({"mode": "custom", "url": "https://example.de/f/{uid}",
                                      "utm": "utm_source=fotobox&utm_campaign={event}"})
    booth.store.profile["event"].update({"slug": "lina", "name": "Lina"})
    s = booth.start_session("photo", "4:5", None, None)
    assert s.share_url.startswith(f"https://example.de/f/{s.uid}")
    assert "utm_source=fotobox" in s.share_url and "utm_campaign=lina" in s.share_url


def test_qr_off_means_no_link_at_all(booth):
    booth.store.profile["qr"].update({"mode": "off"})
    s = booth.start_session("photo", "4:5", None, None)
    assert s.share_url == ""


def test_gallery_link_wins_when_both_are_there(booth):
    booth.store.profile["event"].update({"slug": "e", "name": "E", "share_key": "k",
                                         "base_url": "https://galerie.example"})
    booth.store.profile["qr"].update({"mode": "auto", "url": "https://example.de/f/{uid}"})
    s = booth.start_session("photo", "4:5", None, None)
    assert s.share_url.startswith("https://galerie.example/p/")


# ── E-Mail ────────────────────────────────────────────────
def test_mail_goes_into_the_queue(booth):
    booth.store.profile["mail"].update({"enabled": True, "dry_run": True, "host": "smtp.example.de",
                                        "from_addr": "box@example.de", "subject": "Foto von {event}"})
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    mail_id = booth.mail.enqueue(address="gast@example.de", files=[s.result_web], subject="Foto",
                                 body="Da ist es.", session_uid=s.uid)
    assert booth.mail.get(mail_id)["address"] == "gast@example.de"
    assert booth.mail.status()["pending"] == 1


def test_mail_rejects_nonsense_addresses(booth):
    with pytest.raises(ValueError):
        booth.mail.enqueue(address="kein-at-zeichen", files=[], subject="x", body="y")


# ── Dreifache Ablage ──────────────────────────────────────
def test_folder_and_usb_become_upload_targets(booth, tmp_path):
    booth.store.save_local({"storage": {"folder": str(tmp_path / "zweite"),
                                        "usb": {"enabled": True, "path": str(tmp_path / "stick")}}})
    assert set(booth.agent.target_names()) == {"folder", "usb"}


def test_second_folder_gets_a_copy(booth, tmp_path):
    second = tmp_path / "zweite"
    second.mkdir()
    booth.store.save_local({"storage": {"folder": str(second)}})
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    from photobox_sync.targets import build_extra_targets, storage_targets
    target = build_extra_targets(storage_targets(booth.store.local["storage"]))[0]
    for item in booth.outbox.due():
        target.upload({**item, "uid": item["uid"]})
    copies = list(second.rglob("*.jpg"))
    assert copies and any(s.uid in c.name for c in copies)


def test_missing_stick_only_waits(booth, tmp_path):
    from photobox_sync.targets import build_extra_targets, storage_targets, TemporaryError
    cfg = {"folder": "", "usb": {"enabled": True, "path": str(tmp_path / "nicht-da")}}
    target = build_extra_targets(storage_targets(cfg))[0]
    with pytest.raises(TemporaryError):
        target.upload({"uid": "a", "session_uid": "a", "event_slug": "e", "kind": "photo",
                       "variant": "web", "path": __file__, "meta": "{}"})


# ── Fehlerprotokoll ───────────────────────────────────────
def test_warnings_travel_to_the_gallery(booth):
    import logging
    logging.getLogger("booth").warning("Drucker hat kein Papier")
    logging.getLogger("booth").warning("Drucker hat kein Papier")
    entries = booth.logbuf.take(10)
    assert entries and entries[0]["level"] == "warning"
    assert entries[0]["message"] == "Drucker hat kein Papier"
    assert entries[0]["context"]["count"] == 2          # zusammengefasst, nicht doppelt
    assert booth.logbuf.take(10) == []                  # einmal abgeholt ist weg


def test_undelivered_logs_are_kept_for_the_next_contact(booth):
    """Offline darf keine Meldung verschlucken — sie geht beim nächsten Kontakt mit."""
    import logging
    from photobox_sync.agent import Agent
    from photobox_sync.targets import TemporaryError

    logging.getLogger("booth").error("Kamera weg")
    sent = []

    class Flaky:
        name = "gallery"
        def __init__(self):
            self.up = False
        def sync(self, payload):
            if not self.up:
                raise TemporaryError("kein Netz")
            sent.append(payload)
            return {"ok": True}

    flaky = Flaky()
    agent = Agent(outbox=booth.outbox, local_config=lambda: {"server": {}},
                  logs=lambda: booth.logbuf.take(50))
    agent.gallery = lambda: flaky
    assert agent.sync_once() is False                   # offline: nichts angekommen
    flaky.up = True
    assert agent.sync_once() is True
    messages = [e["message"] for e in sent[0]["logs"]]
    assert "Kamera weg" in messages


# ── Rahmen und Textzeilen ─────────────────────────────────
def test_frame_overlay_and_text_land_on_the_photo(booth):
    from PIL import Image as _I
    overlay = booth.assets_dir / "rahmen.png"
    _I.new("RGBA", (400, 500), (255, 0, 0, 160)).save(overlay)
    booth.store.profile["frame"].update({
        "enabled": True, "overlay": "rahmen.png", "on_photo": True, "on_print": True,
        "texts": [{"value": "{event_name}", "x": 50, "y": 90, "size": 6, "color": "#ffffff"}]})
    booth.store.profile["event"].update({"name": "Lina & Jan"})
    s = booth.start_session("photo", "4:5", None, None)
    booth.take_shot(s)
    booth.finalize(s, "original")
    with _I.open(s.result) as im:
        px = im.convert("RGB").getpixel((im.width // 2, 10))
    assert px[0] > px[1] and px[0] > px[2]             # roter Rahmen liegt über dem Bild
    assert s.print_file.exists()


def test_frame_stays_off_when_nobody_asked_for_it(booth):
    s = booth.start_session("photo", "4:5", None, None)
    img = Image.new("RGB", (40, 50), (10, 20, 30))
    assert booth.apply_frame(img, s, "photo") is img   # nichts angefasst


def test_print_qr_can_move_into_another_corner(booth):
    from boothcore.media import compose
    tpl = compose.single_print_template("4:5", True, {"size": 300, "position": "top-left", "margin": 25})
    assert tpl["qr"] == {"x": 25, "y": 25, "size": 300}


# ── Webhooks und Auslösung von außen ──────────────────────
def test_webhook_is_signed_and_only_sends_chosen_events(booth):
    import json as _json
    from boothcore.webhooks import sign

    calls = []

    class FakeClient:
        def post(self, url, content=None, headers=None):
            calls.append((url, content, headers))
            class R:
                status_code = 200
                text = "ok"
            return R()

    booth.store.profile["webhooks"].update({"enabled": True, "url": "https://hook.example/x",
                                            "secret": "geheim", "events": ["media_ready"]})
    booth.webhooks._client = FakeClient()
    assert booth.webhooks.emit("printed", {"uid": "a"}) is False     # nicht ausgewählt
    assert booth.webhooks.emit("media_ready", {"uid": "a"}) is True
    assert booth.webhooks.drain() == 1
    url, body, headers = calls[0]
    assert url == "https://hook.example/x"
    assert _json.loads(body)["event"] == "media_ready"
    assert headers["X-Photobox-Signature"] == sign("geheim", body)


def test_http_trigger_needs_the_token(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["http_trigger"].update({"enabled": True, "token": "abc", "mode": "photo"})
    client = TestClient(create_app(booth))
    assert client.post("/api/trigger", json={}).status_code == 401
    assert client.post("/api/trigger", json={"token": "abc"}).status_code == 200
    state = client.get("/api/state").json()
    assert state["trigger"]["mode"] == "photo"
    assert client.get("/api/state").json()["trigger"] is None       # gilt nur einmal


def test_trigger_stays_shut_when_switched_off(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    assert client.post("/api/trigger", json={"token": "abc"}).status_code == 403


# ── Profil sichern und einspielen ─────────────────────────
def test_profile_export_import_keeps_event_and_password(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    booth.store.profile["mail"].update({"password": "streng-geheim", "host": "smtp.alt"})
    booth.store.profile["event"].update({"slug": "lina", "share_key": "k"})
    client = TestClient(create_app(booth))
    token = client.post("/api/admin/login", json={"pin": booth.store.local["admin_pin"]}).json()["token"]
    head = {"X-Admin-Token": token}
    export = client.get("/api/admin/profile/export", headers=head).json()
    assert "share_key" not in export["event"] and "password" not in export["mail"]

    export["mail"]["host"] = "smtp.neu"
    export["event"] = {"slug": "andere", "name": "Andere"}
    assert client.post("/api/admin/profile/import", json={"profile": export}, headers=head).status_code == 200
    assert booth.profile["mail"]["host"] == "smtp.neu"
    assert booth.profile["mail"]["password"] == "streng-geheim"     # Passwort bleibt an der Box
    assert booth.profile["event"]["slug"] == "lina"               # Event gehört der Box


# ── Über ──────────────────────────────────────────────────
def test_about_endpoint_tells_version_and_project(booth):
    from fastapi.testclient import TestClient
    from boothcore import __version__
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    data = client.get("/api/about").json()
    assert data["version"] == __version__ and data["name"] == "Photobox Booth"
    assert data["project_url"].endswith("photobox-gallery.com") and "AGPL" in data["license"]
    assert data["standalone"] is True                    # ohne Galerie eingerichtet


def test_booth_and_gallery_carry_the_same_version():
    from pathlib import Path
    from boothcore import __version__
    root = Path(__file__).resolve().parent.parent.parent
    assert (root / "VERSION").read_text().strip() == __version__


def test_admin_config_carries_the_about_block(booth):
    from fastapi.testclient import TestClient
    from boothcore.app import create_app
    client = TestClient(create_app(booth))
    token = client.post("/api/admin/login", json={"pin": booth.store.local["admin_pin"]}).json()["token"]
    cfg = client.get("/api/admin/config", headers={"X-Admin-Token": token}).json()
    assert cfg["about"]["version"] and cfg["about"]["data_dir"]
    assert "api_key" not in str(cfg["about"])            # nichts Geheimes in der Über-Karte
