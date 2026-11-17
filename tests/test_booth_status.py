"""Booth-Heartbeat und Ampel-Logik.

Der Betreiber steht auf einem anderen Event. Er muss auf einen Blick sehen,
ob eine Box Probleme hat — und bei Rot eine Nachricht bekommen.
"""
import secrets

import pytest

from app import booth


def _device(app, name="Booth 1"):
    """Gerät anlegen; create_device erzeugt den API-Schlüssel selbst."""
    from app import db
    with app.app_context():
        db.init_db()
        row = db.create_device(name=name)
        d = dict(row)
        return d["id"], d["api_key"]


# ── Ampel-Logik (rein) ───────────────────────────────────────

def test_fresh_and_healthy_is_green():
    v = booth.evaluate_fresh({"booth_running": True, "disk_free_gb": 120.0,
                              "disk_total_gb": 256.0, "queue_pending": 0,
                              "queue_failed": 0})
    assert v["health"] == "green"
    assert v["reasons"] == ["Alles in Ordnung"]


def test_never_seen_is_unknown():
    v = booth.evaluate({}, None)
    assert v["health"] == "unknown"


def test_stale_heartbeat_escalates():
    assert booth.evaluate({}, "2020-01-01 00:00:00")["health"] == "red"


def test_booth_not_running_is_red():
    v = booth.evaluate_fresh({"booth_running": False, "disk_free_gb": 100.0})
    assert v["health"] == "red"
    assert any("läuft nicht" in r for r in v["reasons"])


def test_low_disk_is_yellow_then_red():
    assert booth.evaluate_fresh({"disk_free_gb": 6.0})["health"] == "yellow"
    assert booth.evaluate_fresh({"disk_free_gb": 1.0})["health"] == "red"


def test_failed_uploads_are_red():
    v = booth.evaluate_fresh({"disk_free_gb": 99.0, "queue_failed": 3})
    assert v["health"] == "red"
    assert any("fehlgeschlagen" in r for r in v["reasons"])


def test_long_queue_is_yellow():
    assert booth.evaluate_fresh({"disk_free_gb": 99.0, "queue_pending": 50})["health"] == "yellow"


def test_printer_error_is_red_offline_is_yellow():
    assert booth.evaluate_fresh({"disk_free_gb": 99.0, "printer_status": "error"})["health"] == "red"
    assert booth.evaluate_fresh({"disk_free_gb": 99.0, "printer_status": "offline"})["health"] == "yellow"


def test_red_wins_over_yellow():
    v = booth.evaluate_fresh({"disk_free_gb": 6.0, "queue_failed": 1})
    assert v["health"] == "red"          # gelb darf rot nicht überschreiben


def test_sanitize_drops_unknown_and_casts():
    clean = booth.sanitize_payload({
        "disk_free_gb": "12.5", "queue_pending": "7", "booth_running": "true",
        "printer_name": "DNP DS-RX1",
        "evil": "DROP TABLE devices", "cpu_percent": 31.4,
    })
    assert clean["disk_free_gb"] == 12.5
    assert clean["queue_pending"] == 7
    assert clean["booth_running"] is True
    assert "evil" not in clean          # unbekannte Felder fliegen raus


def test_sanitize_survives_garbage():
    assert booth.sanitize_payload(None) == {}
    assert booth.sanitize_payload("kein dict") == {}
    assert booth.sanitize_payload({"disk_free_gb": "viel"}) == {}


def test_disk_percent_used():
    assert booth.disk_percent_used({"disk_total_gb": 100, "disk_free_gb": 25}) == 75
    assert booth.disk_percent_used({"disk_total_gb": 0, "disk_free_gb": 0}) is None
    assert booth.disk_percent_used({}) is None


# ── Endpunkt ─────────────────────────────────────────────────

def test_heartbeat_requires_key(app, client):
    _device(app)
    assert client.post("/api/booth/heartbeat", json={}).status_code == 401
    assert client.post("/api/booth/heartbeat", json={},
                       headers={"X-API-Key": "falsch"}).status_code == 401


def test_the_device_key_is_not_taken_from_the_address(app, client):
    """A key in the query string ends up in proxy and access logs; header only.

    Neither the booth nor the sync program send it there (both use the
    X-API-Key header), so an old client gets a clear answer instead of a
    silent 401.
    """
    from app import db
    did, key = _device(app)
    r = client.post(f"/api/booth/heartbeat?api_key={key}", json={"booth_running": True})
    assert r.status_code == 400
    assert "X-API-Key" in r.get_json()["error"]
    assert db.get_device_by_id(did).get("last_heartbeat_at") is None
    assert client.post("/api/booth/heartbeat", json={"booth_running": True},
                       headers={"X-API-Key": key}).status_code == 200


def test_heartbeat_stores_and_answers(app, client):
    from app import db
    did, key = _device(app)
    r = client.post("/api/booth/heartbeat",
                    json={"booth_running": True, "disk_free_gb": 88.0,
                          "disk_total_gb": 256.0, "queue_pending": 2,
                          "agent_version": "2.1.0"},
                    headers={"X-API-Key": key})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True and data["health"] == "green"
    assert data["jobs"] == [] and data["poll_seconds"] == 30

    with app.app_context():
        status = db.get_device_status(did)
        assert status["disk_free_gb"] == 88.0
        assert status["agent_version"] == "2.1.0"
        assert len(db.list_heartbeats(did)) == 1


def test_heartbeat_notifies_once_on_red(app, client, monkeypatch):
    """Bei Rot genau eine Meldung — nicht bei jedem Heartbeat erneut."""
    from app import notifier
    did, key = _device(app)
    calls = []
    monkeypatch.setattr(notifier, "notify_booth_problem",
                        lambda name, reasons=None: calls.append(name))

    bad = {"booth_running": False, "disk_free_gb": 1.0}
    client.post("/api/booth/heartbeat", json=bad, headers={"X-API-Key": key})
    client.post("/api/booth/heartbeat", json=bad, headers={"X-API-Key": key})
    assert len(calls) == 1, "Zweiter roter Heartbeat darf nicht erneut melden"

    # Erholung und erneuter Ausfall -> wieder genau eine Meldung
    client.post("/api/booth/heartbeat",
                json={"booth_running": True, "disk_free_gb": 99.0},
                headers={"X-API-Key": key})
    client.post("/api/booth/heartbeat", json=bad, headers={"X-API-Key": key})
    assert len(calls) == 2


def test_heartbeat_history_is_capped(app, client):
    from app import db
    did, key = _device(app)
    with app.app_context():
        db.HEARTBEAT_KEEP  # existiert
    for i in range(5):
        client.post("/api/booth/heartbeat", json={"disk_free_gb": 50.0 + i},
                    headers={"X-API-Key": key})
    with app.app_context():
        assert len(db.list_heartbeats(did, limit=100)) == 5


# ── Dashboard ────────────────────────────────────────────────

def test_dashboard_renders_and_shows_state(app, client):
    did, key = _device(app, name="Hochzeit Meyer")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    client.post("/api/booth/heartbeat",
                json={"booth_running": False, "disk_free_gb": 1.0},
                headers={"X-API-Key": key})
    html = client.get("/admin/booths").get_data(as_text=True)
    assert "Hochzeit Meyer" in html
    assert "booth-card--red" in html
    assert "Booth-Software läuft nicht" in html


def test_dashboard_requires_login(client):
    assert client.get("/admin/booths").status_code in (301, 302)


def test_dashboard_handles_device_without_heartbeat(app, client):
    _device(app, name="Nie gesehen " + secrets.token_hex(2))
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    r = client.get("/admin/booths")
    assert r.status_code == 200
    assert "noch nie gemeldet" in r.get_data(as_text=True)
