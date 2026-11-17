"""The booth's print queue and failed uploads on the gallery's booth page and over MCP."""
import pytest

from app import booth, db, ecosystem


def _admin(client):
    client.get("/healthz")
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


JOBS = [{"id": 4, "run": "abcd1234", "created": 1763300000.0, "copies": 2, "state": "held",
         "reason": "Papier leer", "slot": 1, "deferred": True},
        {"id": 5, "run": "efgh5678", "created": 1763300060.0, "copies": 1, "state": "queued", "reason": ""}]
FAILED = [{"id": 9, "file": "abcd1234-photo.jpg", "target": "gallery", "kind": "photo", "variant": "",
           "run": "abcd1234", "has_event": True, "error": "HTTP 413: zu groß", "created": 1763300100.0}]


def test_the_heartbeat_keeps_the_entries_and_drops_junk():
    clean = booth.sanitize_payload({"print_jobs": JOBS + [{"id": "x", "state": "held"}, {"id": 7, "state": "done"},
                                                          "<b>"],
                                    "failed_uploads": FAILED + [{"id": 0}]})
    assert [j["id"] for j in clean["print_jobs"]] == [4, 5]
    assert clean["print_jobs"][0]["reason"] == "Papier leer" and clean["print_jobs"][0]["copies"] == 2
    assert [f["id"] for f in clean["failed_uploads"]] == [9]


def test_the_booth_page_lists_both_queues_with_their_buttons(client):
    _admin(client)
    dev = db.create_device(name="Queue-Box")
    db.record_heartbeat(dev["id"], booth.sanitize_payload({"print_jobs": JOBS, "failed_uploads": FAILED,
                                                           "queue_failed": 1}), "yellow", "127.0.0.1")
    body = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert 'id="queues"' in body and "Drucke (2)" in body and "Fehlgeschlagene Uploads (1)" in body
    assert "Papier leer" in body and "HTTP 413" in body
    assert 'data-q-job="discard_prints" data-q-ids="4"' in body and 'data-q-confirm=' in body
    assert 'data-q-job="retry_failed" data-q-ids="9"' in body
    assert "Alle Drucke verwerfen" in body


NEW_FAILED = [{"id": 11, "target": "gallery", "kind": "photo", "variant": "", "run": "abcd1234",
               "has_event": False, "reason": "kein Event zugewiesen", "attempts": 8, "created": 1763300100.0}]
REASONS = [{"reason": "kein Event zugewiesen", "count": 6}, {"reason": "Datei fehlt", "count": 2},
           {"reason": "<script>", "count": "x"}]


def test_the_booth_page_says_why_uploads_failed_in_plain_words(client):
    """A booth since 17.11.2026 sends a reason per entry and the count per reason, no file name."""
    _admin(client)
    clean = booth.sanitize_payload({"failed_uploads": NEW_FAILED, "failed_reasons": REASONS, "queue_failed": 8})
    assert clean["failed_reasons"] == REASONS[:2]
    assert clean["failed_uploads"][0]["reason"] == "kein Event zugewiesen" and clean["failed_uploads"][0]["file"] == ""
    dev = db.create_device(name="Grund-Box")
    db.record_heartbeat(dev["id"], clean, "yellow", "127.0.0.1")
    body = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert "Warum: 6× kein Event zugewiesen · 2× Datei fehlt" in body
    assert "Grund: kein Event zugewiesen" in body and "Durchgang abcd1234 · photo" in body and "8 Versuche" in body


@pytest.mark.parametrize("job", ["discard_prints", "discard_failed_uploads"])
def test_the_page_may_send_the_queue_jobs(client, job):
    _admin(client)
    dev = db.create_device(name=f"Queue-Box {job}")
    client.get(f"/admin/devices/{dev['id']}/sync")
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    r = client.post(f"/admin/devices/{dev['id']}/sync/job", json={"type": job, "payload": {"ids": [4]}},
                    headers={"X-CSRF-Token": token})
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_the_mcp_preview_names_the_prints_it_would_discard(client):
    client.get("/healthz")
    ecosystem.init_schema()
    from app import mcp_more
    dev = db.create_device(name="Queue-Box MCP")
    db.record_heartbeat(dev["id"], booth.sanitize_payload({"print_jobs": JOBS}), "yellow", "127.0.0.1")
    one = mcp_more._discard_preview(dev["id"], ids=[5])
    assert [j["id"] for j in one["print_jobs"]] == [5] and "Diese Drucke" in one["effect"]
    every = mcp_more._discard_preview(dev["id"])
    assert len(every["print_jobs"]) == 2 and "Alle Drucke" in every["effect"]
    with pytest.raises(ValueError):
        mcp_more._print_ids(["x"])
