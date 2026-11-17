"""Drei Schalter, die aus der Ferne stimmen müssen.

* Wer die Gästemail verschickt — Galerie oder eigenes Postfach der Box.
* Ob unter den Modus-Namen die Erklärzeile steht.
* Ob an der Box aus der Galerie heraus nachgedruckt werden darf.

Alle drei stehen im Profil, also in zwei Dateien: ``boothcore/config.py`` und
``app/ecosystem.py``. Laufen die auseinander, setzt die Box beim nächsten Sync
still den Standard ein — und niemand merkt es, bis der Abend läuft.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "booth"))

from app import ecosystem                      # noqa: E402
from boothcore import config                   # noqa: E402


@pytest.fixture()
def admin_client(client):
    with client.session_transaction() as s:
        s["admin_logged_in"] = True
        s["csrf_token"] = "test-token"
    return client


def _device(client):
    client.get("/healthz")
    from app import db
    ecosystem.init_schema()
    return db.get_device_by_id(db.create_device(name="Box")["id"])


def _saved(dev):
    from app import db
    return ecosystem.device_profile(db.get_device_by_id(dev["id"]))


def test_both_sides_know_the_same_defaults():
    for block, key in (("mail", "transport"), ("start", "show_mode_hints"),
                       ("print", "reprint_from_gallery"),
                       ("gif", "from_collage"), ("gif", "from_collage_min")):
        assert config.DEFAULT_PROFILE[block][key] == ecosystem.DEFAULT_PROFILE[block][key], key


def test_the_gallery_is_the_standard_way_to_send():
    """Neu aufgestellte Box: kein Postfach nötig, damit Gästemails gehen."""
    assert config.DEFAULT_PROFILE["mail"]["transport"] == "gallery"
    assert config.DEFAULT_PROFILE["start"]["show_mode_hints"] is True
    assert config.DEFAULT_PROFILE["print"]["reprint_from_gallery"] is True


def test_the_remote_form_writes_all_three(admin_client, client):
    dev = _device(client)
    r = admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token",
        "mail_transport": "smtp", "mail_enabled": "on", "mail_host": "smtp.example.de",
        # beide Haken fehlen = aus
        "print_enabled": "on",
    })
    assert r.status_code in (200, 302)
    p = _saved(dev)
    assert p["mail"]["transport"] == "smtp"
    assert p["start"]["show_mode_hints"] is False
    assert p["print"]["reprint_from_gallery"] is False


def test_switching_back_works_too(admin_client, client):
    dev = _device(client)
    for data in ({"mail_transport": "smtp"},
                 {"mail_transport": "gallery", "show_mode_hints": "on",
                  "reprint_from_gallery": "on"}):
        admin_client.post(f"/admin/devices/{dev['id']}/booth",
                          data={"csrf_token": "test-token", **data})
    p = _saved(dev)
    assert p["mail"]["transport"] == "gallery"
    assert p["start"]["show_mode_hints"] is True and p["print"]["reprint_from_gallery"] is True


def test_an_invented_transport_falls_back_to_the_gallery(admin_client, client):
    """Was nicht 'smtp' heißt, ist 'gallery' — nie etwas Drittes, sonst
    verschickt die Box am Ende gar nichts."""
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth",
                      data={"csrf_token": "test-token", "mail_transport": "brieftaube"})
    assert _saved(dev)["mail"]["transport"] == "gallery"


def test_the_booth_page_explains_the_three(admin_client, client):
    dev = _device(client)
    body = admin_client.get(f"/admin/devices/{dev['id']}/booth").get_data(as_text=True)
    for shown in ("über die Galerie", "Erklärzeile", "Nachdrucken aus der Galerie"):
        assert shown in body, shown


# ── GIF als Zugabe zur Collage ────────────────────────────
def test_the_collage_gif_is_off_until_somebody_wants_it():
    """Eine Zugabe, die Rechenzeit kostet, schaltet man selbst ein."""
    assert config.DEFAULT_PROFILE["gif"]["from_collage"] is False
    assert config.DEFAULT_PROFILE["gif"]["from_collage_min"] == 4


def test_the_remote_form_switches_the_collage_gif(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "gif_from_collage": "on", "gif_from_collage_min": "3"})
    gif = _saved(dev)["gif"]
    assert gif["from_collage"] is True and gif["from_collage_min"] == 3


def test_an_absurd_minimum_is_pulled_back(admin_client, client):
    dev = _device(client)
    admin_client.post(f"/admin/devices/{dev['id']}/booth", data={
        "csrf_token": "test-token", "gif_from_collage": "on", "gif_from_collage_min": "99"})
    assert _saved(dev)["gif"]["from_collage_min"] == 8
