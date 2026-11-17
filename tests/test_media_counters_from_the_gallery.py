"""Paper tray, ink ribbon and job following can be set from the gallery (Paket A, d).

The SELPHY needs the operator at two moments: an empty paper tray and a used
ribbon. Both counts and the Windows job following used to be settable only
at the box; an operator at the venue with only a phone could not correct
them. They are now on REMOTE_LOCAL_KEYS (both sides), with bounds.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "booth"))


def test_the_counts_and_the_job_following_are_accepted_within_bounds():
    from app import ecosystem
    clean, refused = ecosystem.clean_remote_local(
        {"printer.tray_total": 18, "printer.ribbon_total": 36, "printer.follow_jobs": False})
    assert clean == {"printer.tray_total": 18, "printer.ribbon_total": 36, "printer.follow_jobs": False}
    assert refused == []
    clean, refused = ecosystem.clean_remote_local(
        {"printer.tray_total": -1, "printer.ribbon_total": 5000, "printer.follow_jobs": "yes"})
    assert clean == {} and len(refused) == 3


def test_the_box_checks_the_same_list_and_stores_the_values_in_its_printer_section():
    from boothcore import config as booth_config
    from app import ecosystem
    for key in ("printer.tray_total", "printer.ribbon_total", "printer.follow_jobs"):
        assert booth_config.REMOTE_LOCAL_KEYS[key] == ecosystem.REMOTE_LOCAL_KEYS[key]
    local = {"printer": {"name": "SELPHY", "tray_total": 0, "ribbon_total": 0, "follow_jobs": True}}
    clean, _ = booth_config.clean_remote_local({"printer.tray_total": 18, "printer.follow_jobs": False})
    patch = booth_config.nest_remote_local(local, clean)
    assert patch["printer"] == {"name": "SELPHY", "tray_total": 18, "ribbon_total": 0, "follow_jobs": False}


def test_the_gallery_form_has_a_field_for_each(client):
    client.get("/healthz")
    from app import db, ecosystem
    ecosystem.init_schema()
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
    dev_id = db.create_device(name="Zählerbox")["id"]
    page = client.get(f"/admin/devices/{dev_id}/booth").get_data(as_text=True)
    for name in ("hw_printer_tray_total", "hw_printer_ribbon_total", "hw_printer_follow_jobs"):
        assert f'name="{name}"' in page, name
