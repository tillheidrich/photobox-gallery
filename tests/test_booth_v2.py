"""Photobox Booth ↔ Galerie: Sync, idempotenter Upload, QR-Seite, Löschung, Aufträge."""
import io
import json

from PIL import Image


def _jpeg(color=(200, 80, 60), size=(800, 1000)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _setup(client):
    client.get("/healthz")  # Schema anlegen
    from app import db, ecosystem
    slug = "box-test-" + __import__("secrets").token_hex(3)
    eid = db.create_event(name="Box Test", slug=slug, upload_token=__import__("secrets").token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
    con.commit(); con.close()
    _made = db.create_device(name="Box A")
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo", "boomerang"], "print": {"enabled": True, "limit_session": 1}}, eid)
    return db.get_event_by_id(eid), {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _code(event, uid):
    from app import ecosystem
    return f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"


def test_sync_delivers_profile_jobs_and_assets(client):
    event, dev = _setup(client)
    h = {"X-API-Key": dev["api_key"]}
    r = client.post("/api/v2/booth/sync", json={"status": {"disk_free_gb": 50, "camera_state": "ready"}, "profile_version": 0}, headers=h)
    data = r.get_json()
    assert r.status_code == 200 and data["ok"]
    prof = data["profile"]
    assert prof["event"]["slug"] == event["slug"] and prof["event"]["share_key"]
    assert prof["modes"] == ["photo", "boomerang"] and prof["print"]["limit_session"] == 1
    # gleiche Version -> kein Profil erneut
    r2 = client.post("/api/v2/booth/sync", json={"profile_version": prof["version"]}, headers=h).get_json()
    assert "profile" not in r2
    # Auftrag
    from app import ecosystem
    jid = ecosystem.add_job(dev["id"], "test_print")
    r3 = client.post("/api/v2/booth/sync", json={"profile_version": prof["version"]}, headers=h).get_json()
    assert [j["type"] for j in r3["jobs"]] == ["test_print"]
    client.post("/api/v2/booth/sync", json={"job_results": [{"id": jid, "ok": True, "result": "ok"}]}, headers=h)
    assert ecosystem.recent_jobs(dev["id"])[0]["ok"] == 1


def test_sync_rejects_unknown_key(client):
    client.get("/healthz")
    assert client.post("/api/v2/booth/sync", json={}, headers={"X-API-Key": "nope"}).status_code == 401


def test_upload_is_idempotent_and_session_page(client):
    event, dev = _setup(client)
    h = {"X-API-Key": dev["api_key"]}
    uid = "abcd2345xy"
    code = _code(event, uid)
    # vor dem Upload: gültige Signatur -> "unterwegs"
    page = client.get(f"/p/{code}")
    assert page.status_code == 200 and b"unterwegs" in page.data
    assert client.get(f"/p/{uid}-wrongsig").status_code == 404

    form = lambda img: {"uid": uid, "session_uid": uid, "kind": "photo", "variant": "web",
                        "event_slug": event["slug"], "file": (img, "result_web.jpg"),
                        "meta": json.dumps({"taken_at": "2026-09-20T20:00:00"})}
    r1 = client.post("/api/v2/booth/media", data=form(_jpeg()), headers=h, content_type="multipart/form-data")
    assert r1.status_code == 200, r1.data
    r2 = client.post("/api/v2/booth/media", data=form(_jpeg()), headers=h, content_type="multipart/form-data")
    assert r2.get_json().get("duplicate") is True
    # Original-Variante: versteckt in der Galerie, aber per Download erreichbar
    client.post("/api/v2/booth/media", headers=h, content_type="multipart/form-data",
                data={"uid": uid, "session_uid": uid, "kind": "photo", "variant": "original",
                      "event_slug": event["slug"], "file": (_jpeg(size=(3000, 3750)), "result.jpg")})
    from app import db
    listed = db.list_media(event["id"])
    assert len(listed) == 1 and listed[0]["width"] == 800
    ready = client.get(f"/p/{code}")
    assert ready.status_code == 200 and b"Eure Aufnahmen" in ready.data
    dl = client.get(f"/gallery/{event['slug']}/photo/{listed[0]['id']}/download?size=original&code={code}")
    assert dl.status_code == 200
    assert Image.open(io.BytesIO(dl.data)).size == (3000, 3750)

    # Löschung von der Box
    r = client.post("/api/v2/booth/sync", json={"deleted": [uid]}, headers=h).get_json()
    assert r["deleted_ack"] == [uid]
    assert db.list_media(event["id"]) == []


def test_video_upload_with_poster(client):
    event, dev = _setup(client)
    h = {"X-API-Key": dev["api_key"]}
    r = client.post("/api/v2/booth/media", headers=h, content_type="multipart/form-data",
                    data={"uid": "vid2345abc", "kind": "boomerang", "variant": "web", "event_slug": event["slug"],
                          "file": (io.BytesIO(b"\x00\x00\x00 ftypisom fake"), "boomerang.mp4"),
                          "poster": (_jpeg(size=(1080, 1920)), "poster.jpg")})
    assert r.status_code == 200
    from app import db
    m = db.list_media(event["id"])[0]
    assert m["kind"] == "boomerang" and m["path_thumb"] and m["height"] == 1920
    g = client.get(f"/gallery/{event['slug']}")
    assert b"photo-play" in g.data


def test_qr_target_external_and_status(client):
    event, dev = _setup(client)
    from app import ecosystem
    code = _code(event, "zzzz2345aa")
    ecosystem.set_event_qr(event["id"], "external", "https://www.picdrop.com/beispiel/xyz?ref={uid}", "")
    r = client.get(f"/p/{code}")
    # Ziel bleibt das externe, die Kampagnen-Parameter hängen hinten dran
    assert r.status_code == 302
    assert "ref=zzzz2345aa" in r.headers["Location"] and "utm_source=" in r.headers["Location"]
    ecosystem.set_event_qr(event["id"], "status", "", "Galerie wurde gelöscht.")
    r = client.get(f"/p/{code}")
    assert r.status_code == 200 and "Galerie wurde gelöscht.".encode() in r.data


def test_admin_profile_page_renders(client):
    event, dev = _setup(client)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    r = client.get(f"/admin/devices/{dev['id']}/booth")
    assert r.status_code == 200 and b"Box-Einstellungen" in r.data
    assert client.get("/admin/booth-assets").status_code == 200


def test_upload_gif_keeps_animation(client):
    event, dev = _setup(client)
    frames = [Image.new("RGB", (120, 160), c) for c in ((255, 0, 0), (0, 255, 0))]
    buf = io.BytesIO(); frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0); buf.seek(0)
    r = client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                    data={"uid": "gif1", "kind": "gif", "variant": "web", "file": (buf, "x.gif")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    from app import ecosystem
    m = ecosystem.media_by_uid("gif1", "web")
    assert m["kind"] == "gif" and m["path_webp"].endswith(".gif")
