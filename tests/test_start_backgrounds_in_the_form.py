"""The start picture is chosen in the gallery's booth form, built-in or uploaded.

Owner, 16.11.2026: he expects a background on the start screen and wants to
change it himself. The booth ships neutral backgrounds (drawn by
tools/make_start_backgrounds.py), selectable without an upload.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


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


def test_both_sides_know_the_same_backgrounds():
    from app import ecosystem
    sys.path.insert(0, str(ROOT / "booth"))
    from boothcore import config
    assert config.START_BUILTINS == ecosystem.START_BUILTINS
    import make_start_backgrounds as gen
    assert set(gen.BACKGROUNDS) == set(ecosystem.START_BUILTINS)


def test_the_shipped_files_are_the_generated_ones():
    import make_start_backgrounds as gen
    for folder in gen.TARGETS:
        for key, (_, body) in gen.BACKGROUNDS.items():
            assert (folder / f"{key}.svg").read_text(encoding="utf-8") == gen.svg(body), (folder, key)


def test_the_form_offers_the_backgrounds_with_a_preview_and_an_upload_link(client):
    _admin(client)
    from app import db
    dev_id = db.create_device(name="Start-Box 1")["id"]
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'value="builtin:abend.svg"' in body and "booth-start/abend.svg" in body
    assert "bp-start-preview" in body and "#upload" in body
    assert 'name="start_fit"' in body and 'name="start_dim"' in body


def test_a_built_in_background_survives_the_form_and_shows_in_the_preview(client):
    _admin(client)
    from app import db, ecosystem
    dev_id = db.create_device(name="Start-Box 2")["id"]
    token = _token(client, dev_id)
    client.post(f"/admin/devices/{dev_id}/booth", data={
        "csrf_token": token, "mode_photo": "on", "start_media": "builtin:salbei.svg",
        "start_fit": "cover", "start_dim": "20"}, follow_redirects=True)
    start = ecosystem.device_profile(db.get_device_by_id(dev_id))["start"]
    assert start["media"] == ["builtin:salbei.svg"] and start["fit"] == "cover" and start["dim"] == 20
    body = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    assert 'data-fit="cover"' in body and "booth-start/salbei.svg" in body
