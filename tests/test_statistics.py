"""Zahlen zum Abend: je Event, je Box, zurücksetzbar, zum Mitnehmen.

Wer einen Abend abrechnet oder dem Kunden zeigt, was lief, braucht die Zahlen
in der Hand — nicht auf einer Seite, die er abschreibt. Und nach dem Event
gehören sie dem Kunden: Zurücksetzen muss genauso selbstverständlich sein wie
Zählen.
"""
import json
import secrets

import pytest

from app import stats


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    return client


def _event(client, name="Zahlenfest"):
    client.get("/healthz")
    from app import db
    eid = db.create_event(name=name, slug="zahl-" + secrets.token_hex(3),
                          upload_token=secrets.token_hex(8))
    return db.get_event_by_id(eid)


def _box(client, name=None):
    client.get("/healthz")
    from app import db
    return db.create_device(name=name or ("Zähl-Box " + secrets.token_hex(2)))


def _heartbeat(client, dev, sessions, prints):
    return client.post("/api/v2/booth/sync", json={"status": {
        "camera": {"state": "ready"}, "sessions_total": sessions, "prints_today": prints,
        "booth_version": "photobox-booth 0.2.0"}}, headers={"X-API-Key": dev["api_key"]})


# ── Je Box ────────────────────────────────────────────────

def test_the_box_reports_totals_and_we_keep_the_daily_increase(client):
    """Die Box zählt seit ihrem Start — interessant ist, was heute dazukam."""
    dev = _box(client)
    _heartbeat(client, dev, 100, 40)      # erster Kontakt: Ausgangspunkt
    _heartbeat(client, dev, 130, 55)
    _heartbeat(client, dev, 150, 60)
    daten = stats.device(dev["id"])
    assert daten["sessions_total"] == 50 and daten["prints_total"] == 20


def test_a_restart_does_not_produce_negative_days(client):
    """Nach einem Neustart fängt der Zähler der Box von vorn an."""
    dev = _box(client)
    _heartbeat(client, dev, 90, 30)
    _heartbeat(client, dev, 120, 40)
    _heartbeat(client, dev, 5, 2)          # Box neu gestartet
    daten = stats.device(dev["id"])
    assert daten["sessions_total"] == 35 and daten["prints_total"] == 12
    assert all(t["sessions"] >= 0 for t in daten["days"])


def test_the_box_page_shows_the_numbers(admin, client):
    dev = _box(client, "Sichtbare Zählbox")
    _heartbeat(client, dev, 10, 3)
    _heartbeat(client, dev, 40, 11)
    seite = admin.get(f"/admin/analytics/box/{dev['id']}").get_data(as_text=True)
    assert "Sichtbare Zählbox" in seite and "30" in seite


def test_resetting_a_box_clears_its_history(admin, client):
    dev = _box(client)
    _heartbeat(client, dev, 10, 1)
    _heartbeat(client, dev, 60, 20)
    assert stats.device(dev["id"])["sessions_total"] == 50
    admin.post(f"/admin/analytics/reset/box/{dev['id']}", data={"csrf_token": "test-token"},
               follow_redirects=True)
    assert stats.device(dev["id"])["sessions_total"] == 0


# ── Je Event ──────────────────────────────────────────────

def test_event_numbers_come_from_media_and_guest_actions(client):
    from app import db
    ev = _event(client)
    for i in range(3):
        db.add_media(event_id=ev["id"], kind="photo", filename=f"f{i}.jpg", path_original=f"x/{i}.jpg",
                     path_webp=None, path_thumb=None, size_bytes=10,
                     sha256=secrets.token_hex(8), share_token=secrets.token_hex(6))
    db.add_stat(ev["id"], "view_gallery")
    db.add_stat(ev["id"], "download")
    db.add_stat(ev["id"], "download")
    daten = stats.event(ev["id"])
    assert daten["media_total"] == 3
    assert daten["actions"]["download"] == 2 and daten["actions"]["view_gallery"] == 1


def test_resetting_an_event_keeps_the_photos(admin, client):
    from app import db
    ev = _event(client)
    db.add_media(event_id=ev["id"], kind="photo", filename="a.jpg", path_original="x/a.jpg",
                 path_webp=None, path_thumb=None, size_bytes=10,
                 sha256=secrets.token_hex(8), share_token=secrets.token_hex(6))
    db.add_stat(ev["id"], "download")
    admin.post(f"/admin/analytics/reset/event/{ev['id']}", data={"csrf_token": "test-token"},
               follow_redirects=True)
    daten = stats.event(ev["id"])
    assert daten["actions"] == {} and daten["media_total"] == 1


# ── Mitnehmen ─────────────────────────────────────────────

def test_the_export_is_a_real_file(admin, client):
    ev = _event(client)
    from app import db
    db.add_stat(ev["id"], "view_gallery")
    antwort = admin.get(f"/admin/analytics/export/event/{ev['id']}.csv")
    assert antwort.status_code == 200
    assert "attachment" in antwort.headers["Content-Disposition"]
    text = antwort.get_data(as_text=True)
    assert text.startswith("﻿"), "mit BOM, sonst zerlegt Excel die Umlaute"
    assert "Galerie geöffnet" in text and ";" in text


def test_json_export_is_machine_readable(admin, client):
    dev = _box(client)
    _heartbeat(client, dev, 5, 1)
    antwort = admin.get(f"/admin/analytics/export/box/{dev['id']}.json")
    daten = json.loads(antwort.get_data(as_text=True))
    assert "days" in daten and "events" in daten


def test_exports_need_an_admin(client):
    ev = _event(client)
    frisch = client.application.test_client()
    assert frisch.get(f"/admin/analytics/export/event/{ev['id']}.csv").status_code in (302, 401, 403)


def test_nonsense_exports_are_refused(admin, client):
    ev = _event(client)
    assert admin.get(f"/admin/analytics/export/gibtsnicht/{ev['id']}.csv").status_code == 404
    assert admin.get(f"/admin/analytics/export/event/{ev['id']}.xml").status_code == 404
    assert admin.get("/admin/analytics/export/event/999999.csv").status_code == 404


# ── Was gezählt wird ──────────────────────────────────────

def test_everything_the_gallery_sends_is_actually_counted(client):
    """Bis eben liefen Fotoansichten und Teilen-Klicks in ein 400."""
    from app import db
    ev = _event(client)
    for aktion in ("view_photo", "share_photo", "share_whatsapp_link", "download_selection"):
        antwort = client.post("/api/track", json={"event_id": ev["id"], "action": aktion})
        assert antwort.status_code == 200, aktion
    assert stats.event(ev["id"])["actions"].get("view_photo") == 1


def test_an_invented_action_is_still_refused(client):
    ev = _event(client)
    assert client.post("/api/track", json={"event_id": ev["id"], "action": "rm -rf"}).status_code == 400
