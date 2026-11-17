"""The daily backup inside the maintenance run, its dashboard card, the
optional copy off the server, and a safer NAS offload.

Until 12.11.2026 production had no backup, and the offload deleted an
original after nothing more than a size comparison on the target.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
from pathlib import Path

import pytest

from app import backup, db, ecosystem, maintenance, storage


@pytest.fixture(autouse=True)
def schema(client, tmp_path, monkeypatch):
    client.get("/healthz")
    with client.application.app_context():
        db.init_db()
        ecosystem.init_schema()
    monkeypatch.setenv("PHOTOBOX_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("ORIGINALS_BACKEND", raising=False)
    yield


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"


# ── Daily run ────────────────────────────────────────────────────────────────

def test_the_maintenance_run_writes_a_backup(tmp_path):
    report = maintenance.run()
    assert report["backup"]["name"].startswith("photobox-backup-")
    assert len(backup.list_backups(tmp_path / "backups")) == 1
    assert "Sicherung" in maintenance.as_text(report)


def test_the_backup_runs_before_offload_and_retention(monkeypatch):
    order = []
    monkeypatch.setattr(backup, "scheduled", lambda **k: order.append("backup") or {"name": "x"})
    monkeypatch.setattr(storage, "offload_pending", lambda limit=50: order.append("offload") or 0)
    maintenance.run()
    assert order[:2] == ["backup", "offload"]


def test_a_dry_run_writes_no_backup(tmp_path):
    maintenance.run(dry_run=True)
    assert backup.list_backups(tmp_path / "backups") == []


def test_it_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_BACKUP", "off")
    report = maintenance.run()
    assert report["backup"] == "off"
    assert backup.list_backups(tmp_path / "backups") == []


def test_large_media_go_into_the_backup_weekly_only(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_BACKUP_MEDIA_DAILY_MB", "0")       # every gallery counts as large
    first = backup.scheduled()
    assert first["media_included"] is True                          # no full backup yet
    second = backup.scheduled()
    assert second["media_included"] is False                        # database and settings only
    # A full backup older than the weekly interval: the next one has media again.
    for b in backup.list_backups(tmp_path / "backups"):
        Path(b["path"]).unlink()
    old = (backup._utcnow() - dt.timedelta(days=8)).strftime("%Y%m%dT%H%M%SZ")
    backup.create(out_dir=tmp_path / "backups", stamp=old)
    assert backup.scheduled()["media_included"] is True


def test_small_media_go_into_every_backup(monkeypatch):
    monkeypatch.setenv("PHOTOBOX_BACKUP_MEDIA_DAILY_MB", "100000")
    backup.scheduled()
    assert backup.scheduled()["media_included"] is True


def test_old_backups_are_pruned_to_the_configured_number(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBOX_BACKUP_KEEP", "2")
    for day in range(1, 6):
        backup.create(out_dir=tmp_path / "backups", include_media=False, stamp=f"202610{day:02d}T030000Z")
    backup.scheduled()
    assert len(backup.list_backups(tmp_path / "backups")) == 2


# ── Copy off the server ─────────────────────────────────────────────────────

class FakeStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.put_calls = []
        self.deleted = []

    def put(self, local, rel):
        if self.fail:
            raise storage.RemoteUnavailable("NAS asleep")
        self.put_calls.append((Path(local), rel))
        return "/photobox/" + rel

    def verify(self, local, remote):
        return None

    def delete(self, remote):
        self.deleted.append(remote)


def test_with_an_offload_target_the_backup_is_copied_there(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(storage, "store", lambda: fake)
    report = backup.scheduled()
    assert report["offsite"] == "ok"
    assert fake.put_calls and fake.put_calls[0][1] == "backups/" + report["name"]
    side = Path(report["path"] + ".offsite.json")
    assert json.loads(side.read_text())["remote"] == "/photobox/backups/" + report["name"]


def test_an_unreachable_target_does_not_stop_the_local_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "store", lambda: FakeStore(fail=True))
    report = maintenance.run()
    assert len(backup.list_backups(tmp_path / "backups")) == 1
    assert "NAS asleep" in report["backup_offsite_error"]
    assert "backup_error" not in report


def test_pruned_backups_are_removed_from_the_target_too(tmp_path, monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(storage, "store", lambda: fake)
    monkeypatch.setenv("PHOTOBOX_BACKUP_KEEP", "1")
    backup.create(out_dir=tmp_path / "backups", stamp="20261001T030000Z", include_media=False)
    side = tmp_path / "backups" / "photobox-backup-20261001T030000Z-db.tar.offsite.json"
    side.write_text(json.dumps({"remote": "/photobox/backups/old.tar"}))
    backup.scheduled()
    con = db.get_db()
    queued = [r[0] for r in con.execute("SELECT path FROM remote_deletions")]
    con.close()
    assert "/photobox/backups/old.tar" in queued


# ── Dashboard ───────────────────────────────────────────────────────────────

def test_the_dashboard_warns_without_a_backup(client):
    _admin(client)
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert 'id="backup-status"' in page and "backup-stale" in page


def test_the_dashboard_shows_a_fresh_backup(client):
    _admin(client)
    backup.scheduled()
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert 'id="backup-status"' in page and "backup-stale" not in page


def test_a_backup_older_than_48_hours_is_a_warning(tmp_path):
    old = (backup._utcnow() - dt.timedelta(hours=49)).strftime("%Y%m%dT%H%M%SZ")
    backup.create(out_dir=tmp_path / "backups", stamp=old, include_media=False)
    status = backup.status()
    assert status["stale"] is True and status["last"]["age_hours"] >= 49
    fresh = backup.create(out_dir=tmp_path / "backups", include_media=False)
    status = backup.status()
    assert status["stale"] is False and status["last"]["name"] == fresh["name"]


# ── Offload: verified before the local copy goes ───────────────────────────

def _original(tmp_name="orig.jpg", payload=None):
    """A booth original old enough to be offloaded."""
    k = secrets.token_hex(4)
    eid = db.create_event(name=f"Offload {k}", slug=f"offload-{k}", upload_token=f"o-{k}")
    rel = f"storage/offload-{k}/{tmp_name}"
    f = db.data_dir() / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(payload or secrets.token_bytes(300_000))
    con = db.get_db()
    cur = con.execute(
        "INSERT INTO media(event_id, kind, filename, path_original, path_webp, share_token, storage, variant, created_at) "
        "VALUES (?, 'photo', ?, ?, ?, ?, 'local', 'original', datetime('now', '-2 days'))",
        (eid, tmp_name, rel, rel + ".webp", secrets.token_urlsafe(12)))
    con.commit()
    mid = cur.lastrowid
    con.close()
    return mid, f


class RecordingStore(FakeStore):
    def __init__(self, verify_error=None):
        super().__init__()
        self.verify_error = verify_error

    def verify(self, local, remote):
        if self.verify_error:
            raise self.verify_error


def _media(mid):
    con = db.get_db()
    row = dict(con.execute("SELECT storage, remote_path FROM media WHERE id=?", (mid,)).fetchone())
    con.close()
    return row


def test_a_failed_check_after_upload_keeps_the_local_original(monkeypatch):
    monkeypatch.setenv("PHOTOBOX_OFFLOAD_REQUIRE_BACKUP", "off")
    mid, f = _original()
    fake = RecordingStore(verify_error=storage.RemoteMismatch("last block differs"))
    monkeypatch.setattr(storage, "store", lambda: fake)
    storage.offload_pending()
    assert f.exists()
    assert _media(mid)["storage"] == "local"
    assert storage.last_offload_stats["verify_failed"] >= 1


def test_an_original_waits_until_a_backup_holds_it(tmp_path, monkeypatch):
    mid, f = _original()
    fake = RecordingStore()
    monkeypatch.setattr(storage, "store", lambda: fake)
    storage.offload_pending()
    assert f.exists() and _media(mid)["storage"] == "local"
    assert storage.last_offload_stats["waiting_for_backup"] >= 1
    assert not any(str(f) == str(p) for p, _ in fake.put_calls)

    backup.create(out_dir=tmp_path / "backups")          # with media: now it is safe
    storage.offload_pending()
    assert not f.exists()
    assert _media(mid)["storage"] == "remote"


def test_a_changed_file_is_not_covered_by_an_older_backup(tmp_path, monkeypatch):
    mid, f = _original()
    backup.create(out_dir=tmp_path / "backups")
    f.write_bytes(b"rewritten after the backup")
    monkeypatch.setattr(storage, "store", lambda: RecordingStore())
    storage.offload_pending()
    assert f.exists() and _media(mid)["storage"] == "local"


# ── The checks themselves ───────────────────────────────────────────────────

class _Stat:
    def __init__(self, size):
        self.st_size = size


class FakeSftp:
    def __init__(self, content: bytes):
        self.content = content

    def stat(self, path):
        return _Stat(len(self.content))

    def open(self, path, mode="rb"):
        import io
        return io.BytesIO(self.content)


class _T:
    def close(self):
        pass


def _sftp_with(monkeypatch, content):
    s = storage.SftpStore()
    monkeypatch.setattr(s, "_connect", lambda: (_T(), FakeSftp(content)))
    return s


def test_sftp_check_accepts_an_identical_copy(tmp_path, monkeypatch):
    data = secrets.token_bytes(500_000)
    local = tmp_path / "a.jpg"
    local.write_bytes(data)
    _sftp_with(monkeypatch, data).verify(local, "/x/a.jpg")


def test_sftp_check_finds_a_different_end(tmp_path, monkeypatch):
    data = secrets.token_bytes(500_000)
    local = tmp_path / "a.jpg"
    local.write_bytes(data)
    broken = data[:-10] + b"0" * 10
    with pytest.raises(storage.RemoteMismatch):
        _sftp_with(monkeypatch, broken).verify(local, "/x/a.jpg")


def test_sftp_check_finds_a_short_copy(tmp_path, monkeypatch):
    data = secrets.token_bytes(500_000)
    local = tmp_path / "a.jpg"
    local.write_bytes(data)
    with pytest.raises(storage.RemoteMismatch):
        _sftp_with(monkeypatch, data[:400_000]).verify(local, "/x/a.jpg")


class _Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = body

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass


def test_webdav_check_reads_both_ends_with_ranges(tmp_path, monkeypatch):
    import requests

    data = secrets.token_bytes(300_000)
    local = tmp_path / "a.jpg"
    local.write_bytes(data)
    seen = []

    def fake_request(method, url, headers=None, **kw):
        seen.append((method, (headers or {}).get("Range")))
        if method == "HEAD":
            return _Resp(200, headers={"Content-Length": str(len(data))})
        lo, hi = (int(x) for x in headers["Range"].split("=")[1].split("-"))
        return _Resp(206, data[lo:hi + 1])

    monkeypatch.setattr(requests, "request", fake_request)
    store = storage.WebdavStore()
    store.verify(local, "ev/a.jpg")
    assert [m for m, _ in seen] == ["HEAD", "GET", "GET"]
    monkeypatch.setattr(requests, "request", lambda m, u, headers=None, **kw: (
        _Resp(200, headers={"Content-Length": str(len(data))}) if m == "HEAD" else
        _Resp(206, b"\0" * (int(headers["Range"].split("-")[1]) - int(headers["Range"].split("=")[1].split("-")[0]) + 1))))
    with pytest.raises(storage.RemoteMismatch):
        store.verify(local, "ev/a.jpg")


def test_webdav_without_ranges_compares_the_whole_file(tmp_path, monkeypatch):
    import requests

    data = secrets.token_bytes(300_000)
    local = tmp_path / "a.jpg"
    local.write_bytes(data)
    monkeypatch.setattr(requests, "request", lambda m, u, headers=None, **kw: (
        _Resp(200, headers={"Content-Length": str(len(data))}) if m == "HEAD" else _Resp(200, data)))
    storage.WebdavStore().verify(local, "ev/a.jpg")
    monkeypatch.setattr(requests, "request", lambda m, u, headers=None, **kw: (
        _Resp(200, headers={"Content-Length": str(len(data))}) if m == "HEAD" else _Resp(200, data[:-1] + b"x")))
    with pytest.raises(storage.RemoteMismatch):
        storage.WebdavStore().verify(local, "ev/a.jpg")


def test_a_half_written_archive_from_a_killed_run_is_cleared(tmp_path):
    import os
    import time

    folder = tmp_path / "backups"
    folder.mkdir()
    stale = folder / "photobox-backup-20261001T030000Z.tar.part"
    stale.write_bytes(b"half")
    old = time.time() - 13 * 3600
    os.utime(stale, (old, old))
    fresh = folder / "photobox-backup-20261002T030000Z.tar.part"
    fresh.write_bytes(b"being written")
    backup.prune(folder, keep=7)
    assert not stale.exists() and fresh.exists()
