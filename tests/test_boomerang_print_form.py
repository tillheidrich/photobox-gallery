"""The boomerang's print sheet (``boomerang.print``) and its duration in the gallery form.

Off by default; the form switches it on and shows it again, and an
unticked box switches it off. The duration is bounded 1-10 s for the
boomerang (the film keeps 1-60 s), like at the booth.
"""
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


def test_off_by_default_and_the_same_on_both_sides():
    from app import ecosystem
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore import config
    assert ecosystem.DEFAULT_PROFILE["boomerang"]["print"] is False
    assert config.DEFAULT_PROFILE["boomerang"] == ecosystem.DEFAULT_PROFILE["boomerang"]


def test_the_form_switches_the_sheet_and_bounds_the_duration(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Boomerangdruck")["id"]
    token = _token(client, dev_id)
    r = client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_boomerang": "on", "boomerang_print": "on",
        "boomerang_seconds": "25", "video_seconds": "25"}, follow_redirects=True)
    assert r.status_code == 200
    p = ecosystem.device_profile(db.get_device_by_id(dev_id))
    assert p["boomerang"]["print"] is True
    assert p["boomerang"]["seconds"] == 10 and p["video"]["seconds"] == 25
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'name="boomerang_print" checked' in page
    client.post(f"/admin/devices/{dev_id}/booth", data={"csrf_token": token, "mode_boomerang": "on"},
                follow_redirects=True)
    assert ecosystem.device_profile(db.get_device_by_id(dev_id))["boomerang"]["print"] is False


def test_the_booth_editor_names_it():
    js = (ROOT / "booth" / "boothcore" / "web" / "static" / "settings.js").read_text(encoding="utf-8")
    assert '"boomerang.print":' in js and '"boomerang.seconds": [1, 10, 0.5]' in js
