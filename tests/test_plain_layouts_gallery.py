"""The plain layouts ("Schlicht") in the gallery: catalogue, box form, switch.

Photos only, white or black paper (booth/tests/test_plain_layouts.py holds
the layouts themselves). Here: the gallery knows the same 20 built-ins, a
box whose form is saved with the usual ticks still offers the catalogue as
before, a ticked plain layout is stored, and the QR switch is off by default
on both sides and has its field.
"""
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "booth"))

from app import db, ecosystem                          # noqa: E402
from boothcore import config                           # noqa: E402


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    ecosystem.init_schema()
    return client


def _device():
    return db.create_device(name="Schlicht " + secrets.token_hex(2))


def test_the_gallery_knows_the_plain_family():
    plain = [c for c in ecosystem.BUILTIN_COLLAGES if c.get("plain")]
    assert len(plain) == 20
    assert {c["paper"] for c in plain} == {"10x15", "10x15-quer", "streifen"}
    assert all(c["name"].startswith("Schlicht") for c in plain)


def test_the_switches_are_off_by_default_on_both_sides():
    for key in ("collage_plain_qr", "collage_exact"):
        assert config.DEFAULT_PROFILE[key] is False
        assert ecosystem.DEFAULT_PROFILE[key] is False


def test_a_ticked_plain_layout_and_the_qr_switch_are_saved(admin):
    dev = _device()
    admin.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "collage_builtins": ["schlicht-2-schwarz"], "collage_plain_qr": "on"})
    prof = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert prof["collage_builtins"] == ["schlicht-2-schwarz"] and prof["collage_plain_qr"] is True


def test_the_form_shows_the_plain_layouts_unticked_by_default(admin):
    dev = _device()
    page = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert 'name="collage_plain_qr"' in page and 'name="collage_exact"' in page
    i = page.index('value="schlicht-1-weiss"')
    assert "checked" not in page[i:i + 120].split(">")[0]
    j = page.index('value="duo"')
    assert "checked" in page[j:j + 120].split(">")[0]
