"""Nur die Papierformate zeigen, die der Drucker wirklich kann.

Ein SELPHY CP1300 druckt Postkarte und Kartengröße — sonst nichts. Wer ihm
13 × 18 einstellt, merkt das am Eventabend, wenn nichts herauskommt. Deshalb
stehen die Formate des angeschlossenen Druckers oben und alles andere darunter,
und ein unpassendes Format wird beim Ansehen der Seite benannt.

Blockiert wird nichts: Es gibt Aufbauten, die wir hier nicht kennen.
"""
import secrets

import pytest

from app.blueprints.admin_booth import PAPERS, papers_for


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    return client


def _device_mit_drucker(client, name):
    from app import db, ecosystem
    ecosystem.init_schema()
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    db.record_heartbeat(dev["id"], {"printer_name": name, "printer_status": "ready",
                                    "camera_state": "ready", "disk_free_gb": 50}, "green", "127.0.0.1")
    return dev


def test_a_known_printer_gets_its_own_list():
    assert papers_for("Canon SELPHY CP1300") == ["postcard", "card"]
    assert papers_for("DNP DS-RX1") == ["10x15", "5x15", "15x20"]


def test_an_unknown_printer_keeps_every_format():
    assert papers_for("Irgendein Drucker") == []
    assert papers_for("") == []


def test_every_listed_format_really_exists():
    """Sonst stünde in der Liste ein Format, das die Box nicht bauen kann."""
    from app.blueprints.admin_booth import DRUCKER_FORMATE, PAPER_IDS
    for name, formate in DRUCKER_FORMATE.items():
        for f in formate:
            assert f in PAPER_IDS, f"{name}: {f}"


def test_the_form_groups_the_formats(admin, client):
    dev = _device_mit_drucker(client, "Canon SELPHY CP1300")
    seite = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert "kann</optgroup>" in seite.replace('"', "") or "SELPHY CP1300 kann" in seite
    assert "andere Formate" in seite


def test_a_format_the_printer_cannot_do_is_named(admin, client):
    from app import ecosystem
    dev = _device_mit_drucker(client, "Canon SELPHY CP1300")
    ecosystem.save_device_profile(dev["id"], {"print_layout": {"paper": "13x18"}}, None)
    seite = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert "steht nicht in der Liste des angeschlossenen Druckers" in seite


def test_without_a_known_printer_nothing_is_claimed(admin, client):
    dev = _device_mit_drucker(client, "Brother HL-1234")
    seite = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert "andere Formate" not in seite
