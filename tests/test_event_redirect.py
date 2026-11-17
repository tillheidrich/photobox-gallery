"""An event whose gallery moved elsewhere (Picdrop): forward the guests, keep the event.

Two things the operator asked for on 15.11.2026:

* "Galerie-Link leitet weiter auf ..." - ``/gallery/<slug>`` (old names, the
  password page and share links of the event included) answers with a 302 to
  an address stored at the event. The address comes only from the event row,
  never from the request, and only http(s) is accepted.
* "Fotos löschen, Event behalten" - every picture of the event goes, files,
  rows, derivatives and the originals' copy, while slug, old slugs, signing
  key, QR settings and the forward stay. Expiry and the nightly run never take
  more than the pictures of such an event.
"""
import io
import secrets

import pytest
from PIL import Image

PICDROP = "https://www.picdrop.com/muster/abc123"


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "x"


def _guest(client):
    with client.session_transaction() as s:
        s.pop("admin_logged_in", None)


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (900, 1200), (120, 90, 60)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _event_with_run(client, slug, uid=None):
    """An event with one booth run: a web version and its full-size original."""
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    uid = uid or ("rd" + secrets.token_hex(5))
    eid = db.create_event(name=slug, slug=slug, upload_token=secrets.token_hex(8))
    db.update_event(eid, published=1)
    device = db.create_device(name=f"Box {slug}")
    ecosystem.save_device_profile(device["id"], {}, eid)
    for variant in ("web", "original"):
        r = client.post("/api/v2/booth/media", headers={"X-API-Key": device["api_key"]},
                        data={"uid": uid, "session_uid": uid, "event_slug": slug, "kind": "photo",
                              "variant": variant, "file": (_jpeg(), f"{variant}.jpg")},
                        content_type="multipart/form-data")
        assert r.status_code in (200, 201), r.data[:300]
    return db.get_event_by_id(eid), uid


def _code(event, uid):
    from app import ecosystem
    return f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"


def _forward(client, event, url=PICDROP, everything=False, **extra):
    _admin(client)
    data = {"csrf_token": "x", "qr_target": "gallery", "external_url": "", "status_message": "",
            "redirect_url": url, **extra}
    if everything:
        data["redirect_all"] = "1"
    return client.post(f"/admin/events/{event['id']}/qr-target", data=data)


def _alias(event_id, alias):
    from app import db
    con = db.get_db()
    con.execute("INSERT INTO slug_aliases(event_id, alias_slug) VALUES (?, ?)", (event_id, alias))
    con.commit(); con.close()


# ── Forwarding ──────────────────────────────────────────────

def test_without_a_forward_the_gallery_is_what_it_was(client):
    event, _uid = _event_with_run(client, "kein-umzug")
    _guest(client)
    assert client.get("/gallery/kein-umzug").status_code == 200


def test_the_gallery_forwards_guests_to_the_stored_address(client):
    from app import db
    event, _uid = _event_with_run(client, "umzug-picdrop")
    r = _forward(client, event)
    assert r.status_code == 302
    assert db.get_event_by_id(event["id"])["redirect_url"] == PICDROP
    _guest(client)
    r = client.get("/gallery/umzug-picdrop")
    assert r.status_code == 302 and r.headers["Location"] == PICDROP


def test_old_names_the_password_page_and_share_links_forward_too(client):
    from app import db, sharelinks
    event, _uid = _event_with_run(client, "umzug-alles")
    _alias(event["id"], "umzug-alter-name")
    db.update_event(event["id"], password="ein-gaeste-passwort")
    link = sharelinks.create(event["id"], title="Familie")
    _forward(client, event)
    _guest(client)
    for path in ("/gallery/umzug-alter-name", "/gallery/umzug-alles", f"/l/{link['slug']}",
                 f"/g/{event['code']}"):
        r = client.get(path, follow_redirects=False)
        hops = 0
        while r.status_code in (301, 302) and not r.headers["Location"].startswith("https://") and hops < 3:
            r = client.get(r.headers["Location"], follow_redirects=False)
            hops += 1
        assert r.status_code == 302 and r.headers["Location"] == PICDROP, path
    # Typing a password at the old page does not keep anybody here either.
    r = client.post("/gallery/umzug-alles", data={"password": "ein-gaeste-passwort"})
    assert r.status_code == 302 and r.headers["Location"] == PICDROP


def test_an_expired_gallery_forwards_instead_of_saying_gone(client):
    import json
    from app import db, gallery_settings as gsm
    event, _uid = _event_with_run(client, "umzug-abgelaufen")
    db.update_event(event["id"], gallery_json=json.dumps(gsm.clean({"expires_at": "2020-01-01T00:00"})))
    _guest(client)
    assert client.get("/gallery/umzug-abgelaufen").status_code == 410
    _forward(client, event)
    _guest(client)
    r = client.get("/gallery/umzug-abgelaufen")
    assert r.status_code == 302 and r.headers["Location"] == PICDROP


def test_silent_mode_still_wins_over_the_forward(client):
    import json
    from app import db, gallery_settings as gsm
    event, _uid = _event_with_run(client, "umzug-still")
    _forward(client, event)
    db.update_event(event["id"], gallery_json=json.dumps(gsm.clean({"silent": True})))
    _guest(client)
    assert client.get("/gallery/umzug-still").status_code == 404


def test_the_operator_still_reaches_the_event_and_sees_where_it_points(client):
    event, _uid = _event_with_run(client, "umzug-admin")
    _forward(client, event)
    _admin(client)
    assert client.get("/gallery/umzug-admin").status_code == 200
    edit = client.get(f"/admin/events/{event['id']}/edit").get_data(as_text=True)
    assert "leitet weiter auf www.picdrop.com" in edit
    assert 'name="redirect_url"' in edit and "Alles weiterleiten" in edit
    listing = client.get("/admin/events").get_data(as_text=True)
    assert "leitet weiter auf www.picdrop.com" in listing


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "JAVASCRIPT:alert(1)", "//evil.example/x",
                                 "ftp://files.example/x", "https://", "data:text/html,hi",
                                 "http://exa mple.com", "https://example.com/\\@evil", "mailto:a@b.c"])
def test_only_http_addresses_are_accepted(client, bad):
    from app import db
    event, _uid = _event_with_run(client, "umzug-boese-" + secrets.token_hex(2))
    _forward(client, event, url=bad)
    assert not (db.get_event_by_id(event["id"])["redirect_url"] or "")
    _forward(client, event, url="", qr_target="external", external_url=bad)
    stored = db.get_event_by_id(event["id"])
    assert not (stored.get("external_url") or "") and stored["qr_target"] != "external"


def test_a_bad_address_already_in_the_database_is_not_followed(client):
    from app import db
    event, _uid = _event_with_run(client, "umzug-altlast")
    con = db.get_db()
    con.execute("UPDATE events SET redirect_url=? WHERE id=?", ("javascript:alert(1)", event["id"]))
    con.commit(); con.close()
    _guest(client)
    assert client.get("/gallery/umzug-altlast").status_code == 200


def test_the_target_never_comes_from_the_request(client):
    event, _uid = _event_with_run(client, "umzug-offen")
    _guest(client)
    for q in ("?redirect_url=https://evil.example", "?next=https://evil.example", "?url=https://evil.example"):
        r = client.get("/gallery/umzug-offen" + q)
        assert r.status_code == 200, q
    _forward(client, event)
    _guest(client)
    r = client.get("/gallery/umzug-offen?redirect_url=https://evil.example")
    assert r.headers["Location"] == PICDROP


def test_forward_everything_points_the_qr_codes_there_too(client):
    from app import db
    event, uid = _event_with_run(client, "umzug-qr")
    _forward(client, event, everything=True)
    stored = db.get_event_by_id(event["id"])
    assert stored["qr_target"] == "external" and stored["external_url"] == PICDROP
    _guest(client)
    r = client.get(f"/p/{_code(stored, uid)}")
    assert r.status_code == 302 and r.headers["Location"].startswith(PICDROP)


# ── Delete the photos, keep the event ───────────────────────

def _files_of(event_id):
    from app import db, media
    con = db.get_db()
    rows = [dict(r) for r in con.execute("SELECT * FROM media WHERE event_id=?", (event_id,))]
    con.close()
    paths = []
    for r in rows:
        for k in ("path_original", "path_webp", "path_thumb", "path_poster"):
            if r.get(k):
                paths.append(media.data_dir() / r[k])
    return rows, paths


def _purge(client, event, count):
    _admin(client)
    return client.post(f"/admin/events/{event['id']}/purge-media", data={"csrf_token": "x", "confirm_count": str(count)})


def test_the_storage_page_offers_it_with_the_number_of_photos(client):
    event, _uid = _event_with_run(client, "leeren-angebot")
    _admin(client)
    page = client.get(f"/admin/events/{event['id']}/storage").get_data(as_text=True)
    assert "Fotos löschen, Event behalten" in page
    # One picture for the guests; its full-size original goes with it.
    assert 'name="confirm_count" value="1"' in page


def test_delete_photos_keep_event(client):
    from app import db, ecosystem, media, retention, storage
    from app.blueprints.sharing import watermark_path
    event, uid = _event_with_run(client, "leeren-behalten")
    _alias(event["id"], "leeren-alter-name")
    _forward(client, event, everything=True)
    before = db.get_event_by_id(event["id"])
    rows, paths = _files_of(event["id"])
    assert len(rows) == 2 and all(p.exists() for p in paths)
    # Derivatives that exist only because guests looked: download sizes, watermarks.
    web = next(r for r in rows if r["variant"] == "web")
    cached = media.build_download_variant(web["id"], web["path_webp"], "small")
    marked = watermark_path(web, "thumb", "Muster")
    assert cached and cached.exists() and marked and marked.exists()
    # One copy on the NAS.
    con = db.get_db()
    con.execute("UPDATE media SET storage='remote', remote_path=? WHERE id=?", ("nas/leeren/orig.jpg", rows[1]["id"]))
    con.commit(); con.close()

    r = _purge(client, event, 1)
    assert r.status_code == 302

    after = db.get_event_by_id(event["id"])
    assert after is not None
    for key in ("slug", "share_key", "qr_target", "external_url", "redirect_url", "code", "name"):
        assert after[key] == before[key], key
    assert db.resolve_event_slug("leeren-alter-name")[0]["id"] == event["id"]
    assert _files_of(event["id"])[0] == []
    assert not any(p.exists() for p in paths)
    assert not cached.exists() and not marked.exists()
    con = db.get_db()
    queued = [x[0] for x in con.execute("SELECT path FROM remote_deletions")]
    con.close()
    assert "nas/leeren/orig.jpg" in queued
    # The booth must not bring the run back.
    assert ecosystem.is_deleted(uid)
    entry = retention.entries(limit=1, event_slug="leeren-behalten")[0]
    assert entry["scope"] == "event" and entry["actor"] == "admin" and "2 Aufnahmen" in entry["note"]
    assert "Event bleibt" in entry["note"]


def test_a_stale_confirmation_deletes_nothing(client):
    from app import db
    event, _uid = _event_with_run(client, "leeren-veraltet")
    _purge(client, event, 3)          # the page said 3, there is 1 now
    assert len(_files_of(event["id"])[0]) == 2
    _purge(client, event, "")
    assert len(_files_of(event["id"])[0]) == 2


def test_the_forward_works_before_and_after_the_photos_go(client):
    event, uid = _event_with_run(client, "leeren-weiter")
    _forward(client, event, everything=True)
    code = _code(event, uid)
    _guest(client)
    assert client.get("/gallery/leeren-weiter").headers["Location"] == PICDROP
    assert client.get(f"/p/{code}").headers["Location"].startswith(PICDROP)
    _purge(client, event, 1)
    _guest(client)
    assert client.get("/gallery/leeren-weiter").headers["Location"] == PICDROP
    assert client.get(f"/p/{code}").headers["Location"].startswith(PICDROP)


def test_a_deleted_run_with_the_gallery_as_target_says_gone(client):
    event, uid = _event_with_run(client, "leeren-galerieziel")
    code = _code(event, uid)
    _purge(client, event, 1)
    _guest(client)
    r = client.get(f"/p/{code}")
    assert r.status_code == 200
    assert "Aufnahmen" in r.get_data(as_text=True)


def test_purging_needs_an_admin_and_the_form_key(client):
    from app import db
    event, _uid = _event_with_run(client, "leeren-schutz")
    _guest(client)
    client.post(f"/admin/events/{event['id']}/purge-media", data={"confirm_count": "1"})
    assert len(_files_of(event["id"])[0]) == 2
    _admin(client)
    r = client.post(f"/admin/events/{event['id']}/purge-media", data={"csrf_token": "falsch", "confirm_count": "1"})
    assert r.status_code == 400
    assert len(_files_of(event["id"])[0]) == 2


def test_expiry_and_the_nightly_run_keep_a_forwarded_event(client):
    from app import db, maintenance, retention
    event, _uid = _event_with_run(client, "leeren-nacht")
    db.update_event(event["id"], retention_days=1, date="2020-01-01")
    _forward(client, event, everything=True)
    maintenance.run()
    after = db.get_event_by_id(event["id"])
    assert after is not None and after["redirect_url"] == PICDROP
    assert _files_of(event["id"])[0] == []
    # Even a caller that asks the purge for the row keeps it while a forward is set.
    retention.purge_event(event["id"], reason="retention", actor="system", delete_event_row=True)
    assert db.get_event_by_id(event["id"]) is not None
    _guest(client)
    assert client.get("/gallery/leeren-nacht").headers["Location"] == PICDROP
