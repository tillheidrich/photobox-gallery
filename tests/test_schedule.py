"""Zeitplan: geplanter Start mit Countdown, Ablauf mit Countdown.

Vor dem Start darf kein Foto und keine Datei nach draußen. Ist der Zeitpunkt
erreicht, geht die Galerie von selbst live — ohne Cronjob.
"""
import json
import secrets
from datetime import datetime, timedelta

from PIL import Image

from app import gallery_settings as gsm, media, schedule


def _iso(delta_hours: float) -> str:
    return (datetime.now() + timedelta(hours=delta_hours)).isoformat(timespec="minutes")


def _event(app, slug=None, gallery=None, live_at=None, published=1):
    from app import db, ecosystem
    slug = slug or ("sch-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        eid = db.create_event(slug=slug, name="Zeitplan",
                              upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=?, scheduled_live_at=?, gallery_json=? WHERE id=?",
                    (published, live_at, json.dumps(gsm.clean(gallery or {})), eid))
        con.commit(); con.close()

        rel = f"storage/{slug}/webp/uidtest_web.jpg"
        full = media.data_dir() / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (600, 400), (70, 70, 90)).save(full, "JPEG")
        db.add_media(event_id=eid, kind="photo", filename="a.jpg",
                     path_original=rel, path_webp=rel, path_thumb=rel,
                     size_bytes=full.stat().st_size, sha256=secrets.token_hex(8),
                     share_token=secrets.token_hex(6))
        return slug, eid, rel


# ── reine Logik ──────────────────────────────────────────────

def test_parse_dt_accepts_form_and_db_shapes():
    assert schedule.parse_dt("2026-12-24T18:00") == datetime(2026, 12, 24, 18, 0)
    assert schedule.parse_dt("2026-12-24T18:00:00") == datetime(2026, 12, 24, 18, 0)
    assert schedule.parse_dt("2026-12-24") == datetime(2026, 12, 24, 0, 0)
    assert schedule.parse_dt("") is None
    assert schedule.parse_dt("bald") is None


def test_coming_soon_only_in_the_future():
    now = datetime(2026, 5, 1, 12, 0)
    assert schedule.coming_soon({"scheduled_live_at": "2026-05-01T18:00"}, now)
    assert schedule.coming_soon({"scheduled_live_at": "2026-04-01T18:00"}, now) is None
    assert schedule.coming_soon({}, now) is None


def test_due_for_publish_needs_a_draft():
    now = datetime(2026, 5, 2, 12, 0)
    assert schedule.due_for_publish({"scheduled_live_at": "2026-05-01T18:00", "published": 0}, now)
    assert not schedule.due_for_publish({"scheduled_live_at": "2026-05-01T18:00", "published": 1}, now)
    assert not schedule.due_for_publish({"scheduled_live_at": "2026-06-01T18:00", "published": 0}, now)


def test_days_left_only_inside_the_last_week():
    now = datetime(2026, 5, 1, 12, 0)
    assert schedule.days_left({"expires_at": "2026-05-04T12:00"}, now) == 3
    assert schedule.days_left({"expires_at": "2026-05-01T20:00"}, now) == 1
    assert schedule.days_left({"expires_at": "2026-06-01T12:00"}, now) is None   # noch weit weg
    assert schedule.days_left({}, now) is None
    assert schedule.days_left({"expires_at": "2026-04-01T12:00"}, now) == 0      # vorbei


def test_state_names_the_situation():
    now = datetime(2026, 5, 1, 12, 0)
    assert schedule.state({"scheduled_live_at": "2026-06-01T10:00", "published": 0}, {}, now) == "scheduled"
    assert schedule.state({"published": 1}, {"expires_at": "2026-04-01T10:00"}, now) == "expired"
    assert schedule.state({"published": 1}, {}, now) == "live"
    assert schedule.state({"published": 0}, {}, now) == "draft"


# ── Galerie vor dem Start ────────────────────────────────────

def test_gallery_shows_countdown_and_no_photos(app, client):
    slug, _, _ = _event(app, live_at=_iso(48), published=0)
    r = client.get(f"/gallery/{slug}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="csClock"' in html
    assert "photo-grid" not in html               # kein einziges Foto
    assert "noindex" in html


def test_files_are_locked_before_the_start(app, client):
    slug, _, rel = _event(app, live_at=_iso(48), published=1)
    assert client.get(f"/data/{rel}").status_code == 403


def test_own_session_via_qr_still_works_before_the_start(app, client):
    """Der Gast an der Box bekommt seine eigene Aufnahme — sonst wäre die Box kaputt."""
    from app import db
    slug, eid, rel = _event(app, live_at=_iso(48), published=1)
    with app.app_context():
        con = db.get_db()
        con.execute("UPDATE media SET session_uid='uidtest' WHERE event_id=?", (eid,))
        con.commit(); con.close()
    with client.session_transaction() as s:
        s["p_uids"] = ["uidtest"]          # Dateiname beginnt mit „uidtest_"
    assert client.get(f"/data/{rel}").status_code == 200


def test_downloads_and_zips_are_locked_before_the_start(app, client):
    from app import db
    slug, eid, _ = _event(app, live_at=_iso(48), published=1,
                          gallery={"download_zip": True, "selection": True})
    with app.app_context():
        con = db.get_db()
        mid = con.execute("SELECT id FROM media WHERE event_id=?", (eid,)).fetchone()[0]
        con.close()
    assert client.get(f"/gallery/{slug}/photo/{mid}/download?size=web").status_code == 403
    assert client.get(f"/gallery/{slug}/download.zip").status_code == 403
    assert client.get(f"/gallery/{slug}/selection.zip?ids={mid}").status_code == 403
    assert client.get(f"/slideshow/{slug}").status_code == 403


def test_reached_start_publishes_the_gallery_by_itself(app, client):
    from app import db
    slug, eid, _ = _event(app, live_at=_iso(-1), published=0)
    r = client.get(f"/gallery/{slug}")
    assert r.status_code == 200
    assert 'id="csClock"' not in r.get_data(as_text=True)
    with app.app_context():
        assert db.get_event_by_id(eid)["published"] in (1, True)


def test_admin_sees_the_gallery_before_the_start(app, client):
    slug, _, _ = _event(app, live_at=_iso(48), published=1)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert 'id="csClock"' not in html


# ── Ablauf-Countdown ─────────────────────────────────────────

def test_guest_sees_the_remaining_days(app, client):
    slug, _, _ = _event(app, gallery={"expires_at": _iso(60)})     # 2,5 Tage
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert "noch 3 Tage verfügbar" in html


def test_no_countdown_when_expiry_is_far_away(app, client):
    slug, _, _ = _event(app, gallery={"expires_at": _iso(24 * 30)})
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert "gallery-expiry" not in html


def test_expired_gallery_stays_closed(app, client):
    slug, _, _ = _event(app, gallery={"expires_at": _iso(-1)})
    assert client.get(f"/gallery/{slug}").status_code == 410


# ── Admin-Formular: Start und Ablauf an einer Stelle ─────────

def test_admin_form_saves_both_fields_together(app, client):
    from app import db
    slug, eid, _ = _event(app)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/events/{eid}/edit", data={
        "csrf_token": "x", "name": "Zeitplan", "slug": slug,
        "scheduled_live_at": "2026-12-24T18:00",
        "expires_at": "2027-01-06T23:59",
    }, follow_redirects=True)
    with app.app_context():
        ev = db.get_event_by_id(eid)
        assert ev["scheduled_live_at"] == "2026-12-24T18:00"
        assert gsm.parse(ev["gallery_json"])["expires_at"] == "2027-01-06T23:59"


def test_gallery_settings_form_keeps_the_expiry(app, client):
    """Das Ablaufdatum wohnt im Event-Formular — die Galerie-Einstellungen dürfen es nicht löschen."""
    from app import db
    slug, eid, _ = _event(app, gallery={"expires_at": "2027-01-06T23:59"})
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"
    client.post(f"/admin/events/{eid}/gallery-settings",
                data={"csrf_token": "tok", "likes": "on"}, follow_redirects=True)
    with app.app_context():
        assert gsm.parse(db.get_event_by_id(eid)["gallery_json"])["expires_at"] == "2027-01-06T23:59"


def test_admin_list_shows_the_state(app, client):
    slug, eid, _ = _event(app, live_at=_iso(48), published=0)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    html = client.get("/admin/events").get_data(as_text=True)
    assert "Geplant" in html
