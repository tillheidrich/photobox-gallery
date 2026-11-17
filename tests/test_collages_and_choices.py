"""Collagen und Gastauswahl — an der Box, global und je Event.

Eine Box fährt an drei Wochenenden zu drei Kunden. Der eine will vier
Collagen und zwölf Filter, der nächste genau einen Streifen und gar keinen
Filter. Deshalb gibt es beides an zwei Stellen: an der Box (das Haus) und am
Event (der Kunde). Das Event gewinnt, wo es etwas gesagt hat.
"""
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "booth"))

from app import ecosystem                              # noqa: E402
from boothcore.media import compose                    # noqa: E402


@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    client.get("/healthz")
    return client


def _event(client, name="Hochzeit"):
    from app import db
    client.get("/healthz")
    ecosystem.init_schema()
    slug = "co-" + secrets.token_hex(3)
    eid = db.create_event(name=name, slug=slug, upload_token=secrets.token_hex(6))
    return db.get_event_by_id(eid)


def _device(client, event=None):
    from app import db
    ecosystem.init_schema()
    dev = db.create_device(name="Box " + secrets.token_hex(2))
    ecosystem.save_device_profile(dev["id"], {"modes": ["photo", "collage"]},
                                  event["id"] if event else None)
    return db.get_device_by_id(dev["id"])


# ── Katalog ───────────────────────────────────────────────
def test_both_sides_know_the_same_templates():
    """Sonst bietet die Verwaltung Vorlagen an, die es an der Box nicht gibt."""
    im_admin = [c["id"] for c in ecosystem.BUILTIN_COLLAGES]
    an_der_box = [t["id"] for t in compose.BUILTIN_TEMPLATES]
    assert im_admin == an_der_box
    for eintrag in ecosystem.BUILTIN_COLLAGES:
        vorlage = next(t for t in compose.BUILTIN_TEMPLATES if t["id"] == eintrag["id"])
        info = compose.template_info(vorlage)
        assert eintrag["shots"] == info["shots"] and eintrag["orientation"] == info["orientation"]


# ── An der Box ────────────────────────────────────────────
def test_the_box_form_saves_a_selection(admin, client):
    dev = _device(client)
    admin.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "collage_builtins": ["duo", "strips3"]})
    from app import db
    prof = ecosystem.device_profile(db.get_device_by_id(dev["id"]))
    assert prof["collage_builtins"] == ["duo", "strips3"]


def test_everything_ticked_means_the_same_as_nothing(admin, client):
    # "Everything" is the catalogue without the plain layouts (15.11.2026):
    # those are offered only where they are ticked (tests/test_plain_layouts_gallery.py).
    dev = _device(client)
    admin.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token",
        "collage_builtins": [c["id"] for c in ecosystem.BUILTIN_COLLAGES if not c.get("plain")]})
    from app import db
    assert ecosystem.device_profile(db.get_device_by_id(dev["id"]))["collage_builtins"] == []


# ── Je Event ──────────────────────────────────────────────
def test_the_event_wins_over_the_box(admin, client):
    from app import db
    ev = _event(client)
    dev = _device(client, ev)
    admin.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "event_id": str(ev["id"]),
        "collage_builtins": ["grid4"], "looks_max": "0"})
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"],
        "ev_collage_builtins": ["duo"], "ev_looks_mode": "off", "ev_looks_max": "3"})

    profil = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://x.de")
    assert profil["collage_builtins"] == ["duo"]
    assert profil["looks_mode"] == "off" and profil["looks_max"] == 3


def test_an_event_that_says_nothing_changes_nothing(admin, client):
    from app import db
    ev = _event(client)
    dev = _device(client, ev)
    admin.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "event_id": str(ev["id"]), "collage_builtins": ["grid4"]})
    admin.post(f"/admin/events/{ev['id']}/edit", data={
        "csrf_token": "test-token", "name": ev["name"], "slug": ev["slug"]})
    profil = ecosystem.profile_for_box(db.get_device_by_id(dev["id"]), "https://x.de")
    assert profil["collage_builtins"] == ["grid4"]


def test_an_event_cannot_set_whatever_it_likes(admin, client):
    """Nur eine kurze Liste darf vom Event kommen — sonst erklärt niemand mehr,
    warum dieselbe Box an zwei Abenden etwas anderes tut."""
    from app import branding, db
    ev = _event(client)
    design = branding.parse_design(ev.get("design_json"))
    design["booth"] = {"collage_builtins": ["duo"], "admin_pin": "0000", "print_layout": {"paper": "13x18"}}
    db.update_event(ev["id"], design_json=branding.dump_design(design))
    uebernommen = ecosystem.event_profile_overrides(db.get_event_by_id(ev["id"]))
    assert uebernommen == {"collage_builtins": ["duo"]}


# ── An der Box angewandt ──────────────────────────────────
def test_the_booth_only_offers_what_was_chosen(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOTH_DATA", str(tmp_path))
    monkeypatch.setenv("BOOTH_CAMERA", "mock")
    from boothcore.app import Booth
    b = Booth(base=tmp_path, start_threads=False)
    b.store.profile["collage_builtins"] = ["duo"]
    assert [t["id"] for t in b.templates()] == ["duo"]

    # "All" is the catalogue without the plain layouts (offered only ticked).
    alle = len([t for t in compose.BUILTIN_TEMPLATES if not t.get("plain")])
    b.store.profile["collage_builtins"] = []
    assert len(b.templates()) == alle

    b.store.profile["collage_builtins"] = ["gibtesnicht"]
    assert len(b.templates()) == alle     # lieber alle als keine
