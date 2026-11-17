"""Was Gästen versprochen war und nicht funktionierte.

Drei Funde der Durchsicht vom 19.09.2026: der Gäste-Upload, der Erfolg meldete
und nichts hochlud; der Download der eigenen Aufnahmen, der mit dem halben
Nachweis fragte; und der Schlüssel im QR-Bild, den jeder abholen konnte, der
den Namen der Galerie kannte.
"""
import io
import json
import secrets

import pytest
from PIL import Image

from app import db, ecosystem, gallery_settings as gsm


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        db.set_setting("guest_upload_enabled", "0")
    yield


def _event(client, *, passwort="", **gs_werte):
    kennung = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"Gast {kennung}", slug=f"gast-{kennung}",
                              upload_token=f"tok-{kennung}")
        db.update_event(eid, {"published": 1, "gallery_json": json.dumps(gsm.clean(gs_werte))})
        if passwort:
            db.set_event_password(eid, passwort)
        return db.get_event_by_id(eid)


def _bild():
    puffer = io.BytesIO()
    Image.new("RGB", (80, 100), (150, 140, 120)).save(puffer, "JPEG")
    puffer.seek(0)
    return puffer


def _lade_hoch(client, event, **extra):
    daten = {"file": (_bild(), "gast.jpg"), "event_slug": event["slug"]}
    daten.update(extra)
    return client.post("/api/guest-upload", data=daten,
                       content_type="multipart/form-data")


# ── Der Gäste-Upload ──────────────────────────────────────────────────
def test_the_form_field_the_page_actually_sends_is_the_one_that_is_read(client):
    """Die Seite schickt ``event_slug``. Gelesen wurde nur ``event_id``."""
    event = _event(client, guest_upload=True)
    antwort = _lade_hoch(client, event)
    assert antwort.status_code == 200, antwort.get_data(as_text=True)
    assert antwort.get_json()["ok"] is True
    with client.application.app_context():
        assert len(db.list_media(event["id"]) or []) == 1


def test_the_per_event_switch_is_enough(client):
    """Die Galerie zeigt das Feld bei ``guest_upload`` — die API muss mitziehen."""
    event = _event(client, guest_upload=True)
    with client.application.app_context():
        assert db.get_setting("guest_upload_enabled") != "1"
    assert _lade_hoch(client, event).status_code == 200


def test_nothing_is_uploaded_when_nobody_switched_it_on(client):
    event = _event(client)
    assert _lade_hoch(client, event).status_code == 403


def test_a_protected_gallery_does_not_take_strangers_photos(client):
    """Die Prüfung las eine Spalte, die es nicht gibt — sie griff nie."""
    event = _event(client, passwort="geheim1234", guest_upload=True)
    antwort = _lade_hoch(client, event)
    assert antwort.status_code == 403
    with client.application.app_context():
        assert not (db.list_media(event["id"]) or [])


def test_a_guest_who_knows_the_password_may_upload(client):
    event = _event(client, passwort="geheim1234", guest_upload=True)
    with client.session_transaction() as s:
        s[f"gallery_access_{event['id']}"] = True
    assert _lade_hoch(client, event).status_code == 200


def test_an_expired_gallery_takes_nothing(client):
    event = _event(client, guest_upload=True, expires_at="2020-01-01T00:00")
    assert _lade_hoch(client, event).status_code == 403


# ── Der Schlüssel im QR-Bild ──────────────────────────────────────────
def _token(client, event):
    with client.application.app_context():
        return (db.get_event_by_id(event["id"]).get("access_token") or "")


def test_the_qr_image_does_not_hand_the_key_to_strangers(client):
    """Sonst reicht der Name der Galerie, um das Passwort zu umgehen."""
    from app.blueprints.public import gallery_link
    event = _event(client, passwort="geheim1234", link_password=True)
    with client.application.app_context():
        client.get(f"/gallery/{event['slug']}/qr.png")   # legt den Schlüssel an
        frisch = db.get_event_by_id(event["id"])
    with client.application.test_request_context():
        anonym = gallery_link(frisch, mit_schluessel=False)
        berechtigt = gallery_link(frisch, mit_schluessel=True)
    assert "access_token" not in anonym
    assert "access_token" in berechtigt


def test_the_qr_image_of_an_unpublished_event_is_not_public(client):
    event = _event(client)
    with client.application.app_context():
        db.update_event(event["id"], {"published": 0})
    assert client.get(f"/gallery/{event['slug']}/qr.png").status_code == 404
    assert client.get(f"/gallery/{event['slug']}/sharepic.png").status_code == 404
    assert client.get(f"/gallery/{event['slug']}/print").status_code == 404


def test_the_kiosk_of_an_unpublished_event_is_not_public(client):
    event = _event(client)
    with client.application.app_context():
        db.update_event(event["id"], {"published": 0})
    assert client.get(f"/kiosk/{event['slug']}").status_code == 404


def test_the_kiosk_of_a_protected_gallery_asks_for_the_password(client):
    """Kein 403 — das wäre eine Sackgasse auf dem Tablet am Tresen."""
    event = _event(client, passwort="geheim1234")
    antwort = client.get(f"/kiosk/{event['slug']}")
    assert antwort.status_code == 200
    assert "password" in antwort.get_data(as_text=True).lower()


# ── Der Nachweis auf der Durchgangsseite ──────────────────────────────
def test_the_session_page_links_the_full_proof_not_half_of_it(client):
    """``code`` ist „<uid>-<Unterschrift>". Mit der nackten uid gab es 403."""
    event = _event(client, passwort="geheim1234")
    uid = secrets.token_hex(6)
    with client.application.app_context():
        con = db.get_db()
        con.execute(
            "INSERT INTO media (event_id, filename, path_original, path_webp, kind, "
            "share_token, session_uid, created_at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
            (event["id"], "f.jpg", f"storage/{event['slug']}/orig/f.jpg",
             f"storage/{event['slug']}/webp/f.webp", "photo", secrets.token_hex(8), uid))
        con.commit()
        con.close()
        code = ecosystem.session_path(event, uid).rsplit("/", 1)[-1]
    seite = client.get(f"/p/{code}").get_data(as_text=True)
    assert "/download?size=large" in seite, "Die Seite zeigt die Knöpfe nicht"
    assert f"code={code}" in seite, "Der Nachweis ist unvollständig verlinkt"
    assert f"code={uid}&" not in seite
