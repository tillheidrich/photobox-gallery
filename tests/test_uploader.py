"""Tests für den Windows-Sync-Client (windows_uploader/photobox_uploader_v2.py)."""
import importlib.util
import io
import pathlib
import threading
import time

import pytest

_UPLOADER_PATH = pathlib.Path(__file__).resolve().parents[1] / "windows_uploader" / "photobox_uploader_v2.py"


def _load_module():
    import sys
    spec = importlib.util.spec_from_file_location("pb_uploader", _UPLOADER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


up = _load_module()


# ── Pure Logik ────────────────────────────────────────────────
def test_is_transient_status():
    assert up.is_transient_status(500) is True
    assert up.is_transient_status(503) is True
    assert up.is_transient_status(429) is True
    assert up.is_transient_status(400) is False
    assert up.is_transient_status(404) is False


def test_backoff_delay_caps():
    assert up.backoff_delay(1, 2.0, 60.0) == 2.0
    assert up.backoff_delay(2, 2.0, 60.0) == 4.0
    assert up.backoff_delay(10, 2.0, 60.0) == 60.0  # gedeckelt


def test_remote_backup_path():
    from datetime import datetime
    p = up.remote_backup_path("/Fotobox-Backup", "foto.jpg", when=datetime(2026, 7, 28))
    assert p == "/Fotobox-Backup/2026-07/foto.jpg"
    # base ohne führenden Slash
    assert up.remote_backup_path("Backup", "a.png", when=datetime(2026, 1, 5)) == "/Backup/2026-01/a.png"


def test_backup_target_defaults_port():
    assert up.BackupTarget.from_dict({"protocol": "ftps"}).port == 21
    assert up.BackupTarget.from_dict({"protocol": "sftp"}).port == 22
    assert up.BackupTarget.from_dict(None).enabled is False


def test_is_stable(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 100)
    now = time.time()
    # frisch geschrieben -> nicht stabil
    assert up.is_stable(f, min_age=5, now=now) is False
    # 10s alt -> stabil
    assert up.is_stable(f, min_age=5, now=now + 10) is True
    # leere Datei nie stabil
    empty = tmp_path / "e.jpg"; empty.write_bytes(b"")
    assert up.is_stable(empty, min_age=0, now=now + 100) is False


def test_statedb_reset_on_change(tmp_path):
    db = up.StateDB(tmp_path / "state.db")
    db.seen("/x/a.jpg", 100, 111.0)
    db.mark("/x/a.jpg", gallery_done=True, status="done")
    assert db.get("/x/a.jpg")["gallery_done"] == 1
    # Datei wächst (Booth schreibt weiter) -> Status zurückgesetzt
    db.seen("/x/a.jpg", 250, 222.0)
    row = db.get("/x/a.jpg")
    assert row["gallery_done"] == 0 and row["status"] == "pending"


# ── Integration: echter Upload gegen einen laufenden Server ───
def _jpeg(color=(180, 150, 110)):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (600, 800), color).save(b, "JPEG"); b.seek(0)
    return b.read()


def test_integration_upload_reaches_gallery(tmp_path, monkeypatch):
    import os, secrets
    from werkzeug.serving import make_server
    # Über ``monkeypatch``, nicht über ``os.environ`` direkt: sonst zeigt
    # DATA_DIR bis zum Ende des Laufs hierher. Dass das heute niemandem weh
    # tut, liegt allein daran, dass diese Datei alphabetisch die letzte ist.
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    os.environ.setdefault("SECRET_KEY", "itest")
    from app import create_app, db

    app = create_app()
    with app.app_context():
        db.init_db()
        ev = db.create_event(slug="sync-test", name="Sync", upload_token=secrets.token_hex(6))
        con = db.get_db(); con.execute("UPDATE events SET published=1 WHERE id=?", (ev,)); con.commit(); con.close()
        dev = db.create_device(name="Sync-Box"); api_key = dev["api_key"]
        # The old uploader's devices are assigned in the device form
        # ("Zugewiesenes Event"); /api/upload takes only assigned events.
        db.update_device(dev["id"], {"landing_event_id": ev})

    srv = make_server("127.0.0.1", 5099, app)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        watch = tmp_path / "watch"; watch.mkdir()
        (watch / "foto1.jpg").write_bytes(_jpeg())
        (watch / "foto2.jpg").write_bytes(_jpeg((120, 140, 120)))

        cfg = up.Config(
            server_url="http://127.0.0.1:5099",
            api_key=api_key,
            watch_folder=watch,
            event_slug="sync-test",
            min_stable_seconds=0,          # im Test sofort als stabil behandeln
            log_file=tmp_path / "up.log",
        )
        up.run(cfg, once=True)

        with app.app_context():
            media = db.list_media(ev) or []
        assert len(media) == 2, f"erwartet 2 Medien, bekam {len(media)}"
    finally:
        srv.shutdown()
