"""Papierformat im Galerie-Admin — inklusive Streifen."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _admin(client):
    client.get("/healthz")
    from app import ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True


def _token(client, dev_id) -> str:
    client.get(f"/admin/devices/{dev_id}/booth")
    with client.session_transaction() as s:
        return s.get("csrf_token") or ""


def test_gallery_and_booth_ship_the_same_block():
    from app import ecosystem
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore import config
    assert config.DEFAULT_PROFILE["print_layout"] == ecosystem.DEFAULT_PROFILE["print_layout"]


def test_strips_survive_the_form(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Druckbox")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on",
        "paper": "5x15", "per_sheet": "2", "print_margin": "2", "print_dpi": "300",
        "print_fit": "contain", "print_bg": "#f5f0e8", "rotate_to_fit": "on",
    }, follow_redirects=True)
    layout = ecosystem.device_profile(db.get_device_by_id(dev_id))["print_layout"]
    assert layout["paper"] == "5x15" and layout["per_sheet"] == 2
    assert layout["margin_mm"] == 2 and layout["fit"] == "contain"
    assert layout["background"] == "#f5f0e8" and layout["rotate_to_fit"] is True


def test_two_per_sheet_only_makes_sense_for_strips(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Druckbox 2")["id"]
    token = _token(client, dev_id)
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "paper": "10x15", "per_sheet": "2",
    }, follow_redirects=True)
    assert "nur bei Streifen" in r.get_data(as_text=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["print_layout"]["paper"] == "5x15"


def test_all_paper_sizes_are_offered(client):
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Druckbox 3")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    for paper in ("10x15", "postcard", "13x18", "15x20", "5x15", "card"):
        assert f'value="{paper}"' in body
    assert "SELPHY" in body and "2 inch cut" in body.replace("&quot;", '"').replace("„", "").replace("“", "")
