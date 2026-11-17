"""Fotomoderation: neue Aufnahmen erst nach Freigabe.

Die Regel hängt an der Datenbank (Trigger in ``ecosystem.init_schema``), damit
sie für jeden Upload-Weg gilt — auch für den Endpunkt der Fotobox, der von
Moderation nichts weiß.
"""
import json
import secrets

from PIL import Image

from app import ecosystem, gallery_settings as gsm, media


def _event(app, moderation=False, slug=None):
    from app import db
    slug = slug or ("mod-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        eid = db.create_event(slug=slug, name="Moderation",
                              upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1, gallery_json=? WHERE id=?",
                    (json.dumps(gsm.clean({"moderation": moderation})), eid))
        con.commit(); con.close()
        return slug, eid


def _upload(app, event_id, slug, hidden=0):
    from app import db
    with app.app_context():
        name = secrets.token_hex(4)
        rel = f"storage/{slug}/webp/{name}.jpg"
        full = media.data_dir() / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 300), (100, 60, 60)).save(full, "JPEG")
        if hidden:
            # So legt der Endpunkt der Box die Original-Variante an: hidden schon im INSERT.
            con = db.get_db()
            cur = con.execute(
                """INSERT INTO media(event_id, kind, filename, path_original, path_webp, path_thumb,
                                     size_bytes, sha256, share_token, hidden, variant)
                   VALUES (?,?,?,?,?,?,?,?,?,1,'original')""",
                (event_id, "photo", f"{name}.jpg", rel, rel, rel, full.stat().st_size,
                 secrets.token_hex(8), secrets.token_hex(6)))
            con.commit()
            mid = cur.lastrowid
            con.close()
            return mid
        return db.add_media(event_id=event_id, kind="photo", filename=f"{name}.jpg",
                            path_original=rel, path_webp=rel, path_thumb=rel,
                            size_bytes=full.stat().st_size, sha256=secrets.token_hex(8),
                            share_token=secrets.token_hex(6))


def _row(app, media_id):
    from app import db
    with app.app_context():
        return db.get_media(media_id)


# ── Einstellung ──────────────────────────────────────────────

def test_moderation_is_off_by_default():
    assert gsm.clean({})["moderation"] is False


def test_setting_round_trips_through_the_form():
    cfg = gsm.from_form({"moderation": "on"})
    assert cfg["moderation"] is True


# ── Trigger ──────────────────────────────────────────────────

def test_new_upload_stays_hidden_when_moderation_is_on(app, client):
    from app import db
    slug, eid = _event(app, moderation=True)
    mid = _upload(app, eid, slug)
    row = _row(app, mid)
    assert row["hidden"] == 1
    assert row["review_state"] == "pending"
    with app.app_context():
        assert [m["id"] for m in db.list_media(eid)] == []       # nicht in der Galerie


def test_new_upload_is_visible_when_moderation_is_off(app, client):
    from app import db
    slug, eid = _event(app, moderation=False)
    mid = _upload(app, eid, slug)
    row = _row(app, mid)
    assert row["hidden"] == 0
    assert not row["review_state"]
    with app.app_context():
        assert [m["id"] for m in db.list_media(eid)] == [mid]


def test_original_variant_is_not_a_review_item(app, client):
    """Das Original einer Web-Fassung ist schon hidden — es wartet nicht."""
    slug, eid = _event(app, moderation=True)
    mid = _upload(app, eid, slug, hidden=1)
    assert _row(app, mid)["review_state"] is None
    with app.app_context():
        assert mid not in [m["id"] for m in ecosystem.pending_review(eid)]


def test_gallery_does_not_show_waiting_items(app, client):
    slug, eid = _event(app, moderation=True)
    _upload(app, eid, slug)
    html = client.get(f"/gallery/{slug}").get_data(as_text=True)
    assert 'class="photo-item' not in html


# ── Freigeben / Ablehnen ─────────────────────────────────────

def test_count_and_approve_all(app, client):
    from app import db
    slug, eid = _event(app, moderation=True)
    _upload(app, eid, slug)
    _upload(app, eid, slug)
    with app.app_context():
        assert ecosystem.pending_review_count(eid) == 2
        assert ecosystem.approve_media(eid) == 2
        assert ecosystem.pending_review_count(eid) == 0
        assert len(db.list_media(eid)) == 2


def test_approve_single_item_via_admin(app, client):
    from app import db
    slug, eid = _event(app, moderation=True)
    mid = _upload(app, eid, slug)
    other = _upload(app, eid, slug)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/events/{eid}/review/approve",
                data={"csrf_token": "x", "media_id": str(mid)}, follow_redirects=True)
    assert _row(app, mid)["hidden"] == 0
    assert _row(app, other)["hidden"] == 1
    with app.app_context():
        assert ecosystem.pending_review_count(eid) == 1


def test_reject_deletes_the_item_and_its_file(app, client):
    slug, eid = _event(app, moderation=True)
    mid = _upload(app, eid, slug)
    rel = _row(app, mid)["path_original"]
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    client.post(f"/admin/events/{eid}/review/{mid}/reject",
                data={"csrf_token": "x"}, follow_redirects=True)
    assert _row(app, mid) is None
    with app.app_context():
        assert not (media.data_dir() / rel).exists()


def test_reject_refuses_a_foreign_event(app, client):
    slug_a, eid_a = _event(app, moderation=True)
    _slug_b, eid_b = _event(app, moderation=True)
    mid = _upload(app, eid_a, slug_a)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    r = client.post(f"/admin/events/{eid_b}/review/{mid}/reject", data={"csrf_token": "x"})
    assert r.status_code == 404
    assert _row(app, mid) is not None


def test_review_view_shows_waiting_items(app, client):
    slug, eid = _event(app, moderation=True)
    _upload(app, eid, slug)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    html = client.get(f"/admin/events/{eid}/review").get_data(as_text=True)
    assert "Alle freigeben (1)" in html
    assert "Freigeben" in html and "Löschen" in html


def test_review_view_needs_admin(app, client):
    slug, eid = _event(app, moderation=True)
    r = client.get(f"/admin/events/{eid}/review")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def test_pending_count_appears_in_the_admin_navigation(app, client):
    """Zwei Anzeigen: die Zahl im Menü und die Marke am Event.

    Das Dashboard zeigt nur die acht neuesten Events. Ob dieses dazugehört,
    hängt davon ab, wie viele Events andere Tests vorher angelegt haben —
    geprüft wird die Marke deshalb auf der vollständigen Eventliste.
    """
    slug, eid = _event(app, moderation=True)
    _upload(app, eid, slug)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"
    assert "nav-badge" in client.get("/admin/dashboard").get_data(as_text=True)
    liste = client.get("/admin/events").get_data(as_text=True)
    assert f'/admin/events/{eid}/review' in liste and "zu prüfen" in liste


def test_switching_moderation_off_releases_the_queue(app, client):
    from app import db
    slug, eid = _event(app, moderation=True)
    _upload(app, eid, slug)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"
    client.post(f"/admin/events/{eid}/gallery-settings",
                data={"csrf_token": "tok"}, follow_redirects=True)
    with app.app_context():
        assert ecosystem.pending_review_count(eid) == 0
        assert len(db.list_media(eid)) == 1
