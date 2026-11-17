"""The camera battery from the heartbeat shows on the box's status link and admin page."""
import secrets


def _box(client):
    from app import db, ecosystem
    client.get("/healthz")
    ecosystem.init_schema()
    return dict(db.create_device(name=f"Box {secrets.token_hex(3)}"))


def _beat(client, dev, status):
    return client.post("/api/v2/booth/sync", headers={"X-API-Key": dev["api_key"]}, json={"status": status})


def test_the_status_link_shows_percent_and_mains(client):
    from app import status_page
    dev = _box(client)
    token = status_page.enable(dev["id"])
    _beat(client, dev, {"camera_state": "ready", "camera_battery": 15})
    html = client.get(f"/status/{token}").get_data(as_text=True)
    assert "Kamera-Akku" in html and "15 %" in html and "bs-bad" in html
    _beat(client, dev, {"camera_state": "ready", "camera_battery": "ac"})
    assert "Netzteil" in client.get(f"/status/{token}").get_data(as_text=True)


def test_no_battery_no_line(client):
    from app import status_page
    dev = _box(client)
    token = status_page.enable(dev["id"])
    _beat(client, dev, {"camera_state": "ready"})
    assert "Kamera-Akku" not in client.get(f"/status/{token}").get_data(as_text=True)


def test_nonsense_from_a_box_is_not_shown(client):
    from app import status_page
    dev = _box(client)
    token = status_page.enable(dev["id"])
    _beat(client, dev, {"camera_state": "ready", "camera_battery": "<b>999</b>"})
    html = client.get(f"/status/{token}").get_data(as_text=True)
    assert "Kamera-Akku" not in html and "999" not in html


def test_the_admin_box_page_shows_it(client):
    dev = _box(client)
    _beat(client, dev, {"camera_state": "ready", "camera_battery": 42})
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    html = client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert "Kamera-Akku" in html and "42 %" in html
