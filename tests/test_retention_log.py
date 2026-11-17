"""Aufbewahrung durchsetzen und die Löschung belegen können."""
import datetime as dt
import io
import secrets

from PIL import Image


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"


def _jpeg(size=(900, 1200)):
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 90, 60)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _event_with_photo(client, slug, *, retention_days=30, date="2026-09-01"):
    client.get("/healthz")
    from app import db
    eid = db.create_event(name=slug.title(), slug=slug, upload_token=secrets.token_hex(8),
                          retention_days=retention_days)
    db.update_event(eid, date=date, published=1)
    device = db.create_device(name=f"Box {slug}")
    from app import ecosystem
    ecosystem.save_device_profile(device["id"], {}, eid)   # /api/upload takes only its own events
    r = client.post("/api/upload", headers={"X-API-Key": device["api_key"]},
                    data={"event_id": str(eid), "file": (_jpeg(), "foto.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code in (200, 201), r.data[:300]
    return db.get_event_by_id(eid)


def test_retention_is_actually_enforced(client):
    """Bisher stand die Frist nur im Formular — jetzt verschwinden die Dateien."""
    from app import db, retention
    event = _event_with_photo(client, "alte-galerie", retention_days=1, date="2026-01-01")
    assert db.count_media(event["id"]) == 1
    assert [e["slug"] for e in retention.due()] == ["alte-galerie"]

    result = retention.sweep()
    assert result["events"] == ["alte-galerie"] and result["files"] >= 1
    assert db.count_media(event["id"]) == 0
    assert db.get_event_by_id(event["id"]) is not None, "Das Event bleibt, nur die Bilder gehen"


def test_a_fresh_gallery_is_left_alone(client):
    from app import db, retention
    event = _event_with_photo(client, "frische-galerie", retention_days=365,
                              date=dt.date.today().isoformat())
    retention.sweep()
    assert db.count_media(event["id"]) == 1


def test_every_deletion_leaves_a_receipt(client):
    from app import retention
    _event_with_photo(client, "beleg-galerie", retention_days=1, date="2026-01-01")
    retention.sweep()
    rows = retention.entries(limit=10, event_slug="beleg-galerie")
    assert rows and rows[0]["reason"] == "retention" and rows[0]["actor"] == "system"
    assert rows[0]["files"] >= 1 and rows[0]["bytes"] > 0


def test_the_receipt_holds_no_pictures_and_no_names(client):
    from app import retention
    _event_with_photo(client, "sparsam", retention_days=1, date="2026-01-01")
    retention.sweep()
    row = retention.entries(limit=1, event_slug="sparsam")[0]
    assert set(row) >= {"at", "scope", "event_slug", "session_uid", "files", "bytes", "reason", "actor"}
    assert "path" not in str(row).lower() and "jpg" not in str(row).lower()


def test_admin_deletion_is_logged_too(client):
    from app import db, retention
    event = _event_with_photo(client, "von-hand")
    media_id = db.list_media(event["id"])[0]["id"]
    _admin(client)
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    client.get("/admin/deletions")                      # erzeugt das CSRF-Token
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    client.post(f"/admin/media/{media_id}/delete", data={"csrf_token": token}, follow_redirects=True)
    rows = retention.entries(limit=5, event_slug="von-hand")
    assert rows and rows[0]["actor"] == "admin" and rows[0]["reason"] == "admin"


def test_the_log_is_downloadable_for_the_file(client):
    from app import retention
    _event_with_photo(client, "akte", retention_days=1, date="2026-01-01")
    retention.sweep()
    _admin(client)
    r = client.get("/admin/deletions.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["Content-Type"]
    body = r.data.decode("utf-8")
    assert "Zeitpunkt" in body and "akte" in body
    assert "Aufbewahrungsdauer abgelaufen" in body      # Klartext statt Kürzel


def test_the_page_shows_what_is_due(client):
    _event_with_photo(client, "faellig", retention_days=1, date="2026-01-01")
    _admin(client)
    body = client.get("/admin/deletions").data.decode()
    assert "faellig" in body and "Jetzt löschen" in body


def test_sweeping_needs_an_admin(client):
    """Ohne Anmeldung wird nichts gelöscht.

    Geantwortet wird seit dem 19.09.2026 schon von der CSRF-Wache (400), bevor
    die Route überhaupt nach dem Konto fragt. Abgewiesen ist abgewiesen — der
    Test hält beides als „refused" fest, damit er nicht am Statuscode klebt.
    """
    client.get("/healthz")
    r = client.post("/admin/deletions/sweep")
    assert r.status_code in (302, 400, 401, 403)

    # Und auch mit einem gültigen Zeichen, aber ohne Konto, passiert nichts.
    with client.session_transaction() as s:
        s["csrf_token"] = "x"
        s.pop("admin_logged_in", None)
    r = client.post("/admin/deletions/sweep", data={"csrf_token": "x"})
    assert r.status_code in (302, 401, 403)


def test_maintenance_runs_all_three_tasks_even_when_one_fails(client, monkeypatch):
    """Ein nicht erreichbares NAS darf nicht verhindern, dass Fristen eingehalten werden."""
    from app import maintenance, storage
    _event_with_photo(client, "wartung", retention_days=1, date="2026-01-01")

    def kaputt(*a, **k):
        raise RuntimeError("NAS nicht erreichbar")

    monkeypatch.setattr(storage, "offload_pending", kaputt)
    report = maintenance.run()
    assert "NAS nicht erreichbar" in report["offload_error"]
    assert "wartung" in report["retention"]["events"], "Die Fristen laufen trotzdem"
    text = maintenance.as_text(report)
    assert "Aufbewahrung" in text and "wartung" in text


def test_the_sweep_button_also_clears_the_remote_queue(client, monkeypatch):
    from app import storage
    seen = {}
    monkeypatch.setattr(storage, "process_remote_deletions", lambda limit=100: seen.setdefault("called", 3))
    monkeypatch.setattr(storage, "offload_pending", lambda limit=50: 0)
    _admin(client)
    client.get("/admin/deletions")
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    r = client.post("/admin/deletions/sweep", data={"csrf_token": token}, follow_redirects=True)
    assert r.status_code == 200 and seen.get("called") == 3


def _session_code(event, uid):
    from app import ecosystem
    return f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"


def _event_with_session(client, slug, uid="qrqr234567"):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    eid = db.create_event(name=slug, slug=slug, upload_token=secrets.token_hex(8))
    db.update_event(eid, published=1)
    event = db.get_event_by_id(eid)
    device = db.create_device(name=f"Box {slug}")
    ecosystem.save_device_profile(device["id"], {}, eid)      # Box gehört zu diesem Event
    r = client.post("/api/v2/booth/media", headers={"X-API-Key": device["api_key"]},
                    data={"uid": uid, "session_uid": uid, "event_slug": slug, "kind": "photo",
                          "variant": "web", "file": (_jpeg(), "foto.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code in (200, 201), r.data[:300]
    return db.get_event_by_id(eid), uid


def test_a_guest_can_delete_their_own_run(client):
    """Art. 17 in der Praxis: Wer den QR-Code hat, war an der Box."""
    from app import ecosystem, retention
    event, uid = _event_with_session(client, "gastloeschung")
    assert ecosystem.session_media(uid)
    r = client.post(f"/p/{_session_code(event, uid)}/delete", follow_redirects=True)
    assert r.status_code == 200 and "Gelöscht".encode() in r.data
    assert ecosystem.session_media(uid) == []
    row = retention.entries(limit=3, event_slug="gastloeschung")[0]
    assert row["actor"] == "guest" and row["reason"] == "guest" and row["files"] >= 1


def test_a_wrong_code_deletes_nothing(client):
    from app import ecosystem
    event, uid = _event_with_session(client, "fremdzugriff", uid="zzzz234567")
    r = client.post(f"/p/{uid}-falschesig/delete")
    assert r.status_code == 404
    assert ecosystem.session_media(uid), "Ohne gültige Signatur wird nichts gelöscht"


def test_the_operator_can_switch_guest_deletion_off(client):
    import json as _json

    from app import db, ecosystem, gallery_settings
    event, uid = _event_with_session(client, "kein-gastloeschen", uid="aaaa234567")
    gs = gallery_settings.parse(event.get("gallery_json"))
    gs["guest_delete"] = False
    db.update_event(event["id"], gallery_json=_json.dumps(gallery_settings.clean(gs)))
    event = db.get_event_by_id(event["id"])
    r = client.post(f"/p/{_session_code(event, uid)}/delete")
    assert r.status_code == 403
    assert ecosystem.session_media(uid)
