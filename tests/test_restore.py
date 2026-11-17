"""Restoring a backup: into an empty folder, proven afterwards, and the
gallery runs against the result."""
from __future__ import annotations

import io
import os
import sqlite3
import tarfile
from pathlib import Path

import pytest

from app import backup


def _gallery_with_one_event(monkeypatch, data: Path):
    """A real gallery in its own data folder: one event, one photo, through the app."""
    from PIL import Image

    monkeypatch.setenv("DATA_DIR", str(data))
    from app import create_app, db, media

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    assert client.get("/healthz").status_code == 200       # schema
    event_id = db.create_event(slug="restore-probe", name="Restore probe",
                               upload_token="restore-probe-token")
    db.update_event(event_id, {"published": 1})
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "JPEG")
    buf.seek(0)
    from werkzeug.datastructures import FileStorage
    saved = media.save_upload(event_id, FileStorage(stream=buf, filename="probe.jpg",
                                                    content_type="image/jpeg"))
    return event_id, saved


def test_restore_into_an_empty_folder_brings_the_gallery_back(tmp_path, monkeypatch):
    source = tmp_path / "source"
    event_id, saved = _gallery_with_one_event(monkeypatch, source)
    archive = backup.create(data_dir=source, out_dir=tmp_path / "out")["path"]

    target = tmp_path / "restored"
    report = backup.restore(archive, target)
    assert report["ok"] is True, report["problems"]
    assert report["database"]["integrity"] == "ok"
    assert (target / "gallery.sqlite3").exists()
    assert (target / ".secret_key").exists() or os.environ.get("SECRET_KEY")

    # The app, started against the restored folder, shows the event and its photo.
    monkeypatch.setenv("DATA_DIR", str(target))
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    page = client.get("/gallery/restore-probe")
    assert page.status_code == 200
    rel = saved.get("path_thumb") or saved.get("path_webp")
    assert rel and rel in page.get_data(as_text=True)
    picture = client.get(f"/data/{rel}")
    assert picture.status_code == 200
    assert picture.data == (source / rel).read_bytes()


def test_restore_refuses_a_folder_that_is_not_empty(tmp_path):
    from tests.test_backup import _old_style_data

    data = _old_style_data(tmp_path / "data")
    archive = backup.create(data_dir=data, out_dir=tmp_path / "out")["path"]
    target = tmp_path / "busy"
    target.mkdir()
    (target / "something.txt").write_text("keep me")
    with pytest.raises(backup.BackupError):
        backup.restore(archive, target)
    assert (target / "something.txt").read_text() == "keep me"


def test_forced_restore_backs_up_the_target_first(tmp_path):
    from tests.test_backup import _old_style_data

    data = _old_style_data(tmp_path / "data")
    archive = backup.create(data_dir=data, out_dir=tmp_path / "out")["path"]
    target = _old_style_data(tmp_path / "live")
    (target / "storage" / "hochzeit" / "only-here.jpg").write_bytes(b"newer photo")
    report = backup.restore(archive, target, force=True)
    assert report["ok"] is True
    assert not (target / "storage" / "hochzeit" / "only-here.jpg").exists()
    safety = Path(report["safety_backup"])
    assert safety.parent == target / "backups"
    with tarfile.open(safety) as tar:
        assert "data/storage/hochzeit/only-here.jpg" in tar.getnames()


def test_a_broken_archive_restores_nothing(tmp_path):
    from tests.test_backup import _old_style_data

    data = _old_style_data(tmp_path / "data")
    archive = Path(backup.create(data_dir=data, out_dir=tmp_path / "out")["path"])
    raw = bytearray(archive.read_bytes())
    pos = raw.find(b"webpwebp")
    raw[pos] = ord("X")
    archive.write_bytes(bytes(raw))
    target = tmp_path / "restored"
    with pytest.raises(backup.BackupError) as err:
        backup.restore(archive, target)
    assert "verification" in str(err.value)
    assert not target.exists() or not any(target.iterdir())


def test_restore_rejects_paths_that_leave_the_target(tmp_path):
    archive = tmp_path / "evil.tar"
    manifest = (b'{"kind": "photobox-gallery-backup", "format": 1, "files": '
                b'[{"path": "../escape.txt", "size": 4, "sha256": '
                b'"88d4266fd4e6338d13b845fcf289579d209c897823b9217da3e161936f031589"}]}')
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("data/../escape.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"abcd"))
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    target = tmp_path / "restored"
    try:
        backup.restore(archive, target)
    except backup.BackupError:
        pass
    assert not (tmp_path / "escape.txt").exists()


def test_restore_reports_foreign_key_problems_without_failing(tmp_path):
    from tests.test_backup import _old_style_data

    data = _old_style_data(tmp_path / "data")
    con = sqlite3.connect(data / "gallery.sqlite3")
    con.execute("INSERT INTO media VALUES (2, 99, 'storage/none.jpg')")   # event 99 does not exist
    con.commit()
    con.close()
    archive = backup.create(data_dir=data, out_dir=tmp_path / "out")["path"]
    report = backup.restore(archive, tmp_path / "restored")
    assert report["ok"] is True
    assert any("foreign_key_check" in w and "media 1" in w for w in report["warnings"])


def test_command_line_restore(tmp_path, capsys):
    from tests.test_backup import _old_style_data

    data = _old_style_data(tmp_path / "data")
    archive = backup.create(data_dir=data, out_dir=tmp_path / "out")["path"]
    assert backup.main(["restore", archive, "--into", str(tmp_path / "r")]) == 0
    assert "Restored" in capsys.readouterr().out
    assert backup.main(["restore", archive, "--into", str(tmp_path / "r")]) == 2
