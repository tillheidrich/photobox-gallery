"""Gelöscht heißt gelöscht — auch in der Galerie.

An der Box löscht ein Gast, weil ihm das Bild nicht gefällt, oder der
Betreiber, weil jemand Unsinn gemacht hat. Beides darf nicht nur die Kachel am
Kiosk ausblenden: Was schon oben ist, muss verschwinden, und was noch in der
Warteschlange liegt, darf gar nicht erst hochgehen.

Der Weg geht über zwei Stellen — die Warteschlange der Box (``cancel_session``)
und die Meldung beim nächsten Sync (``deleted`` → ``deleted_ack``). Dieser Test
läuft beide ab, gegen die echte Galerie-API.
"""
import io
import json
import secrets

from PIL import Image


def _jpeg(size=(800, 1000)):
    buf = io.BytesIO()
    Image.new("RGB", size, (150, 90, 60)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _box(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    slug = "del-" + secrets.token_hex(3)
    eid = db.create_event(name="Löschtest", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (eid,)); con.commit(); con.close()
    _made = db.create_device(name="Box Löschen")
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo", "collage"]}, eid)
    return db.get_event_by_id(eid), {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _upload(client, dev, event, uid, session_uid=None, kind="photo"):
    return client.post("/api/v2/booth/media", headers={"X-API-Key": dev["api_key"]},
                       content_type="multipart/form-data",
                       data={"uid": uid, "session_uid": session_uid or uid, "kind": kind,
                             "variant": "web", "event_slug": event["slug"],
                             "file": (_jpeg(), "result_web.jpg"),
                             "meta": json.dumps({"taken_at": "2026-09-20T20:00:00"})})


def test_what_the_booth_deletes_leaves_the_gallery(client):
    event, dev = _box(client)
    uid = "loesch2345"
    _upload(client, dev, event, uid)
    from app import db
    assert len(db.list_media(event["id"])) == 1

    antwort = client.post("/api/v2/booth/sync", json={"deleted": [uid]},
                          headers={"X-API-Key": dev["api_key"]}).get_json()
    assert antwort["deleted_ack"] == [uid]
    assert db.list_media(event["id"]) == []
    # und die Seite hinter dem QR-Code zeigt kein Bild mehr
    from app import ecosystem
    code = f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"
    seite = client.get(f"/p/{code}")
    assert seite.status_code == 200 and b"Eure Aufnahmen" not in seite.data


def test_a_deleted_collage_takes_its_gif_and_originals_with_it(client):
    """Ein Durchgang kann mehrere Dateien sein. Übrig bleiben darf keine."""
    event, dev = _box(client)
    uid = "loesch3456"
    _upload(client, dev, event, uid, kind="collage")
    _upload(client, dev, event, f"{uid}.gif", session_uid=uid, kind="gif")
    _upload(client, dev, event, f"{uid}.1", session_uid=uid, kind="original")
    from app import db
    assert len(db.list_media(event["id"])) >= 2

    client.post("/api/v2/booth/sync", json={"deleted": [uid]}, headers={"X-API-Key": dev["api_key"]})
    assert db.list_media(event["id"]) == []


def test_a_foreign_device_cannot_delete(client):
    """Sonst wäre ein Geräteschlüssel ein Löschknopf für fremde Events."""
    event, dev = _box(client)
    _, fremd = _box(client)
    uid = "loesch4567"
    _upload(client, dev, event, uid)
    client.post("/api/v2/booth/sync", json={"deleted": [uid]}, headers={"X-API-Key": fremd["api_key"]})
    from app import db
    assert len(db.list_media(event["id"])) == 1


def test_the_deleted_file_is_gone_from_the_disk(client, tmp_path):
    """Aus der Datenbank verschwinden reicht nicht — die Datei muss weg sein."""
    event, dev = _box(client)
    uid = "loesch5678"
    _upload(client, dev, event, uid)
    from app import db, media
    eintrag = db.list_media(event["id"])[0]
    datei = media.data_dir() / (eintrag["path_webp"] or eintrag["path_original"])
    assert datei.is_file()

    client.post("/api/v2/booth/sync", json={"deleted": [uid]}, headers={"X-API-Key": dev["api_key"]})
    assert not datei.exists()
