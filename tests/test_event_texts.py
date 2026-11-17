"""Eigene Texte: je Event, global, und die Rangfolge dazwischen."""


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _event(client, slug="texte-test"):
    client.get("/healthz")
    import secrets

    from app import db
    eid = db.create_event(name="Texte Test", slug=slug, upload_token=secrets.token_hex(8))
    con = db.get_db()
    con.execute("UPDATE events SET published=1 WHERE id=?", (eid,))
    con.commit()
    con.close()
    return db.get_event_by_id(eid)


def test_event_text_beats_the_translation(client):
    from app import db, texts
    event = _event(client)
    db.update_event(event["id"], texts_json=texts.dump({"de": {"gallery.expired_title": "Vorbei!"}}))
    body = client.get(f"/gallery/{event['slug']}").data.decode()
    assert body                                  # Galerie erreichbar
    from app.i18n import t
    with client.application.test_request_context(f"/gallery/{event['slug']}"):
        texts.use(db.get_event_by_id(event["id"]))
        assert t("gallery.expired_title") == "Vorbei!"


def test_global_text_applies_when_the_event_says_nothing(client):
    from app import db, texts
    from app.i18n import t
    client.get("/healthz")
    db.set_setting("gallery_texts_json", texts.dump({"de": {"gallery.expired_title": "Global weg"}}))
    with client.application.test_request_context("/"):
        texts.use(None)
        assert t("gallery.expired_title") == "Global weg"


def test_event_wins_over_global(client):
    from app import db, texts
    from app.i18n import t
    event = _event(client, "rangfolge")
    db.set_setting("gallery_texts_json", texts.dump({"de": {"gallery.expired_title": "Global"}}))
    db.update_event(event["id"], texts_json=texts.dump({"de": {"gallery.expired_title": "Event"}}))
    with client.application.test_request_context("/"):
        texts.use(db.get_event_by_id(event["id"]))
        assert t("gallery.expired_title") == "Event"


def test_empty_value_keeps_the_translation(client):
    from app import db, texts
    from app.i18n import t
    client.get("/healthz")
    db.set_setting("gallery_texts_json", "{}")        # keine globale Überschreibung
    with client.application.test_request_context("/"):
        texts.use({"texts_json": texts.dump({"de": {"gallery.expired_title": "   "}})})
        assert t("gallery.expired_title") == "Diese Galerie ist abgelaufen"


def test_broken_json_does_not_break_the_page(client):
    from app import db, texts
    from app.i18n import t
    client.get("/healthz")
    db.set_setting("gallery_texts_json", "{}")
    with client.application.test_request_context("/"):
        texts.use({"texts_json": "{kaputt"})
        assert t("gallery.expired_title") == "Diese Galerie ist abgelaufen"


def test_admin_form_saves_and_shows_the_texts(client):
    from app import db, texts
    event = _event(client, "formular")
    _admin(client)
    page = client.get(f"/admin/events/{event['id']}/edit")
    assert page.status_code == 200 and b"Texte dieser Galerie" in page.data

    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    r = client.post(f"/admin/events/{event['id']}/edit", data={
        "csrf_token": token, "name": "Texte Test", "slug": "formular",
        "text_de_session.pending_title": "Gleich hast du's",
        "text_en_session.pending_title": "",
        "texts_extra_json": '{"de": {"gallery.btn.share": "Weitersagen"}}',
    }, follow_redirects=True)
    assert r.status_code == 200
    saved = texts.parse(db.get_event_by_id(event["id"])["texts_json"])
    assert saved["de"]["session.pending_title"] == "Gleich hast du's"
    assert saved["de"]["gallery.btn.share"] == "Weitersagen"
    assert "en" not in saved                        # leeres Feld legt nichts an

    again = client.get(f"/admin/events/{event['id']}/edit").data.decode()
    assert "Gleich hast du&#39;s" in again or "Gleich hast du's" in again


def test_session_page_uses_the_event_texts(client):
    from app import db, ecosystem, texts
    event = _event(client, "durchgang")
    ecosystem.init_schema()
    db.update_event(event["id"], texts_json=texts.dump({"de": {"session.pending_title": "Moment noch"}}))
    event = db.get_event_by_id(event["id"])
    uid = "abcd234567"
    code = f"{uid}-{ecosystem.signature(ecosystem.ensure_share_key(event), uid)}"
    body = client.get(f"/p/{code}").data.decode()
    assert "Moment noch" in body


def test_global_texts_can_be_set_in_the_settings_form(client):
    from app import db, texts
    client.get("/healthz")
    _admin(client)
    page = client.get("/admin/settings/homepage")
    assert page.status_code == 200 and b"Texte der Galerie" in page.data

    # Das Formular speichert alle Schalter der Seite mit — vorher merken,
    # sonst schaltet dieser Test anderen Tests die Downloads ab.
    switches = ["zip_download_enabled", "zip_download_admin_only", "selection_download_enabled",
                "download_sizes_enabled", "guest_upload_enabled", "footer_show_powered_by",
                "homepage_show_galleries", "homepage_show_empty_events"]
    before = {k: db.get_setting(k) for k in switches}

    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    r = client.post("/admin/settings/homepage", data={
        "csrf_token": token,
        "gtext_de_gallery.footer.booking_text": "Fotobox mieten? Melde dich.",
        "gtexts_extra_json": '{"en": {"gallery.btn.like": "Love it"}}',
        **{k: "1" for k, v in before.items() if v == "1"},
    }, follow_redirects=True)
    assert r.status_code == 200
    saved = texts.parse(db.get_setting("gallery_texts_json"))
    assert saved["de"]["gallery.footer.booking_text"].startswith("Fotobox mieten")
    assert saved["en"]["gallery.btn.like"] == "Love it"

    db.set_setting("gallery_texts_json", "{}")      # andere Tests nicht beeinflussen
    for key, value in before.items():
        if value is not None:
            db.set_setting(key, value)
