"""QR target with campaign parameters.

Every printed code points at ``/p/<uid>-<sig>``; where it leads is decided in
the admin at any time. The campaign parameters ride along so the operator can
see which QR code actually brings visitors — without breaking a target URL
that already carries its own query.
"""
import secrets

from app import ecosystem


def _event(app, slug=None, **fields):
    from app import db
    slug = slug or ("qr-" + secrets.token_hex(3))
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        eid = db.create_event(slug=slug, name="QR", upload_token=secrets.token_hex(8))
        con = db.get_db()
        con.execute("UPDATE events SET published=1, share_key=? WHERE id=?",
                    (secrets.token_urlsafe(16), eid))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE events SET {sets} WHERE id=?", [*fields.values(), eid])
        con.commit(); con.close()
        return slug, eid, db.get_event_by_id(eid)


# ── parameters ───────────────────────────────────────────────

def test_defaults_are_photobox_qr_and_the_slug():
    utm = ecosystem.event_utm({"slug": "sommerfest"})
    assert utm == {"utm_source": "photobox", "utm_medium": "qr", "utm_campaign": "sommerfest"}


def test_event_values_win_over_the_defaults():
    utm = ecosystem.event_utm({"slug": "sommerfest", "utm_source": "aushang",
                               "utm_campaign": "hochzeit-mai", "utm_content": "eingang"})
    assert utm["utm_source"] == "aushang"
    assert utm["utm_medium"] == "qr"                  # nicht gesetzt: Standard
    assert utm["utm_campaign"] == "hochzeit-mai"
    assert utm["utm_content"] == "eingang"


def test_parameters_are_cleaned_for_a_url():
    clean = ecosystem.clean_utm({"utm_campaign": "Sommer Fest 2026!", "utm_source": "  a b  ",
                                 "utm_content": "x" * 200})
    assert clean["utm_campaign"] == "Sommer-Fest-2026"
    assert clean["utm_source"] == "a-b"
    assert len(clean["utm_content"]) <= ecosystem.UTM_MAX_LEN


def test_empty_content_is_left_out_entirely():
    assert "utm_content" not in ecosystem.event_utm({"slug": "x"})


# ── building the URL ─────────────────────────────────────────

def test_with_params_keeps_the_existing_query_and_fragment():
    out = ecosystem.with_params("https://a.test/g?ref=x#oben", {"utm_source": "photobox"})
    assert out == "https://a.test/g?ref=x&utm_source=photobox#oben"


def test_with_params_never_writes_a_second_question_mark():
    out = ecosystem.with_params("https://a.test/g", {"utm_source": "photobox"})
    assert out.count("?") == 1


def test_existing_parameters_of_the_target_win():
    out = ecosystem.with_params("https://a.test/g?utm_source=kunde",
                                {"utm_source": "photobox", "utm_medium": "qr"})
    assert "utm_source=kunde" in out and "utm_source=photobox" not in out
    assert "utm_medium=qr" in out


def test_gallery_target_points_at_the_signed_session_page(app):
    slug, _eid, event = _event(app)
    with app.app_context():
        url = ecosystem.qr_url_for(event, "abcd2345xy", "https://fotos.test")
    assert url.startswith("https://fotos.test/p/abcd2345xy-")
    assert "utm_source=photobox" in url and f"utm_campaign={slug}" in url


def test_status_target_stays_on_our_own_page(app):
    slug, _eid, event = _event(app, qr_target="status", status_message="offline")
    with app.app_context():
        url = ecosystem.qr_url_for(event, "abcd2345xy", "https://fotos.test")
    assert "/p/abcd2345xy-" in url


def test_external_target_replaces_the_uid_placeholder(app):
    _slug, _eid, event = _event(app, qr_target="external",
                                external_url="https://picdrop.test/g/{uid}?ref=abc")
    with app.app_context():
        url = ecosystem.qr_url_for(event, "abcd2345xy", "https://fotos.test")
    assert url.startswith("https://picdrop.test/g/abcd2345xy?ref=abc")
    assert "utm_medium=qr" in url


def test_external_without_url_falls_back_to_our_page(app):
    _slug, _eid, event = _event(app, qr_target="external", external_url=None)
    with app.app_context():
        url = ecosystem.qr_url_for(event, "abcd2345xy", "https://fotos.test")
    assert "/p/abcd2345xy-" in url


def test_signature_matches_the_session_route(app, client):
    """Die gebaute Adresse muss auch wirklich aufgehen."""
    slug, eid, event = _event(app)
    with app.app_context():
        url = ecosystem.qr_url_for(event, "abcd2345xy", "")
    assert client.get(url).status_code == 200


# ── admin form ───────────────────────────────────────────────

def test_admin_saves_the_campaign_parameters(app, client):
    from app import db
    _slug, eid, _event_row = _event(app)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "tok"
    client.post(f"/admin/events/{eid}/qr-target", data={
        "csrf_token": "tok", "qr_target": "gallery",
        "utm_source": "aushang", "utm_medium": "print",
        "utm_campaign": "Sommerfest 2026", "utm_content": "eingang",
    }, follow_redirects=True)
    with app.app_context():
        ev = db.get_event_by_id(eid)
    assert ev["utm_source"] == "aushang"
    assert ev["utm_medium"] == "print"
    assert ev["utm_campaign"] == "Sommerfest-2026"
    assert ev["utm_content"] == "eingang"


def test_admin_form_shows_the_fields_and_an_example(app, client):
    _slug, eid, _ = _event(app)
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    html = client.get(f"/admin/events/{eid}/edit").get_data(as_text=True)
    assert 'name="utm_source"' in html and 'name="utm_content"' in html
    assert "utm_source=photobox" in html            # Beispieladresse


def test_admin_form_warns_when_the_qr_leaves_our_gallery(app, client):
    _slug, eid, _ = _event(app, qr_target="external", external_url="https://picdrop.test/g/{uid}")
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    html = client.get(f"/admin/events/{eid}/edit").get_data(as_text=True)
    assert "nicht mehr" in html and "eigene Galerie" in html


# ── guest side ───────────────────────────────────────────────

def test_session_page_link_to_the_gallery_carries_the_parameters(app, client):
    slug, eid, event = _event(app)
    with app.app_context():
        key = ecosystem.ensure_share_key(event)
        code = f"abcd2345xy-{ecosystem.signature(key, 'abcd2345xy')}"
    html = client.get(f"/p/{code}").get_data(as_text=True)
    assert f"/gallery/{slug}?src=qr&amp;utm_source=photobox" in html
    assert f"utm_campaign={slug}" in html


# ── booth profile ────────────────────────────────────────────

def test_profile_carries_moderation_flag_and_campaign_query(app):
    import json
    from app import db, gallery_settings as gsm
    slug, eid, _ = _event(app)
    with app.app_context():
        con = db.get_db()
        con.execute("UPDATE events SET gallery_json=? WHERE id=?",
                    (json.dumps(gsm.clean({"moderation": True})), eid))
        con.commit(); con.close()
        device = db.create_device(name="box-" + secrets.token_hex(3))
        ecosystem.save_device_profile(int(device["id"]), {}, eid)
        prof = ecosystem.profile_for_box(db.get_device_by_id(int(device["id"])), "https://fotos.test")
    assert prof["event"]["moderation"] is True
    assert prof["event"]["qr_query"].startswith("utm_source=photobox")
    assert prof["event"]["utm"]["utm_campaign"] == slug


def test_profile_without_event_is_still_complete(app):
    from app import db
    with app.app_context():
        db.init_db()
        ecosystem.init_schema()
        device = db.create_device(name="box-" + secrets.token_hex(3))
        prof = ecosystem.profile_for_box(db.get_device_by_id(int(device["id"])), "https://fotos.test")
    assert prof["event"]["moderation"] is False
    assert prof["event"]["qr_query"] == ""
