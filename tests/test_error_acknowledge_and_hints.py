"""Dashboard (17.11.2026): errors can be marked as read, are grouped by cause,
and the maintenance and backup cards say what to do and can back up now.

Seen on the real gallery: "37 Fehler von 1 Box — bitte ansehen." with no way
to say "seen", although 34 of them were one repeated message.
"""
import json
import re
import secrets
from datetime import datetime, timedelta

import pytest

from app import backup, db, ecosystem, maintenance


@pytest.fixture(autouse=True)
def schema(client, tmp_path, monkeypatch):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
        # The app fixture is shared: other tests leave log lines and boxes behind.
        con = db.get_db()
        con.execute("DELETE FROM device_logs")
        con.execute("UPDATE devices SET logs_seen_at=NULL")
        con.commit()
        con.close()
    monkeypatch.setenv("PHOTOBOX_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("ORIGINALS_BACKEND", raising=False)
    yield


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"


def _box(app, name="Box Eins"):
    with app.app_context():
        return int(db.create_device(name=name)["id"])


def _ago(minutes):
    return (datetime.now() - timedelta(minutes=minutes)).isoformat(sep=" ", timespec="seconds")


def _log(app, did, message, minutes_ago, level="error"):
    with app.app_context():
        ecosystem.add_device_logs(did, [{"level": level, "code": "x", "message": message,
                                         "occurred_at": _ago(minutes_ago)}])


def _fill(app, did):
    """34 camera lines half a minute apart (one incident) and 3 printer lines 20 minutes apart."""
    for i in range(34):
        _log(app, did, f"Kamera nicht bereit (Versuch {i})", 60 - i * 0.5)
    for m in (50, 30, 10):
        _log(app, did, "Drucker leer", m)


def _post(client, path, **data):
    return client.post(path, data={"csrf_token": "tok", **data})


# ── grouping ────────────────────────────────────────────────────────────────

def test_repeated_messages_are_one_incident_with_a_count(app):
    did = _box(app)
    _fill(app, did)
    with app.app_context():
        groups = ecosystem.error_groups(did, 24)
        assert len(ecosystem.device_logs(did)) == 37            # display only: no line dropped
    cam, printer = groups
    assert cam["count"] == 34 and cam["incidents"] == 1
    assert printer["count"] == 3 and printer["incidents"] == 3


def test_the_dashboard_card_groups_by_reason(app, client):
    did = _box(app)
    _fill(app, did)
    _admin(client)
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert "34× Kamera nicht bereit" in page and "3× Drucker leer" in page
    assert "4 Vorfälle aus 37 Fehlermeldungen bei 1 Box" in page


# ── acknowledging ───────────────────────────────────────────────────────────

def test_acknowledging_needs_the_form_token_and_an_admin(app, client):
    did = _box(app)
    _fill(app, did)
    assert client.post(f"/admin/devices/{did}/logs/ack", data={"csrf_token": "tok"}).status_code in (302, 400)
    with app.app_context():
        assert ecosystem.logs_acknowledged_at(did) == ""
    _admin(client)
    assert client.post(f"/admin/devices/{did}/logs/ack", data={"csrf_token": "falsch"}).status_code == 400
    with app.app_context():
        assert ecosystem.logs_acknowledged_at(did) == ""


def test_after_acknowledging_the_card_counts_only_new_errors(app, client):
    did = _box(app)
    _fill(app, did)
    _admin(client)
    assert _post(client, f"/admin/devices/{did}/logs/ack", back="dashboard").status_code == 302
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert "keine neuen Fehler" in page and "frühere anzeigen" in page
    assert "bitte ansehen" not in page
    with app.app_context():
        assert len(ecosystem.device_logs(did)) == 37            # the lines are still there
    # A new error after the mark counts again, and only it.
    _log(app, did, "Platte voll", -2)
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert "1 Vorfälle aus 1 Fehlermeldungen" in page and "keine neuen Fehler" not in page


def test_the_booth_list_follows_the_acknowledgement(app, client):
    did = _box(app)
    _fill(app, did)
    _admin(client)
    with app.app_context():
        assert ecosystem.log_counts_by_device(24)[did]["error"] == 37
    _post(client, f"/admin/devices/{did}/logs/ack", back="booths")
    with app.app_context():
        assert ecosystem.log_counts_by_device(24).get(did, {}).get("error", 0) == 0
        assert ecosystem.device_log_counts(did, 24)["error"] == 0


def test_the_log_page_has_the_button_the_summary_and_all_lines(app, client):
    did = _box(app)
    _fill(app, did)
    _admin(client)
    html = client.get(f"/admin/devices/{did}/logs?range=all").get_data(as_text=True)
    assert "Als gelesen markieren" in html and "34×" in html
    assert html.count("Kamera nicht bereit (Versuch") >= 34
    _post(client, f"/admin/devices/{did}/logs/ack", back="logs")
    html = client.get(f"/admin/devices/{did}/logs?range=all").get_data(as_text=True)
    assert "Als gelesen markiert:" in html and html.count("Kamera nicht bereit (Versuch") >= 34


def test_acknowledging_is_per_box(app, client):
    a, b = _box(app, "A"), _box(app, "B")
    _log(app, a, "Fehler A", 5)
    _log(app, b, "Fehler B", 5)
    with app.app_context():
        ecosystem.acknowledge_logs(a)
        assert [r["device_id"] for r in ecosystem.recent_error_devices(24)] == [b]


def test_the_assistant_reads_the_groups_and_the_mark(app, client):
    did = _box(app)
    _fill(app, did)
    from app import mcp as mcp_mod
    key = mcp_mod.new_key()

    def call():
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "booth_diagnostics", "arguments": {"booth_id": did}}},
                        headers={"X-API-Key": key})
        return json.loads(r.get_json()["result"]["content"][0]["text"])

    data = call()
    assert data["error_groups_24h"][0]["count"] == 34 and data["errors_acknowledged_at"] is None
    with app.app_context():
        ecosystem.acknowledge_logs(did)
    data = call()
    assert data["error_groups_24h"] == [] and data["errors_acknowledged_at"]


# ── wording on the maintenance and backup cards ─────────────────────────────

def test_the_stale_cards_say_what_to_do(client, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_MAINTENANCE", "off")
    _admin(client)
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert "maintenance-todo" in page and "docs/MAINTENANCE.md" in page
    assert "backup-todo" in page and 'id="backup-now"' in page


def test_a_fresh_backup_and_a_running_maintenance_show_no_todo(client):
    _admin(client)
    backup.scheduled()
    from app import db as dbm
    dbm.set_setting(maintenance.LAST_RUN_KEY, maintenance._iso(maintenance._utcnow()))
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert "backup-todo" not in page and "maintenance-todo" not in page


# ── backup on demand ────────────────────────────────────────────────────────

def test_the_button_backs_up_although_the_self_start_is_off(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_MAINTENANCE", "off")
    with app.app_context():
        assert maintenance.start_backup_now(app, background=False) == "started"
        state = maintenance.backup_now_status()
    assert state["state"] == "done" and state["name"].startswith("photobox-backup-")
    assert len(backup.list_backups(tmp_path / "backups")) == 1


def test_a_second_press_while_one_runs_does_nothing(app, tmp_path):
    with app.app_context():
        db.set_setting(maintenance.BACKUP_NOW_KEY, json.dumps(
            {"state": "running", "at": maintenance._iso(maintenance._utcnow())}))
        assert maintenance.start_backup_now(app, background=False) == "running"
    assert backup.list_backups(tmp_path / "backups") == []


def test_a_dead_running_mark_does_not_block_forever(app):
    old = maintenance._iso(maintenance._utcnow() - timedelta(hours=2))
    with app.app_context():
        db.set_setting(maintenance.BACKUP_NOW_KEY, json.dumps({"state": "running", "at": old}))
        assert maintenance.start_backup_now(app, background=False) == "started"


def test_backups_switched_off_stay_off(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_BACKUP", "off")
    with app.app_context():
        assert maintenance.start_backup_now(app, background=False) == "off"
    assert backup.list_backups(tmp_path / "backups") == []


def test_the_button_route_is_admin_only_with_the_token(app, client, monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance, "start_backup_now", lambda a, **k: calls.append(1) or "started")
    assert client.post("/admin/backup/now", data={"csrf_token": "tok"}).status_code in (302, 400)
    assert calls == []
    _admin(client)
    assert client.post("/admin/backup/now", data={"csrf_token": "falsch"}).status_code == 400
    assert calls == []
    r = _post(client, "/admin/backup/now")
    assert r.status_code == 302 and calls == [1]
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert "Die Sicherung läuft im Hintergrund" in page


def test_the_card_shows_the_result(app, client):
    _admin(client)
    with app.app_context():
        maintenance.start_backup_now(app, background=False)
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert "Sicherung angelegt: photobox-backup-" in page
