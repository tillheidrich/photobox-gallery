"""Speicherüberblick: Summen aus der Datenbank, Platte gecacht.

Wichtig ist nicht nur die Zahl, sondern wie sie zustande kommt: keine Läufe
über den Dateibaum, und die Plattenabfrage höchstens einmal pro Minute.
"""
import secrets

from app import storage_stats


def _event(app, slug=None):
    from app import db, ecosystem
    slug = slug or ("sto-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        eid = db.create_event(slug=slug, name="Speicher", upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
        con.commit(); con.close()
        return slug, eid


def _media(app, event_id, *, size, variant="web", storage="local", remote_path=None):
    from app import db
    with app.app_context():
        name = secrets.token_hex(4)
        mid = db.add_media(event_id=event_id, kind="photo", filename=f"{name}.jpg",
                           path_original=f"storage/x/{name}.jpg", path_webp=None, path_thumb=None,
                           size_bytes=size, sha256=secrets.token_hex(8),
                           share_token=secrets.token_hex(6))
        con = db.get_db()
        con.execute("UPDATE media SET variant=?, storage=?, remote_path=? WHERE id=?",
                    (variant, storage, remote_path, mid))
        con.commit(); con.close()
        return mid


# ── Summen je Event ──────────────────────────────────────────

def test_event_usage_splits_web_and_originals(app):
    slug, eid = _event(app)
    _media(app, eid, size=1000, variant="web")
    _media(app, eid, size=9000, variant="original")
    with app.app_context():
        u = storage_stats.event_usage(eid)
    assert u["files"] == 2
    assert u["web_bytes"] == 1000
    assert u["original_bytes"] == 9000
    assert u["total_bytes"] == 10000


def test_event_usage_splits_local_and_nas(app):
    slug, eid = _event(app)
    _media(app, eid, size=500, storage="local")
    _media(app, eid, size=7000, variant="original", storage="remote", remote_path="nas/a.jpg")
    with app.app_context():
        u = storage_stats.event_usage(eid)
    assert u["local_bytes"] == 500
    assert u["remote_bytes"] == 7000
    assert u["remote_files"] == 1


def test_unknown_event_reports_zeros(app):
    _event(app)
    with app.app_context():
        u = storage_stats.event_usage(999999)
    assert u["files"] == 0 and u["total_bytes"] == 0


def test_sum_usage_adds_up(app):
    rows = [{"files": 2, "total_bytes": 10, "web_bytes": 4, "original_bytes": 6,
             "local_bytes": 10, "remote_bytes": 0, "remote_files": 0},
            {"files": 1, "total_bytes": 5, "web_bytes": 5, "original_bytes": 0,
             "local_bytes": 0, "remote_bytes": 5, "remote_files": 1}]
    total = storage_stats.sum_usage(rows)
    assert total["files"] == 3 and total["total_bytes"] == 15 and total["remote_files"] == 1


# ── Platte ───────────────────────────────────────────────────

def test_disk_reports_usage_and_caches_for_a_minute(app, monkeypatch):
    with app.app_context():
        storage_stats.clear_cache()
        first = storage_stats.disk()
        assert first["ok"] and first["total"] > 0
        assert 0 <= first["percent_used"] <= 100

        calls = {"n": 0}
        real = storage_stats.shutil.disk_usage

        def counting(path):
            calls["n"] += 1
            return real(path)

        monkeypatch.setattr(storage_stats.shutil, "disk_usage", counting)
        storage_stats.disk()
        storage_stats.disk()
        assert calls["n"] == 0                   # innerhalb einer Minute: aus dem Cache

        storage_stats.clear_cache()
        storage_stats.disk()
        assert calls["n"] == 1


def test_warn_percent_setting_is_clamped(app):
    from app import db
    with app.app_context():
        assert storage_stats.warn_percent() == 85
        db.set_setting("storage_warn_percent", "70")
        assert storage_stats.warn_percent() == 70
        db.set_setting("storage_warn_percent", "5")
        assert storage_stats.warn_percent() == 50
        db.set_setting("storage_warn_percent", "kaputt")
        assert storage_stats.warn_percent() == storage_stats.DEFAULT_WARN_PERCENT
        db.set_setting("storage_warn_percent", "")


def test_overview_sorts_events_by_size(app):
    _slug_a, eid_a = _event(app)
    _slug_b, eid_b = _event(app)
    _media(app, eid_a, size=100)
    _media(app, eid_b, size=900)
    with app.app_context():
        ov = storage_stats.overview()
    ids = [e["id"] for e in ov["events"]]
    assert ids.index(eid_b) < ids.index(eid_a)
    assert ov["totals"]["total_bytes"] >= 1000
    assert "warning" in ov and "disk" in ov


def test_warning_flag_follows_the_threshold(app):
    from app import db
    with app.app_context():
        storage_stats.clear_cache()
        db.set_setting("storage_warn_percent", "50")
        ov = storage_stats.overview()
        assert ov["warning"] == (ov["disk"]["percent_used"] >= 50)
        db.set_setting("storage_warn_percent", "")


# ── Admin-Oberfläche ─────────────────────────────────────────

def test_dashboard_shows_the_storage_card(app, client):
    slug, eid = _event(app)
    _media(app, eid, size=2048)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "Speicher" in html
    assert "Web-Fassungen" in html
    assert "/storage" in html                     # Link auf die Detailseite je Event
    assert "Auf dem NAS" in html


def test_event_storage_page(app, client):
    slug, eid = _event(app)
    _media(app, eid, size=1024, variant="web")
    _media(app, eid, size=4096, variant="original", storage="remote", remote_path="nas/x.jpg")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    html = client.get(f"/admin/events/{eid}/storage").get_data(as_text=True)
    assert "Originale" in html and "NAS" in html
    assert "1.00 KB" in html                      # Web-Fassung
    assert "4.00 KB" in html                      # Original auf dem NAS


def test_event_storage_page_needs_admin(app, client):
    slug, eid = _event(app)
    r = client.get(f"/admin/events/{eid}/storage")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def test_missing_event_storage_page_is_404(app, client):
    _event(app)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    assert client.get("/admin/events/999999/storage").status_code == 404


def test_dashboard_warns_visibly_when_the_disk_runs_full(app, client, monkeypatch):
    """Über der Schwelle muss die Karte deutlich werden, nicht nur intern warnen."""
    from app import storage_stats as st
    _event(app)
    monkeypatch.setattr(st, "disk", lambda path=None: {
        "ok": True, "path": "/data", "total": 100_000_000_000, "used": 92_000_000_000,
        "free": 8_000_000_000, "percent_used": 92})
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "alert--warning" in html
    assert "92 % voll" in html
    assert "Warnschwelle" in html


def test_dashboard_stays_calm_below_the_threshold(app, client, monkeypatch):
    from app import storage_stats as st
    _event(app)
    monkeypatch.setattr(st, "disk", lambda path=None: {
        "ok": True, "path": "/data", "total": 100_000_000_000, "used": 10_000_000_000,
        "free": 90_000_000_000, "percent_used": 10})
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "% voll" not in html
