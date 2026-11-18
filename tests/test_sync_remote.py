"""Fernwartung des Sync-Programms: Bericht, Knöpfe, Einstellungen, Ordnersuche.

Der Weg ist immer derselbe: Der PC meldet sich, die Galerie legt einen Auftrag
hin, der PC holt ihn beim nächsten Melden ab und schickt die Antwort mit. Hier
wird genau dieser Weg geprüft — mit dem echten Sync-Programm am anderen Ende.
"""
import json
import secrets

import pytest


@pytest.fixture()
def admin_client(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _device(client, name="Sync-PC"):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    return db.create_device(name=name + "-" + secrets.token_hex(2))


REPORT = {
    "kind": "sync", "version": "0.1.0", "device_name": "Fotobox",
    "queue": {"pending": 3, "done": 12, "failed": 1},
    "jobs": [{"name": "Drucke", "path": "C:/x/Prints", "kind": "print", "enabled": True,
              "targets": ["nas"], "exists": False}],
    "targets": [{"name": "nas", "type": "sftp", "host": "nas.local", "user": "box",
                 "path": "/fotos", "has_password": True, "key_file": ""}],
    "target_names": ["gallery", "nas"], "upload_to_gallery": True,
    "last_files": [{"name": "IMG_1.jpg", "kind": "print", "at": 1}],
    "last_upload": {"file": "IMG_1.jpg", "target": "nas", "at": 1},
    "autostart": {"supported": True, "enabled": True, "detail": "läuft bei jeder Anmeldung"},
    "port": 8766, "log": ["07:00 INFO gestartet"], "booth_running": True, "session": None,
}


def test_the_report_survives_the_round_trip(client, admin_client):
    dev = _device(client)
    r = client.post("/api/v2/booth/sync", json={"status": {"disk_free_gb": 40}, "sync": REPORT},
                    headers={"X-API-Key": dev["api_key"]})
    assert r.status_code == 200

    page = admin_client.get(f"/admin/devices/{dev['id']}/sync")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    for shown in ("Drucke", "nas.local", "läuft bei jeder Anmeldung", "gestartet"):
        assert shown in body, shown
    # Der Bericht trägt nur, ob ein Passwort gesetzt ist — nie das Passwort selbst
    assert '"password":' not in body

    state = admin_client.get(f"/admin/devices/{dev['id']}/sync/state").get_json()
    assert state["report"]["queue"]["pending"] == 3 and state["report"]["updated_at"]


def test_a_device_without_a_report_gets_an_explanation_not_an_error(admin_client, client):
    dev = _device(client)
    body = admin_client.get(f"/admin/devices/{dev['id']}/sync").get_data(as_text=True)
    assert "Noch kein Bericht" in body


def test_a_button_becomes_a_job_and_the_answer_comes_back(client, admin_client):
    dev = _device(client)
    h = {"X-API-Key": dev["api_key"]}
    made = admin_client.post(f"/admin/devices/{dev['id']}/sync/job", headers={"X-CSRF-Token": "test-token"},
                             json={"type": "check_targets"}).get_json()
    assert made["ok"]
    job_id = made["job_id"]

    # Solange etwas offen ist, soll sich der PC schnell wieder melden
    resp = client.post("/api/v2/booth/sync", json={"status": {}}, headers=h).get_json()
    assert resp["poll_seconds"] == 5
    assert [j["type"] for j in resp["jobs"]] == ["check_targets"]

    answer = json.dumps([{"name": "nas", "ok": True, "detail": "angemeldet"}])
    client.post("/api/v2/booth/sync", headers=h,
                json={"status": {}, "job_results": [{"id": job_id, "ok": True, "result": answer}]})
    got = admin_client.get(f"/admin/devices/{dev['id']}/sync/job/{job_id}").get_json()
    assert got["done"] and got["success"] and json.loads(got["result"])[0]["name"] == "nas"

    # Nichts mehr offen: zurück auf den ruhigen Takt
    assert client.post("/api/v2/booth/sync", json={"status": {}}, headers=h).get_json()["poll_seconds"] == 30


def test_a_folder_listing_is_not_cut_in_half(client, admin_client):
    """Eine Ordnerliste ist länger als ein Satz — früher waren nach 300 Zeichen Schluss."""
    dev = _device(client)
    h = {"X-API-Key": dev["api_key"]}
    jid = admin_client.post(f"/admin/devices/{dev['id']}/sync/job", headers={"X-CSRF-Token": "test-token"},
                            json={"type": "browse_remote", "payload": {"target": "nas", "path": "/"}}).get_json()["job_id"]
    client.post("/api/v2/booth/sync", json={"status": {}}, headers=h)
    listing = json.dumps({"target": "nas", "path": "/", "parent": None,
                          "dirs": [f"Ordner-{i:02d}" for i in range(40)], "files": [], "more_files": 0})
    assert len(listing) > 300
    client.post("/api/v2/booth/sync", headers=h,
                json={"status": {}, "job_results": [{"id": jid, "ok": True, "result": listing}]})
    got = admin_client.get(f"/admin/devices/{dev['id']}/sync/job/{jid}").get_json()
    assert len(json.loads(got["result"])["dirs"]) == 40


def test_only_the_listed_jobs_may_be_sent(admin_client, client):
    dev = _device(client)
    r = admin_client.post(f"/admin/devices/{dev['id']}/sync/job", headers={"X-CSRF-Token": "test-token"},
                          json={"type": "run_shell", "payload": {"cmd": "format c:"}})
    assert r.status_code == 400
    assert not admin_client.get(f"/admin/devices/{dev['id']}/sync/state").get_json()["jobs_waiting"]


def test_a_nonsense_report_does_not_wipe_the_last_good_one(client, admin_client):
    from app import ecosystem

    dev = _device(client)
    h = {"X-API-Key": dev["api_key"]}
    client.post("/api/v2/booth/sync", json={"status": {}, "sync": REPORT}, headers=h)
    for junk in ("kaputt", [], {}, None):
        client.post("/api/v2/booth/sync", json={"status": {}, "sync": junk}, headers=h)
    assert ecosystem.sync_report(dev["id"])["queue"]["pending"] == 3


def test_the_remote_page_needs_a_login(client):
    dev = _device(client)
    for path in (f"/admin/devices/{dev['id']}/sync", f"/admin/devices/{dev['id']}/sync/state"):
        r = client.get(path)
        assert r.status_code in (302, 401, 403), path


def test_the_real_sync_program_answers_the_jobs(tmp_path):
    """Kein Attrappen-Test: das echte Programm bekommt die Aufträge und antwortet."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "sync"))
    from photobox_sync.standalone import SyncApp

    app = SyncApp(base=tmp_path / "data", start_threads=False)
    app.save_config({"server": {"url": "", "api_key": ""}, "upload_to_gallery": False,
                     "targets": [{"type": "folder", "name": "ablage", "path": str(tmp_path / "ablage")},
                                 {"type": "ftp", "name": "web", "host": "h", "user": "u",
                                  "password": "streng-geheim", "key_file": "C:/keys/id_ed25519"}],
                     "folders": [{"path": str(tmp_path), "name": "Alles"}]})
    (tmp_path / "ablage" / "Hochzeit").mkdir(parents=True)

    report = app.report()
    assert report["kind"] == "sync" and report["target_names"] == ["ablage", "web"]
    assert report["jobs"][0]["name"] == "Alles" and report["jobs"][0]["exists"]
    text = json.dumps(report)
    assert "streng-geheim" not in text and "C:/keys" not in text     # Zugangsdaten bleiben auf dem PC
    assert report["targets"][1]["has_password"] and report["targets"][1]["key_file"] == "id_ed25519"

    handlers = app.agent.job_handlers
    checked = {c["name"]: c for c in json.loads(handlers["check_targets"]({}))}
    assert checked["ablage"]["ok"] and checked["web"]["ok"] is False

    listing = json.loads(handlers["browse_remote"]({"target": "ablage", "path": str(tmp_path / "ablage")}))
    assert "Hochzeit" in listing["dirs"]

    handlers["set_sync_config"]({"folders": [{"path": str(tmp_path), "name": "Umbenannt"}]})
    assert app.config["folders"][0]["name"] == "Umbenannt"


def test_remote_settings_never_blank_a_password(tmp_path):
    """Die Ferne sieht keine Passwörter — also darf sie auch keine löschen."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "sync"))
    from photobox_sync.standalone import SyncApp

    app = SyncApp(base=tmp_path / "data", start_threads=False)
    app.save_config({"targets": [{"type": "ftp", "name": "nas", "host": "h", "user": "u", "password": "geheim"}]})
    app.agent.job_handlers["set_sync_config"]({"targets": [{"type": "ftp", "name": "nas", "host": "h2", "user": "u"}]})
    t = app.config["targets"][0]
    assert t["host"] == "h2" and t["password"] == "geheim"
    # Ein neues Passwort ersetzt das alte sehr wohl
    app.agent.job_handlers["set_sync_config"]({"targets": [{"type": "ftp", "name": "nas", "host": "h2",
                                                            "user": "u", "password": "neu"}]})
    assert app.config["targets"][0]["password"] == "neu"


def test_errors_from_the_event_pc_show_up_in_the_gallery_log(client, admin_client, tmp_path):
    """Warum das zählt: Wer nicht am Event-PC steht, sieht sonst gar nichts."""
    import logging
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "sync"))
    from photobox_sync.standalone import SyncApp

    dev = _device(client)
    app = SyncApp(base=tmp_path / "data", start_threads=False)
    logging.getLogger("photobox.sync").warning("Ziel nas nicht erreichbar")
    logging.getLogger("photobox.sync").error("Nur noch 1.2 GB frei")
    for _ in range(4):
        logging.getLogger("photobox.sync").warning("Ziel nas nicht erreichbar")   # wird gefaltet

    entries = app.agent.logs()
    assert len(entries) == 2 and "(5×)" in entries[0]["message"]

    client.post("/api/v2/booth/sync", json={"status": {}, "logs": entries},
                headers={"X-API-Key": dev["api_key"]})

    page = admin_client.get(f"/admin/devices/{dev['id']}/logs").get_data(as_text=True)
    assert "nas nicht erreichbar" in page and "1.2 GB frei" in page
    nur_fehler = admin_client.get(f"/admin/devices/{dev['id']}/logs?level=error").get_data(as_text=True)
    assert "1.2 GB frei" in nur_fehler and "nas nicht erreichbar" not in nur_fehler

    from app import ecosystem
    assert ecosystem.device_log_counts(dev["id"])["error"] == 1
    assert app.agent.logs() == []          # abgeholt heißt abgeholt, nichts doppelt
