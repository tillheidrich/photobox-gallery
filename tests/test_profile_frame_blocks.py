"""Rahmen, Töne, Bildschirmschoner, Webhooks, Trigger — und Export/Import des Profils."""
import json
import secrets


def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _token(client):
    with client.session_transaction() as s:
        return s.get("csrf_token") or ""


def _setup(client, name="Box Rahmen"):
    client.get("/healthz")                      # Schema anlegen
    from app import db, ecosystem
    ecosystem.init_schema()
    _admin(client)
    dev_id = db.create_device(name=name)["id"]
    client.get(f"/admin/devices/{dev_id}/booth")   # legt das CSRF-Token in der Sitzung an
    return dev_id


def _base_form(client):
    return {"csrf_token": _token(client), "mode_photo": "on", "format_4:5": "on", "default_format": "4:5"}


def test_form_shows_and_saves_the_new_blocks(client):
    from app import db, ecosystem
    dev_id = _setup(client)
    r = client.get(f"/admin/devices/{dev_id}/booth")
    assert r.status_code == 200
    for needle in ("Rahmen &amp; Textzeilen", "Bildschirmschoner", "Webhooks",
                   "Auslösen von außen", "X-Photobox-Signature", "Profil sichern"):
        assert needle.encode("utf-8") in r.data

    data = _base_form(client)
    data.update({
        "frame_enabled": "on", "frame_on_photo": "on",
        "frame_text0_value": "{event_name}", "frame_text0_x": "50", "frame_text0_y": "90",
        "frame_text0_size": "5", "frame_text0_color": "#ffcc00", "frame_text0_align": "center",
        "frame_text0_target": "both", "frame_text0_bold": "on",
        "frame_text1_value": "  ", "frame_text2_value": "{date}", "frame_text2_x": "8",
        "frame_text2_y": "4", "frame_text2_size": "3", "frame_text2_color": "#ffffff",
        "frame_text2_align": "left", "frame_text2_target": "print",
        "print_qr_size": "300", "print_qr_position": "top-left", "print_qr_margin": "24",
        "sounds_enabled": "on", "sounds_volume": "0.5",
        "attract_enabled": "on", "attract_after": "90", "attract_source": "media",
        "attract_seconds": "4", "attract_show_hint": "on",
        "webhooks_enabled": "on", "webhook_url": "https://hooks.example.de/box",
        "webhook_secret": "geheim", "webhook_event_media_ready": "on", "webhook_event_printed": "on",
        "webhook_event_nonsense": "on",
        "http_trigger_enabled": "on", "http_trigger_token": "buzzer-token", "http_trigger_mode": "photo",
    })
    r = client.post(f"/admin/devices/{dev_id}/booth", data=data, follow_redirects=True)
    assert r.status_code == 200

    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["frame"]["enabled"] is True and p["frame"]["on_print"] is False
    assert [l["value"] for l in p["frame"]["texts"]] == ["{event_name}", "{date}"]   # leere Zeile fällt weg
    first = p["frame"]["texts"][0]
    assert first["color"] == "#ffcc00" and first["align"] == "center" and first["bold"] is True
    assert first["size"] == 5 and first["x"] == 50 and first["shadow"] is True
    assert p["frame"]["texts"][1]["target"] == "print" and p["frame"]["texts"][1]["align"] == "left"
    assert p["print_qr"] == {"size": 300, "position": "top-left", "margin": 24}
    assert p["sounds"]["enabled"] is True and p["sounds"]["volume"] == 0.5 and p["sounds"]["shutter"] == ""
    # Die neuen Felder des Bildschirmschoners fallen auf ihren Standard zurück,
    # wenn das Formular sie nicht mitschickt — die alten bleiben, wie gesendet.
    assert p["attract"]["enabled"] is True and p["attract"]["after_seconds"] == 90
    assert p["attract"]["source"] == "media" and p["attract"]["seconds_per_item"] == 4
    assert p["attract"]["show_hint"] is True
    assert p["attract"]["hint_style"] == "bar" and p["attract"]["dim"] == 25
    assert p["attract"]["color"] == "#000000" and p["attract"]["media"] == []
    assert p["webhooks"]["url"] == "https://hooks.example.de/box" and p["webhooks"]["secret"] == "geheim"
    assert p["webhooks"]["events"] == ["media_ready", "printed"]        # „nonsense" fliegt raus
    assert p["http_trigger"] == {"enabled": True, "token": "buzzer-token", "mode": "photo"}

    r = client.get(f"/admin/devices/{dev_id}/booth")        # gespeicherte Zeilen stehen wieder im Formular
    assert r.status_code == 200
    assert b'name="frame_text0_value" value="{event_name}"' in r.data
    assert b'name="frame_text1_value" value="{date}"' in r.data
    assert b'value="#ffcc00"' in r.data


def test_out_of_range_and_unknown_values_fall_back(client):
    from app import db, ecosystem
    dev_id = _setup(client, "Box Grenzen")
    data = _base_form(client)
    data.update({
        "frame_enabled": "on",
        "frame_text0_value": "x" * 300, "frame_text0_x": "400", "frame_text0_y": "-20",
        "frame_text0_size": "999", "frame_text0_color": "rot", "frame_text0_align": "diagonal",
        "frame_text0_target": "beamer",
        "frame_overlay": "gibtesnicht.png", "frame_print_overlay": "auch-nicht.png",
        "print_qr_size": "9000", "print_qr_position": "mittendrin", "print_qr_margin": "-5",
        "sounds_enabled": "on", "sounds_volume": "7", "sound_shutter": "kein-asset.mp3",
        "attract_enabled": "on", "attract_after": "1", "attract_source": "youtube", "attract_seconds": "999",
        "webhooks_enabled": "on", "webhook_url": "ftp://example.de",
        "http_trigger_enabled": "on", "http_trigger_token": "t", "http_trigger_mode": "video",
    })
    r = client.post(f"/admin/devices/{dev_id}/booth", data=data, follow_redirects=True)
    assert r.status_code == 200

    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    line = p["frame"]["texts"][0]
    assert len(line["value"]) == 120 and line["x"] == 100 and line["y"] == 0 and line["size"] == 40
    assert line["color"] == "#ffffff" and line["align"] == "center" and line["target"] == "both"
    assert p["frame"]["overlay"] == "" and p["frame"]["print_overlay"] == ""
    assert p["print_qr"] == {"size": 600, "position": "bottom-right", "margin": 0}
    assert p["sounds"]["volume"] == 1 and p["sounds"]["shutter"] == ""
    assert p["attract"]["after_seconds"] == 10 and p["attract"]["source"] == "recent"
    assert p["attract"]["seconds_per_item"] == 120
    assert p["webhooks"]["url"] == ""                      # nur http(s)
    # "video" ist an dieser Box gar nicht freigeschaltet — die Box würde den Auslöser ablehnen
    assert p["http_trigger"]["mode"] == "photo"


def test_empty_token_is_generated_once_and_kept(client):
    from app import db, ecosystem
    dev_id = _setup(client, "Box Token")
    data = _base_form(client)
    data["http_trigger_enabled"] = "on"
    r = client.post(f"/admin/devices/{dev_id}/booth", data=data, follow_redirects=True)
    assert r.status_code == 200 and b"Trigger-Token erzeugt" in r.data
    token = ecosystem.device_profile(db.get_device_by_id(dev_id))["http_trigger"]["token"]
    assert len(token) >= 20

    data = _base_form(client)                # leeres Feld lässt das gespeicherte Token stehen
    data["http_trigger_enabled"] = "on"
    client.post(f"/admin/devices/{dev_id}/booth", data=data, follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["http_trigger"]["token"] == token


def test_export_is_json_without_secrets(client):
    from app import db, ecosystem
    dev_id = _setup(client, "Box Export")
    data = _base_form(client)
    data.update({"mail_enabled": "on", "mail_host": "smtp.example.de", "mail_password": "sehr-geheim",
                 "webhooks_enabled": "on", "webhook_url": "https://hooks.example.de/box",
                 "webhook_secret": "hmac-geheim", "http_trigger_enabled": "on",
                 "http_trigger_token": "buzzer", "attract_source": "media"})
    client.post(f"/admin/devices/{dev_id}/booth", data=data, follow_redirects=True)

    r = client.get(f"/admin/devices/{dev_id}/booth/export")
    assert r.status_code == 200 and r.mimetype == "application/json"
    assert "attachment" in r.headers.get("Content-Disposition", "")
    body = json.loads(r.data.decode("utf-8"))
    assert body["mail"]["password"] == "" and body["webhooks"]["secret"] == ""
    assert body["http_trigger"]["token"] == ""
    assert "sehr-geheim" not in r.data.decode("utf-8")
    assert body["mail"]["host"] == "smtp.example.de" and body["attract"]["source"] == "media"
    assert "version" not in body and "event" not in body


def test_import_keeps_event_and_secrets(client):
    from app import db, ecosystem
    import io
    dev_id = _setup(client, "Box Import")
    event_id = db.create_event(slug="imp-" + secrets.token_hex(3), name="Import",
                               upload_token=secrets.token_hex(8))
    data = _base_form(client)
    data.update({"event_id": str(event_id), "mail_enabled": "on", "mail_host": "smtp.example.de",
                 "mail_password": "sehr-geheim", "webhooks_enabled": "on",
                 "webhook_url": "https://hooks.example.de/box", "webhook_secret": "hmac-geheim",
                 "result_seconds": "45"})
    client.post(f"/admin/devices/{dev_id}/booth", data=data, follow_redirects=True)
    before = db.get_device_by_id(dev_id)
    assert before["event_id"] == event_id

    incoming = {"version": 999, "event": {"slug": "fremd"}, "result_seconds": 80,
                "attract": {"source": "media", "after_seconds": 30},
                "mail": {"host": "smtp.neu.de", "password": ""},
                "webhooks": {"url": "https://neu.example.de/hook", "secret": ""},
                "unbekannt": {"egal": 1}}
    r = client.post(f"/admin/devices/{dev_id}/booth/import", data={
        "csrf_token": _token(client),
        "profile": (io.BytesIO(json.dumps(incoming).encode("utf-8")), "profil.json"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200 and b"Profil \xc3\xbcbernommen" in r.data

    after = db.get_device_by_id(dev_id)
    p = ecosystem.device_profile(after)
    assert after["event_id"] == event_id                              # Event bleibt an der Box
    assert after["profile_version"] > (before["profile_version"] or 1)
    assert p["mail"]["password"] == "sehr-geheim"                     # nie aus der Datei gelöscht
    assert p["webhooks"]["secret"] == "hmac-geheim"
    assert p["result_seconds"] == 80 and p["mail"]["host"] == "smtp.neu.de"
    assert p["attract"]["source"] == "media" and p["attract"]["after_seconds"] == 30
    assert p["attract"]["seconds_per_item"] == 6                      # nicht in der Datei = bleibt
    assert "unbekannt" not in p and "version" not in p


def test_import_rejects_anything_that_is_not_an_object(client):
    from app import db, ecosystem
    import io
    dev_id = _setup(client, "Box Murks")
    before = ecosystem.device_profile(db.get_device_by_id(dev_id))
    for payload, needle in ((b"[1, 2, 3]", "kein Profil"), (b"kein json", "lesbare JSON")):
        r = client.post(f"/admin/devices/{dev_id}/booth/import", data={
            "csrf_token": _token(client),
            "profile": (io.BytesIO(payload), "profil.json"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert r.status_code == 200 and needle.encode("utf-8") in r.data
    assert ecosystem.device_profile(db.get_device_by_id(dev_id)) == before


def test_export_and_import_need_an_admin(client):
    import io
    dev_id = _setup(client, "Box Fremd")
    with client.session_transaction() as s:
        s.pop("admin_logged_in", None)
    assert client.get(f"/admin/devices/{dev_id}/booth/export").status_code in (302, 401, 403)
    r = client.post(f"/admin/devices/{dev_id}/booth/import", data={
        "csrf_token": _token(client),
        "profile": (io.BytesIO(b'{"result_seconds": 300}'), "profil.json"),
    }, content_type="multipart/form-data")
    assert r.status_code in (302, 401, 403)
    from app import db, ecosystem
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["result_seconds"] == 45


def test_privacy_mode_saves_and_warns_about_a_pointless_event(client):
    """Kita-Modus: speichern, und deutlich sagen, dass das Event leer bleibt."""
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    _admin(client)
    import secrets
    eid = db.create_event(name="Kita Fest", slug="kita-fest", upload_token=secrets.token_hex(8))
    dev_id = db.create_device(name="Kita-Box")["id"]
    client.get(f"/admin/devices/{dev_id}/booth")        # legt das CSRF-Token an
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "format_4:5": "on", "default_format": "4:5",
        "event_id": str(eid), "print_enabled": "on",
        "privacy_mode": "print_only", "privacy_delete_after": "print",
        "privacy_delete_seconds": "90", "privacy_notice": "Fotos bleiben hier.",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "lädt nichts hoch" in r.data.decode()
    prof = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert prof["privacy"]["mode"] == "print_only"
    assert prof["privacy"]["delete_seconds"] == 90
    assert prof["privacy"]["notice"] == "Fotos bleiben hier."


def test_privacy_seconds_are_clamped(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    _admin(client)
    dev_id = db.create_device(name="Grenzwerte")["id"]
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "format_4:5": "on", "default_format": "4:5",
        "privacy_mode": "print_only", "privacy_delete_seconds": "2", "privacy_delete_after": "quatsch",
    }, follow_redirects=True)
    prof = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert prof["privacy"]["delete_seconds"] == 10        # Untergrenze
    assert prof["privacy"]["delete_after"] == "print"     # unbekannt → sicherer Standard
