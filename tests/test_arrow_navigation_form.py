"""``controls.arrow_navigation`` in the gallery form: on by default, switched off and on again."""


def test_the_form_switches_arrow_navigation(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    assert ecosystem.DEFAULT_PROFILE["controls"]["arrow_navigation"] is True
    dev_id = db.create_device(name="Pfeilbox")["id"]
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        token = s.get("csrf_token") or ""
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'name="arrow_navigation" checked' in page                 # the default is shown ticked
    client.post(f"/admin/devices/{dev_id}/booth", data={"csrf_token": token, "mode_photo": "on"},
                follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["controls"]["arrow_navigation"] is False
    client.post(f"/admin/devices/{dev_id}/booth", data={"csrf_token": token, "mode_photo": "on",
                                                        "arrow_navigation": "on"}, follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["controls"]["arrow_navigation"] is True
