"""Aus der Ferne sehen, ob der Drucker mitspielt.

Am Eventabend steht der Betreiber nicht neben der Box. Trotzdem muss er drei
Dinge wissen können: Kommt überhaupt noch etwas heraus, hängt etwas, und wie
viele Blätter sind noch in der Kassette. Und er muss von dort aus „Papier
gewechselt, weiter" sagen können.
"""
import secrets

import pytest

from app import booth as booth_bewertung


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    return client


def _box_mit_status(client, **status):
    from app import db, ecosystem
    ecosystem.init_schema()
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    voll = {"camera_state": "ready", "disk_free_gb": 40, "printer_name": "Canon SELPHY CP1300",
            "printer_status": "ready", **status}
    db.record_heartbeat(dev["id"], voll, "green", "127.0.0.1")
    return dev


# ── Bewertung ─────────────────────────────────────────────
def test_waiting_prints_turn_the_light_red():
    urteil = booth_bewertung.evaluate_fresh({"prints_held": 3, "printer_hint": "Kein Papier mehr",
                                             "printer_status": "error"})
    assert urteil["health"] == "red"
    assert any("warten auf Papier" in g for g in urteil["reasons"])
    assert any("Kein Papier mehr" in g for g in urteil["reasons"])


def test_one_of_two_printers_missing_is_a_warning_not_a_stop():
    urteil = booth_bewertung.evaluate_fresh({"printers_total": 2, "printers_ready": 1,
                                             "printer_status": "ready", "camera_state": "ready"})
    assert urteil["health"] == "yellow"
    assert any("1 von 2" in g for g in urteil["reasons"])


def test_a_nearly_empty_cassette_is_mentioned_early():
    urteil = booth_bewertung.evaluate_fresh({"sheets_left": 8, "printer_status": "ready"})
    assert any("8 Blätter" in g for g in urteil["reasons"])


def test_a_healthy_booth_says_so():
    urteil = booth_bewertung.evaluate_fresh({"printer_status": "ready", "camera_state": "ready",
                                             "disk_free_gb": 100, "prints_held": 0,
                                             "printers_total": 1, "printers_ready": 1})
    assert urteil["health"] == "green"


# ── Ansicht ───────────────────────────────────────────────
def test_the_booth_page_shows_what_is_waiting(admin, client):
    dev = _box_mit_status(client, prints_pending=4, prints_held=2, printer_hint="Kein Papier mehr",
                          sheets_left=6, printers_total=2, printers_ready=1)
    seite = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert "2 Druck(e) warten</b>" in seite
    assert "warten auf Papier</b>" not in seite     # held for any reason, not only paper
    assert "Kein Papier mehr" in seite
    assert "noch etwa 6 Blätter" in seite
    assert "1/2 bereit" in seite
    assert "Papier nachgelegt" in seite          # der Knopf ist da


def test_the_overview_shows_it_too(admin, client):
    _box_mit_status(client, prints_held=1, sheets_left=3)
    seite = admin.get("/admin/booths").get_data(as_text=True)
    assert "auf Papier oder Farbband" in seite and "3 Blätter" in seite


def test_without_a_problem_nothing_is_claimed(admin, client):
    dev = _box_mit_status(client, prints_pending=0, prints_held=0)
    seite = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert "Druck(e) warten</b>" not in seite


def test_a_printer_with_tray_and_ribbon_shows_both_counts(admin, client):
    sheets = [{"slot": 1, "label": "SELPHY", "quelle": "gezählt", "rest": 2, "gesamt": 18,
               "fach": {"rest": 2, "gesamt": 18}, "farbband": {"rest": 11, "gesamt": 18}, "leer": False}]
    dev = _box_mit_status(client, sheets=sheets)
    seite = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert "Papierfach 2 von 18 · Farbband 11 von 18 (gezählt)" in seite
    assert "noch etwa 2 von 18 Blatt" not in seite


def test_a_printer_with_one_counter_keeps_its_line(admin, client):
    sheets = [{"slot": 1, "label": "DNP", "quelle": "gezählt", "rest": 5, "gesamt": 18, "leer": False}]
    dev = _box_mit_status(client, sheets=sheets)
    seite = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert "noch etwa 5 von 18 Blatt" in seite


def test_the_release_job_can_be_sent_from_the_gallery(admin, client):
    from app import ecosystem
    dev = _box_mit_status(client, prints_held=2)
    antwort = admin.post(f"/admin/devices/{dev['id']}/job",
                         data={"csrf_token": "test-token", "type": "release_prints"})
    assert antwort.status_code in (200, 302)
    assert any(j["type"] == "release_prints" for j in ecosystem.recent_jobs(dev["id"]))


def test_the_booth_knows_that_job(client):
    """Sonst steht der Auftrag in der Galerie und passiert an der Box nichts."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "booth"))
    from app import ecosystem
    assert "release_prints" in ecosystem.JOB_TYPES


# ── SELPHY: paper tray and ink ribbon, each with the booth's "low" rule ──
def _selphy(fach, farbband, gesamt_fach=18, gesamt_band=36):
    return {"printer_status": "ready", "camera_state": "ready", "disk_free_gb": 100,
            "sheets": [{"slot": 1, "label": "SELPHY", "quelle": "gezählt",
                        "rest": min(fach, farbband), "gesamt": gesamt_fach,
                        "fach": {"rest": fach, "gesamt": gesamt_fach},
                        "farbband": {"rest": farbband, "gesamt": gesamt_band}, "leer": False}]}


def test_a_half_full_selphy_tray_is_not_a_warning():
    # rest 9 of 18 was yellow under the old rule (rest <= 10) — half of every tray.
    urteil = booth_bewertung.evaluate_fresh(_selphy(9, 30))
    assert urteil["health"] == "green", urteil["reasons"]


def test_a_low_tray_is_named_as_the_paper_tray():
    # The booth's rule: low at max(2, round(18 * 0.15)) = 3.
    urteil = booth_bewertung.evaluate_fresh(_selphy(3, 30))
    assert urteil["health"] == "yellow"
    assert any("Papierfach" in g and "3" in g for g in urteil["reasons"])
    assert not any("Farbband" in g for g in urteil["reasons"])


def test_a_low_ribbon_is_named_as_the_ribbon():
    # Ribbon 36: low at round(36 * 0.15) = 5.
    urteil = booth_bewertung.evaluate_fresh(_selphy(12, 5))
    assert urteil["health"] == "yellow"
    assert any("Farbband" in g and "5" in g for g in urteil["reasons"])
    assert not any("Papierfach" in g for g in urteil["reasons"])
    assert booth_bewertung.evaluate_fresh(_selphy(12, 6))["health"] == "green"


def test_a_printer_without_tray_and_ribbon_keeps_the_old_rule():
    status = {"printer_status": "ready", "camera_state": "ready", "disk_free_gb": 100,
              "sheets": [{"slot": 1, "quelle": "gezählt", "rest": 9, "gesamt": 400}]}
    assert booth_bewertung.evaluate_fresh(status)["health"] == "yellow"


def test_the_gallery_can_say_ribbon_changed(admin, client):
    from app import ecosystem
    assert "ribbon_changed" in ecosystem.JOB_TYPES
    dev = _box_mit_status(client, **_selphy(12, 5))
    seite = admin.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    assert 'value="ribbon_changed"' in seite and "Farbband gewechselt" in seite
