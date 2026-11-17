"""The operator's traffic light: one look at the phone has to be enough.

Register C3/C4: the booth checked itself and raised operator messages, but the
gallery's light ignored both, and the sync page of a booth that reported every
30 seconds said it had never reported. The light now takes the worst finding
of the self-check and the open messages, says how old the last report is, and
the admin page and the MCP tool read the same function.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app import accounts, booth


def _stamp(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


DOCTOR_WARN = {"worst": "warn", "at": 0, "findings": [
    {"name": "Medien aus dem Profil", "state": "warn", "message": "1 Datei(en) fehlen: logo.png."}]}
DOCTOR_FAIL = {"worst": "fail", "at": 0, "findings": [
    {"name": "Kamera", "state": "fail", "message": "edsdk: nicht verbunden."}]}


def test_sanitize_keeps_the_self_check_and_messages_bounded():
    clean = booth.sanitize_payload({
        "doctor": {"worst": "warn", "at": 5, "evil": 1, "findings": [
            {"name": "A" * 300, "state": "warn", "message": "B" * 900, "hint": "x"},
            {"name": "C", "state": "boom", "message": "unknown state"}] + [
            {"name": f"n{i}", "state": "fail", "message": "m"} for i in range(40)]},
        "notices": [{"stufe": "kritisch", "titel": "Farbband leer", "quelle": "drucker", "at": 1,
                     "text": "T" * 900, "key": "x"}, "nonsense"] * 20,
    })
    d = clean["doctor"]
    assert d["worst"] == "warn" and "evil" not in d
    assert len(d["findings"]) <= 20
    assert all(f["state"] in ("warn", "fail") for f in d["findings"])
    assert len(d["findings"][0]["name"]) <= 80 and len(d["findings"][0]["message"]) <= 300
    assert len(clean["notices"]) <= 10
    n = clean["notices"][0]
    assert n["titel"] == "Farbband leer" and n["stufe"] == "kritisch" and len(n["text"]) <= 300


def test_a_warning_of_the_self_check_turns_the_light_yellow_with_its_words():
    v = booth.light({"last_heartbeat_at": _stamp(0.5)}, {"disk_free_gb": 80.0, "doctor": DOCTOR_WARN})
    assert v["health"] == "yellow"
    assert any("logo.png" in f["text"] and f["level"] == "yellow" for f in v["findings"])


def test_a_failure_of_the_self_check_and_a_critical_message_are_red():
    v = booth.light({"last_heartbeat_at": _stamp(0.5)}, {"disk_free_gb": 80.0, "doctor": DOCTOR_FAIL})
    assert v["health"] == "red" and any("Kamera" in f["text"] for f in v["findings"])
    v = booth.light({"last_heartbeat_at": _stamp(0.5)}, {"disk_free_gb": 80.0, "notices": [
        {"stufe": "kritisch", "titel": "Kein Drucker verfügbar", "quelle": "drucker", "at": 1}]})
    assert v["health"] == "red" and any("Kein Drucker" in f["text"] for f in v["findings"])
    v = booth.light({"last_heartbeat_at": _stamp(0.5)}, {"disk_free_gb": 80.0, "notices": [
        {"stufe": "warnung", "titel": "Noch 20 Blatt", "quelle": "drucker", "at": 1}]})
    assert v["health"] == "yellow"


def test_the_age_reads_like_a_sentence():
    # The stored stamp has whole seconds, so 40 s ago may read 40 or 41.
    assert booth.light({"last_heartbeat_at": _stamp(40 / 60)}, {})["age_text"] in ("vor 40 s", "vor 41 s")
    v = booth.light({"last_heartbeat_at": _stamp(12.2)}, {}, event_running=True)
    assert v["age_text"] == "seit 12 min nichts gehört"
    assert booth.light({"last_heartbeat_at": None}, {})["age_text"] == "noch nie gemeldet"


def test_silence_is_yellow_after_three_and_red_after_ten_minutes_during_an_event():
    assert booth.light({"last_heartbeat_at": _stamp(2.5)}, {}, event_running=True)["health"] == "green"
    assert booth.light({"last_heartbeat_at": _stamp(4)}, {}, event_running=True)["health"] == "yellow"
    assert booth.light({"last_heartbeat_at": _stamp(11)}, {}, event_running=True)["health"] == "red"


def test_a_box_in_the_cupboard_is_not_an_alarm():
    v = booth.light({"last_heartbeat_at": _stamp(60 * 30)}, {}, event_running=False)
    assert v["health"] == "unknown"


def test_paper_and_print_queue_are_in_the_light():
    v = booth.light({"last_heartbeat_at": _stamp(0.2)}, {
        "disk_free_gb": 80.0, "prints_pending": 3, "prints_held": 1, "queue_pending": 4, "queue_failed": 0,
        "sheets": [{"slot": 1, "label": "SELPHY", "quelle": "gezählt", "rest": 42, "gesamt": 108}]})
    assert v["print_queue"] == {"pending": 3, "held": 1}
    assert v["upload_queue"] == {"pending": 4, "failed": 0}
    assert "42" in v["paper_text"]


def test_an_event_dated_today_is_running(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    eid = db.create_event(slug="heute", name="Heute", upload_token="t-heute")
    db.update_event(eid, {"date": date.today().isoformat()})
    dev = db.create_device(name="Heute-Box")
    ecosystem.save_device_profile(dev["id"], {}, eid)
    assert booth.event_running(db.get_device_by_id(dev["id"])) is True
    old = db.create_event(slug="frueher", name="Früher", upload_token="t-frueher")
    db.update_event(old, {"date": (date.today() - timedelta(days=20)).isoformat()})
    ecosystem.save_device_profile(dev["id"], {}, old)
    assert booth.event_running(db.get_device_by_id(dev["id"])) is False
    ecosystem.save_device_profile(dev["id"], {}, None)
    assert booth.event_running(db.get_device_by_id(dev["id"])) is False


# ── The pages and the assistant say the same ────────────────

@pytest.fixture()
def admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["admin_epoch"] = accounts.session_epoch()
        s["csrf_token"] = "test-token"
    return client


def _reporting_box(client, name="Abend-Box"):
    from app import db
    dev = db.create_device(name=name)
    r = client.post("/api/v2/booth/sync", json={"status": {
        "booth_running": True, "camera_state": "ready", "disk_free_gb": 80.0, "disk_total_gb": 200.0,
        "queue_pending": 2, "queue_failed": 0, "prints_pending": 1, "prints_held": 0,
        "sheets": [{"slot": 1, "label": "SELPHY", "quelle": "gezählt", "rest": 37, "gesamt": 108}],
        "doctor": DOCTOR_WARN,
        "notices": [{"stufe": "warnung", "titel": "Noch 37 Blatt", "quelle": "drucker", "at": 1}],
    }}, headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200
    return dev


def test_the_booth_page_shows_the_findings_paper_and_queue(admin):
    dev = _reporting_box(admin)
    html = admin.get("/admin/booths").get_data(as_text=True)
    assert "logo.png" in html and "Noch 37 Blatt" in html
    assert "37" in html and "Druck-Warteschlange" in html
    assert 'name="viewport"' in html            # read on a phone at the venue
    assert f'data-light="{dev["id"]}:yellow"' in html


def test_the_assistant_reads_the_same_light(admin):
    import json
    from app import booth as booth_mod, db, mcp as mcp_mod
    dev = _reporting_box(admin, "MCP-Ampel")
    key = mcp_mod.new_key()
    r = admin.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "list_booths", "arguments": {}}},
                   headers={"X-API-Key": key})
    payload = r.get_json()["result"]
    assert not payload.get("isError")
    boxen = json.loads(payload["content"][0]["text"])
    mine = next(b for b in boxen if b["id"] == dev["id"])
    expected = booth_mod.light(db.get_device_by_id(dev["id"]))
    assert mine["health"] == expected["health"] == "yellow"
    assert mine["findings"] == expected["findings"]
    # A second may pass between the two calls ("vor 0 s" / "vor 1 s").
    assert abs(mine["age_seconds"] - expected["age_seconds"]) <= 2
    assert mine["age_text"].startswith("vor ") and expected["age_text"].startswith("vor ")


def test_the_sync_page_of_a_reporting_booth_does_not_say_it_never_reported(admin):
    dev = _reporting_box(admin, "Sync-Box")
    html = admin.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert "noch nichts gemeldet" not in html
    assert "meldet sich" in html
