"""Backups of the data folder: one archive that can be checked and restored.

The archive holds the database (taken with SQLite's backup API, so a running
gallery does not hand out a half-written file), the media folders and a
manifest with a checksum for every file. These tests build their own data
folders under ``tmp_path`` and never touch the suite's shared DATA_DIR.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tarfile
from pathlib import Path

import pytest

from app import backup


def _old_style_data(root: Path) -> Path:
    """A data folder as the production gallery of September 2026 leaves it:
    a database with the old schema and nothing of the new tables."""
    (root / "storage" / "hochzeit" / "web").mkdir(parents=True)
    (root / "storage" / "hochzeit" / "web" / "a.webp").write_bytes(b"webp" * 1000)
    (root / "storage" / "hochzeit" / "orig.jpg").write_bytes(os.urandom(5000))
    (root / "assets" / "branding").mkdir(parents=True)
    (root / "assets" / "branding" / "logo.png").write_bytes(b"\x89PNG" + b"0" * 100)
    (root / "faq").mkdir()
    (root / "faq" / "bild.png").write_bytes(b"faq")
    (root / "cache" / "downloads").mkdir(parents=True)
    (root / "cache" / "downloads" / "1_m.jpg").write_bytes(b"cache" * 100)
    (root / ".secret_key").write_text("k" * 40)
    con = sqlite3.connect(root / "gallery.sqlite3")
    con.executescript(
        """
        CREATE TABLE events (id INTEGER PRIMARY KEY, slug TEXT, name TEXT);
        CREATE TABLE media (id INTEGER PRIMARY KEY, event_id INTEGER, path_original TEXT,
                            FOREIGN KEY(event_id) REFERENCES events(id));
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO events VALUES (1, 'hochzeit', 'Hochzeit');
        INSERT INTO media VALUES (1, 1, 'storage/hochzeit/orig.jpg');
        INSERT INTO settings VALUES ('admin_password_hash', 'pbkdf2:sha256:1$x$y');
        """)
    con.commit()
    con.close()
    return root


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_holds_database_media_and_manifest(tmp_path):
    data = _old_style_data(tmp_path / "data")
    result = backup.create(data_dir=data, out_dir=tmp_path / "out")
    archive = Path(result["path"])
    assert archive.is_file() and archive.suffix == ".tar"
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
        manifest = json.load(tar.extractfile("manifest.json"))
    assert "data/gallery.sqlite3" in names
    assert "data/storage/hochzeit/orig.jpg" in names
    assert "data/assets/branding/logo.png" in names
    assert "data/faq/bild.png" in names
    # Signs share links and sessions when SECRET_KEY is not set: without it
    # every share link breaks after a restore.
    assert "data/.secret_key" in names
    # Derived files are left out and the manifest says so.
    assert not any(n.startswith("data/cache/") for n in names)
    assert any(s["path"] == "cache" for s in manifest["skipped"])
    assert manifest["media_included"] is True
    assert manifest["database"]["tables"] == {"events": 1, "media": 1, "settings": 1}
    entry = next(f for f in manifest["files"] if f["path"] == "storage/hochzeit/orig.jpg")
    assert entry["size"] == 5000
    assert entry["sha256"] == _sha(data / "storage" / "hochzeit" / "orig.jpg")
    assert manifest["confidential"] is True
    assert manifest["app_version"]


def test_backup_does_not_change_an_old_database(tmp_path):
    """The command must work against the old production folder before the
    upgrade: no migration, no new table, not a byte changed."""
    data = _old_style_data(tmp_path / "data")
    before = _sha(data / "gallery.sqlite3")
    backup.create(data_dir=data, out_dir=tmp_path / "out")
    assert _sha(data / "gallery.sqlite3") == before


def test_backup_is_private_and_written_atomically(tmp_path):
    data = _old_style_data(tmp_path / "data")
    out = tmp_path / "out"
    result = backup.create(data_dir=data, out_dir=out)
    mode = stat.S_IMODE(os.stat(result["path"]).st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o700
    assert not list(out.glob("*.part"))
    assert result["confidential"] is True


def test_database_only_backup_leaves_media_out(tmp_path):
    data = _old_style_data(tmp_path / "data")
    result = backup.create(data_dir=data, out_dir=tmp_path / "out", include_media=False)
    with tarfile.open(result["path"]) as tar:
        names = tar.getnames()
        manifest = json.load(tar.extractfile("manifest.json"))
    assert "data/gallery.sqlite3" in names
    assert "data/.secret_key" in names
    assert not any(n.startswith("data/storage/") for n in names)
    assert manifest["media_included"] is False
    assert Path(result["path"]).name.endswith("-db.tar")


def test_backup_folder_inside_data_is_not_backed_up_again(tmp_path):
    data = _old_style_data(tmp_path / "data")
    first = backup.create(data_dir=data)                  # default: DATA_DIR/backups
    assert Path(first["path"]).parent == data / "backups"
    second = backup.create(data_dir=data)
    with tarfile.open(second["path"]) as tar:
        assert not any(n.startswith("data/backups") for n in tar.getnames())


def test_verify_finds_a_changed_file(tmp_path):
    data = _old_style_data(tmp_path / "data")
    result = backup.create(data_dir=data, out_dir=tmp_path / "out")
    assert backup.verify(result["path"])["ok"] is True

    # Rewrite the archive with one byte changed in a media file.
    bad = tmp_path / "bad.tar"
    with tarfile.open(result["path"]) as src, tarfile.open(bad, "w") as dst:
        for member in src.getmembers():
            payload = src.extractfile(member).read() if member.isfile() else None
            if member.name == "data/storage/hochzeit/orig.jpg":
                payload = b"X" + payload[1:]
            import io
            dst.addfile(member, io.BytesIO(payload) if payload is not None else None)
    report = backup.verify(bad)
    assert report["ok"] is False
    assert any("storage/hochzeit/orig.jpg" in p for p in report["problems"])


def test_not_enough_space_fails_before_writing(tmp_path, monkeypatch):
    data = _old_style_data(tmp_path / "data")
    out = tmp_path / "out"

    class Usage:
        total, used, free = 10**9, 10**9 - 1000, 1000

    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: Usage)
    with pytest.raises(backup.BackupError) as err:
        backup.create(data_dir=data, out_dir=out)
    assert "space" in str(err.value).lower()
    assert not list(out.glob("*"))


def test_list_and_prune_keep_the_newest_full_backup(tmp_path):
    data = _old_style_data(tmp_path / "data")
    out = tmp_path / "out"
    full = backup.create(data_dir=data, out_dir=out, stamp="20261101T030000Z")
    for day in range(2, 7):
        backup.create(data_dir=data, out_dir=out, include_media=False, stamp=f"202611{day:02d}T030000Z")
    listed = backup.list_backups(out)
    assert [b["name"] for b in listed][0].startswith("photobox-backup-20261106")
    assert len(listed) == 6

    removed = backup.prune(out, keep=2)
    left = {b["name"] for b in backup.list_backups(out)}
    # Two newest, plus the newest one with media — pruning database-only
    # copies must never take the last copy of the photos with them.
    assert Path(full["path"]).name in left
    assert len(left) == 3
    assert len(removed) == 3


def test_manifest_names_offloaded_originals_without_credentials(tmp_path, monkeypatch):
    data = _old_style_data(tmp_path / "data")
    con = sqlite3.connect(data / "gallery.sqlite3")
    con.execute("ALTER TABLE media ADD COLUMN storage TEXT NOT NULL DEFAULT 'local'")
    con.execute("UPDATE media SET storage='remote'")
    con.commit()
    con.close()
    monkeypatch.setenv("ORIGINALS_BACKEND", "sftp")
    monkeypatch.setenv("ORIGINALS_SFTP_HOST", "nas.example")
    monkeypatch.setenv("ORIGINALS_SFTP_USER", "photobox")
    monkeypatch.setenv("ORIGINALS_SFTP_PASSWORD", "geheim-123")
    monkeypatch.setenv("ORIGINALS_SFTP_PATH", "/volume1/photobox")
    result = backup.create(data_dir=data, out_dir=tmp_path / "out")
    with tarfile.open(result["path"]) as tar:
        raw = tar.extractfile("manifest.json").read().decode()
    manifest = json.loads(raw)
    assert manifest["originals"]["backend"] == "sftp"
    assert manifest["originals"]["remote_media"] == 1
    assert "nas.example" in manifest["originals"]["target"]
    assert "geheim-123" not in raw and "photobox@" not in raw


def test_command_line_create_verify_list(tmp_path, monkeypatch, capsys):
    data = _old_style_data(tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(data))
    assert backup.main(["create", "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "confidential" in out.lower()
    archive = next((tmp_path / "out").glob("*.tar"))
    assert backup.main(["verify", str(archive)]) == 0
    assert backup.main(["list", "--dir", str(tmp_path / "out")]) == 0
    assert archive.name in capsys.readouterr().out


def test_backups_in_the_data_folder_are_never_served(client):
    from app import db

    folder = db.data_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "photobox-backup-20261101T030000Z.tar").write_bytes(b"secret")
    assert client.get("/data/backups/photobox-backup-20261101T030000Z.tar").status_code == 404
    assert client.get("/data/storage/../backups/photobox-backup-20261101T030000Z.tar").status_code == 404
