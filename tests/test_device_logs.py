"""Device logs: what a booth reports, kept for a while and readable remotely.

The point of the feature is a box that misbehaves at a venue nobody can reach.
So the write path has to survive junk input, and the retention has to happen
without anyone running a cleanup job.
"""
import secrets
from datetime import datetime, timedelta

from app import ecosystem


def _device(app, name=None):
    from app import db
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        return int(db.create_device(name=name or ("box-" + secrets.token_hex(3)))["id"])


def _add(app, device_id, entries):
    with app.app_context():
        return ecosystem.add_device_logs(device_id, entries)


def _entry(level="error", code="printer.offline", message="Drucker antwortet nicht", **kw):
    return {"level": level, "code": code, "message": message, **kw}


# ── writing ──────────────────────────────────────────────────

def test_entries_are_stored_and_counted(app):
    did = _device(app)
    assert _add(app, did, [_entry(), _entry(level="warning"), _entry(level="info")]) == 3
    with app.app_context():
        assert len(ecosystem.device_logs(did)) == 3


def test_unknown_level_becomes_info(app):
    did = _device(app)
    _add(app, did, [_entry(level="katastrophe"), _entry(level=None), _entry(level="ERROR")])
    with app.app_context():
        levels = sorted(e["level"] for e in ecosystem.device_logs(did))
    assert levels == ["error", "info", "info"]


def test_fields_are_length_capped(app):
    did = _device(app)
    _add(app, did, [{"level": "error", "code": "x" * 500, "message": "y" * 5000,
                     "context": {"blob": "z" * 9000}}])
    with app.app_context():
        row = ecosystem.device_logs(did)[0]
    assert len(row["code"]) == 64
    assert len(row["message"]) == 2000
    assert len(row["context_json"]) == 4000


def test_context_survives_as_json_text(app):
    did = _device(app)
    _add(app, did, [_entry(context={"queue": 12, "printer": "DS-RX1"})])
    with app.app_context():
        import json
        assert json.loads(ecosystem.device_logs(did)[0]["context_json"])["queue"] == 12


def test_context_may_also_be_a_string(app):
    did = _device(app)
    _add(app, did, [_entry(context="traceback: boom")])
    with app.app_context():
        assert ecosystem.device_logs(did)[0]["context_json"] == "traceback: boom"


def test_empty_entries_are_skipped(app):
    did = _device(app)
    assert _add(app, did, [{"level": "error"}, {}, "kaputt", None, _entry()]) == 1


def test_at_most_200_entries_per_call(app):
    did = _device(app)
    assert _add(app, did, [_entry(message=f"m{i}") for i in range(250)]) == 200


def test_occurred_at_is_taken_over_and_falls_back(app):
    did = _device(app)
    stamp = (datetime.now() - timedelta(hours=2)).replace(microsecond=0)
    _add(app, did, [_entry(message="mit Zeit", occurred_at=stamp.isoformat()),
                    _entry(message="ohne Zeit", occurred_at="völlig kaputt")])
    with app.app_context():
        rows = {e["message"]: e["occurred_at"] for e in ecosystem.device_logs(did)}
    assert rows["mit Zeit"] == stamp.isoformat(sep=" ", timespec="seconds")
    assert rows["ohne Zeit"][:4] == str(datetime.now().year)


def test_entry_older_than_the_retention_is_not_kept(app):
    """Eine uralte Meldung fällt schon beim Schreiben unter die Aufbewahrung."""
    did = _device(app)
    old = (datetime.now() - timedelta(days=ecosystem.LOG_RETENTION_DAYS + 5)).isoformat()
    _add(app, did, [_entry(message="steinalt", occurred_at=old)])
    with app.app_context():
        assert ecosystem.device_logs(did) == []


def test_old_entries_are_dropped_on_write(app):
    from app import db
    did = _device(app)
    _add(app, did, [_entry(message="alt")])
    with app.app_context():
        con = db.get_db()
        con.execute("UPDATE device_logs SET occurred_at=? WHERE device_id=?",
                    ((datetime.now() - timedelta(days=45)).isoformat(sep=" ", timespec="seconds"), did))
        con.commit(); con.close()
    _add(app, did, [_entry(message="neu")])
    with app.app_context():
        messages = [e["message"] for e in ecosystem.device_logs(did)]
    assert messages == ["neu"]


def test_per_device_cap_keeps_the_newest(app, monkeypatch):
    did = _device(app)
    monkeypatch.setattr(ecosystem, "LOG_MAX_PER_DEVICE", 5)
    _add(app, did, [_entry(message=f"m{i}") for i in range(10)])
    _add(app, did, [_entry(message="letzte")])
    with app.app_context():
        rows = ecosystem.device_logs(did)
    assert len(rows) == 5
    assert rows[0]["message"] == "letzte"


def test_logs_of_other_devices_stay_separate(app):
    a, b = _device(app), _device(app)
    _add(app, a, [_entry(message="von A")])
    _add(app, b, [_entry(message="von B")])
    with app.app_context():
        assert [e["message"] for e in ecosystem.device_logs(a)] == ["von A"]


# ── reading ──────────────────────────────────────────────────

def test_filters_by_level_period_and_search(app):
    did = _device(app)
    _add(app, did, [_entry(level="error", code="printer.offline", message="Drucker antwortet nicht"),
                    _entry(level="warning", code="disk.low", message="Wenig Platz"),
                    _entry(level="info", code="sync.ok", message="Alles gut")])
    with app.app_context():
        assert len(ecosystem.device_logs(did, level="error")) == 1
        assert len(ecosystem.device_logs(did, since_hours=24)) == 3
        assert len(ecosystem.device_logs(did, query="drucker")) == 1
        assert len(ecosystem.device_logs(did, query="disk.low")) == 1
        assert len(ecosystem.device_logs(did, limit=2)) == 2


def test_old_entries_are_outside_the_period(app):
    from app import db
    did = _device(app)
    _add(app, did, [_entry(message="gestern")])
    with app.app_context():
        con = db.get_db()
        con.execute("UPDATE device_logs SET occurred_at=? WHERE device_id=?",
                    ((datetime.now() - timedelta(days=3)).isoformat(sep=" ", timespec="seconds"), did))
        con.commit(); con.close()
        assert ecosystem.device_logs(did, since_hours=24) == []
        assert len(ecosystem.device_logs(did, since_hours=24 * 7)) == 1
        assert len(ecosystem.device_logs(did)) == 1            # ohne Zeitraum: alles


def test_counts_per_level(app):
    did = _device(app)
    _add(app, did, [_entry(), _entry(), _entry(level="warning"), _entry(level="info")])
    with app.app_context():
        counts = ecosystem.device_log_counts(did, since_hours=24)
    assert counts == {"error": 2, "warning": 1, "info": 1, "total": 4}


def test_counts_by_device_in_one_query(app):
    a, b = _device(app), _device(app)
    _add(app, a, [_entry(), _entry(level="warning")])
    _add(app, b, [_entry(level="info")])
    with app.app_context():
        counts = ecosystem.log_counts_by_device(since_hours=24)
    assert counts[a]["error"] == 1 and counts[a]["warning"] == 1
    assert counts[b]["error"] == 0 and counts[b]["total"] == 1


def _clear_logs(app):
    """Die Testdatenbank ist geteilt — für Übersichten den Tisch abräumen."""
    from app import db
    with app.app_context():
        con = db.get_db()
        con.execute("DELETE FROM device_logs")
        con.commit(); con.close()


def test_recent_error_devices_lists_only_real_errors(app):
    _clear_logs(app)
    quiet, loud = _device(app, "leise"), _device(app, "laut")
    _add(app, quiet, [_entry(level="info", message="läuft")])
    _add(app, loud, [_entry(message="Kamera weg"), _entry(message="Kamera weg"),
                     _entry(level="warning", message="knapp")])
    with app.app_context():
        rows = ecosystem.recent_error_devices(since_hours=24)
    assert [r["device_id"] for r in rows] == [loud]
    assert rows[0]["errors"] == 2 and rows[0]["warnings"] == 1
    assert rows[0]["last_message"] == "Kamera weg"
    assert rows[0]["name"] == "laut"


# ── admin view ───────────────────────────────────────────────

def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def test_log_page_shows_entries_and_context(app, client):
    did = _device(app)
    _add(app, did, [_entry(message="Drucker antwortet nicht", context={"printer": "DS-RX1"})])
    _admin(client)
    html = client.get(f"/admin/devices/{did}/logs").get_data(as_text=True)
    assert "Drucker antwortet nicht" in html
    assert "printer.offline" in html
    assert "DS-RX1" in html and "<details" in html


def test_log_page_filters_by_level(app, client):
    did = _device(app)
    _add(app, did, [_entry(message="ein Fehler"), _entry(level="info", code="sync.ok", message="nur Info")])
    _admin(client)
    html = client.get(f"/admin/devices/{did}/logs?level=error").get_data(as_text=True)
    assert "ein Fehler" in html and "nur Info" not in html


def test_log_page_search(app, client):
    did = _device(app)
    _add(app, did, [_entry(message="Kamera antwortet nicht", code="camera.lost"),
                    _entry(message="Wenig Platz", code="disk.low")])
    _admin(client)
    html = client.get(f"/admin/devices/{did}/logs?q=kamera&range=all").get_data(as_text=True)
    assert "Kamera antwortet nicht" in html and "Wenig Platz" not in html


def test_log_page_needs_admin(app, client):
    did = _device(app)
    r = client.get(f"/admin/devices/{did}/logs")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def test_log_page_404_for_unknown_device(app, client):
    _device(app)
    _admin(client)
    assert client.get("/admin/devices/999999/logs").status_code == 404


def test_plain_text_export(app, client):
    did = _device(app)
    _add(app, did, [_entry(message="Drucker antwortet nicht")])
    _admin(client)
    r = client.get(f"/admin/devices/{did}/logs.txt?range=all")
    assert r.status_code == 200
    assert r.mimetype == "text/plain"
    assert "Drucker antwortet nicht" in r.get_data(as_text=True)


def test_booth_overview_shows_the_error_column(app, client):
    did = _device(app, "Box Eingang")
    _add(app, did, [_entry(message="Kamera weg")])
    _admin(client)
    html = client.get("/admin/booths").get_data(as_text=True)
    assert "Fehler 24 h" in html
    assert f"/admin/devices/{did}/logs" in html


def test_dashboard_card_is_quiet_without_errors(app, client):
    _clear_logs(app)
    _device(app)
    _admin(client)
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "Fehler der letzten 24 Stunden" in html
    assert "Keine Fehlermeldungen" in html


def test_dashboard_card_names_the_device_on_errors(app, client):
    _clear_logs(app)
    did = _device(app, "Box Terrasse")
    _add(app, did, [_entry(message="Kamera weg")])
    _admin(client)
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "Box Terrasse" in html
    assert "Keine Fehlermeldungen" not in html
