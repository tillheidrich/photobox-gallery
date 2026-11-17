"""Photobox Sync target passwords never sit in the gallery in clear (17.11.2026).

Before, the sync page sent FTP/SFTP passwords to the separate program with a
``set_sync_config`` job, and ``device_jobs.payload_json`` kept them in clear
for good. Now the program publishes a public key (its private key stays on
that PC), the request handler seals every typed password before anything is
written, the payload is emptied when the program answers, on expiry and on
replacement, pages show dots, a version without a key gets no password at
all, and a one-time clean-up blanks what older versions left behind.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GEHEIM = "Dummy-Zielpasswort-91c2"     # an obvious test value, searched for everywhere below


@pytest.fixture()
def admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


@pytest.fixture()
def sync_app(tmp_path):
    sys.path.insert(0, str(ROOT / "sync"))
    from photobox_sync.standalone import SyncApp
    app = SyncApp(base=tmp_path / "pc", start_threads=False)
    app.save_config({"targets": [{"type": "sftp", "name": "nas", "host": "nas.example.org", "user": "box",
                                  "password": "alt-dummy"}]})
    return app


def _device_with_report(report):
    from app import db, ecosystem
    dev = db.create_device(name="Sync-PC-" + secrets.token_hex(2))
    ecosystem.save_sync_report(dev["id"], report)
    return dev


def _rows(dev_id):
    from app import db
    con = db.get_db()
    rows = [dict(r) for r in con.execute("SELECT * FROM device_jobs WHERE device_id=? AND type='set_sync_config' "
                                         "ORDER BY id", (dev_id,))]
    con.close()
    return rows


def _send(admin, dev_id, targets):
    return admin.post(f"/admin/devices/{dev_id}/sync/job", json={"type": "set_sync_config",
                                                                 "payload": {"targets": targets}},
                      headers={"X-CSRF-Token": "test-token"})


def test_a_typed_password_is_sealed_and_the_pc_opens_it(admin, sync_app, client):
    rep = sync_app.report()
    assert set(rep["sealing"]) == {"v", "public_key", "key_id"}
    assert "alt-dummy" not in json.dumps(rep)
    assert not (Path(sync_app.data) / "keys" / "sync_targets.key").read_text() in json.dumps(rep)
    dev = _device_with_report(rep)
    r = _send(admin, dev["id"], [{"type": "sftp", "name": "nas", "host": "nas.example.org", "user": "box",
                                  "password": GEHEIM}])
    assert r.status_code == 200, r.get_json()
    (row,) = _rows(dev["id"])
    assert GEHEIM not in row["payload_json"]
    payload = json.loads(row["payload_json"])
    assert payload["targets"][0]["password_sealed"] and "password" not in payload["targets"][0]
    # The real program takes the job as the agent would and stores the opened password.
    sync_app.agent.job_handlers["set_sync_config"](payload)
    assert sync_app.config["targets"][0]["password"] == GEHEIM
    # Pages and MCP (job_result / recent_jobs) show dots, never ciphertext.
    from app import ecosystem
    shown = ecosystem.job_result(dev["id"], row["id"])
    assert payload["targets"][0]["password_sealed"] not in shown["payload_json"]
    assert json.loads(shown["payload_json"])["targets"][0]["password_sealed"] == "••••"
    assert all(payload["targets"][0]["password_sealed"] not in j["payload_json"]
               for j in ecosystem.recent_jobs(dev["id"]))
    # Answered: the payload is emptied.
    ecosystem.finish_jobs(dev["id"], [{"id": row["id"], "ok": True, "result": "übernommen"}])
    assert _rows(dev["id"])[0]["payload_json"] == "{}"


def test_an_empty_field_or_the_dots_keep_the_stored_password(admin, sync_app):
    dev = _device_with_report(sync_app.report())
    for leer in ("", "••••"):
        assert _send(admin, dev["id"], [{"type": "sftp", "name": "nas", "host": "h2", "password": leer}]).status_code == 200
    payload = json.loads(_rows(dev["id"])[-1]["payload_json"])
    assert "password" not in payload["targets"][0] and "password_sealed" not in payload["targets"][0]
    sync_app.agent.job_handlers["set_sync_config"](payload)
    assert sync_app.config["targets"][0]["password"] == "alt-dummy"


def test_a_version_without_a_key_gets_no_password_never_in_clear(admin):
    dev = _device_with_report({"kind": "sync", "version": "0.1.0", "targets": []})
    r = _send(admin, dev["id"], [{"type": "ftp", "name": "web", "host": "h", "password": GEHEIM}])
    assert r.status_code == 409 and "Fassung zu alt — Passwort am PC eintragen" in r.get_json()["error"]
    assert _rows(dev["id"]) == []
    # Without a password the settings still go.
    assert _send(admin, dev["id"], [{"type": "ftp", "name": "web", "host": "h"}]).status_code == 200


def test_no_other_path_can_store_a_clear_password(admin):
    from app import ecosystem
    dev = _device_with_report({"kind": "sync"})
    with pytest.raises(ValueError):
        ecosystem.add_job(dev["id"], "set_sync_config", {"targets": [{"name": "x", "password": GEHEIM}]})
    assert _rows(dev["id"]) == []


def test_expiry_and_replacement_empty_the_payload(admin, sync_app):
    from app import db, ecosystem
    dev = _device_with_report(sync_app.report())
    _send(admin, dev["id"], [{"type": "sftp", "name": "nas", "host": "h", "password": GEHEIM}])
    _send(admin, dev["id"], [{"type": "sftp", "name": "nas", "host": "h", "password": GEHEIM + "2"}])
    alt, neu = _rows(dev["id"])
    assert alt["payload_json"] == "{}" and alt["expired"] == 1, "replaced by the newer one"
    con = db.get_db()
    con.execute("UPDATE device_jobs SET created_at=datetime('now', '-11 minutes') WHERE id=?", (neu["id"],))
    con.commit()
    con.close()
    ecosystem.expire_jobs(dev["id"])
    assert _rows(dev["id"])[-1]["payload_json"] == "{}"


def test_the_page_says_when_the_version_is_too_old(admin, sync_app):
    alt = {k: v for k, v in sync_app.report().items() if k != "sealing"}
    dev = _device_with_report(alt)
    html = admin.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert "const SEALING = false" in html and "Fassung zu alt — Passwort am PC eintragen" in html


# ── The one-time clean-up ───────────────────────────────────

def _prepared_db(path: Path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE device_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER, type TEXT, "
                "payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT, delivered_at TEXT, done_at TEXT, "
                "ok INTEGER, result TEXT, expired INTEGER NOT NULL DEFAULT 0)")
    rows = [
        ("set_sync_config", {"targets": [{"name": "nas", "host": "h", "user": "u", "password": GEHEIM},
                                         {"name": "web", "host": "w", "password": ""}], "folders": [{"path": "C:/x"}]}),
        ("set_sync_config", {"paused": True}),
        ("set_sync_config", {"targets": [{"name": "nas", "password_sealed": "QUJD", "key_id": "abc"}]}),
        ("check_targets", {"note": "password"}),
    ]
    for t, p in rows:
        con.execute("INSERT INTO device_jobs(device_id, type, payload_json, done_at, ok) VALUES (1,?,?,datetime('now'),1)",
                    (t, json.dumps(p)))
    con.commit()
    return con


def test_the_clean_up_blanks_old_clear_passwords_once_and_logs_no_secret(tmp_path, caplog):
    from app import sync_secrets
    con = _prepared_db(tmp_path / "alt.sqlite")
    with caplog.at_level(logging.WARNING):
        assert sync_secrets.blank_clear_passwords(con) == 1
    con.commit()
    assert sync_secrets.blank_clear_passwords(con) == 0, "safe to run twice"
    rows = {r["id"]: json.loads(r["payload_json"]) for r in con.execute("SELECT * FROM device_jobs")}
    assert rows[1]["targets"][0] == {"name": "nas", "host": "h", "user": "u", "password": ""}
    assert rows[1]["folders"] == [{"path": "C:/x"}]
    assert rows[2] == {"paused": True} and rows[3]["targets"][0]["password_sealed"] == "QUJD"
    assert rows[4] == {"note": "password"}
    assert GEHEIM not in caplog.text and "1 alten Sync-Aufträgen" in caplog.text
    con.close()
    # secure_delete: the old bytes are overwritten, not left in a free page of the file.
    assert GEHEIM.encode() not in (tmp_path / "alt.sqlite").read_bytes()


def test_the_clean_up_runs_with_the_schema_at_start(admin):
    from app import db, ecosystem, sync_secrets
    dev = _device_with_report({"kind": "sync"})
    con = db.get_db()
    con.execute("INSERT INTO device_jobs(device_id, type, payload_json) VALUES (?,?,?)",
                (dev["id"], "set_sync_config", json.dumps({"targets": [{"name": "n", "password": GEHEIM}]})))
    con.commit()
    con.close()
    ecosystem._eingerichtet.discard(str(db.db_path()))
    ecosystem.init_schema()
    assert GEHEIM not in json.dumps(_rows(dev["id"]))
    assert sync_secrets.blank_clear_passwords(db.get_db()) == 0
