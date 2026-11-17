"""Remote jobs arrive or expire — they never vanish and never wait forever.

Until 11.11.2026 a job counted as delivered the moment it was put into a sync
answer. If that answer got lost, or the booth restarted before reporting, the
job was gone — and ``jobs_waiting`` counted it forever, so the booth polled
every 5 s instead of every 30 s for the rest of its life.

Now: a delivered job without a result is delivered again after 60 seconds; a
job with no result 10 minutes after it was created expires (done, not ok,
"no answer from the booth"). The admin shows four states: issued, delivered,
done, expired.
"""
import ast
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _device(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    eid = db.create_event(name="Jobs", slug="jobs-" + secrets.token_hex(3), upload_token=secrets.token_hex(8))
    _made = db.create_device(name="Box Jobs")
    did = _made["id"]
    ecosystem.save_device_profile(did, {"modes": ["photo"]}, eid)
    return {**db.get_device_by_id(did), "api_key": _made["api_key"]}


def _sync(client, dev, **body):
    return client.post("/api/v2/booth/sync", json=body, headers={"X-API-Key": dev["api_key"]}).get_json()


def _age(job_id, column, seconds):
    from app import db
    con = db.get_db()
    con.execute(f"UPDATE device_jobs SET {column}=datetime('now', ?) WHERE id=?", (f"-{int(seconds)} seconds", job_id))
    con.commit()
    con.close()


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"


def test_a_lost_answer_brings_the_job_again_after_a_minute(client):
    from app import ecosystem
    dev = _device(client)
    jid = ecosystem.add_job(dev["id"], "test_print")
    assert [j["id"] for j in _sync(client, dev)["jobs"]] == [jid]     # this answer gets lost
    assert _sync(client, dev)["jobs"] == []                           # not yet: maybe it is running
    _age(jid, "delivered_at", 61)
    assert [j["id"] for j in _sync(client, dev)["jobs"]] == [jid]


def test_a_reported_job_is_not_delivered_again(client):
    from app import ecosystem
    dev = _device(client)
    jid = ecosystem.add_job(dev["id"], "doctor")
    _sync(client, dev)
    _sync(client, dev, job_results=[{"id": jid, "ok": True, "result": "fine"}])
    _age(jid, "delivered_at", 300)
    assert _sync(client, dev)["jobs"] == []
    assert ecosystem.job_result(dev["id"], jid)["state"] == "done"


def test_a_booth_that_is_off_lets_the_job_expire_after_ten_minutes(client):
    from app import ecosystem
    dev = _device(client)
    jid = ecosystem.add_job(dev["id"], "install_update")
    assert ecosystem.jobs_waiting(dev["id"]) == 1
    _age(jid, "created_at", 601)
    assert ecosystem.jobs_waiting(dev["id"]) == 0
    row = ecosystem.job_result(dev["id"], jid)
    assert row["state"] == "expired" and row["done_at"] and row["ok"] == 0
    answer = _sync(client, dev)
    assert answer["jobs"] == [] and answer["poll_seconds"] == 30       # the 5-second polling ends


def test_a_delivered_job_without_answer_expires_too(client):
    from app import ecosystem
    dev = _device(client)
    jid = ecosystem.add_job(dev["id"], "test_capture")
    _sync(client, dev)
    _age(jid, "created_at", 700)
    _age(jid, "delivered_at", 650)
    assert _sync(client, dev)["jobs"] == []
    assert ecosystem.job_result(dev["id"], jid)["state"] == "expired"


def test_a_late_answer_still_counts(client):
    """The booth was slow, not dead: its real result beats our guess."""
    from app import ecosystem
    dev = _device(client)
    jid = ecosystem.add_job(dev["id"], "fetch_update")
    _sync(client, dev)
    _age(jid, "created_at", 700)
    assert ecosystem.jobs_waiting(dev["id"]) == 0
    _sync(client, dev, job_results=[{"id": jid, "ok": True, "result": "downloaded"}])
    row = ecosystem.job_result(dev["id"], jid)
    assert row["state"] == "done" and row["ok"] == 1 and row["result"] == "downloaded"


def test_the_admin_shows_four_states(client):
    from app import ecosystem
    dev = _device(client)
    # The sync page shows jobs once the device has sent a report.
    from tests.test_sync_remote import REPORT
    _sync(client, dev, sync=REPORT)
    issued = ecosystem.add_job(dev["id"], "lock")
    delivered = ecosystem.add_job(dev["id"], "unlock")
    done = ecosystem.add_job(dev["id"], "doctor")
    expired = ecosystem.add_job(dev["id"], "test_print")
    from app import db
    con = db.get_db()
    con.execute("UPDATE device_jobs SET delivered_at=datetime('now') WHERE id IN (?, ?)", (delivered, done))
    con.execute("UPDATE device_jobs SET done_at=datetime('now'), ok=1, result='fine' WHERE id=?", (done,))
    con.commit()
    con.close()
    _age(expired, "created_at", 900)
    _admin(client)
    states = {}
    for jid in (issued, delivered, done, expired):
        res = client.get(f"/admin/devices/{dev['id']}/sync/job/{jid}").get_json()
        states[jid] = res["state"]
    assert states == {issued: "issued", delivered: "delivered", done: "done", expired: "expired"}
    exp = client.get(f"/admin/devices/{dev['id']}/sync/job/{expired}").get_json()
    assert exp["done"] and not exp["success"] and exp["result"] == "keine Antwort von der Box"
    recent = client.get(f"/admin/devices/{dev['id']}/sync/state").get_json()["recent"]
    assert {r["state"] for r in recent} >= {"issued", "delivered", "done", "expired"}
    page = client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert "abgelaufen" in page and "keine Antwort von der Box" in page
    profile = client.get(f"/admin/devices/{dev['id']}/booth")
    assert profile.status_code == 200
    text = profile.get_data(as_text=True)
    assert "abgelaufen" in text and "zugestellt" in text


def test_the_expired_text_is_translated():
    from app.i18n import de, en
    assert de.STRINGS["booth.job.expired_result"] == "keine Antwort von der Box"
    assert en.STRINGS["booth.job.expired_result"] == "no answer from the booth"
    for state in ("issued", "delivered", "done", "expired"):
        assert de.STRINGS[f"booth.job.state.{state}"] and en.STRINGS[f"booth.job.state.{state}"]


def _allowed_jobs_of_the_agent():
    """Read the agent's list as text: the suites do not share an import path in CI."""
    tree = ast.parse((ROOT / "sync" / "photobox_sync" / "agent.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ALLOWED_JOBS" for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise AssertionError("ALLOWED_JOBS not found in agent.py")


def test_the_gallery_and_the_agent_know_the_same_jobs():
    """One list, not two: a job the gallery cannot issue is dead code at the booth,
    a job the booth does not know comes back as "not supported"."""
    from app import ecosystem
    assert _allowed_jobs_of_the_agent() == set(ecosystem.JOB_TYPES)
