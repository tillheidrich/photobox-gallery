"""Drei Türen, die offen standen — und eine Aufnahme, die noch niemand freigegeben hatte.

Gefunden bei der Durchsicht am 19.09.2026. Jeder Test hier beschreibt den
Angriff, nicht die Umsetzung: Wer die Prüfung später anders baut, soll diese
Tests bestehen können, ohne sie anzufassen.
"""
import json
import secrets

import pytest

from app import db, ecosystem, gallery_settings as gsm


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _event(client, **gs_werte):
    kennung = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"Tuer {kennung}", slug=f"tuer-{kennung}",
                              upload_token=f"tok-{kennung}")
        felder = {"published": 1}
        if gs_werte:
            felder["gallery_json"] = json.dumps(gsm.clean(gs_werte))
        db.update_event(eid, felder)
        return db.get_event_by_id(eid)


def _foto(client, event, *, wartet=False):
    """Eine Aufnahme mit echten Dateien auf der Platte.

    Die Dateien müssen wirklich existieren, sonst antwortet jede Route mit
    404 — und ein Test, der 404 erwartet, wäre dann grün, ohne irgendetwas
    zu prüfen. Genau diese Falle hat der erste Entwurf dieser Datei gestellt.
    """
    from PIL import Image
    from app import media as media_mod

    pfade = {"path_original": f"storage/{event['slug']}/orig/f.jpg",
             "path_webp": f"storage/{event['slug']}/webp/f.webp",
             "path_thumb": f"storage/{event['slug']}/thumb/f.jpg"}
    with client.application.app_context():
        wurzel = media_mod.data_dir()
        for rel in pfade.values():
            ziel = wurzel / rel
            ziel.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (60, 80), (120, 110, 100)).save(ziel)
        con = db.get_db()
        cur = con.execute(
            "INSERT INTO media (event_id, filename, path_original, path_webp, path_thumb, kind, "
            "share_token, review_state, hidden, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))",
            (event["id"], "f.jpg", pfade["path_original"], pfade["path_webp"],
             pfade["path_thumb"], "photo", secrets.token_hex(8),
             "pending" if wartet else "approved", 1 if wartet else 0))
        mid = int(cur.lastrowid)
        # Der Trigger in ``ecosystem`` hält jede neue Aufnahme eines moderierten
        # Events zurück — auch die, die dieser Test als freigegeben braucht.
        con.execute("UPDATE media SET review_state=?, hidden=? WHERE id=?",
                    ("pending" if wartet else "approved", 1 if wartet else 0, mid))
        con.commit()
        con.close()
        return mid


# ── Tür 1: der Einrichtungsassistent ──────────────────────────────────
def test_the_setup_wizard_closes_behind_itself(client):
    """Sonst übernimmt jeder mit einem Formular die ganze Instanz."""
    with client.application.app_context():
        db.set_setting("setup_completed", "1")
        db.set_setting("admin_username", "till")
    assert client.get("/setup").status_code == 404
    antwort = client.post("/setup", data={"admin_username": "eve",
                                          "admin_password": "Ein-sehr-langes-Kennwort-42"})
    assert antwort.status_code == 404
    with client.application.app_context():
        assert db.get_setting("admin_username") == "till", "Der Benutzername wurde übernommen"


def test_the_admin_may_still_open_the_wizard(admin):
    with admin.application.app_context():
        db.set_setting("setup_completed", "1")
    assert admin.get("/setup").status_code == 200


# ── Tür 2: die Auswertung ─────────────────────────────────────────────
def test_analytics_needs_a_login_like_every_other_admin_page(client):
    """404 wäre hier kein Beweis — das hieße nur „Event nicht gefunden"."""
    event = _event(client)
    for pfad in ("/admin/analytics", f"/admin/analytics/{event['id']}"):
        antwort = client.get(pfad)
        assert antwort.status_code in (302, 401, 403), \
            f"{pfad} gibt Eventnamen, Zahlen und Vorschaubilder ohne Anmeldung heraus"
        assert event["name"] not in antwort.get_data(as_text=True)


# ── Tür 3: Aufnahmen, die auf Freigabe warten ─────────────────────────
def test_a_photo_awaiting_approval_is_not_downloadable_by_its_number(client):
    event = _event(client, moderation=True)
    mid = _foto(client, event, wartet=True)
    antwort = client.get(f"/gallery/{event['slug']}/photo/{mid}/download")
    assert antwort.status_code == 404


def test_a_photo_awaiting_approval_has_no_watermark_preview(client):
    event = _event(client, moderation=True, watermark=True)
    mid = _foto(client, event, wartet=True)
    assert client.get(f"/gallery/{event['slug']}/wm/{mid}/full.jpg").status_code == 404


def test_a_photo_awaiting_approval_has_no_qr_code(client):
    event = _event(client, moderation=True)
    mid = _foto(client, event, wartet=True)
    assert client.get(f"/gallery/{event['slug']}/photo/{mid}/qr.png").status_code == 404


def test_an_approved_photo_stays_reachable(client):
    """Die Gegenprobe: Ohne sie prüfte oben nur, dass irgendetwas 404 sagt."""
    event = _event(client)
    mid = _foto(client, event)
    assert client.get(f"/gallery/{event['slug']}/photo/{mid}/download").status_code == 200
    assert client.get(f"/gallery/{event['slug']}/photo/{mid}/qr.png").status_code == 200


def test_an_approved_photo_has_a_watermark_preview(client):
    event = _event(client, watermark=True)
    mid = _foto(client, event)
    assert client.get(f"/gallery/{event['slug']}/wm/{mid}/full.jpg").status_code == 200


def test_a_file_awaiting_approval_is_not_served_by_its_path(client):
    """Nicht nur über die Kennung — auch der direkte Dateipfad ist zu."""
    event = _event(client, moderation=True)
    _foto(client, event, wartet=True)
    antwort = client.get(f"/data/storage/{event['slug']}/webp/f.webp")
    assert antwort.status_code == 404


def test_an_approved_file_is_still_served_by_its_path(client):
    event = _event(client, moderation=True)
    _foto(client, event)
    assert client.get(f"/data/storage/{event['slug']}/webp/f.webp").status_code == 200


# ── Die Wasserzeichen-Route kennt jetzt alle Schranken ────────────────
def test_the_watermark_route_respects_silence(client):
    event = _event(client, watermark=True, silent=True)
    mid = _foto(client, event)
    assert client.get(f"/gallery/{event['slug']}/wm/{mid}/full.jpg").status_code == 404


def test_the_watermark_route_respects_the_expiry_date(client):
    event = _event(client, watermark=True, expires_at="2020-01-01T00:00")
    mid = _foto(client, event)
    assert client.get(f"/gallery/{event['slug']}/wm/{mid}/full.jpg").status_code == 410


def test_the_wizard_is_closed_even_if_it_was_never_run(client):
    """A deployment with ADMIN_PASSWORD never opens the wizard.

    ``setup_completed`` then stays "0" for ever -- and the wizard used to stay
    open for everybody (staging, 11.11.2026). An admin password always exists
    after init_db, so nobody needs the wizard without a login.
    """
    with client.application.app_context():
        db.set_setting("setup_completed", "0")
        db.set_setting("admin_username", "till")
        vorher = db.get_setting("admin_password_hash")
    assert client.get("/setup").status_code == 404
    antwort = client.post("/setup", data={"admin_username": "eve",
                                          "admin_password": "Ein-sehr-langes-Kennwort-42"})
    assert antwort.status_code == 404
    with client.application.app_context():
        assert db.get_setting("admin_username") == "till"
        assert db.get_setting("admin_password_hash") == vorher


def test_the_login_page_no_longer_points_strangers_to_the_wizard(client):
    with client.application.app_context():
        db.set_setting("setup_completed", "0")
    assert "/setup" not in client.get("/admin/login").get_data(as_text=True)
