"""Was die offene Schnittstelle herausgibt — und an wen.

Funde der Durchsicht vom 19.09.2026: ein Upload-Schlüssel für fremde Events,
eine Statistik, die jeder fälschen konnte, und ein Anmeldeformular für
Galerien, die es nach außen gar nicht gibt.
"""
import secrets

import pytest

from app import db, ecosystem


@pytest.fixture(autouse=True)
def schema(client):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    yield


def _event(client, *, veroeffentlicht=True):
    kennung = secrets.token_hex(4)
    with client.application.app_context():
        eid = db.create_event(name=f"API {kennung}", slug=f"api-{kennung}",
                              upload_token=f"GEHEIM-{kennung}")
        db.update_event(eid, {"published": 1 if veroeffentlicht else 0})
        return db.get_event_by_id(eid)


def _box(client, *, aktiv=True, event_id=None):
    with client.application.app_context():
        con = db.get_db()
        schluessel = secrets.token_hex(12)
        # Since S-6 only the hash of a key is stored; the column api_key holds a placeholder.
        cur = con.execute("INSERT INTO devices (name, api_key, api_key_hash, active) VALUES (?,?,?,?)",
                          (f"Box {schluessel[:4]}", "hashed:" + schluessel[:16],
                           db.hash_device_key(schluessel), 1 if aktiv else 0))
        did = int(cur.lastrowid)
        if event_id:
            con.execute("UPDATE devices SET event_id=? WHERE id=?", (int(event_id), did))
            con.execute("INSERT OR IGNORE INTO device_event_history(device_id, event_id) VALUES (?,?)",
                        (did, int(event_id)))
        con.commit()
        con.close()
        return {"id": did, "api_key": schluessel}


# ── Der Upload-Schlüssel ──────────────────────────────────────────────
def test_a_foreign_box_does_not_get_the_upload_key(client):
    """Der Slug steht in jeder Galerie-Adresse — er darf kein Schlüssel sein."""
    fremd = _event(client)
    box = _box(client)                      # gehört zu keinem Event
    antwort = client.post("/api/events/ensure", json={"name": "egal", "slug": fremd["slug"]},
                          headers={"X-Api-Key": box["api_key"]})
    assert antwort.status_code == 200
    daten = antwort.get_json()
    assert daten["event_id"] == fremd["id"]
    assert not daten.get("upload_token"), "Der Schlüssel eines fremden Events wurde herausgegeben"


def test_the_box_that_belongs_to_the_event_still_gets_it(client):
    """Die Gegenprobe — sonst wäre die Sperre nur eine kaputte Funktion."""
    event = _event(client)
    box = _box(client, event_id=event["id"])
    daten = client.post("/api/events/ensure", json={"name": "egal", "slug": event["slug"]},
                        headers={"X-Api-Key": box["api_key"]}).get_json()
    assert daten["upload_token"] == event["upload_token"]


def test_a_switched_off_box_is_refused(client):
    """Geprüft, weil es behauptet wurde: ``get_device_by_api_key`` filtert
    ``active=1`` bereits selbst — eine stillgelegte Box kommt gar nicht erst
    durch die Anmeldung. Der Test hält das fest, damit es so bleibt."""
    event = _event(client)
    box = _box(client, aktiv=False, event_id=event["id"])
    antwort = client.post("/api/events/ensure", json={"name": "egal", "slug": event["slug"]},
                          headers={"X-Api-Key": box["api_key"]})
    assert antwort.status_code == 401


# ── Die Statistik ─────────────────────────────────────────────────────
def test_numbers_cannot_be_invented_for_an_unpublished_event(client):
    event = _event(client, veroeffentlicht=False)
    antwort = client.post("/api/track", json={"event_id": event["id"], "action": "download"})
    assert antwort.status_code == 404
    with client.application.app_context():
        assert not (db.get_event_stats(event["id"]) or {}).get("download")


def test_a_visible_gallery_still_counts(client):
    event = _event(client)
    assert client.post("/api/track", json={"event_id": event["id"], "action": "download"}).status_code == 200
    with client.application.app_context():
        assert (db.get_event_stats(event["id"]) or {}).get("download")


def test_a_protected_gallery_counts_only_for_those_who_got_in(client):
    event = _event(client)
    with client.application.app_context():
        db.set_event_password(event["id"], "geheim1234")
    assert client.post("/api/track", json={"event_id": event["id"], "action": "view_photo"}).status_code == 404
    with client.session_transaction() as s:
        s[f"gallery_access_{event['id']}"] = True
    assert client.post("/api/track", json={"event_id": event["id"], "action": "view_photo"}).status_code == 200


# ── Die Benachrichtigung ──────────────────────────────────────────────
def test_nobody_subscribes_to_a_gallery_that_is_not_published(client):
    event = _event(client, veroeffentlicht=False)
    antwort = client.post("/api/notify/subscribe",
                          json={"event_id": event["id"], "email": "gast@example.de"})
    assert antwort.status_code == 404


def test_subscribing_to_a_published_gallery_works(client):
    event = _event(client)
    antwort = client.post("/api/notify/subscribe",
                          json={"event_id": event["id"], "email": "gast@example.de"})
    assert antwort.status_code == 200 and antwort.get_json()["ok"] is True
