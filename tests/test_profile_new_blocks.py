"""The new profile blocks survive a round trip through the admin form."""
def _admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def test_profile_form_saves_luts_chroma_mail_qr(client):
    client.get("/healthz")                      # Schema anlegen
    from app import db, ecosystem
    ecosystem.init_schema()
    _admin(client)
    dev_id = db.create_device(name="Box 1")["id"]
    r = client.get(f"/admin/devices/{dev_id}/booth")
    assert r.status_code == 200 and b"LUT-Filter" in r.data and b"Greenscreen" in r.data

    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "format_4:5": "on", "default_format": "4:5",
        "luts_enabled": "on", "lut_show": ["golden-warm", "nope"], "lut_strength": "0.8",
        "lut_baked": "natural-portrait", "lut_baked_strength": "0.5",
        "chroma_enabled": "on", "chroma_guest_choice": "on", "chroma_color": "green",
        "chroma_colors": "#101014\nkaputt", "chroma_tolerance": "0.4",
        "mail_enabled": "on", "mail_host": "smtp.example.de", "mail_port": "465",
        "mail_security": "ssl", "mail_from_addr": "box@example.de", "mail_attach": "web",
        "qr_mode": "custom", "qr_url": "https://example.de/{uid}", "qr_utm": "utm_source=fotobox",
    }, follow_redirects=True)
    assert r.status_code == 200
    from app import ecosystem as eco
    prof = eco.device_profile(db.get_device_by_id(dev_id))
    assert prof["luts"]["show"] == ["golden-warm"] and prof["luts"]["baked"] == "natural-portrait"
    assert prof["luts"]["strength"] == 0.8
    assert prof["chroma"]["enabled"] and prof["chroma"]["backgrounds"] == ["#101014"]
    assert prof["mail"]["host"] == "smtp.example.de" and prof["mail"]["port"] == 465
    assert prof["qr"] == {"mode": "custom", "url": "https://example.de/{uid}",
                          "utm": "utm_source=fotobox", "label": ""}
