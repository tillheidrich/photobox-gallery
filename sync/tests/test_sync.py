import time
from pathlib import Path

from fastapi.testclient import TestClient

from photobox_sync.outbox import Outbox
from photobox_sync.standalone import SyncApp, create_app
from photobox_sync.watcher import Watcher, default_kind


def test_watcher_waits_for_stable_file_and_ignores_old(tmp_path):
    folder = tmp_path / "dslrBooth"; folder.mkdir()
    old = folder / "old.jpg"; old.write_bytes(b"x")
    seen = []
    cfg = [{"path": str(folder), "kind": "auto", "since": time.time() + 1}]
    w = Watcher(tmp_path / "w.sqlite", folders=lambda: cfg, on_file=lambda p, u, k, f=None: seen.append((p.name, k)),
                stable_seconds=0.2, interval=0.1)
    time.sleep(1.1)
    new = folder / "Prints" / "IMG_1.jpg"; new.parent.mkdir(); new.write_bytes(b"y")
    clip = folder / "clip.mp4"; clip.write_bytes(b"z")
    w.scan_once()                      # erster Blick: noch nicht stabil
    assert seen == []
    time.sleep(0.3)
    w.scan_once()
    assert ("IMG_1.jpg", default_kind(new)) in seen and ("clip.mp4", "video") in seen
    assert all(n != "old.jpg" for n, _ in seen)
    w.scan_once()
    assert len(seen) == 2              # kein Doppel-Upload


def test_classify_rules_and_forced():
    f = {"kind": "auto", "rules": [{"match": "*/prints/*", "kind": "print"}]}
    assert Watcher.classify(Path("C:/x/Prints/a.jpg"), f) == "print"
    assert Watcher.classify(Path("C:/x/a.gif"), {"kind": "auto"}) == "gif"
    assert Watcher.classify(Path("C:/x/a.jpg"), {"kind": "original"}) == "original"


def test_standalone_queues_files_per_target_and_session(tmp_path):
    app = SyncApp(base=tmp_path / "data", start_threads=False)
    app.save_config({"server": {"url": "https://g.example", "api_key": "k"},
                     "targets": [{"type": "sftp", "name": "nas", "host": "nas", "user": "u", "password": "p"}]})
    app.trigger("session_start")
    img = tmp_path / "a.jpg"; img.write_bytes(b"jpg")
    app.on_file(img, "uid1", "photo")
    counts = app.outbox.counts()
    assert counts["pending"] == 2
    assert app.last_files[0]["name"] == "a.jpg"


def test_api_masks_secrets_and_pin(tmp_path):
    app = SyncApp(base=tmp_path / "data", start_threads=False)
    app.save_config({"server": {"url": "https://g.example", "api_key": "secret-key-1234"}, "pin": "9999",
                     "targets": [{"type": "ftps", "name": "picdrop", "host": "h", "user": "u", "password": "pw"}]})
    c = TestClient(create_app(app))
    assert c.get("/api/config").status_code == 401
    cfg = c.get("/api/config", headers={"X-Pin": "9999"}).json()
    assert "secret" not in str(cfg) and cfg["server"]["api_key"].endswith("1234") and cfg["targets"][0]["password"] == "••••"
    # Maskierte Werte zurückschicken darf Secrets nicht überschreiben
    c.post("/api/config", headers={"X-Pin": "9999"}, json=cfg)
    assert app.config["server"]["api_key"] == "secret-key-1234" and app.config["targets"][0]["password"] == "pw"
    st = c.get("/api/status").json()
    assert st["pin_required"] and "picdrop" in st["targets"]
    assert c.get("/trigger?event_type=session_start").json()["session"]["uid"]
    assert "Photobox Sync" in c.get("/").text


def test_local_api_rejects_foreign_host_and_origin(tmp_path):
    app = SyncApp(base=tmp_path / "data", start_threads=False)
    c = TestClient(create_app(app))
    assert c.get("/api/status", headers={"host": "evil.example"}).status_code == 403
    assert c.post("/api/config", json={"device_name": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/config", json={"device_name": "ok"}).status_code == 200


def test_each_job_uploads_only_to_its_own_targets(tmp_path):
    """dslrBooth legt Originale und Drucke getrennt ab — die Ziele auch."""
    app = SyncApp(base=tmp_path / "data", start_threads=False)
    app.save_config({"server": {"url": "https://g.example", "api_key": "k"},
                     "targets": [{"type": "ftp", "name": "picdrop", "host": "h", "user": "u", "password": "p"},
                                 {"type": "sftp", "name": "nas", "host": "n", "user": "u", "password": "p"}]})
    originals = {"path": "C:/x/Originals", "name": "Originale", "targets": ["picdrop"]}
    prints = {"path": "C:/x/Prints", "name": "Drucke", "targets": ["nas", "gallery"]}

    assert app.targets_for(originals) == ["picdrop"]
    assert app.targets_for(prints) == ["gallery", "nas"]
    assert app.targets_for({"path": "C:/x/All"}) == ["gallery", "picdrop", "nas"]   # leer = alle
    assert app.targets_for({"path": "C:/x/Typo", "targets": ["tippfehler"]}) == []

    img = tmp_path / "a.jpg"; img.write_bytes(b"jpg")
    app.on_file(img, "uid1", "photo", originals)
    assert [r["target"] for r in app.outbox.due(10)] == ["picdrop"]


def test_a_paused_job_stops_without_being_deleted(tmp_path):
    folder = tmp_path / "Prints"; folder.mkdir()
    cfg = [{"path": str(folder), "kind": "print", "enabled": False, "since": 0}]
    seen = []
    w = Watcher(tmp_path / "w.sqlite", folders=lambda: cfg, on_file=lambda p, u, k, f=None: seen.append(p.name),
                stable_seconds=0, interval=0.1)
    (folder / "IMG_1.jpg").write_bytes(b"x")
    time.sleep(0.1)
    assert w.scan_once() == 0 and seen == []
    cfg[0]["enabled"] = True
    assert w.scan_once() == 1 and seen == ["IMG_1.jpg"]


def test_it_runs_without_a_gallery_at_all(tmp_path):
    """Notfall-Betrieb: kein Server eingetragen, nur eigene FTP-Ziele."""
    app = SyncApp(base=tmp_path / "data", start_threads=False)
    app.save_config({"server": {"url": "", "api_key": ""}, "upload_to_gallery": False,
                     "targets": [{"type": "ftp", "name": "webspace", "host": "h", "user": "u", "password": "p"}]})
    assert app.agent.target_names() == ["webspace"]
    img = tmp_path / "a.jpg"; img.write_bytes(b"jpg")
    app.on_file(img, "uid1", "photo", {"path": "C:/x"})
    assert [r["target"] for r in app.outbox.due(10)] == ["webspace"]
    assert app.agent.sync_once() is False           # ohne Galerie kein Kontaktversuch, aber kein Absturz
    c = TestClient(create_app(app))
    assert c.get("/api/status").json()["targets"] == ["webspace"]
